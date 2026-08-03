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
  # "video" (기본값, Luma 영상 파이프라인) | "spine" (스켈레톤 리깅 데이터 준비됨).
  # device-renderer(C++)의 CreateRendererForAssetDir()가 이 값으로 SpineRenderer /
  # VideoLayerRenderer 를 고른다 — 기존 클라이언트와의 호환을 위해 항상 기본값 "video".
  asset_type: str = "video"
  motions: list[DeviceMotionItem]
