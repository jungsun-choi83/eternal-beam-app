"""인앱 결제(IAP) 검증·충전 API 모델."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

StoreType = Literal["apple", "google"]


class VerifyAndChargeRequest(BaseModel):
  user_id: str = Field(..., min_length=1, description="앱 사용자 ID (지갑 키)")
  receipt_data: str = Field(..., min_length=8, description="Apple/Google 영수증 토큰 (base64 또는 purchaseToken)")
  store_type: StoreType = Field(..., description="'apple' | 'google'")
  product_id: str = Field(
    default="credit_pack_4",
    description="스토어 상품 ID (기본: credit_pack_4)",
  )


class VerifyAndChargeResponse(BaseModel):
  success: bool
  user_id: str
  product_id: str
  amount_krw: int
  credits_added: int
  credits_remaining: int
  payment_id: Optional[int] = None
  transaction_id: Optional[str] = None
  store_type: StoreType
  status: str
  idempotent_replay: bool = False
  message: str = ""


class PaymentHistoryRow(BaseModel):
  id: Optional[int] = None
  user_id: str
  product_id: str
  store_type: str
  receipt_fingerprint: str
  transaction_id: Optional[str] = None
  amount_krw: int
  credits_added: int
  status: str
  error_message: Optional[str] = None
  raw_receipt_meta: Optional[dict[str, Any]] = None
  created_at: Optional[datetime] = None
