"""
/api/live-portrait — LivePortrait 액션 20종 배치 잡 큐 (4단계).

이 엔드포인트는 무거운 작업(LivePortrait+SAM2, GPU 20회 추론)을 직접 처리하지
않는다 — Supabase의 action_video_jobs 테이블에 잡을 등록만 하고 즉시 반환한다.
실제 처리는 사용자의 로컬 RTX 4090 머신에서 도는
`python -m backend.workers.live_portrait_worker` 가 이 테이블을 polling해서
가져간다. (Modal GPU 경로를 쓰고 싶다면 backend/modal_apps/live_portrait_app.py
참고 — 이 라우터는 두 실행 경로 어느 쪽이든 동일하게 그냥 "Supabase에 잡을
등록/조회"만 한다.)

배포 시 기본 비활성 — main.py에서 ENABLE_LIVE_PORTRAIT_API=1일 때만 등록된다
(generate.py의 ENABLE_GENERATE_API 패턴과 동일. 무거운 파이프라인이라 경량
배포에서는 굳이 라우트를 노출하지 않음).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models.live_portrait import (
    ActionVideoJobStatusResponse,
    CreateActionVideoJobRequest,
    CreateActionVideoJobResponse,
)
from ..services import action_video_jobs

router = APIRouter(prefix="/live-portrait", tags=["live-portrait"])


@router.post("/generate-action-set", response_model=CreateActionVideoJobResponse)
async def post_generate_action_set(body: CreateActionVideoJobRequest):
    """
    강아지 사진 URL 1개로 액션 20종 생성 잡을 큐에 등록. 즉시 job_id 반환(비동기).
    실제 처리는 로컬 RTX 4090 워커가 폴링해서 가져간다.
    """
    try:
        row = action_video_jobs.create_job(
            user_id=body.user_id,
            content_id=(body.content_id or "").strip() or body.user_id,
            dog_image_url=body.dog_image_url,
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"잡 등록 실패: {e}") from e

    return CreateActionVideoJobResponse(job_id=row["id"], status=row["status"])


@router.get("/jobs/{job_id}", response_model=ActionVideoJobStatusResponse)
async def get_job_status(job_id: str):
    """잡 진행 상황/결과 조회 — 프론트가 polling으로 상태를 확인하는 용도."""
    try:
        row = action_video_jobs.get_job(job_id)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"잡 조회 실패: {e}") from e

    if not row:
        raise HTTPException(status_code=404, detail=f"잡을 찾을 수 없습니다: {job_id}")

    return ActionVideoJobStatusResponse(
        job_id=row["id"],
        status=row["status"],
        progress=row.get("progress_json") or {},
        results=row.get("results_json"),
        error=row.get("error"),
    )
