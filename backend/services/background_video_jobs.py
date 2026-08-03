"""
background_video_jobs 테이블 CRUD — "내 사진으로 나만의 배경 만들기"(custom_photo_bg)
잡 큐. `action_video_jobs.py`(LivePortrait 액션 20종 큐)와 완전히 같은 패턴
(Supabase 테이블 polling, Redis/Celery 없음)이지만 별도 테이블을 쓴다.

★ 왜 action_video_jobs 테이블을 재사용하지 않고 별도 테이블/워커로 분리했는가
LivePortrait 큐는 "사진 1장 → 드라이빙 영상 20개 매칭 → 결과 20개 배열
(results_json)"이라는 스키마고, 이 배경 파이프라인은 "사진 1장 → 배경 영상
1개(result_video_url)"로 산출물 개수·모양이 다르다. 컬럼을 억지로 겹치면
(`dog_image_url`처럼 이름부터 의미가 다른 컬럼을 재사용해야 함) 양쪽 다 읽기
어려워지고, 무엇보다 LivePortrait 큐/워커(`action_video_jobs.py`,
`live_portrait_worker.py`)는 다른 에이전트가 동시에 작업 중인 파일이라 그 파일을
수정하면 병합 충돌 위험이 커진다. 그래서 요청사항의 (b) 옵션(동일한 관례를 따르는
자매 테이블/워커)을 선택했다 — CRUD 함수 이름/시그니처 스타일, 클레임(낙관적 잠금)
방식, 내결함성 정책을 모두 action_video_jobs.py와 동일하게 맞췄다.

테이블 스키마: supabase/migrations/20260721000100_background_video_jobs.sql
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from .supabase_assets import get_client

TABLE = "background_video_jobs"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(
    user_id: str,
    content_id: str,
    source_image_url: str,
    *,
    target_fps: Optional[float] = None,
    target_duration_sec: Optional[float] = None,
) -> dict:
    """새 잡을 status='queued'로 insert하고 생성된 행을 반환.

    target_fps/target_duration_sec: 지정하지 않으면 워커가
    background_video_sync.target_fps()/target_duration_sec() 기본값을 쓴다 —
    호출자가 이미 알고 있는 실제 강아지 영상의 fps/길이가 있으면 여기 넘겨서
    정확히 맞출 수 있다(예: 특정 content_id의 LivePortrait 액션 영상 duration).
    """
    supabase = get_client()
    if not supabase:
        raise RuntimeError("Supabase가 설정되지 않았습니다(SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY 필요).")
    row = {
        "user_id": user_id,
        "content_id": content_id,
        "source_image_url": source_image_url,
        "status": "queued",
        "progress_json": {"stage": "queued"},
        "target_fps": target_fps,
        "target_duration_sec": target_duration_sec,
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
    """action_video_jobs.claim_next_job()과 동일한 낙관적 잠금 패턴
    (조회 → 조건부 UPDATE → 실패하면 다음 후보로)."""
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
    """단계 이름만 기록(예: "inpainting", "luma_generation", "seamless_loop",
    "syncing_fps", "uploading") — LivePortrait처럼 항목 개수 진행률이 아니라
    단계 진행률이라 스키마가 더 단순하다."""
    supabase = get_client()
    if not supabase:
        return
    progress = {"stage": stage, "detail": detail, "updated_at": _now_iso()}
    supabase.table(TABLE).update({"progress_json": progress}).eq("id", job_id).execute()


def mark_done(job_id: str, *, result_video_url: str, result_meta: dict) -> None:
    supabase = get_client()
    if not supabase:
        return
    supabase.table(TABLE).update(
        {
            "status": "done",
            "result_video_url": result_video_url,
            "result_meta_json": result_meta,
            "error": None,
        }
    ).eq("id", job_id).execute()


def mark_failed(job_id: str, error: str) -> None:
    supabase = get_client()
    if not supabase:
        return
    supabase.table(TABLE).update({"status": "failed", "error": error[:4000]}).eq(
        "id", job_id
    ).execute()


def worker_id_from_env() -> str:
    return os.getenv("BACKGROUND_VIDEO_WORKER_ID") or f"bgworker-{os.getpid()}"
