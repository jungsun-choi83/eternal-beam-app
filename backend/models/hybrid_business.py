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
  completed = "completed"   # 후보가 검증을 통과해 canonical 로 승격됨
  rejected = "rejected"     # 후보는 생성됐지만 검증에서 탈락 (canonical 은 그대로)
  failed = "failed"         # 프로바이더 실패 — 후보 자체가 없음


class SessionStatus(str, Enum):
  """
  크레딧 세션 종료 상태.

  processing : 아직 열린 액션이 있다
  completed  : 제출한 액션 전부 승격 → 환불 없음
  partial    : 일부만 승격 → 전액 환불 (불완전 세트는 /device/sync 에서 404 라 가치가 0)
  failed     : 하나도 승격 못 함 → 전액 환불
  """

  processing = "processing"
  completed = "completed"
  partial = "partial"
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
  # 프로바이더 중립 외부 ID. 컬럼명은 하위호환을 위해 luma_generation_id 로 남는다 —
  # fal 의 request_id 도 같은 자리에 들어가고, 조회 경로는 바뀌지 않는다.
  luma_generation_id: Optional[str] = None
  #: "luma" | "wan_turbo" | "wan_a14b". 기존 행은 기본값 luma 로 읽힌다.
  provider: str = "luma"
  #: 실제 모델 식별자 (ray-2 / fal-ai/wan/...). 진단·재조정용.
  provider_model: Optional[str] = None
  status: MotionJobStatus = MotionJobStatus.pending
  video_url: Optional[str] = None
  error: Optional[str] = None
  #: 검증 전 후보 MP4. 승격되기 전까지 canonical 을 건드리지 않는다.
  candidate_url: Optional[str] = None
  #: 이 액션의 시도 번호 (1 = 최초). MAX_ACTION_ATTEMPTS 로 제한.
  attempt: int = 1
  #: 진단 검증 결과. 현재는 비차단.
  validation: Optional[dict] = None
  #: canonical 승격 시각. None 이면 아직 정규 자산이 아니다.
  promoted_at: Optional[datetime] = None


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
