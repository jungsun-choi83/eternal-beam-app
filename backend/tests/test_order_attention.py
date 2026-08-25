"""
운영 목록의 "처리 필요" 판정 — **서버가 한다.**

── 왜 서버로 옮겼는가 ───────────────────────────────────────────────────────
목록 행만으로는 답할 수 없는 질문이 있었다: 메모리 박스의 **사진 카드 원본이
있는가.** 그 값은 production_packages 에 있어서, 목록에서 알려면 주문마다 상세를
부르는 수밖에 없었다(화면 한 번에 N 개의 요청).

이제 패키지를 한 번의 일괄 질의로 읽고 여기서 판정한다. 목록 응답에는 불리언
하나와 사유 하나만 늘어난다 — pendingFiles 전체를 복제하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from backend.services import order_attention


@dataclass
class FakeOrder:
    order_id: str = "o1"
    product_type: str = "LETTER"
    payment_status: str = "paid"
    production_status: str = "pending"
    shipping_status: str = "pending"
    tracking_number: Optional[str] = None


@dataclass
class FakePackage:
    photo_image_url: Optional[str] = None


def test_unpaid_orders_are_not_operations_work():
    a = order_attention.evaluate(FakeOrder(payment_status="pending"), None)
    assert a.needs_attention is False


def test_not_prepared_is_flagged():
    a = order_attention.evaluate(FakeOrder(production_status="pending"), None)
    assert a.reason_code == "NOT_PREPARED"
    assert a.reason


def test_memory_box_without_photo_is_flagged_from_the_list():
    """
    **이 판정이 서버로 옮긴 이유다.** 예전에는 상세를 열어야만 알 수 있었다.
    """
    a = order_attention.evaluate(
        FakeOrder(product_type="MEMORY_BOX", production_status="ready"),
        FakePackage(photo_image_url=None),
    )
    assert a.reason_code == "PHOTO_MISSING"


def test_memory_box_with_photo_is_quiet():
    a = order_attention.evaluate(
        FakeOrder(product_type="MEMORY_BOX", production_status="ready"),
        FakePackage(photo_image_url="https://s/pet.png"),
    )
    assert a.needs_attention is False


def test_letter_never_asks_for_a_photo_card():
    """제품 구성에서 파생한다 — 편지에는 사진 카드가 없다."""
    a = order_attention.evaluate(
        FakeOrder(product_type="LETTER", production_status="ready"),
        FakePackage(photo_image_url=None),
    )
    assert a.needs_attention is False


def test_produced_without_tracking_is_flagged():
    a = order_attention.evaluate(
        FakeOrder(production_status="produced"), FakePackage(photo_image_url="x")
    )
    assert a.reason_code == "TRACKING_MISSING"


def test_produced_with_tracking_is_quiet():
    a = order_attention.evaluate(
        FakeOrder(production_status="produced", tracking_number="1234"),
        FakePackage(photo_image_url="x"),
    )
    assert a.needs_attention is False


def test_shipped_without_tracking_is_flagged():
    a = order_attention.evaluate(
        FakeOrder(production_status="produced", shipping_status="shipped"), None
    )
    assert a.reason_code == "SHIPPED_WITHOUT_TRACKING"


def test_shipped_with_tracking_is_quiet():
    a = order_attention.evaluate(
        FakeOrder(
            production_status="produced", shipping_status="shipped", tracking_number="1"
        ),
        None,
    )
    assert a.needs_attention is False


def test_delivered_orders_are_done():
    a = order_attention.evaluate(
        FakeOrder(
            production_status="produced", shipping_status="delivered", tracking_number="1"
        ),
        None,
    )
    assert a.needs_attention is False


def test_exactly_one_reason_per_order():
    """여러 사유를 내면 목록이 같은 주문으로 부풀고 무엇부터 할지 알 수 없다."""
    a = order_attention.evaluate(
        FakeOrder(product_type="MEMORY_BOX", production_status="produced"),
        FakePackage(photo_image_url=None),
    )
    assert a.needs_attention is True
    # 사진 누락이 송장 누락보다 앞선다 — 사진이 없으면 애초에 만들 수가 없다.
    assert a.reason_code == "PHOTO_MISSING"


def test_message_card_pending_is_not_attention():
    """
    문구 미승인 메시지 카드는 **주의가 아니다.** 스태프가 할 수 있는 일이 없고,
    패키지도 막지 않는다(ZIP 에서 빠질 뿐이다).
    """
    assert "MESSAGE" not in " ".join(order_attention.REASONS)


def test_every_reason_code_has_readable_text():
    for code, text in order_attention.REASONS.items():
        assert text and text != code, code


@pytest.mark.parametrize("missing_package", [None])
def test_missing_package_does_not_crash(missing_package):
    """패키지 일괄 조회가 실패해도 목록은 떠야 한다."""
    a = order_attention.evaluate(
        FakeOrder(product_type="MEMORY_BOX", production_status="ready"), missing_package
    )
    assert a.needs_attention is False
