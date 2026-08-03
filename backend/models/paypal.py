"""PayPal 테마 결제(Orders v2) API 모델."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class CreatePaypalOrderRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    theme_key: str = Field(..., min_length=1, description="예: aurora, sunset, ocean_deep")


class CreatePaypalOrderResponse(BaseModel):
    order_id: str
    amount_usd: str
    currency: str = "USD"
    theme_key: str
    status: str


class CapturePaypalOrderRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    theme_key: str = Field(..., min_length=1)
    order_id: str = Field(..., min_length=1)


class CapturePaypalOrderResponse(BaseModel):
    success: bool
    status: str
    theme_key: str
    order_id: str
    payment_id: Optional[str] = None
    message: str = ""
