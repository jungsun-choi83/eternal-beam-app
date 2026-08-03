"""SAM2+포즈추정+Spine2D 자동 리깅 잡 큐 API 모델."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class CreateAutoRiggingJobRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    pet_image_url: str = Field(..., min_length=1, description="Supabase 등 공개 접근 가능한 반려동물 사진 URL")
    content_id: Optional[str] = None
    requested_actions: list[str] = Field(
        default_factory=list,
        description="예: ['lie_down'] — 빈 배열이면 구현된 액션 전체(현재 lie_down만) 생성",
    )
    pose_backend: str = Field(
        default="heuristic",
        description="'heuristic' | 'deeplabcut_superanimal' | 'auto'",
    )


class CreateAutoRiggingJobResponse(BaseModel):
    job_id: str
    status: str


class AutoRiggingJobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: dict[str, Any] = Field(default_factory=dict)
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
