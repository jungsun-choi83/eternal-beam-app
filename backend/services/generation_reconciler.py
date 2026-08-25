"""
누락된 웹훅 복구 — 정체된(stale) 작업을 프로바이더에 직접 물어보고 기존 완료 경로로 흘려보낸다.

왜 필요한가
-----------
System B 는 전적으로 push(웹훅)에 의존한다. 웹훅이 유실되면(터널 끊김, 배포 중
재시작, 프로바이더 전달 실패) 작업은 `submitted` 로 영원히 남는다. 재시도도 돌지
않고, 세션은 finalize 되지 않으며, 환불도 일어나지 않는다. 사용자는 4코인을 낸 채
아무것도 받지 못한다.

설계 원칙: **완료 처리를 두 번 구현하지 않는다.**
리컨사일러는 상태만 읽어 GenerationOutcome 으로 정규화하고, 웹훅이 쓰는 것과
정확히 같은 함수(handle_luma_webhook_for_credit)에 넘긴다. 후보 저장 → 검증 →
승격 → 재시도 → 세션 확정은 전부 그쪽 경로가 담당한다.

멱등성은 공짜로 얻는다: 그 함수는 이미 promoted_at / 종료 상태 / refunded_at 로
재전송을 막는다. 웹훅이 먼저 도착했다면 리컨사일러는 자동으로 no-op 이 된다.

스케줄링은 이 파일의 책임이 아니다 — 호출 가능한 서비스 함수만 제공한다.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..models.hybrid_business import MotionJobRow, MotionJobStatus
from .video_generation import PROVIDER_LUMA, GenerationOutcome, is_wan_provider

logger = logging.getLogger(__name__)

#: 이 시간(초)을 넘겨도 종료되지 않은 작업을 정체된 것으로 본다.
#: 실측 생성 시간은 Wan ~20-120초, Luma 는 더 길다. 기본 15분은 충분히 보수적이다.
DEFAULT_RECONCILE_AFTER_SEC = 900

#: 리컨사일러가 들여다보는 비종료 상태.
NON_TERMINAL = (MotionJobStatus.submitted, MotionJobStatus.pending, MotionJobStatus.dreaming)


def reconcile_after_sec() -> int:
    try:
        return int(os.getenv("GENERATION_RECONCILE_AFTER_SEC", str(DEFAULT_RECONCILE_AFTER_SEC)))
    except ValueError:
        return DEFAULT_RECONCILE_AFTER_SEC


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def is_stale(row: dict[str, Any], *, now: Optional[datetime] = None, threshold_sec: Optional[int] = None) -> bool:
    """updated_at(없으면 created_at) 기준으로 정체 여부를 판단한다."""
    now = now or datetime.now(timezone.utc)
    limit = timedelta(seconds=threshold_sec if threshold_sec is not None else reconcile_after_sec())
    ts = _parse_ts(row.get("updated_at")) or _parse_ts(row.get("created_at"))
    if ts is None:
        return False  # 시각을 모르면 건드리지 않는다
    return (now - ts) >= limit


async def fetch_outcome_by_id(
    external_id: str,
    *,
    provider: Optional[str] = None,
    provider_model: Optional[str] = None,
) -> GenerationOutcome:
    """
    프로바이더 작업 id → 현재 상태. **폴링 구현은 여기 한 곳뿐이다.**

    System A(장면 기반 생성)의 복구도 이 함수를 쓴다 — 폴링을 두 번 구현하면
    한쪽만 프로바이더 응답 변화를 따라가고, 그 어긋남은 "완료된 유료 작업을
    실패로 읽고 다시 제출"로 나타난다.
    """
    external_id = (external_id or "").strip()
    provider = provider or PROVIDER_LUMA

    if is_wan_provider(provider):
        from . import wan_service

        body = await wan_service.fetch_status(external_id, model=provider_model)
        status = str(body.get("status") or "").upper()
        if status == "COMPLETED" and body.get("video_url"):
            state = "completed"
        elif status in ("FAILED", "ERROR", "CANCELLED"):
            state = "failed"
        else:
            state = "pending"
        return GenerationOutcome(
            provider=provider, external_id=external_id, state=state,
            video_url=body.get("video_url"), error=body.get("error"),
        )

    from . import luma_service

    body = await luma_service.fetch_status(external_id)
    raw = str(body.get("state") or "").lower()
    if raw == "completed" and body.get("video_url"):
        state = "completed"
    elif raw in ("failed", "error"):
        state = "failed"
    else:
        state = "pending"
    return GenerationOutcome(
        provider=PROVIDER_LUMA, external_id=external_id, state=state,
        video_url=body.get("video_url"), error=body.get("error"),
    )


async def fetch_provider_outcome(job: MotionJobRow) -> GenerationOutcome:
    """
    프로바이더에 현재 상태를 직접 물어 GenerationOutcome 으로 정규화한다.
    웹훅 본문 대신 폴링 응답을 쓸 뿐, 산출물의 모양은 완전히 같다.
    """
    return await fetch_outcome_by_id(
        job.luma_generation_id or "",
        provider=job.provider or PROVIDER_LUMA,
        provider_model=job.provider_model,
    )


async def reconcile_job(job: MotionJobRow) -> dict[str, Any]:
    """
    작업 하나를 복구한다. 정체 판정은 호출자(reconcile_stale_jobs)가 이미 했다고 본다.

    - 이미 종료 상태 → skip (웹훅이 이겼다)
    - 프로바이더가 pending → 아무것도 하지 않는다
    - completed / failed → **웹훅과 완전히 동일한 경로**로 넘긴다
    """
    from .credit_generation_service import handle_luma_webhook_for_credit

    if job.status not in NON_TERMINAL:
        return {"external_id": job.luma_generation_id, "result": "skipped_terminal"}

    try:
        outcome = await fetch_provider_outcome(job)
    except Exception as e:
        logger.warning("reconcile: 상태 조회 실패 external_id=%s: %s", job.luma_generation_id, e)
        return {"external_id": job.luma_generation_id, "result": "poll_failed", "error": str(e)[:200]}

    if outcome.state == "pending":
        return {"external_id": job.luma_generation_id, "result": "still_pending"}

    logger.info(
        "reconcile: 웹훅 누락 복구 external_id=%s action=%s provider=%s -> %s",
        job.luma_generation_id, job.action_id, job.provider, outcome.state,
    )
    summary = await handle_luma_webhook_for_credit(
        outcome.external_id, outcome.state,
        video_url=outcome.video_url, error=outcome.error,
    )
    return {
        "external_id": job.luma_generation_id,
        "result": f"reconciled_{outcome.state}",
        "summary": summary,
    }


async def list_stale_jobs(
    *, now: Optional[datetime] = None, threshold_sec: Optional[int] = None, limit: int = 50
) -> list[MotionJobRow]:
    """비종료 상태이면서 정체된 작업 목록."""
    from . import generated_motions_service as gms

    rows: list[dict[str, Any]] = []
    if gms._use_db() and gms._supabase():
        statuses = [s.value for s in NON_TERMINAL]
        r = (
            gms._supabase()
            .table(gms._jobs_table())
            .select("*")
            .in_("status", statuses)
            .limit(limit)
            .execute()
        )
        rows = r.data or []
    else:
        for j in gms._MOCK_JOBS.values():
            if j.status in NON_TERMINAL:
                rows.append({
                    "luma_generation_id": j.luma_generation_id,
                    "updated_at": getattr(j, "_mock_updated_at", None),
                    "created_at": getattr(j, "_mock_created_at", None),
                })

    out: list[MotionJobRow] = []
    for row in rows:
        if not is_stale(row, now=now, threshold_sec=threshold_sec):
            continue
        job = await gms.resolve_luma_job(row.get("luma_generation_id") or "")
        if job and job.status in NON_TERMINAL:
            out.append(job)
    return out


async def reconcile_stale_jobs(
    *, now: Optional[datetime] = None, threshold_sec: Optional[int] = None, limit: int = 50
) -> dict[str, Any]:
    """정체된 작업들을 한 번 훑어 복구한다. 스케줄링은 호출자 책임."""
    jobs = await list_stale_jobs(now=now, threshold_sec=threshold_sec, limit=limit)
    results = [await reconcile_job(j) for j in jobs]
    counts: dict[str, int] = {}
    for r in results:
        counts[r["result"]] = counts.get(r["result"], 0) + 1
    if jobs:
        logger.info("reconcile: %d개 검사 → %s", len(jobs), counts)
    return {"inspected": len(jobs), "counts": counts, "results": results}
