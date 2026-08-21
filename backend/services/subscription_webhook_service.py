"""
구독 웹훅 오케스트레이션 — 갱신 시 +12 크레딧, 만료 시 차단 상태.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from ..data.subscription_plans import get_subscription_plan
from ..models.subscription import SubscriptionWebhookResponse
from . import subscription_store_service as sub_store
from .subscription_webhook_parser import (
  is_cancel_event,
  is_expiration_event,
  is_renewal_event,
  parse_subscription_webhook,
)
from .wallet_service import get_wallet

_SUB_LOCKS: dict[str, asyncio.Lock] = {}


def _user_lock(user_id: str) -> asyncio.Lock:
  uid = user_id.strip()
  if uid not in _SUB_LOCKS:
    _SUB_LOCKS[uid] = asyncio.Lock()
  return _SUB_LOCKS[uid]


async def handle_subscription_webhook(body: dict[str, Any]) -> SubscriptionWebhookResponse:
  parsed = parse_subscription_webhook(body)
  plan = get_subscription_plan(parsed.plan_id)

  async with _user_lock(parsed.user_id):
    if await sub_store.find_event_by_fingerprint(parsed.event_fingerprint):
      sub = await sub_store.get_subscription(parsed.user_id)
      w = await get_wallet(parsed.user_id, create_if_missing=True)
      return SubscriptionWebhookResponse(
        success=True,
        user_id=parsed.user_id,
        plan_id=plan.plan_id,
        event_type=parsed.event_type,
        subscription_status=sub.status if sub else "expired",
        credits_added=0,
        credits_remaining=w.current_credits if w else None,
        next_billing_date=_iso(sub.next_billing_date) if sub else None,
        entitled=sub_store.is_entitled(sub),
        idempotent_replay=True,
        message="이미 처리된 구독 웹훅입니다.",
      )

    if is_renewal_event(parsed.event_type):
      return await _handle_renewal(parsed, plan)

    if is_expiration_event(parsed.event_type):
      return await _handle_expire(parsed, plan, status="expired")

    if is_cancel_event(parsed.event_type):
      return await _handle_expire(parsed, plan, status="canceled")

    sub = await sub_store.get_subscription(parsed.user_id)
    w = await get_wallet(parsed.user_id, create_if_missing=True)
    return SubscriptionWebhookResponse(
      success=True,
      user_id=parsed.user_id,
      plan_id=plan.plan_id,
      event_type=parsed.event_type,
      subscription_status=sub.status if sub else "expired",
      credits_added=0,
      credits_remaining=w.current_credits if w else None,
      next_billing_date=_iso(sub.next_billing_date) if sub else None,
      entitled=sub_store.is_entitled(sub),
      message=f"알 수 없는 이벤트 유형({parsed.event_type}) — 구독 상태 변경 없음",
    )


async def _handle_renewal(parsed, plan) -> SubscriptionWebhookResponse:
  next_bill = sub_store.next_billing_from_now(1)
  credits = plan.credits_per_month
  amount = plan.price_krw_monthly

  try:
    if sub_store._use_db() and sub_store._supabase():
      result = await sub_store.process_renewal_via_rpc(
        user_id=parsed.user_id,
        plan_id=plan.plan_id,
        store_type=parsed.store_type,
        event_type=parsed.event_type,
        event_fingerprint=parsed.event_fingerprint,
        transaction_id=parsed.transaction_id,
        original_transaction_id=parsed.original_transaction_id,
        credits=credits,
        amount_krw=amount,
        next_billing=next_bill,
        raw_payload=parsed.raw,
      )
      remaining = int(result.get("credits_remaining", 0))
      nb = result.get("next_billing_date")
    else:
      _, remaining = await sub_store.process_renewal_mock(
        user_id=parsed.user_id,
        plan_id=plan.plan_id,
        store_type=parsed.store_type,
        event_type=parsed.event_type,
        event_fingerprint=parsed.event_fingerprint,
        transaction_id=parsed.transaction_id,
        original_transaction_id=parsed.original_transaction_id,
        credits=credits,
        amount_krw=amount,
        next_billing=next_bill,
        raw_payload=parsed.raw,
      )
      nb = next_bill.isoformat()
  except Exception as e:
    err = str(e).lower()
    if "duplicate_subscription_event" in err or "unique" in err:
      sub = await sub_store.get_subscription(parsed.user_id)
      w = await get_wallet(parsed.user_id, create_if_missing=True)
      return SubscriptionWebhookResponse(
        success=True,
        user_id=parsed.user_id,
        plan_id=plan.plan_id,
        event_type=parsed.event_type,
        subscription_status="active",
        credits_added=0,
        credits_remaining=w.current_credits if w else None,
        next_billing_date=_iso(sub.next_billing_date) if sub else None,
        entitled=True,
        idempotent_replay=True,
        message="동시 요청으로 이미 처리되었습니다.",
      )
    raise

  sub = await sub_store.get_subscription(parsed.user_id)
  return SubscriptionWebhookResponse(
    success=True,
    user_id=parsed.user_id,
    plan_id=plan.plan_id,
    event_type=parsed.event_type,
    subscription_status="active",
    credits_added=credits,
    credits_remaining=remaining,
    next_billing_date=_iso(nb) if nb else _iso(next_bill),
    entitled=sub_store.is_entitled(sub),
    message="구독이 활성화되었고 월 크레딧이 지급되었습니다.",
  )


async def _handle_expire(parsed, plan, *, status: str) -> SubscriptionWebhookResponse:
  from ..models.subscription import SubscriptionStatus

  st: SubscriptionStatus = status  # type: ignore[assignment]

  try:
    if sub_store._use_db() and sub_store._supabase():
      await sub_store.process_status_change_via_rpc(
        user_id=parsed.user_id,
        plan_id=plan.plan_id,
        status=st,
        event_type=parsed.event_type,
        event_fingerprint=parsed.event_fingerprint,
        store_type=parsed.store_type,
        transaction_id=parsed.transaction_id,
        raw_payload=parsed.raw,
      )
    else:
      await sub_store.process_status_change_mock(
        user_id=parsed.user_id,
        plan_id=plan.plan_id,
        status=st,
        event_type=parsed.event_type,
        event_fingerprint=parsed.event_fingerprint,
        store_type=parsed.store_type,
        transaction_id=parsed.transaction_id,
        raw_payload=parsed.raw,
      )
  except Exception as e:
    err = str(e).lower()
    if "duplicate_subscription_event" not in err and "unique" not in err:
      raise

  sub = await sub_store.get_subscription(parsed.user_id)
  w = await get_wallet(parsed.user_id, create_if_missing=True)
  msg = (
    "구독이 만료되었습니다. Unity 동기화가 차단됩니다."
    if status == "expired"
    else "구독 자동 갱신이 해지되었습니다. 결제 기간 종료 후 만료됩니다."
  )
  return SubscriptionWebhookResponse(
    success=True,
    user_id=parsed.user_id,
    plan_id=plan.plan_id,
    event_type=parsed.event_type,
    subscription_status=st,
    credits_added=0,
    credits_remaining=w.current_credits if w else None,
    next_billing_date=_iso(sub.next_billing_date) if sub else None,
    entitled=sub_store.is_entitled(sub),
    message=msg,
  )


def _iso(val: Any) -> Optional[str]:
  if val is None:
    return None
  if hasattr(val, "isoformat"):
    return val.isoformat()
  return str(val)


async def get_subscription_status(user_id: str):
  from ..data.subscription_plans import SUBSCRIPTION_PLANS
  from ..models.subscription import SubscriptionStatusResponse

  uid = user_id.strip()
  sub = await sub_store.get_subscription(uid)
  w = await get_wallet(uid, create_if_missing=False)
  # 사용자의 **실제** 플랜을 보여 준다. 예전에는 standard_subscription 을
  # 하드코딩해서, 웹 멤버십 가입자에게 레거시 플랜 이름·가격이 보였다.
  from ..data.subscription_plans import DEFAULT_PLAN_ID

  plan = SUBSCRIPTION_PLANS.get((sub.plan_id if sub else None) or DEFAULT_PLAN_ID)

  return SubscriptionStatusResponse(
    user_id=uid,
    plan_id=sub.plan_id if sub else None,
    status=sub.status if sub else None,
    next_billing_date=_iso(sub.next_billing_date) if sub else None,
    entitled=sub_store.is_entitled(sub),
    credits_remaining=w.current_credits if w else None,
    display_name=plan.display_name if plan else None,
    price_krw_monthly=plan.price_krw_monthly if plan else None,
    credits_per_month=plan.credits_per_month if plan else None,
  )
