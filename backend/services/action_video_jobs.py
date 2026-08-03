"""
action_video_jobs 테이블 CRUD — LivePortrait 액션 20종 배치 잡 큐(4단계).

Redis/Celery 같은 별도 큐 인프라 대신 기존 Supabase(Postgres) 테이블을 polling하는
방식을 쓴다 — 이 워크로드 규모(사용자 1명당 잡 1건, 워커 1~소수대)에는 이 정도로
충분하고 이 프로젝트의 기존 스택(Supabase만 사용)과 일치한다.

테이블 스키마: supabase/migrations/20260721000000_action_video_jobs.sql
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from .supabase_assets import get_client

TABLE = "action_video_jobs"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(
    user_id: str, content_id: str, dog_image_url: str
) -> dict:
    """새 잡을 status='queued'로 insert하고 생성된 행을 반환."""
    supabase = get_client()
    if not supabase:
        raise RuntimeError("Supabase가 설정되지 않았습니다(SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY 필요).")
    row = {
        "user_id": user_id,
        "content_id": content_id,
        "dog_image_url": dog_image_url,
        "status": "queued",
        "progress_json": {"total": None, "completed": 0},
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
    """
    queued 상태의 가장 오래된 잡, 또는 status='running'인데 claimed_at이
    stale_after_minutes 이상 지난(=워커가 죽었을 가능성이 높은) 잡을 하나 클레임한다.

    Supabase Python 클라이언트는 "UPDATE ... WHERE status='queued' RETURNING"을 원자적
    조건부 갱신으로 못 표현하므로, 여기서는 (조회 → 조건부 UPDATE → data 비어있으면
    누가 먼저 가져간 것으로 보고 재시도) 패턴으로 경쟁 상황을 낮춘다. 워커가 1대뿐인
    운영 형태(이 프로젝트의 기본 전제)에서는 경쟁이 실질적으로 발생하지 않는다.
    """
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


def update_progress(job_id: str, *, total: int, completed: int, current_action: Optional[str] = None) -> None:
    supabase = get_client()
    if not supabase:
        return
    progress = {"total": total, "completed": completed, "current_action": current_action}
    supabase.table(TABLE).update({"progress_json": progress}).eq("id", job_id).execute()


def mark_done(job_id: str, results: list[dict]) -> None:
    supabase = get_client()
    if not supabase:
        return
    supabase.table(TABLE).update(
        {"status": "done", "results_json": results, "error": None}
    ).eq("id", job_id).execute()


def mark_failed(job_id: str, error: str) -> None:
    supabase = get_client()
    if not supabase:
        return
    supabase.table(TABLE).update({"status": "failed", "error": error[:4000]}).eq(
        "id", job_id
    ).execute()


def worker_id_from_env() -> str:
    return os.getenv("LIVE_PORTRAIT_WORKER_ID") or f"worker-{os.getpid()}"
