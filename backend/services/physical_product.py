"""
물리 제품 카탈로그 — LETTER / MEMORY BOX.

── 가격은 PM 이 확정했다 ─────────────────────────────────────────────────────
Phase 11 의 테마와 달리 여기 숫자는 **발명이 아니다.** 핸드오프와 지시가 명시했다:

    LETTER      ₩14,900
    MEMORY BOX  ₩49,000

그래서 기본값으로 넣는다. 다만 환경변수로 덮어쓸 수 있게 둔다 — 인쇄 단가나
배송비가 바뀌면 코드 배포 없이 조정해야 한다.

⚠️ **₩14,900 은 BREATHING 값이 아니다.** 종이·봉투·인쇄·배송에 대한 값이다.
   BREATHING 은 언제나 무료이고, 이 주문이 그것을 잠금 해제하지 않는다.
   QR 은 이미 무료인 경험으로 가는 길일 뿐이다.

── NFC 는 지금 없다 ──────────────────────────────────────────────────────────
핸드오프가 "MEMORY BOX + NFC ₩59,000 은 나중"이라고 못 박았다. 카탈로그에 넣지
않는다 — 넣으면 팔 수 있게 되고, 팔면 만들 수 없는 것을 판 것이 된다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

PRODUCT_LETTER = "LETTER"
PRODUCT_MEMORY_BOX = "MEMORY_BOX"

CURRENCY = "KRW"

#: PM 확정 가격. 환경변수로 덮어쓸 수 있다.
_DEFAULT_PRICES = {
    PRODUCT_LETTER: 14_900,
    PRODUCT_MEMORY_BOX: 49_000,
}

#: 구성품. Phase 13 이 이 목록을 보고 인쇄물을 만든다 — 지금은 **선언만** 한다.
_CONTENTS = {
    PRODUCT_LETTER: (
        "printed_letter",   # Soul Trace 편지 (여기서 생성하지 않는다)
        "envelope",
        "qr",               # 기존 Shaker 공유를 가리킨다
    ),
    PRODUCT_MEMORY_BOX: (
        "printed_letter",
        "envelope",
        "photo_card",       # 85×55mm 펫 사진 카드
        "qr_memory_card",
        "rigid_box",
        "black_tissue",
        "message_card",
    ),
}


class ProductError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class PhysicalProduct:
    product_type: str
    price_krw: int
    currency: str = CURRENCY
    contents: tuple[str, ...] = field(default_factory=tuple)

    @property
    def includes_letter(self) -> bool:
        """Soul Trace 편지가 필요한가 — 둘 다 필요하다(둘 다 편지를 인쇄한다)."""
        return "printed_letter" in self.contents


def price_krw(product_type: str) -> int:
    raw = (os.getenv(f"PRODUCT_PRICE_{product_type}_KRW") or "").strip()
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass  # 설정 오타는 확정 가격으로 떨어진다 — 0원 배송보다 낫다
    return _DEFAULT_PRICES[product_type]


def normalize_product(raw: str | None) -> str:
    p = (raw or "").strip().upper()
    if not p:
        raise ProductError("PRODUCT_REQUIRED", "product_type 이 필요합니다.")
    if p not in _DEFAULT_PRICES:
        # NFC 상품을 요청해도 여기서 걸린다 — 아직 만들 수 없는 것을 팔지 않는다.
        raise ProductError(
            "PRODUCT_UNKNOWN", f"{p} 는 판매 중인 제품이 아닙니다.", status=404
        )
    return p


def get_product(product_type: str) -> PhysicalProduct:
    p = normalize_product(product_type)
    return PhysicalProduct(
        product_type=p, price_krw=price_krw(p), contents=_CONTENTS[p]
    )


def catalog() -> list[PhysicalProduct]:
    return [get_product(p) for p in (PRODUCT_LETTER, PRODUCT_MEMORY_BOX)]
