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


SUBSCRIPTION_PLANS: dict[str, SubscriptionPlan] = {
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
