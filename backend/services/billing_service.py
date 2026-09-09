"""
청구 오케스트레이션 — 제공자 결제 ↔ 정규화된 자격 이벤트.

    start_checkout()   결제창에 필요한 값 발급 (아직 돈은 움직이지 않는다)
    confirm_checkout() 카드 등록 → 첫 청구 → INITIAL_BUY  → ACTIVE
    renew_due()        기간 만료분 청구   → RENEWAL       → 기간 연장
                                실패 시   → DID_FAIL_TO_RENEW (연장 없음)
    cancel()           해지 예약          → CANCEL        → 기간 끝까지 유지
    expire_if_due()    기간 종료          → EXPIRATION    → 만료

지켜야 하는 것 세 가지:

  1) **자격을 직접 쓰지 않는다.** 언제나 apply_subscription_event() 를 거친다.
     그래야 Apple/Google 이 붙어도 같은 경로를 탄다.

  2) **실패가 연장이 되지 않는다.** 청구 실패는 DID_FAIL_TO_RENEW 로 나가고,
     그 이벤트는 자격 코어에서 만료 계열로 분류된다. 기간을 늘리는 코드는
     성공 분기에만 있다.

  3) **자산을 건드리지 않는다.** 구매·갱신·해지 어느 경로에서도 생성 요청이
     없고 READY 자산·선호를 지우지 않는다. 이 파일은 생성 모듈을 import 하지 않는다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from ..data.subscription_plans import DEFAULT_PLAN_ID, get_subscription_plan
from . import billing_store as store
from . import toss_billing as toss
from .billing_events import NormalizedSubscriptionEvent, apply_subscription_event

logger = logging.getLogger(__name__)

PROVIDER_TOSS = "toss"

#: 라우터가 기본 플랜을 참조할 수 있게 재노출한다.
__all__ = ["DEFAULT_PLAN_ID", "PROVIDER_TOSS"]


class BillingError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class CheckoutSession:
    provider: str
    customer_key: str
    order_id: str
    amount: int
    order_name: str
    client_key: str
    plan_id: str


async def start_checkout(*, user_id: str, plan_id: str = DEFAULT_PLAN_ID) -> CheckoutSession:
    """
    결제창을 띄우는 데 필요한 값만 발급한다. **돈은 움직이지 않는다.**

    customerKey 는 기존 것이 있으면 재사용한다 — 사용자가 결제를 중간에 그만두고
    다시 시작해도 Toss 쪽 고객이 계속 늘어나지 않게 한다.
    """
    toss.assert_configured()
    plan = get_subscription_plan(plan_id)

    existing = await store.get_subscription(user_id, PROVIDER_TOSS)
    customer_key = (existing.customer_key if existing else None) or toss.new_customer_key(user_id)

    if not existing:
        await store.upsert_subscription(
            store.BillingSubscription(
                user_id=user_id, provider=PROVIDER_TOSS, plan_id=plan.plan_id,
                customer_key=customer_key, status="expired",  # 아직 결제 전 — 자격 없음
            )
        )
    elif not existing.customer_key:
        existing.customer_key = customer_key
        await store.upsert_subscription(existing)

    return CheckoutSession(
        provider=PROVIDER_TOSS,
        customer_key=customer_key,
        order_id=toss.new_order_id("initial"),
        amount=plan.price_krw_monthly,
        order_name=f"{plan.display_name} (월 정기결제)",
        client_key=toss.client_key(),
        plan_id=plan.plan_id,
    )


async def confirm_checkout(
    *, user_id: str, auth_key: str, customer_key: str, order_id: str,
    plan_id: str = DEFAULT_PLAN_ID,
) -> dict[str, Any]:
    """
    카드 등록 인증 → billingKey 발급 → **첫 청구** → 자격 ACTIVE.

    멱등하다: 같은 order_id 로 두 번 들어오면 두 번째는 청구하지 않고 현재 상태만
    돌려준다. 사용자가 성공 페이지를 새로고침해도 두 번 결제되지 않는다.
    """
    plan = get_subscription_plan(plan_id)

    # ① 이미 처리한 주문인가 — 청구 **이전에** 확인한다.
    if await store.find_payment(order_id):
        logger.info("중복 confirm — order=%s (재청구하지 않는다)", order_id)
        sub = await store.get_subscription(user_id, PROVIDER_TOSS)
        return {"already_processed": True, "billing": sub.public_view() if sub else None}

    sub = await store.get_subscription(user_id, PROVIDER_TOSS)
    if not sub or sub.customer_key != customer_key:
        # customerKey 는 우리가 만들어 저장한 값이다. 다르면 남의 결제이거나 위조다.
        raise BillingError("CUSTOMER_KEY_MISMATCH", "결제 정보가 일치하지 않습니다.", status=403)

    # ② 결제 수단 등록 (돈 안 나감)
    key = await toss.issue_billing_key(auth_key=auth_key, customer_key=customer_key)

    # ③ 첫 청구
    result = await toss.charge(
        billing_key=key.billing_key, customer_key=customer_key,
        amount=plan.price_krw_monthly, order_id=order_id,
        order_name=f"{plan.display_name} (첫 결제)",
    )

    period_end = store.period_end_from() if result.ok else None
    fresh = await store.record_payment(
        order_id=order_id, user_id=user_id, provider=PROVIDER_TOSS, kind="INITIAL",
        amount=plan.price_krw_monthly, status="paid" if result.ok else "failed",
        provider_payment_id=result.payment_key, failure_code=result.failure_code,
        failure_message=result.failure_message, period_end=period_end, raw=result.raw,
    )
    if not fresh:
        # 동시 요청이 먼저 기록했다 — 자격을 두 번 올리지 않는다.
        sub = await store.get_subscription(user_id, PROVIDER_TOSS)
        return {"already_processed": True, "billing": sub.public_view() if sub else None}

    if not result.ok:
        sub.billing_key = key.billing_key       # 카드 자체는 등록됐다 — 재시도에 쓴다
        sub.failure_count += 1
        sub.last_error = result.failure_message
        await store.upsert_subscription(sub)
        raise BillingError(
            "PAYMENT_FAILED",
            result.failure_message or "결제에 실패했습니다.",
            status=402,
        )

    # ④ 청구 성공 → 청구 상태 갱신 → **정규화 이벤트로** 자격 활성화
    sub.billing_key = key.billing_key
    sub.plan_id = plan.plan_id
    sub.status = "active"
    sub.cancel_at_period_end = False
    sub.current_period_end = period_end
    sub.failure_count = 0
    sub.last_error = None
    await store.upsert_subscription(sub)

    ent = await apply_subscription_event(
        NormalizedSubscriptionEvent(
            provider=PROVIDER_TOSS, event_type="INITIAL_BUY", user_id=user_id,
            plan_id=plan.plan_id, transaction_id=order_id, period_end=period_end,
            raw={"payment_key": result.payment_key},
        )
    )
    return {
        "already_processed": False,
        "billing": sub.public_view(),
        "entitled": ent.entitled,
        "subscription_status": ent.subscription_status,
    }


async def cancel(*, user_id: str) -> dict[str, Any]:
    """
    해지 **예약**. 즉시 끊지 않는다 — 이미 낸 기간은 쓸 수 있어야 한다.

    자격 코어의 CANCEL 은 status=canceled 로 두되 next_billing_date 까지
    is_entitled 를 참으로 유지한다(해지 유예). 기간이 실제로 끝나면
    expire_if_due() 가 EXPIRATION 을 보낸다.
    """
    sub = await store.get_subscription(user_id, PROVIDER_TOSS)
    if not sub or sub.status != "active":
        raise BillingError("NO_ACTIVE_SUBSCRIPTION", "해지할 구독이 없습니다.", status=404)

    sub.cancel_at_period_end = True
    await store.upsert_subscription(sub)

    ent = await apply_subscription_event(
        NormalizedSubscriptionEvent(
            provider=PROVIDER_TOSS, event_type="CANCEL", user_id=user_id,
            plan_id=sub.plan_id,
            transaction_id=f"cancel_{user_id}_{int(store.now_utc().timestamp())}",
            period_end=sub.current_period_end,
        )
    )
    return {
        "billing": sub.public_view(),
        "entitled": ent.entitled,
        "subscription_status": ent.subscription_status,
    }


async def resume(*, user_id: str) -> dict[str, Any]:
    """
    해지 예약 취소 (기간이 아직 남아 있을 때). 재결제는 하지 않는다.

    복원(restore)과 다르다: 여기는 "해지를 물린다"이고, 이미 낸 기간이 살아 있다.
    """
    sub = await store.get_subscription(user_id, PROVIDER_TOSS)
    if not sub or not sub.cancel_at_period_end:
        raise BillingError("NOT_CANCELED", "해지 예약 상태가 아닙니다.", status=409)
    if not sub.current_period_end or sub.current_period_end <= store.now_utc():
        raise BillingError("PERIOD_ENDED", "이용 기간이 이미 끝났습니다. 다시 결제해 주세요.", status=409)

    sub.cancel_at_period_end = False
    sub.status = "active"
    await store.upsert_subscription(sub)

    ent = await apply_subscription_event(
        NormalizedSubscriptionEvent(
            provider=PROVIDER_TOSS, event_type="RENEWAL", user_id=user_id,
            plan_id=sub.plan_id,
            transaction_id=f"resume_{user_id}_{int(store.now_utc().timestamp())}",
            period_end=sub.current_period_end,
        )
    )
    return {
        "billing": sub.public_view(),
        "entitled": ent.entitled,
        "subscription_status": ent.subscription_status,
    }


async def _renew_one(sub: store.BillingSubscription) -> dict[str, Any]:
    """구독 하나를 갱신 또는 만료 처리. 예외를 밖으로 내지 않는다."""
    plan = get_subscription_plan(sub.plan_id)

    # 해지 예약분은 청구하지 않는다 — 기간이 끝났으므로 만료시킨다.
    if sub.cancel_at_period_end:
        sub.status = "expired"
        await store.upsert_subscription(sub)
        await apply_subscription_event(
            NormalizedSubscriptionEvent(
                provider=PROVIDER_TOSS, event_type="EXPIRATION", user_id=sub.user_id,
                plan_id=sub.plan_id,
                transaction_id=f"expire_{sub.user_id}_{int(store.now_utc().timestamp())}",
            )
        )
        return {"user_id": sub.user_id, "outcome": "expired_after_cancel"}

    if not sub.billing_key:
        return {"user_id": sub.user_id, "outcome": "skipped_no_payment_method"}

    order_id = toss.new_order_id("renewal")
    result = await toss.charge(
        billing_key=sub.billing_key, customer_key=sub.customer_key or "",
        amount=plan.price_krw_monthly, order_id=order_id,
        order_name=f"{plan.display_name} (정기결제)",
    )
    period_end = store.period_end_from() if result.ok else None
    if not await store.record_payment(
        order_id=order_id, user_id=sub.user_id, provider=PROVIDER_TOSS, kind="RENEWAL",
        amount=plan.price_krw_monthly, status="paid" if result.ok else "failed",
        provider_payment_id=result.payment_key, failure_code=result.failure_code,
        failure_message=result.failure_message, period_end=period_end, raw=result.raw,
    ):
        return {"user_id": sub.user_id, "outcome": "duplicate_order"}

    if not result.ok:
        # **연장하지 않는다.** current_period_end 를 건드리지 않으므로 다음 배치에서
        # 다시 대상이 되고, 자격은 만료 이벤트로 내려간다.
        sub.failure_count += 1
        sub.last_error = result.failure_message
        sub.status = "expired"
        await store.upsert_subscription(sub)
        await apply_subscription_event(
            NormalizedSubscriptionEvent(
                provider=PROVIDER_TOSS, event_type="DID_FAIL_TO_RENEW", user_id=sub.user_id,
                plan_id=sub.plan_id, transaction_id=order_id,
                raw={"failure_code": result.failure_code},
            )
        )
        return {"user_id": sub.user_id, "outcome": "failed", "code": result.failure_code}

    sub.current_period_end = period_end
    sub.failure_count = 0
    sub.last_error = None
    sub.status = "active"
    await store.upsert_subscription(sub)
    await apply_subscription_event(
        NormalizedSubscriptionEvent(
            provider=PROVIDER_TOSS, event_type="RENEWAL", user_id=sub.user_id,
            plan_id=sub.plan_id, transaction_id=order_id, period_end=period_end,
            raw={"payment_key": result.payment_key},
        )
    )
    return {"user_id": sub.user_id, "outcome": "renewed"}


async def renew_due(*, limit: int = 100) -> list[dict[str, Any]]:
    """
    기간이 끝난 구독을 청구한다 (배치/크론).

    한 건이 실패해도 나머지를 계속 처리한다 — 배치가 통째로 멈추면 그날 전원이
    갱신되지 않는다.
    """
    results: list[dict[str, Any]] = []
    for sub in await store.due_subscriptions(PROVIDER_TOSS, limit=limit):
        try:
            results.append(await _renew_one(sub))
        except Exception:  # noqa: BLE001
            logger.exception("갱신 실패 (user=%s)", sub.user_id)
            results.append({"user_id": sub.user_id, "outcome": "error"})
    return results


async def status(*, user_id: str) -> dict[str, Any]:
    """
    청구 상태 조회 = **복원(restore)** 경로이기도 하다.

    웹 결제에는 스토어의 "구매 복원"이 없다. 대신 신원이 곧 구독이므로, 다른
    기기에서 같은 계정으로 로그인하면 이 조회만으로 상태가 그대로 돌아온다.
    """
    sub = await store.get_subscription(user_id, PROVIDER_TOSS)
    return {
        "provider": PROVIDER_TOSS,
        "configured": bool(toss.client_key()) or toss.mock_enabled(),
        "billing": sub.public_view() if sub else None,
    }
