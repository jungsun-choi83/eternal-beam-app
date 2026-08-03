"""LivePortrait 액션 20종 배치 잡 큐 API 모델."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class CreateActionVideoJobRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    dog_image_url: str = Field(..., min_length=1, description="Supabase 등 공개 접근 가능한 강아지 사진 URL")
    content_id: Optional[str] = None


class CreateActionVideoJobResponse(BaseModel):
    job_id: str
    status: str


class ActionVideoJobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: dict[str, Any] = Field(default_factory=dict)
    results: Optional[list[dict[str, Any]]] = None
    error: Optional[str] = None
