"""정기 구독 API 모델."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SubscriptionStatus = Literal["active", "canceled", "expired"]
WebhookEventType = Literal[
  "INITIAL_BUY",
  "RENEWAL",
  "EXPIRATION",
  "CANCEL",
  "DID_FAIL_TO_RENEW",
  "GRACE_PERIOD",
  "OTHER",
]
StoreType = Literal["apple", "google", "mock"]


class UserSubscriptionRow(BaseModel):
  user_id: str
  plan_id: str
  status: SubscriptionStatus
  next_billing_date: Optional[datetime] = None
  store_type: Optional[str] = None
  original_transaction_id: Optional[str] = None
  latest_transaction_id: Optional[str] = None
  created_at: Optional[datetime] = None
  updated_at: Optional[datetime] = None


class SubscriptionWebhookRequest(BaseModel):
  """
  통합 웹훅 바디.

  - 테스트/목업: 아래 필드를 직접 전달
  - Apple ASN v2 / Google RTDN: `raw` 에 원본 JSON, 파서가 notification_type 추출
  """

  store_type: Optional[StoreType] = Field(
    None, description="apple | google | mock (raw만 있으면 자동 추론)"
  )
  notification_type: Optional[str] = Field(
    None,
    description="INITIAL_BUY | RENEWAL | EXPIRATION | CANCEL | DID_FAIL_TO_RENEW 등",
  )
  user_id: Optional[str] = Field(None, description="앱 사용자 ID (목업·앱 연동 시 필수)")
  plan_id: Optional[str] = Field(
    default="standard_subscription",
    description="내부 플랜 ID",
  )
  product_id: Optional[str] = Field(
    None, description="스토어 product id (plan_id 대신 사용 가능)"
  )
  transaction_id: Optional[str] = None
  original_transaction_id: Optional[str] = None
  raw: Optional[dict[str, Any]] = Field(
    None, description="Apple/Google 원본 알림 JSON"
  )


class SubscriptionWebhookResponse(BaseModel):
  success: bool
  user_id: str
  plan_id: str
  event_type: str
  subscription_status: SubscriptionStatus
  credits_added: int = 0
  credits_remaining: Optional[int] = None
  next_billing_date: Optional[str] = None
  entitled: bool = Field(
    description="Unity·앱에서 기능 허용 여부 (active 또는 해지 유예 기간)"
  )
  idempotent_replay: bool = False
  message: str = ""


class SubscriptionStatusResponse(BaseModel):
  user_id: str
  plan_id: Optional[str] = None
  status: Optional[SubscriptionStatus] = None
  next_billing_date: Optional[str] = None
  entitled: bool = False
  credits_remaining: Optional[int] = None
  display_name: Optional[str] = None
  price_krw_monthly: Optional[int] = None
  credits_per_month: Optional[int] = None
