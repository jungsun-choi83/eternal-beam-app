"""
/api/v1/subscription — 정기 구독 웹훅 · 상태 조회 (Unity 차단용)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..data.subscription_plans import SUBSCRIPTION_PLANS
from ..models.subscription import (
  SubscriptionStatusResponse,
  SubscriptionWebhookRequest,
  SubscriptionWebhookResponse,
)
from ..services.subscription_webhook_service import (
  get_subscription_status,
  handle_subscription_webhook,
)

router = APIRouter(prefix="/v1/subscription", tags=["subscription-v1"])


@router.get("/plans")
async def list_subscription_plans():
  """앱·QA용 구독 플랜."""
  return {
    "plans": [
      {
        "plan_id": p.plan_id,
        "display_name": p.display_name,
        "price_krw_monthly": p.price_krw_monthly,
        "credits_per_month": p.credits_per_month,
        "billing_period": p.billing_period,
        "store_product_ids": list(p.store_product_ids),
      }
      for p in SUBSCRIPTION_PLANS.values()
    ]
  }


@router.get("/status/{user_id}", response_model=SubscriptionStatusResponse)
async def subscription_status(user_id: str):
  """
  구독 상태 — 앱·Unity가 동기화 전에 호출.

  - `entitled: false` → 기기 영상 동기화 차단 권장
  """
  uid = (user_id or "").strip()
  if not uid:
    raise HTTPException(400, detail="user_id is required")
  return await get_subscription_status(uid)


@router.post("/webhook", response_model=SubscriptionWebhookResponse)
async def subscription_webhook(
  request: Request,
  body: SubscriptionWebhookRequest | None = None,
):
  """
  Apple App Store Server Notifications / Google Play RTDN / 목업 JSON.

  **갱신 이벤트** (`INITIAL_BUY`, `RENEWAL`):
  - `user_subscriptions.status` → `active`
  - `next_billing_date` → +30일
  - `user_wallets` → +12 크레딧

  **만료** (`EXPIRATION`, `DID_FAIL_TO_RENEW` 등): `status` → `expired`

  **해지** (`CANCEL`): `status` → `canceled` (결제 기간까지 `entitled` 유지 가능)
  """
  payload: dict[str, Any]
  if body is not None:
    payload = body.model_dump(exclude_none=True)
  else:
    try:
      payload = await request.json()
    except Exception:
      payload = {}

  if not payload:
    raise HTTPException(400, detail="empty webhook body")

  try:
    return await handle_subscription_webhook(payload)
  except ValueError as e:
    raise HTTPException(status_code=400, detail=str(e)) from e
