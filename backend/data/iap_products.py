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
}


def get_product(product_id: str) -> IAPProduct:
  pid = (product_id or "").strip()
  if pid not in IAP_PRODUCTS:
    raise ValueError(f"Unknown product_id: {product_id}")
  return IAP_PRODUCTS[pid]
