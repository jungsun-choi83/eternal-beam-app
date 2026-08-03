"""
auto_rigging_jobs 테이블 CRUD — SAM2+포즈추정+Spine2D 자동 리깅 잡 큐.

action_video_jobs.py(LivePortrait 큐)와 정확히 같은 패턴(Supabase(Postgres)
테이블 polling, Redis/Celery 없음)을 그대로 따른다 — 새 큐 인프라를 발명하지
않기 위해 의도적으로 구조를 복붙 수준으로 맞췄다.

테이블 스키마: supabase/migrations/20260721000300_auto_rigging_jobs.sql
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from .supabase_assets import get_client

TABLE = "auto_rigging_jobs"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(
    user_id: str,
    content_id: str,
    pet_image_url: str,
    *,
    requested_actions: Optional[list[str]] = None,
    pose_backend: str = "heuristic",
) -> dict:
    """새 잡을 status='queued'로 insert하고 생성된 행을 반환."""
    supabase = get_client()
    if not supabase:
        raise RuntimeError("Supabase가 설정되지 않았습니다(SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY 필요).")
    row = {
        "user_id": user_id,
        "content_id": content_id,
        "pet_image_url": pet_image_url,
        "requested_actions": requested_actions or [],
        "pose_backend": pose_backend,
        "status": "queued",
        "progress_json": {"stage": None, "detail": None},
    }
    res = supabase.table(TABLE).insert(row).execute()
    if not res.data:
        raise RuntimeError(f"잡 생성 실패: {res}")
    return res.data[0]


def get_job(job_id: str) -> Optional[dict]:
    supabase = get_client()
    if not supabase:
        raise RuntimeError("Supabase가 설정되지 않았습니다.")
    res = supabase.table(TABLE).select("*").eq("id", job_id).limit(1).execute()
    if res.data:
        return res.data[0]
    return None


def claim_next_job(worker_id: str, *, stale_after_minutes: int = 30) -> Optional[dict]:
    """action_video_jobs.claim_next_job()과 동일한 낙관적 잠금 패턴(자세한 설명은 그쪽 docstring 참고)."""
    supabase = get_client()
    if not supabase:
        raise RuntimeError("Supabase가 설정되지 않았습니다.")

    stale_cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=stale_after_minutes)
    ).isoformat()

    candidates_res = (
        supabase.table(TABLE)
        .select("*")
        .in_("status", ["queued", "running"])
        .order("created_at")
        .limit(20)
        .execute()
    )
    for row in candidates_res.data or []:
        if row["status"] == "queued":
            pass
        elif row["status"] == "running" and (row.get("claimed_at") or "") < stale_cutoff:
            pass
        else:
            continue

        upd = (
            supabase.table(TABLE)
            .update(
                {
                    "status": "running",
                    "claimed_by": worker_id,
                    "claimed_at": _now_iso(),
                }
            )
            .eq("id", row["id"])
            .eq("status", row["status"])
            .execute()
        )
        if upd.data:
            return upd.data[0]

    return None


def update_progress(job_id: str, *, stage: str, detail: Optional[str] = None) -> None:
    supabase = get_client()
    if not supabase:
        return
    progress = {"stage": stage, "detail": detail, "updated_at": _now_iso()}
    supabase.table(TABLE).update({"progress_json": progress}).eq("id", job_id).execute()


def mark_done(job_id: str, result: dict) -> None:
    supabase = get_client()
    if not supabase:
        return
    supabase.table(TABLE).update(
        {"status": "done", "result_json": result, "error": None}
    ).eq("id", job_id).execute()


def mark_failed(job_id: str, error: str) -> None:
    supabase = get_client()
    if not supabase:
        return
    supabase.table(TABLE).update({"status": "failed", "error": error[:4000]}).eq(
        "id", job_id
    ).execute()


def worker_id_from_env() -> str:
    return os.getenv("AUTO_RIGGING_WORKER_ID") or f"worker-{os.getpid()}"
