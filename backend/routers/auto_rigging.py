"""
/api/auto-rigging — SAM2+포즈추정+Spine2D 자동 리깅 잡 큐.

live_portrait.py 라우터와 정확히 같은 이유/구조: 무거운 작업(SAM2 세그멘테이션+
포즈 추정+이미지 워핑)을 직접 처리하지 않고, Supabase의 auto_rigging_jobs
테이블에 잡을 등록만 하고 즉시 반환한다. 실제 처리는 사용자의 로컬 RTX 4090
머신에서 도는 `python -m backend.workers.auto_rigging_worker`가 이 테이블을
polling해서 가져간다.

배포 시 기본 비활성 — main.py에서 ENABLE_AUTO_RIGGING_API=1일 때만 등록된다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models.auto_rigging import (
    AutoRiggingJobStatusResponse,
    CreateAutoRiggingJobRequest,
    CreateAutoRiggingJobResponse,
)
from ..services import auto_rigging_jobs

router = APIRouter(prefix="/auto-rigging", tags=["auto-rigging"])


@router.post("/generate-rig", response_model=CreateAutoRiggingJobResponse)
async def post_generate_rig(body: CreateAutoRiggingJobRequest):
    """반려동물 사진 URL 1개로 Spine2D 리깅 잡을 큐에 등록. 즉시 job_id 반환(비동기).
    실제 처리는 로컬 RTX 4090 워커가 폴링해서 가져간다."""
    try:
        row = auto_rigging_jobs.create_job(
            user_id=body.user_id,
            content_id=(body.content_id or "").strip() or body.user_id,
            pet_image_url=body.pet_image_url,
            requested_actions=body.requested_actions,
            pose_backend=body.pose_backend,
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"잡 등록 실패: {e}") from e

    return CreateAutoRiggingJobResponse(job_id=row["id"], status=row["status"])


@router.get("/jobs/{job_id}", response_model=AutoRiggingJobStatusResponse)
async def get_job_status(job_id: str):
    """잡 진행 상황/결과 조회 — 프론트가 polling으로 상태를 확인하는 용도."""
    try:
        row = auto_rigging_jobs.get_job(job_id)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"잡 조회 실패: {e}") from e

    if not row:
        raise HTTPException(status_code=404, detail=f"잡을 찾을 수 없습니다: {job_id}")

    return AutoRiggingJobStatusResponse(
        job_id=row["id"],
        status=row["status"],
        progress=row.get("progress_json") or {},
        result=row.get("result_json"),
        error=row.get("error"),
    )
