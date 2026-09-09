"""
"이 주문에 지금 사람이 손대야 하는가" — **순수 판정.**

── 왜 서버가 판정하는가 ─────────────────────────────────────────────────────
운영 목록은 주문 행만 갖고 있었다. 그 값만으로는 답할 수 없는 질문이 있다:
**메모리 박스의 사진 카드 원본이 있는가.** 그것은 production_packages 에 있고,
목록에서 알려면 주문마다 상세를 부르는 수밖에 없었다 — 화면 한 번에 N 개의 요청.

그래서 판정을 서버로 옮긴다. 패키지는 한 번의 일괄 질의로 읽고(get_packages),
나머지는 이미 손에 있는 주문 행으로 답한다. 목록 응답은 **불리언 하나와 사유
하나**만 늘어난다 — pendingFiles 전체를 목록에 복제하지 않는다.

── 새 신호를 만들지 않는다 ──────────────────────────────────────────────────
전부 이미 있는 도메인 상태에서 나온다. 각 사유는 "무엇을 하면 사라지는가"가
분명해야 하고, 그렇지 않은 것은 넣지 않는다. 예: 문구 미승인 메시지 카드는
**주의가 아니다** — 스태프가 할 수 있는 일이 없고, 패키지도 막지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

#: 사유 코드 → 사람이 읽는 문장. 화면이 문구를 또 만들지 않게 서버가 준다.
REASONS: dict[str, str] = {
    "NOT_PREPARED": "결제됐지만 생산 준비가 아직입니다.",
    "PHOTO_MISSING": "사진 카드 원본이 없어 카드와 패키지를 만들 수 없습니다.",
    "TRACKING_MISSING": "제작이 끝났지만 송장이 없어 발송할 수 없습니다.",
    "SHIPPED_WITHOUT_TRACKING": "송장 없이 발송으로 표시되어 있습니다.",
}


@dataclass(frozen=True)
class Attention:
    needs_attention: bool
    reason_code: Optional[str] = None
    reason: Optional[str] = None


_NONE = Attention(False)


def _flag(code: str) -> Attention:
    return Attention(True, code, REASONS[code])


def evaluate(order: Any, package: Any | None) -> Attention:
    """
    주문 한 건의 판정.

    순서가 곧 우선순위다 — 한 주문은 사유를 **하나만** 낸다. 여러 개를 내면
    목록이 같은 주문으로 부풀고, 스태프는 무엇부터 할지 알 수 없다.
    """
    payment = (getattr(order, "payment_status", "") or "").lower()
    if payment != "paid":
        # 결제 전 주문은 운영이 할 일이 없다. 목록 기본이 paid_only 이므로
        # 여기 오는 일 자체가 드물다.
        return _NONE

    production = (getattr(order, "production_status", "") or "").lower()
    shipping = (getattr(order, "shipping_status", "") or "").lower()
    tracking = (getattr(order, "tracking_number", "") or "").strip()

    if shipping in ("shipped", "delivered"):
        return _NONE if tracking else _flag("SHIPPED_WITHOUT_TRACKING")

    if production == "pending":
        return _flag("NOT_PREPARED")

    # 준비된 뒤에만 물을 수 있는 질문 — 이 판정 때문에 목록이 상세를 부르지
    # 않아도 되게 하는 것이 이 모듈의 존재 이유다.
    if package is not None and _needs_photo(order) and not _has_photo(package):
        return _flag("PHOTO_MISSING")

    if production == "produced" and not tracking:
        return _flag("TRACKING_MISSING")

    return _NONE


def _needs_photo(order: Any) -> bool:
    """제품 구성에서 파생한다 — 제품 이름으로 분기하지 않는다."""
    try:
        from . import physical_product

        return physical_product.get_product(
            getattr(order, "product_type", "") or ""
        ).needs_photo_card
    except Exception:
        return False


def _has_photo(package: Any) -> bool:
    return bool((getattr(package, "photo_image_url", "") or "").strip())
