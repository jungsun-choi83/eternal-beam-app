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
  # ── 웹 멤버십 (Toss) — Eternal Beam Plus ──────────────────────────────────
  #
  # ── credits_per_month 가 0 에서 12 로 돌아왔다 (Phase 10) ─────────────────
  # Phase 3 에서 0 으로 내린 이유는 "소비자 크레딧이 제품에서 사라져 쓸 곳 없는
  # 잔액만 쌓인다" 였다. 그 전제가 Phase 4–8 에서 사라졌다 — 이제 크레딧으로
  # 테마·아이들·액션을 산다.
  #
  # ── 멤버십은 크레딧 **전달 수단**이지 소유의 조건이 아니다 ────────────────
  # 여기서 지급되는 것은 멤버 전용 화폐가 아니라 **같은 Beam Credit** 이다.
  # 크레딧 팩으로 산 것과 구분되지 않고, 같은 지갑에 들어가 같은 원장에 남는다.
  # 'member coin' 도 'subscription token' 도 만들지 않는다 — 두 화폐가 생기면
  # 고객은 어느 것이 먼저 쓰이는지 물어야 하고, 우리는 그 규칙을 지켜야 한다.
  #
  # 해지하면 **지급이 멈출 뿐** 이미 받은 크레딧도, 그것으로 산 테마·모션도
  # 그대로 남는다. 소유는 user_theme_entitlements / owned_generated_assets 가
  # 정하고 그 표들은 구독 상태를 읽지 않는다.
  "web_membership": SubscriptionPlan(
    plan_id="web_membership",
    display_name="이터널빔 멤버십",
    price_krw_monthly=9900,
    credits_per_month=12,
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
