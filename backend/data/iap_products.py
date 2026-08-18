"""
인앱 결제(IAP) 단품 상품 카탈로그.

앱 스토어 Product ID ↔ 서버 검증 시 사용.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IAPProduct:
  product_id: str
  price_krw: int
  credits: int
  display_name: str


IAP_PRODUCTS: dict[str, IAPProduct] = {
  "credit_pack_4": IAPProduct(
    product_id="credit_pack_4",
    price_krw=4900,
    credits=4,
    display_name="크레딧 4개 팩",
  ),
  # ── 목업 테스트 전용 팩 ────────────────────────────────────────────────────
  # 설정 화면의 "테스트 크레딧 추가" 버튼이 쓰는 상품이다. 새 지갑 시스템을 만들지
  # 않기 위해 **기존 IAP 경로를 그대로 재사용한다**:
  #   verify-and-charge → iap_charge_service → wallet_service.add_credits(RPC)
  # 덕분에 멱등성·결제 이력·원자적 충전이 전부 검증된 경로 그대로 동작한다.
  #
  # ⚠️ PAYMENT_MOCK=1 일 때만 의미가 있다. 실제 스토어에는 이 상품 ID 가 없으므로
  # 실 결제 검증(Apple/Google)에서는 영수증이 매칭되지 않아 통과하지 못한다.
  # 가격 0원인 것도 의도적이다 — 실 매출로 오인될 수 없다.
  "credit_pack_test_2": IAPProduct(
    product_id="credit_pack_test_2",
    price_krw=0,
    credits=2,
    display_name="테스트 크레딧 2개",
  ),
  "credit_pack_test_5": IAPProduct(
    product_id="credit_pack_test_5",
    price_krw=0,
    credits=5,
    display_name="테스트 크레딧 5개",
  ),
}

#: 목업 전용 상품 — UI 가 "테스트" 라고 표시할 근거이자, 실 결제 경로에서 걸러 낼 목록.
TEST_ONLY_PRODUCT_IDS: frozenset[str] = frozenset(
  {"credit_pack_test_2", "credit_pack_test_5"}
)


def get_product(product_id: str) -> IAPProduct:
  pid = (product_id or "").strip()
  if pid not in IAP_PRODUCTS:
    raise ValueError(f"Unknown product_id: {product_id}")
  return IAP_PRODUCTS[pid]
