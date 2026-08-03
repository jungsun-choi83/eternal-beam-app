"""background_video_jobs 큐 API 모델 ("내 사진으로 나만의 배경 만들기")."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class CreateBackgroundVideoJobResponse(BaseModel):
    job_id: str
    status: str


class BackgroundVideoJobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: dict[str, Any] = Field(default_factory=dict)
    result_video_url: Optional[str] = None
    result_meta: Optional[dict[str, Any]] = None
    error: Optional[str] = None
