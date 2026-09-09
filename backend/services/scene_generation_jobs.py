"""
장면 × 행동 → 프로바이더 작업 1건. **같은 그림에 두 번 과금하지 않는다.**

── 무엇이 비어 있었나 ───────────────────────────────────────────────────────
System B(크레딧 액션)에는 이미 강한 멱등성이 있다: find_active_job_for_key 가
비종료 작업을 찾아 재제출을 막고, generation_reconciler 가 웹훅 유실을 복구한다.

System A(`/generate-pet-video`, `/generate-idle-variant`)에는 **아무것도 없었다.**
이 엔드포인트들은 동기식이다 — 제출하고 최대 20분 폴링한다. 클라이언트 타임아웃은
25분이고, 그 사이 새로고침·재시도·프록시 502 가 한 번이라도 나면 요청이 다시 들어와
**두 번째 유료 Luma 작업**이 제출됐다. 첫 작업은 여전히 돌고 있고, 둘 다 과금된다.

이 모듈이 그 구멍을 메운다. 키는 (user_id, scene_id, behavior) 이고 — 장면이
같고 행동이 같으면 결과도 같아야 하므로 그것이 자연스러운 동일성 단위다.

── 이 모듈이 하지 않는 것 ───────────────────────────────────────────────────
프로바이더를 부르지 않는다. 프롬프트를 만들지 않는다. 과금하지 않는다.
System B 의 멱등성 경로를 대체하지 않는다 — 그쪽은 그대로 둔다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: 아직 결과가 확정되지 않은 상태 — 이때 재제출하면 이중 과금이다.
ACTIVE_STATUSES = ("submitted", "pending", "dreaming")
TERMINAL_STATUSES = ("completed", "failed")

#: 예약만 되고 제출 기록이 없는 작업을 "죽은 것"으로 보기까지의 시간(초).
#:
#: 이 창이 필요한 이유: 예약 직후 워커가 죽으면 status=pending, provider_job_id=None
#: 인 행이 남는다. 그 상태로는 유료 작업이 실제로 나갔는지 **알 수 없다**.
#:   * 창 안 → 지금 다른 요청이 제출 중일 가능성이 높다 → 막는다(409)
#:   * 창 밖 → 워커가 죽은 것으로 보고 한 번 회수한다
#: 창을 없애면 그 장면은 영원히 생성 불가가 되고, 창을 0 으로 두면 동시 요청이
#: 서로를 회수하며 이중 제출한다.
STALE_PENDING_SEC = int(os.getenv("SCENE_JOB_STALE_PENDING_SEC", "900"))


class IdempotencyUnavailableError(Exception):
    """
    멱등성 저장소를 신뢰할 수 없다 — **아무것도 제출하지 않는다.**

    예전에는 여기서 로그만 남기고 생성을 계속했다. 그러면 테이블이 잠깐 죽은
    동안 들어온 모든 재시도가 각각 유료 작업을 만든다. 보호 장치가 없는 상태로
    계속 진행하는 것은 보호 장치가 없는 것과 같다.
    """

    code = "GENERATION_IDEMPOTENCY_UNAVAILABLE"
    message = "생성 중복 방지 저장소를 확인할 수 없어 생성을 시작하지 않았습니다. 잠시 후 다시 시도해 주세요."
    status = 503


def _table() -> str:
    return os.getenv("SCENE_GENERATION_JOBS_TABLE", "scene_generation_jobs")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


_MOCK: dict[str, dict[str, Any]] = {}


def __reset_for_tests() -> None:
    _MOCK.clear()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SceneJob:
    job_key: str
    user_id: str
    scene_id: str
    behavior: str
    status: str
    provider: Optional[str] = None
    #: 프로바이더가 준 작업 id. **이것이 있으면 다시 제출하지 않고 폴링한다.**
    provider_job_id: Optional[str] = None
    video_url: Optional[str] = None
    content_id: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def completed(self) -> bool:
        return self.status == "completed" and bool(self.video_url)

    @property
    def age_seconds(self) -> Optional[float]:
        """예약된 지 얼마나 됐는가. 판정 불가면 None."""
        raw = self.updated_at or self.created_at
        if not raw:
            return None
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()

    @property
    def reserved_but_never_submitted(self) -> bool:
        """
        자리는 잡았는데 프로바이더 id 가 없다.

        두 가지가 겹쳐 있다: (a) 지금 다른 요청이 제출 중, (b) 워커가 제출 전에
        죽었다. 둘을 구별할 방법이 없으므로 **시간**으로 가른다.
        """
        return self.status == "pending" and not self.provider_job_id

    @property
    def is_stale_reservation(self) -> bool:
        if not self.reserved_but_never_submitted:
            return False
        age = self.age_seconds
        return age is not None and age >= STALE_PENDING_SEC


def job_key(user_id: str, scene_id: str, behavior: str) -> str:
    """
    (사용자, 장면, 행동) → 안정적인 키.

    장면 id 가 이미 (콘텐츠·배경·배치)에서 결정적으로 파생되므로, 같은 그림을 두 번
    승인해도 같은 키가 나온다 — 그것이 재과금을 막는 근거다.
    """
    return f"{(user_id or '').strip()}|{(scene_id or '').strip()}|{(behavior or '').strip().upper()}"


def _from_row(row: dict[str, Any]) -> SceneJob:
    return SceneJob(
        job_key=str(row.get("job_key") or ""),
        user_id=str(row.get("user_id") or ""),
        scene_id=str(row.get("scene_id") or ""),
        behavior=str(row.get("behavior") or ""),
        status=str(row.get("status") or "pending"),
        provider=(row.get("provider") or None),
        provider_job_id=(row.get("provider_job_id") or None),
        video_url=(row.get("video_url") or None),
        content_id=(row.get("content_id") or None),
        error=(row.get("error") or None),
        created_at=(row.get("created_at") or None),
        updated_at=(row.get("updated_at") or None),
    )


async def get(user_id: str, scene_id: str, behavior: str) -> Optional[SceneJob]:
    key = job_key(user_id, scene_id, behavior)
    if _use_db() and _supabase():
        try:
            r = (
                _supabase()
                .table(_table())
                .select("*")
                .eq("job_key", key)
                .limit(1)
                .execute()
            )
            data = getattr(r, "data", None) or []
            return _from_row(data[0]) if data else None
        except Exception as e:
            # 조회 실패로 **재제출하지 않는다** — 그것이 이중 과금의 입구다.
            logger.exception("scene job 조회 실패 (key=%s)", key)
            raise IdempotencyUnavailableError() from e
    row = _MOCK.get(key)
    return _from_row(row) if row else None


async def produced_baked_object(
    *, user_id: str, content_id: str, bucket: str, object_path: str
) -> bool:
    """
    **우리가** 이 객체를 구운 영상으로 만든 적이 있는가. (Phase 27)

    ── 왜 이 질문을 서버가 하는가 ──────────────────────────────────────────
    `background_baked` 는 지금까지 브라우저 sessionStorage 에만 살았다. 펫을
    등록하는 것은 브라우저이므로, 가장 쉬운 길은 그 값을 요청에 실어 받는
    것이었다. 그러면 **브라우저가 자산에 대한 사실을 주장**하게 된다.

    그럴 필요가 없다. 우리는 이미 자기 기록을 갖고 있다 — 구운 생성은 전부
    scene_generation_jobs 를 거치고(그것이 유료 제출의 유일한 통로다) 완료
    시점에 video_url 이 적힌다. 그러니 등록하려는 객체가 그 기록 중 하나와
    **정확히 같은 객체**인지 보면 된다.

    URL 문자열이 아니라 (bucket, object_path) 로 비교하는 이유: 저장된 값도
    지금 받은 값도 서명 URL 이고, 서명은 매번 다르다. 경로는 다르지 않다.

    확신이 없으면 **False**. 추측으로 True 를 적으면 멀쩡한 레거시 영상이
    검은 사각형인 채로 재생된다 — 모르는 쪽의 대가가 훨씬 싸다.
    """
    from .asset_url_refresh import parse_storage_object

    uid = (user_id or "").strip()
    cid = (content_id or "").strip()
    b = (bucket or "").strip()
    path = (object_path or "").strip()
    if not uid or not cid or not path:
        return False

    rows: list[dict[str, Any]]
    if _use_db() and _supabase():
        try:
            r = (
                _supabase()
                .table(_table())
                .select("video_url")
                .eq("user_id", uid)
                .eq("content_id", cid)
                .eq("status", "completed")
                .execute()
            )
            rows = list(getattr(r, "data", None) or [])
        except Exception:
            # 조회 실패는 "구워지지 않았다"로 답한다. 여기서 예외를 올리면
            # 배경 표시 하나 때문에 펫 등록 자체가 실패한다 — 등록되지 않은
            # 펫은 운영에서 보이지 않고 QR 도 붙지 않는다.
            logger.warning(
                "구움 여부 조회 실패 — 레거시로 간주한다 (user=%s content=%s)",
                uid, cid, exc_info=True,
            )
            return False
    else:
        rows = [
            row
            for row in _MOCK.values()
            if row.get("user_id") == uid
            and row.get("content_id") == cid
            and row.get("status") == "completed"
        ]

    for row in rows:
        obj = parse_storage_object(row.get("video_url"))
        if obj and obj.path == path and (not b or obj.bucket == b):
            return True
    return False


async def reserve(
    *, user_id: str, scene_id: str, behavior: str, content_id: str | None = None
) -> tuple[SceneJob, bool]:
    """
    작업 자리를 잡는다. `(job, is_new)`.

    is_new=False 면 **이미 누군가 제출했거나 끝냈다** — 호출부는 제출하지 말고
    기존 작업을 폴링하거나 저장된 결과를 돌려줘야 한다.

    자리를 먼저 잡고 나중에 provider_job_id 를 채우는 순서가 중요하다. 제출부터
    하면 그 사이에 들어온 두 번째 요청이 빈 테이블을 보고 또 제출한다.
    """
    key = job_key(user_id, scene_id, behavior)
    existing = await get(user_id, scene_id, behavior)
    if existing:
        return existing, False

    row = {
        "job_key": key,
        "user_id": (user_id or "").strip(),
        "scene_id": (scene_id or "").strip(),
        "behavior": (behavior or "").strip().upper(),
        "content_id": (content_id or "").strip() or None,
        "status": "pending",
        "created_at": _now(),
        "updated_at": _now(),
    }

    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).insert(row).execute()
        except Exception as e:
            msg = f"{e}".lower()
            if "duplicate" in msg or "unique" in msg or "23505" in msg:
                # 경합에서 졌다 — 이긴 쪽의 작업을 쓴다. 새로 제출하지 않는다.
                again = await get(user_id, scene_id, behavior)
                if again:
                    return again, False
            logger.exception("scene job 예약 실패 (key=%s)", key)
            raise IdempotencyUnavailableError() from e
    else:
        if key in _MOCK:
            return _from_row(_MOCK[key]), False
        _MOCK[key] = row

    return _from_row(row), True


async def _patch(key: str, patch: dict[str, Any]) -> Optional[SceneJob]:
    patch = {**patch, "updated_at": _now()}
    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).update(patch).eq("job_key", key).execute()
        except Exception:
            logger.exception("scene job 갱신 실패 (key=%s)", key)
            raise
        row = (
            getattr(
                _supabase().table(_table()).select("*").eq("job_key", key).limit(1).execute(),
                "data",
                None,
            )
            or [None]
        )[0]
        return _from_row(row) if row else None

    row = _MOCK.get(key)
    if row is None:
        return None
    row.update(patch)
    return _from_row(row)


async def mark_submitted(
    *, user_id: str, scene_id: str, behavior: str, provider: str, provider_job_id: str
) -> Optional[SceneJob]:
    """
    프로바이더 작업 id 를 **즉시** 기록한다.

    제출 직후 한 번의 await 도 끼우지 않는 이유: 그 사이에 프로세스가 죽거나
    요청이 재시도되면 id 를 잃은 유료 작업이 남는다. 되찾을 방법이 없다.
    """
    return await _patch(
        job_key(user_id, scene_id, behavior),
        {"status": "submitted", "provider": provider, "provider_job_id": provider_job_id},
    )


async def mark_completed(
    *, user_id: str, scene_id: str, behavior: str, video_url: str
) -> Optional[SceneJob]:
    return await _patch(
        job_key(user_id, scene_id, behavior),
        {"status": "completed", "video_url": video_url, "error": None},
    )


async def mark_failed(
    *, user_id: str, scene_id: str, behavior: str, error: str
) -> Optional[SceneJob]:
    """
    실패 기록. **재시도를 막지 않는다** — 종료 상태이므로 다음 요청은 새 작업을
    예약할 수 있다. 막아야 하는 것은 '아직 돌고 있는데 또 제출' 뿐이다.
    """
    return await _patch(
        job_key(user_id, scene_id, behavior),
        {"status": "failed", "error": (error or "")[:500]},
    )


async def reclaim_stale_reservation(
    *, user_id: str, scene_id: str, behavior: str
) -> bool:
    """
    죽은 예약을 회수한다. 회수했으면 True.

    **오래된 pending 만** 회수한다. 제출 기록(provider_job_id)이 있는 작업은
    절대 건드리지 않는다 — 그쪽은 유료 작업이 실제로 존재하므로 폴링해서
    되찾아야 하고, 회수하면 그 돈이 그대로 사라진다.
    """
    job = await get(user_id, scene_id, behavior)
    if not job or not job.is_stale_reservation:
        return False
    logger.warning(
        "scene job 죽은 예약 회수 — key=%s age=%.0fs (워커가 제출 전에 종료된 것으로 본다)",
        job.job_key,
        job.age_seconds or 0.0,
    )
    await clear_for_retry(user_id=user_id, scene_id=scene_id, behavior=behavior)
    return True


async def clear_for_retry(*, user_id: str, scene_id: str, behavior: str) -> None:
    """실패한 작업 자리를 비운다 — 다음 요청이 새로 예약할 수 있게."""
    key = job_key(user_id, scene_id, behavior)
    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).delete().eq("job_key", key).execute()
        except Exception:
            logger.exception("scene job 삭제 실패 (key=%s)", key)
        return
    _MOCK.pop(key, None)
