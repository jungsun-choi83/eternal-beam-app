"""
/api/background-video — "내 사진으로 나만의 배경 만들기"(custom_photo_bg) 잡 큐.

live_portrait.py 라우터와 동일한 설계: 무거운 작업(SAM2+LaMa 인페인팅, Luma 배경
애니메이션, seamless loop, fps/duration 동기화)을 여기서 직접 하지 않고
background_video_jobs 테이블에 잡을 등록만 하고 즉시 반환한다. 실제 처리는
사용자의 로컬 RTX 4090 머신에서 도는
`python -m backend.workers.background_video_worker` 가 이 테이블을 polling해서
가져간다.

원본 사진은 프론트가 이미 들고 있는 파일(사용자가 처음 업로드한, 누끼 이전 원본)을
그대로 멀티파트로 올린다 — 별도 "먼저 URL을 만드세요" 단계 없이 이 엔드포인트가
Supabase Storage 업로드까지 한 번에 처리한다(요청사항: "이미 업로드한 사진을
재사용" — 프론트가 두 번째 업로드를 요구하지 않도록, 세션에 있는 원본 파일을
그대로 이 엔드포인트에 보내면 됨).

배포 시 기본 비활성 — main.py에서 ENABLE_BACKGROUND_VIDEO_API=1일 때만 등록된다
(live_portrait.py의 ENABLE_LIVE_PORTRAIT_API 패턴과 동일).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..models.background_video import (
    BackgroundVideoJobStatusResponse,
    CreateBackgroundVideoJobResponse,
)
from ..services import background_video_jobs, supabase_assets

router = APIRouter(prefix="/background-video", tags=["background-video"])


@router.post("/generate", response_model=CreateBackgroundVideoJobResponse)
async def post_generate_background_video(
    file: UploadFile = File(..., description="사용자의 원본 사진(누끼 이전, 강아지 포함)"),
    user_id: str = Form("anonymous"),
    content_id: str | None = Form(None),
    target_fps: float | None = Form(None),
    target_duration_sec: float | None = Form(None),
):
    """
    원본 사진 1장 → 배경 애니메이션 생성 잡을 큐에 등록. 즉시 job_id 반환(비동기).
    실제 처리는 로컬 RTX 4090 워커가 폴링해서 가져간다.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")

    cid = (content_id or "").strip() or str(uuid.uuid4())
    content_type = file.content_type or "image/jpeg"

    try:
        source_url = await supabase_assets.upload_asset_to_storage(
            f"{user_id}/{cid}/background_source/original.jpg", raw, content_type
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"원본 사진 업로드 실패: {e}") from e

    try:
        row = background_video_jobs.create_job(
            user_id=user_id,
            content_id=cid,
            source_image_url=source_url,
            target_fps=target_fps,
            target_duration_sec=target_duration_sec,
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"잡 등록 실패: {e}") from e

    return CreateBackgroundVideoJobResponse(job_id=row["id"], status=row["status"])


@router.get("/jobs/{job_id}", response_model=BackgroundVideoJobStatusResponse)
async def get_background_video_job_status(job_id: str):
    """잡 진행 상황/결과 조회 — 프론트가 polling으로 상태를 확인하는 용도."""
    try:
        row = background_video_jobs.get_job(job_id)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"잡 조회 실패: {e}") from e

    if not row:
        raise HTTPException(status_code=404, detail=f"잡을 찾을 수 없습니다: {job_id}")

    return BackgroundVideoJobStatusResponse(
        job_id=row["id"],
        status=row["status"],
        progress=row.get("progress_json") or {},
        result_video_url=row.get("result_video_url"),
        result_meta=row.get("result_meta_json"),
        error=row.get("error"),
    )
