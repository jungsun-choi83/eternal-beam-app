"""
UserSubscription 저장소 — Supabase 또는 인메모리 MOCK.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..data.subscription_plans import get_subscription_plan
from ..models.subscription import SubscriptionStatus, UserSubscriptionRow


def _table() -> str:
  return os.getenv("USER_SUBSCRIPTION_TABLE", "user_subscriptions")


def _events_table() -> str:
  return os.getenv("SUBSCRIPTION_EVENTS_TABLE", "subscription_webhook_events")


def _supabase():
  from ..models.content import _supabase_client

  return _supabase_client()


def _use_db() -> bool:
  return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


_MOCK_SUBS: dict[str, UserSubscriptionRow] = {}
_MOCK_EVENTS: dict[str, dict[str, Any]] = {}


def is_entitled(
  sub: Optional[UserSubscriptionRow],
  *,
  now: Optional[datetime] = None,
) -> bool:
  """
  Unity·device 동기화 허용 여부.

  - active: 허용
  - canceled: next_billing_date 전까지 허용 (해지 유예)
  - expired: 거부
  """
  if not sub:
    return False
  if sub.status == "active":
    return True
  if sub.status == "canceled" and sub.next_billing_date:
    ref = now or datetime.now(timezone.utc)
    nb = sub.next_billing_date
    if nb.tzinfo is None:
      nb = nb.replace(tzinfo=timezone.utc)
    return nb > ref
  return False


def next_billing_from_now(months: int = 1) -> datetime:
  return datetime.now(timezone.utc) + timedelta(days=30 * months)


async def get_subscription(user_id: str) -> Optional[UserSubscriptionRow]:
  uid = user_id.strip()
  if not uid:
    return None

  if _use_db() and _supabase():
    sb = _supabase()
    r = sb.table(_table()).select("*").eq("user_id", uid).limit(1).execute()
    if r.data:
      return _row_from_db(r.data[0])
    return None

  return _MOCK_SUBS.get(uid)


async def find_event_by_fingerprint(fingerprint: str) -> bool:
  fp = fingerprint.strip()
  if _use_db() and _supabase():
    sb = _supabase()
    r = (
      sb.table(_events_table())
      .select("id")
      .eq("event_fingerprint", fp)
      .limit(1)
      .execute()
    )
    return bool(r.data)
  return fp in _MOCK_EVENTS


async def process_renewal_via_rpc(
  *,
  user_id: str,
  plan_id: str,
  store_type: str,
  event_type: str,
  event_fingerprint: str,
  transaction_id: Optional[str],
  original_transaction_id: Optional[str],
  credits: int,
  amount_krw: int,
  next_billing: datetime,
  raw_payload: Optional[dict[str, Any]],
) -> dict[str, Any]:
  sb = _supabase()
  if not sb:
    raise RuntimeError("Supabase not configured")

  nb = next_billing.isoformat()
  r = sb.rpc(
    "process_subscription_renewal",
    {
      "p_user_id": user_id,
      "p_plan_id": plan_id,
      "p_store_type": store_type,
      "p_event_type": event_type,
      "p_event_fingerprint": event_fingerprint,
      "p_transaction_id": transaction_id,
      "p_original_transaction_id": original_transaction_id,
      "p_credits": credits,
      "p_amount_krw": amount_krw,
      "p_next_billing": nb,
      "p_raw_payload": raw_payload or {},
    },
  ).execute()

  data = r.data
  if isinstance(data, list) and data:
    data = data[0]
  if not isinstance(data, dict):
    raise RuntimeError("process_subscription_renewal returned no data")
  return data


async def process_status_change_via_rpc(
  *,
  user_id: str,
  plan_id: str,
  status: SubscriptionStatus,
  event_type: str,
  event_fingerprint: str,
  store_type: str,
  transaction_id: Optional[str],
  raw_payload: Optional[dict[str, Any]],
) -> dict[str, Any]:
  sb = _supabase()
  if not sb:
    raise RuntimeError("Supabase not configured")

  r = sb.rpc(
    "process_subscription_status_change",
    {
      "p_user_id": user_id,
      "p_plan_id": plan_id,
      "p_status": status,
      "p_event_type": event_type,
      "p_event_fingerprint": event_fingerprint,
      "p_store_type": store_type,
      "p_transaction_id": transaction_id,
      "p_raw_payload": raw_payload or {},
    },
  ).execute()

  data = r.data
  if isinstance(data, list) and data:
    data = data[0]
  if not isinstance(data, dict):
    raise RuntimeError("process_subscription_status_change returned no data")
  return data


async def process_renewal_mock(
  *,
  user_id: str,
  plan_id: str,
  store_type: str,
  event_type: str,
  event_fingerprint: str,
  transaction_id: Optional[str],
  original_transaction_id: Optional[str],
  credits: int,
  amount_krw: int,
  next_billing: datetime,
  raw_payload: Optional[dict[str, Any]],
) -> tuple[int, int]:
  from .wallet_service import add_credits

  if event_fingerprint in _MOCK_EVENTS:
    sub = _MOCK_SUBS.get(user_id)
    from .wallet_service import get_wallet

    w = await get_wallet(user_id, create_if_missing=True)
    return 0, w.current_credits if w else 0

  plan = get_subscription_plan(plan_id)
  # 크레딧 0 플랜(웹 멤버십)은 지갑을 아예 건드리지 않는다. add_credits 는 0을
  # 거절하고, 무엇보다 쓸 곳 없는 잔액을 만들 이유가 없다 — 자격은 구독 상태가
  # 정한다. 레거시 4코인 플랜(credits_per_month>0)의 동작은 그대로다.
  if credits > 0:
    w = await add_credits(user_id, credits)
  else:
    from .wallet_service import get_wallet as _get_wallet

    w = await _get_wallet(user_id, create_if_missing=False)
  row = UserSubscriptionRow(
    user_id=user_id,
    plan_id=plan.plan_id,
    status="active",
    next_billing_date=next_billing,
    store_type=store_type,
    original_transaction_id=original_transaction_id or transaction_id,
    latest_transaction_id=transaction_id,
    updated_at=datetime.now(timezone.utc),
    created_at=_MOCK_SUBS.get(user_id).created_at if user_id in _MOCK_SUBS else datetime.now(timezone.utc),
  )
  _MOCK_SUBS[user_id] = row
  _MOCK_EVENTS[event_fingerprint] = {
    "user_id": user_id,
    "event_type": event_type,
    "credits": credits,
    "amount_krw": amount_krw,
    "raw": raw_payload,
  }
  return len(_MOCK_EVENTS), (w.current_credits if w else 0)


async def process_status_change_mock(
  *,
  user_id: str,
  plan_id: str,
  status: SubscriptionStatus,
  event_type: str,
  event_fingerprint: str,
  store_type: str,
  transaction_id: Optional[str],
  raw_payload: Optional[dict[str, Any]],
) -> None:
  if event_fingerprint in _MOCK_EVENTS:
    return

  existing = _MOCK_SUBS.get(user_id)
  row = UserSubscriptionRow(
    user_id=user_id,
    plan_id=plan_id,
    status=status,
    next_billing_date=existing.next_billing_date if existing else None,
    store_type=store_type,
    original_transaction_id=existing.original_transaction_id if existing else transaction_id,
    latest_transaction_id=transaction_id,
    updated_at=datetime.now(timezone.utc),
    created_at=existing.created_at if existing else datetime.now(timezone.utc),
  )
  _MOCK_SUBS[user_id] = row
  _MOCK_EVENTS[event_fingerprint] = {
    "user_id": user_id,
    "event_type": event_type,
    "status": status,
    "raw": raw_payload,
  }


def _row_from_db(row: dict) -> UserSubscriptionRow:
  nb = row.get("next_billing_date")
  parsed_nb = None
  if nb:
    if isinstance(nb, str):
      parsed_nb = datetime.fromisoformat(nb.replace("Z", "+00:00"))
    else:
      parsed_nb = nb

  return UserSubscriptionRow(
    user_id=row["user_id"],
    plan_id=row["plan_id"],
    status=row["status"],
    next_billing_date=parsed_nb,
    store_type=row.get("store_type"),
    original_transaction_id=row.get("original_transaction_id"),
    latest_transaction_id=row.get("latest_transaction_id"),
    created_at=row.get("created_at"),
    updated_at=row.get("updated_at"),
  )
