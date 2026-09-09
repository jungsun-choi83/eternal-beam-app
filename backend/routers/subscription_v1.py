"""
/api/v1/subscription — 정기 구독 웹훅 · 상태 조회

**신원 규칙 (Phase 3)**: 이 라우터가 다루는 user_id 는 언제나 프리미엄 인가가
조회하는 것과 **같은 정규 Eternal Beam 신원**이다.

  * 목업 웹훅 → 바디의 user_id 를 **무시하고** 토큰에서 확정한다.
  * 상태 조회 → 경로 파라미터를 신뢰하지 않고 본인 것만 돌려준다.
  * 실제 스토어 웹훅 → 스토어가 준 값을 identity_service 규칙으로 정규화한다.

이 규칙이 없으면 결제한 사용자가 "구독 없음"으로 읽힌다 — 저장은 A 신원으로,
조회는 B 신원으로 일어나기 때문이다. 조용히 틀리는 종류의 버그라 라우터 층에서
못을 박는다.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from ..auth import AuthedUser, require_user
from ..data.subscription_plans import SUBSCRIPTION_PLANS
from ..models.subscription import (
  SubscriptionStatusResponse,
  SubscriptionWebhookRequest,
  SubscriptionWebhookResponse,
)
from ..services import subscription_auth
from ..services.subscription_webhook_service import (
  get_subscription_status,
  handle_subscription_webhook,
)

router = APIRouter(prefix="/v1/subscription", tags=["subscription-v1"])


@router.get("/plans")
async def list_subscription_plans():
  """앱·QA용 구독 플랜. 사용자 데이터가 없으므로 공개."""
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


@router.get("/status", response_model=SubscriptionStatusResponse)
async def my_subscription_status(user: AuthedUser = Depends(require_user)):
  """
  **본인** 구독 상태. 정규 신원 기준 — 프리미엄 인가가 보는 것과 같은 값이다.

  경로에 user_id 를 받는 아래 레거시 라우트를 대체한다.
  """
  return await get_subscription_status(user.user_id)


@router.get("/status/{user_id}", response_model=SubscriptionStatusResponse)
async def subscription_status(
  user_id: str,
  user: AuthedUser = Depends(require_user),
):
  """
  레거시 경로 — 문서·기존 클라이언트 호환용. **본인 것만** 조회할 수 있다.

  예전에는 인증이 없어 아무나 남의 구독 상태를 읽을 수 있었다. 경로 값은 이제
  신뢰의 근거가 아니라 **일치해야 하는 값**이다.
  """
  from ..services.identity_service import canonical_user_id

  requested = canonical_user_id(user_id)
  if not requested:
    raise HTTPException(400, detail={"code": "USER_REQUIRED", "message": "user_id is required"})
  if requested != user.user_id:
    raise HTTPException(
      status_code=403,
      detail={
        "code": "IDENTITY_MISMATCH",
        "message": "본인의 구독 상태만 조회할 수 있습니다.",
      },
    )
  return await get_subscription_status(user.user_id)


@router.post("/webhook", response_model=SubscriptionWebhookResponse)
async def subscription_webhook(
  request: Request,
  body: SubscriptionWebhookRequest | None = None,
  authorization: str = Header(default=""),
  x_subscription_webhook_secret: Optional[str] = Header(default=None),
):
  """
  Apple App Store Server Notifications / Google Play RTDN / 목업 JSON.

  인가는 두 갈래다 (services/subscription_auth.py):

    apple·google  →  X-Subscription-Webhook-Secret 헤더가 SUBSCRIPTION_WEBHOOK_SECRET
                     과 일치해야 한다. 미설정이면 503 (닫힌다).
    mock          →  SUBSCRIPTION_MOCK=1 + 유효한 사용자 토큰.
                     user_id 는 **토큰에서 확정**하고 바디 값은 버린다.

  **갱신 이벤트** (`INITIAL_BUY`, `RENEWAL`):
  - `user_subscriptions.status` → `active`
  - `next_billing_date` → +30일
  - `user_wallets` → +12 크레딧 (레거시 4코인 팩 재원 — 프리미엄 생성과 무관)

  **만료** (`EXPIRATION`, `DID_FAIL_TO_RENEW` 등): `status` → `expired`

  **해지** (`CANCEL`): `status` → `canceled` (결제 기간까지 `entitled` 유지)
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
    raise HTTPException(400, detail={"code": "EMPTY_BODY", "message": "empty webhook body"})

  if subscription_auth.is_mock_payload(payload):
    # 목업: 사용자 인증을 요구하고, 신원을 토큰에서 덮어쓴다.
    subscription_auth.assert_mock_webhook_allowed()
    user = await require_user(authorization=authorization)
    payload["user_id"] = user.user_id
    payload["store_type"] = "mock"
  else:
    # 실제 스토어: 공유 시크릿. 사용자 토큰은 존재할 수 없다.
    subscription_auth.assert_store_webhook_authorized(x_subscription_webhook_secret)

  try:
    return await handle_subscription_webhook(payload)
  except ValueError as e:
    raise HTTPException(status_code=400, detail=str(e)) from e
