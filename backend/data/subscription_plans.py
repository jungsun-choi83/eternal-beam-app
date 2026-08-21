"""
정기 구독 플랜 카탈로그.

App Store / Google Play 구독 Product ID ↔ 서버 plan_id
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubscriptionPlan:
  plan_id: str
  display_name: str
  price_krw_monthly: int
  credits_per_month: int
  billing_period: str  # "monthly"
  store_product_ids: tuple[str, ...]


#: 기본 플랜 id — 새 가입(웹 멤버십)이 쓰는 값.
DEFAULT_PLAN_ID = "web_membership"

SUBSCRIPTION_PLANS: dict[str, SubscriptionPlan] = {
  # ── 웹 멤버십 (Toss) ───────────────────────────────────────────────────────
  # credits_per_month=0 이 의도적이다. 소비자 크레딧은 Phase 3 에서 제품에서
  # 사라졌고(UI 없음), 여기서 다시 발행하면 쓸 곳 없는 잔액만 쌓인다.
  # 자격은 크레딧이 아니라 구독 상태가 정한다(premium_entitlement).
  #
  # ⚠️ 아래 standard_subscription 의 월 12크레딧은 **레거시 4코인 기기 팩**의
  #    재원이므로 그대로 둔다 — 그쪽 계약을 건드리지 않는다.
  "web_membership": SubscriptionPlan(
    plan_id="web_membership",
    display_name="이터널빔 멤버십",
    price_krw_monthly=9900,
    credits_per_month=0,
    billing_period="monthly",
    store_product_ids=("web_membership",),
  ),
  "standard_subscription": SubscriptionPlan(
    plan_id="standard_subscription",
    display_name="스탠다드 구독 플랜",
    price_krw_monthly=9900,
    credits_per_month=12,
    billing_period="monthly",
    store_product_ids=(
      "standard_subscription",
      "com.eternalbeam.subscription.standard",
    ),
  ),
}


def get_subscription_plan(plan_id: str) -> SubscriptionPlan:
  pid = (plan_id or "").strip()
  if pid not in SUBSCRIPTION_PLANS:
    raise ValueError(f"Unknown plan_id: {plan_id}")
  return SUBSCRIPTION_PLANS[pid]


def resolve_plan_id_from_product(product_id: str) -> str:
  """스토어 product id → 내부 plan_id."""
  raw = (product_id or "").strip()
  for plan in SUBSCRIPTION_PLANS.values():
    if raw in plan.store_product_ids or raw == plan.plan_id:
      return plan.plan_id
  if raw in SUBSCRIPTION_PLANS:
    return raw
  raise ValueError(f"Unknown subscription product: {product_id}")
