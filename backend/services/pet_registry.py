"""
canonical 펫 레지스트리 (Phase 13.2) — **모든** 펫, BREATHING 하나만 있어도.

── 무엇을 고치는가 ─────────────────────────────────────────────────────────
Phase 10 의 운영 검색은 generated_motions 를 펫 목록으로 썼다. 그건 **프리미엄
모션이 승격됐을 때만** 채워지는 테이블이라, 무료 BREATHING 펫은 운영 콘솔에
아예 나타나지 않았다 — QR 도 공유도 만들 수 없었다. QR 제품의 주 고객이 정확히
그 사람들(기기 없는 무료 사용자)이므로, 제품의 핵심 경로가 막혀 있었다.

여기 등록되면 멤버십도 프리미엄 모션도 없이 발견된다.

── 이 모듈이 하지 않는 것 ──────────────────────────────────────────────────
생성하지 않는다. 프로바이더를 호출하지 않는다. **generated_motions 에 가짜 행을
만들지 않는다** — 그러면 asset_state 가 오염되어 없는 프리미엄 행동이 READY 로
보이고, 공개 Shaker 응답에까지 새어 나갈 수 있다.

구독·테마·결제 모듈을 import 하지 않는다.

── 소유권 모델 ─────────────────────────────────────────────────────────────
premium_purchase.assert_pet_owned 와 같은 **최초 사용 시 귀속**(TOFU)이다:
아직 등록되지 않은 pet_id 는 등록하는 사람이 소유자가 되고, 이미 등록된 pet_id 를
다른 사용자가 등록하려 하면 거절한다.

남는 위험은 "아직 등록되지 않은 남의 pet_id 를 선점하는 것"인데, pet_id 는
content_id(UUID) 에서 파생되므로 추측할 수 없다. 기존 소유권 검사와 같은 수준이다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

SOURCE_APP = "app"
SOURCE_OPS = "ops"


class PetRegistryError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _table() -> str:
    return os.getenv("PETS_TABLE", "pets")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


_MOCK_PETS: dict[str, dict[str, Any]] = {}


def __reset_for_tests() -> None:
    _MOCK_PETS.clear()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RegisteredPet:
    pet_id: str
    user_id: str
    content_id: Optional[str] = None
    breathing_bucket: Optional[str] = None
    breathing_object_path: Optional[str] = None
    source: str = SOURCE_APP
    created_at: Optional[str] = None
    #: BREATHING 영상이 배경을 이미 담고 있는가 (Phase 27).
    #: **없으면 false** 다 — 마이그레이션 이전 행에는 이 컬럼이 아예 없고,
    #: 그 행들은 전부 레거시(지금까지처럼 재생)여야 한다.
    background_baked: bool = False


_SELECT = (
    "pet_id, user_id, content_id, breathing_bucket, breathing_object_path, "
    "source, created_at, background_baked"
)


def _to_pet(row: dict[str, Any]) -> RegisteredPet:
    return RegisteredPet(
        pet_id=str(row.get("pet_id") or ""),
        user_id=str(row.get("user_id") or ""),
        content_id=(row.get("content_id") or None),
        breathing_bucket=(row.get("breathing_bucket") or None),
        breathing_object_path=(row.get("breathing_object_path") or None),
        source=str(row.get("source") or SOURCE_APP),
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
        # 컬럼이 없는(마이그레이션 전) 행은 None 이고, None 은 레거시다.
        background_baked=row.get("background_baked") is True,
    )


def content_id_of(pet_id: str) -> Optional[str]:
    """petId → content_id. 프론트 규약(pet-identity.ts)과 같은 규칙."""
    p = (pet_id or "").strip()
    if not p.startswith("pet_") or len(p) <= 4:
        return None
    return p[4:]


async def get(pet_id: str) -> Optional[RegisteredPet]:
    pid = (pet_id or "").strip()
    if not pid:
        return None

    if _use_db() and _supabase():
        try:
            r = _supabase().table(_table()).select(_SELECT).eq("pet_id", pid).limit(1).execute()
            data = getattr(r, "data", None) or []
            return _to_pet(data[0]) if data else None
        except Exception as e:
            # 조회 실패를 "없음"으로 답하지 않는다 — 등록된 펫이 사라진 것처럼 보이고,
            # 그 상태로 등록하면 소유권 검사를 우회하게 된다.
            logger.exception("펫 레지스트리 조회 실패 (pet=%s)", pid)
            raise PetRegistryError(
                "PET_REGISTRY_UNAVAILABLE", "펫 정보를 확인하지 못했습니다.", status=503
            ) from e

    row = _MOCK_PETS.get(pid)
    return _to_pet(row) if row else None


async def owner_of(pet_id: str) -> Optional[str]:
    pet = await get(pet_id)
    return pet.user_id if pet else None


def _verify_breathing(bucket: str, path: str) -> bool:
    """
    BREATHING 객체가 **실제로 존재하는가**.

    서명 시도가 곧 존재 확인이다 — 없는 객체에는 서명이 만들어지지 않는다
    (shaker_ops.locate_breathing 과 같은 방식). 별도 목록 API 를 두지 않는다.
    """
    from .asset_url_refresh import StorageObject, sign_object

    return bool(sign_object(StorageObject(bucket=bucket, path=path)))


def _resolve_breathing(
    *, breathing_url: str | None, bucket: str | None, path: str | None
) -> tuple[str, str]:
    """(bucket, object_path) — URL 에서 유도하거나 직접 받은 값."""
    from .asset_url_refresh import default_bucket, parse_storage_object

    b = (bucket or "").strip()
    p = (path or "").strip()
    if p:
        return (b or default_bucket()), p

    obj = parse_storage_object(breathing_url)
    if obj:
        return obj.bucket, obj.path

    raise PetRegistryError(
        "BREATHING_LOCATION_UNKNOWN",
        "BREATHING 영상의 스토리지 위치를 알 수 없습니다.",
        status=400,
    )


async def register(
    *,
    user_id: str,
    pet_id: str,
    content_id: str | None = None,
    breathing_url: str | None = None,
    breathing_bucket: str | None = None,
    breathing_object_path: str | None = None,
    source: str = SOURCE_APP,
    verify: bool = True,
) -> RegisteredPet:
    """
    펫을 등록한다. **멱등이며, BREATHING 이 실제로 있어야 한다.**

    이미 등록돼 있으면:
      * 같은 소유자 → 그대로 돌려준다 (재등록해도 아무것도 바뀌지 않는다)
      * 다른 소유자 → 거절한다 (남의 펫을 가로챌 수 없다)

    verify=False 는 스토리지가 없는 환경(로컬/테스트)을 위한 것이며, 운영 백필에서
    쓸 때는 호출부가 그 사실을 알고 있어야 한다.
    """
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    if not uid or not pid:
        raise PetRegistryError("PET_REGISTER_INVALID", "user_id 와 pet_id 가 필요합니다.")

    existing = await get(pid)
    if existing:
        if existing.user_id != uid:
            # **핵심 보호**: 이미 등록된 펫을 다른 사용자가 등록할 수 없다.
            raise PetRegistryError(
                "PET_NOT_OWNED", "이 펫에 접근할 권한이 없습니다.", status=403
            )
        return existing

    bucket, path = _resolve_breathing(
        breathing_url=breathing_url, bucket=breathing_bucket, path=breathing_object_path
    )

    if verify and not _verify_breathing(bucket, path):
        # BREATHING 이 없으면 등록하지 않는다. 등록해 두면 운영이 QR 을 붙일 수
        # 있게 되고, 열어 보면 아무것도 재생되지 않는 링크가 인쇄돼 나간다.
        raise PetRegistryError(
            "BREATHING_NOT_FOUND",
            "BREATHING 영상을 찾을 수 없어 펫을 등록하지 않았습니다.",
            status=409,
        )

    cid = (content_id or "").strip() or content_id_of(pid)

    # ── 배경이 구워졌는가 — **서버가 자기 기록으로 판정한다** (Phase 27) ─────
    # 요청 바디에서 받지 않는다. 브라우저가 자산에 대한 사실을 주장하게 두면,
    # 값이 틀렸을 때 재생이 조용히 깨지고 원인을 어디서도 찾을 수 없다.
    # 우리는 이미 근거를 갖고 있다: 구운 생성은 전부 scene_generation_jobs 를
    # 거치고(유료 제출의 유일한 통로다) 완료 시점에 video_url 이 남는다.
    baked = False
    if cid:
        try:
            from . import scene_generation_jobs

            baked = await scene_generation_jobs.produced_baked_object(
                user_id=uid, content_id=cid, bucket=bucket, object_path=path
            )
        except Exception:  # noqa: BLE001 — 배경 표시가 등록을 막지 않는다
            logger.warning("구움 여부 판정 실패 — 레거시로 등록한다 (pet=%s)", pid, exc_info=True)

    row: dict[str, Any] = {
        "pet_id": pid,
        "user_id": uid,
        "content_id": cid,
        "breathing_bucket": bucket,
        "breathing_object_path": path,
        "source": source,
        "background_baked": baked,
        "created_at": _now().isoformat(),
        "updated_at": _now().isoformat(),
    }

    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).insert(row).execute()
        except Exception as e:
            # 경쟁으로 이미 삽입됐을 수 있다 — 다시 읽어 같은 소유자면 성공으로 본다.
            again = await get(pid)
            if again and again.user_id == uid:
                return again
            logger.exception("펫 등록 실패 (user=%s pet=%s)", uid, pid)
            raise PetRegistryError(
                "PET_REGISTRY_UNAVAILABLE", "펫을 등록하지 못했습니다.", status=503
            ) from e
        return _to_pet(row)

    _MOCK_PETS[pid] = row
    logger.info("펫 등록 — user=%s pet=%s source=%s", uid, pid, source)
    return _to_pet(row)


@dataclass(frozen=True)
class PetSearchRow:
    pet_id: str
    user_id: str
    content_id: Optional[str] = None
    created_at: Optional[str] = None


async def search(query: str | None = None, *, limit: int = 200) -> list[PetSearchRow]:
    """
    pet_id / user_id / content_id 부분 일치.

    **정확한 pet_id 로도 찾을 수 있어야 한다** — 운영이 고객에게 받은 id 를 그대로
    붙여 넣는 것이 가장 흔한 경로다.
    """
    q = (query or "").strip().lower()

    if _use_db() and _supabase():
        try:
            r = _supabase().table(_table()).select(_SELECT).limit(2000).execute()
            rows = getattr(r, "data", None) or []
        except Exception as e:
            logger.exception("펫 레지스트리 검색 실패")
            raise PetRegistryError(
                "PET_REGISTRY_UNAVAILABLE", "펫을 조회하지 못했습니다.", status=503
            ) from e
    else:
        rows = list(_MOCK_PETS.values())

    out: list[PetSearchRow] = []
    for row in rows:
        pet = _to_pet(row)
        if q and not (
            q in pet.pet_id.lower()
            or q in pet.user_id.lower()
            or q in (pet.content_id or "").lower()
        ):
            continue
        out.append(
            PetSearchRow(
                pet_id=pet.pet_id,
                user_id=pet.user_id,
                content_id=pet.content_id,
                created_at=pet.created_at,
            )
        )
    out.sort(key=lambda p: p.pet_id)
    return out[: max(1, min(limit, 500))]
