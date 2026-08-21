"""
청구 상태 저장소 — **자격과 분리된 제공자 계층**.

    billing_subscriptions  누가 어떻게 돈을 내고 있는가 (billingKey, 기간, 해지 예약)
    billing_payments       어떤 결제가 실제로 일어났는가 (order_id 로 멱등)

자격(user_subscriptions)은 여기서 절대 건드리지 않는다. 자격 변경은 오직
billing_events.apply_subscription_event() 를 통해서만 일어난다.

DB 가 없으면 인메모리로 떨어진다(로컬/테스트) — 다른 저장소들과 같은 규칙이다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: 한 결제 주기 길이. 자격 쪽 next_billing_from_now(1) 과 같은 30일이다.
PERIOD_DAYS = 30


def _subs_table() -> str:
    return os.getenv("BILLING_SUBSCRIPTIONS_TABLE", "billing_subscriptions")


def _pay_table() -> str:
    return os.getenv("BILLING_PAYMENTS_TABLE", "billing_payments")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


_MOCK_SUBS: dict[tuple[str, str], "BillingSubscription"] = {}
_MOCK_PAYMENTS: dict[str, dict[str, Any]] = {}


def __reset_for_tests() -> None:
    _MOCK_SUBS.clear()
    _MOCK_PAYMENTS.clear()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def period_end_from(start: Optional[datetime] = None) -> datetime:
    return (start or now_utc()) + timedelta(days=PERIOD_DAYS)


class BillingStoreError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 503):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class BillingSubscription:
    user_id: str
    provider: str
    plan_id: str = "standard_subscription"
    customer_key: Optional[str] = None
    #: ⚠️ 결제 수단 그 자체. **절대 응답에 싣지 않는다.**
    billing_key: Optional[str] = None
    status: str = "active"           # active | canceled | expired
    cancel_at_period_end: bool = False
    current_period_end: Optional[datetime] = None
    failure_count: int = 0
    last_error: Optional[str] = None

    def public_view(self) -> dict[str, Any]:
        """프론트에 내려도 되는 모양 — 키·시크릿은 빠진다."""
        return {
            "provider": self.provider,
            "plan_id": self.plan_id,
            "status": self.status,
            "cancel_at_period_end": self.cancel_at_period_end,
            "current_period_end": (
                self.current_period_end.isoformat() if self.current_period_end else None
            ),
            "has_payment_method": bool(self.billing_key),
            "failure_count": self.failure_count,
        }


def _parse_dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


def _row_to_sub(row: dict[str, Any]) -> BillingSubscription:
    return BillingSubscription(
        user_id=row["user_id"],
        provider=row["provider"],
        plan_id=row.get("plan_id") or "standard_subscription",
        customer_key=row.get("customer_key"),
        billing_key=row.get("billing_key"),
        status=row.get("status") or "active",
        cancel_at_period_end=bool(row.get("cancel_at_period_end")),
        current_period_end=_parse_dt(row.get("current_period_end")),
        failure_count=int(row.get("failure_count") or 0),
        last_error=row.get("last_error"),
    )


async def get_subscription(user_id: str, provider: str) -> Optional[BillingSubscription]:
    uid, prov = user_id.strip(), provider.strip()
    if _use_db() and _supabase():
        try:
            r = (
                _supabase().table(_subs_table()).select("*")
                .eq("user_id", uid).eq("provider", prov).limit(1).execute()
            )
        except Exception as e:
            logger.exception("청구 구독 조회 실패 (user=%s provider=%s)", uid, prov)
            raise BillingStoreError("BILLING_UNAVAILABLE", "청구 정보를 불러오지 못했습니다.") from e
        rows = getattr(r, "data", None) or []
        return _row_to_sub(rows[0]) if rows else None
    return _MOCK_SUBS.get((uid, prov))


async def upsert_subscription(sub: BillingSubscription) -> BillingSubscription:
    if _use_db() and _supabase():
        row = {
            "user_id": sub.user_id, "provider": sub.provider, "plan_id": sub.plan_id,
            "customer_key": sub.customer_key, "billing_key": sub.billing_key,
            "status": sub.status, "cancel_at_period_end": sub.cancel_at_period_end,
            "current_period_end": (
                sub.current_period_end.isoformat() if sub.current_period_end else None
            ),
            "failure_count": sub.failure_count, "last_error": sub.last_error,
            "updated_at": now_utc().isoformat(),
        }
        try:
            _supabase().table(_subs_table()).upsert(row, on_conflict="user_id,provider").execute()
        except Exception as e:
            logger.exception("청구 구독 저장 실패 (user=%s)", sub.user_id)
            raise BillingStoreError("BILLING_UNAVAILABLE", "청구 정보를 저장하지 못했습니다.") from e
        return sub
    _MOCK_SUBS[(sub.user_id, sub.provider)] = sub
    return sub


# ── 결제 원장 (멱등성) ───────────────────────────────────────────────────────


async def find_payment(order_id: str) -> Optional[dict[str, Any]]:
    oid = order_id.strip()
    if _use_db() and _supabase():
        try:
            r = _supabase().table(_pay_table()).select("*").eq("order_id", oid).limit(1).execute()
        except Exception as e:
            raise BillingStoreError("BILLING_UNAVAILABLE", "결제 기록을 조회하지 못했습니다.") from e
        rows = getattr(r, "data", None) or []
        return rows[0] if rows else None
    return _MOCK_PAYMENTS.get(oid)


async def record_payment(
    *,
    order_id: str,
    user_id: str,
    provider: str,
    kind: str,
    amount: int,
    status: str,
    provider_payment_id: Optional[str] = None,
    failure_code: Optional[str] = None,
    failure_message: Optional[str] = None,
    period_end: Optional[datetime] = None,
    raw: Optional[dict[str, Any]] = None,
) -> bool:
    """
    결제를 원장에 기록한다. 이미 있으면 False (중복 — 다시 처리하지 않는다).

    이것이 **이중 청구 방어의 마지막 층**이다. order_id 가 PK 이므로 같은 주문은
    한 번만 들어간다. 앞단(Toss Idempotency-Key)이 뚫려도 여기서 걸린다.
    """
    row = {
        "order_id": order_id, "user_id": user_id, "provider": provider, "kind": kind,
        "amount": amount, "status": status, "provider_payment_id": provider_payment_id,
        "failure_code": failure_code, "failure_message": failure_message,
        "period_end": period_end.isoformat() if period_end else None,
        "raw": raw or {}, "created_at": now_utc().isoformat(),
    }
    if _use_db() and _supabase():
        try:
            _supabase().table(_pay_table()).insert(row).execute()
        except Exception as e:
            msg = f"{e}".lower()
            if "duplicate" in msg or "unique" in msg or "23505" in msg:
                logger.info("결제 기록 중복 — order=%s (재처리하지 않는다)", order_id)
                return False
            logger.exception("결제 기록 실패 — order=%s", order_id)
            raise BillingStoreError("BILLING_UNAVAILABLE", "결제 기록을 저장하지 못했습니다.") from e
        return True
    if order_id in _MOCK_PAYMENTS:
        return False
    _MOCK_PAYMENTS[order_id] = row
    return True


async def due_subscriptions(provider: str, *, limit: int = 100) -> list[BillingSubscription]:
    """
    지금 갱신 청구해야 하는 구독.

    조건: 청구 상태가 active 이고 이용 기간이 끝났다. 해지 예약(cancel_at_period_end)
    은 **여기서 제외하지 않는다** — 기간이 끝나면 갱신 대신 만료 처리해야 하므로
    호출부가 함께 다뤄야 한다.
    """
    now = now_utc()
    if _use_db() and _supabase():
        try:
            r = (
                _supabase().table(_subs_table()).select("*")
                .eq("provider", provider).eq("status", "active")
                .lte("current_period_end", now.isoformat())
                .limit(limit).execute()
            )
        except Exception as e:
            raise BillingStoreError("BILLING_UNAVAILABLE", "갱신 대상 조회에 실패했습니다.") from e
        return [_row_to_sub(x) for x in (getattr(r, "data", None) or [])]

    out: list[BillingSubscription] = []
    for sub in _MOCK_SUBS.values():
        if sub.provider != provider or sub.status != "active":
            continue
        if sub.current_period_end and sub.current_period_end <= now:
            out.append(sub)
    return out[:limit]
