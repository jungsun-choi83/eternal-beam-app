"""하이브리드 비즈니스 모델 (NFC 장소 카드 + 구독 크레딧)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MotionJobStatus(str, Enum):
  pending = "pending"
  submitted = "submitted"
  dreaming = "dreaming"
  completed = "completed"
  failed = "failed"


class UserWallet(BaseModel):
  user_id: str
  current_credits: int = 0
  updated_at: Optional[datetime] = None


class GeneratedMotion(BaseModel):
  user_id: str
  pet_id: str
  place_id: str
  action_id: str
  video_url: str
  created_at: Optional[datetime] = None


class MotionJobRow(BaseModel):
  session_id: str
  user_id: str
  pet_id: str
  place_key: str
  action_id: str
  luma_generation_id: Optional[str] = None
  status: MotionJobStatus = MotionJobStatus.pending
  video_url: Optional[str] = None
  error: Optional[str] = None


class GenerateWithCreditRequest(BaseModel):
  user_id: str
  pet_image_url: str
  selected_place_id: str
  pet_id: Optional[str] = None


class GenerateWithCreditResponse(BaseModel):
  session_id: str
  user_id: str
  pet_id: str
  place_id: str
  credits_charged: int
  credits_remaining: int
  submitted: int
  submit_errors: list[dict]
  status: str
  webhook_path: str


class DeviceMotionItem(BaseModel):
  action_id: str
  video_url: str
  created_at: Optional[str] = None


class DeviceSyncResponse(BaseModel):
  user_id: str
  pet_id: str
  place_id: str
  motions: list[DeviceMotionItem]
