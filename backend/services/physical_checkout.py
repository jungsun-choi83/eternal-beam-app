"""
물리 제품 체크아웃 — 일회성 Toss 결제 → 주문 PAID.

Phase 11 의 테마 결제와 **같은 구조**를 쓴다: 서버가 주문 금액을 확정해 보관하고,
확인할 때 리다이렉트 쿼리가 아니라 저장된 금액으로 Toss 에 묻는다. 구조를 맞춘
이유는 두 결제가 같은 실패 양상을 갖기 때문이다 — URL 위조, 새로고침 재확인,
남의 주문 확인. 같은 방어를 두 번 다르게 구현하면 한쪽이 반드시 약해진다.

── 이 모듈이 만들지 않는 것 ────────────────────────────────────────────────
  * 펫 — 이미 있는 canonical petId 를 가리킨다
  * 편지 — Soul Trace 가 만든 것을 가리킨다
  * Shaker 공유 — 기존 것을 **재사용**한다 (없으면 null, Phase 13 이 붙인다)
  * 구독 · 테마 · 크레딧 — 결제가 성공해도 주문 한 행만 PAID 가 된다
  * 생성 — 프로바이더로 가는 import 가 없다

결제 성공이 하는 일은 **physical_orders 한 행을 paid 로 바꾸는 것**이 전부다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from . import (
    acquisition_bonus,
    pet_ownership,
    physical_order,
    physical_product,
    soul_trace_letter,
    toss_billing,
)

logger = logging.getLogger(__name__)


class CheckoutError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _as_checkout_error(e: Exception) -> CheckoutError:
    return CheckoutError(
        getattr(e, "code", "ERROR"), getattr(e, "message", str(e)),
        status=getattr(e, "status", 400),
    )


@dataclass
class OrderCheckout:
    order_id: str
    product_type: str
    amount: int
    order_name: str
    currency: str
    client_key: str
    pet_id: str
    soul_trace_letter_id: Optional[str]


async def _reusable_share_id(user_id: str, pet_id: str) -> Optional[str]:
    """
    이 펫의 **기존** Shaker 공유가 있으면 그 id. 없으면 None.

    ⚠️ 여기서 새로 발급하지 않는다. 발급은 판매자/운영의 권한이고(Phase 10 소유
    모델), 주문마다 공유를 찍어 내면 "주문 수만큼 펫 경험이 생기는" 상태가 된다 —
    요구사항이 금지한 중복이다. 없으면 null 로 두고 Phase 13 에서 운영이 붙인다.

    조회 실패는 무시한다 — 공유 연결은 편의이지 결제의 전제가 아니다.
    """
    try:
        from . import shaker_share

        rows = await shaker_share.list_shares(user_id=user_id, pet_id=pet_id)
    except Exception:  # noqa: BLE001 — 공유 조회 실패가 주문을 막지 않는다
        logger.warning("기존 Shaker 공유 조회 실패 — 주문은 계속한다 (pet=%s)", pet_id)
        return None

    for r in rows:
        if not r.revoked_at:
            return r.share_id
    return None


async def start_checkout(
    *,
    user_id: str,
    pet_id: str,
    product_type: str,
    soul_trace_letter_id: str | None,
    recipient_name: str,
    recipient_phone: str,
    postal_code: str,
    address_line1: str,
    address_line2: str | None = None,
) -> OrderCheckout:
    """
    주문 생성 + 결제창 값 발급. **아직 아무 돈도 움직이지 않는다.**

    멤버십도 저장된 카드도 요구하지 않는다 — 인증된 사용자면 누구나 부를 수 있다.
    """
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    if not uid:
        raise CheckoutError("ORDER_INVALID", "user_id 가 필요합니다.")
    if not pid:
        # 주문은 canonical petId 를 **가리킨다**. 없으면 만들지 않고 거절한다.
        raise CheckoutError("PET_REQUIRED", "pet_id 가 필요합니다.", status=400)

    # 남의 펫으로 주문할 수 없다. 검사가 없으면 생산 패키지가 **남의 펫 QR** 을
    # 찍은 실물을 내 주소로 배송한다 — 종이라서 되돌릴 수 없다.
    # (편지 소유권은 아래에서 따로 본다. 둘 중 하나만 봐서는 막지 못한다.)
    try:
        await pet_ownership.assert_owned(uid, pid)
    except pet_ownership.PetOwnershipError as e:
        raise _as_checkout_error(e) from e

    try:
        product = physical_product.get_product(product_type)
    except physical_product.ProductError as e:
        raise _as_checkout_error(e) from e

    # 배송지는 실물의 전제다. 비어 있으면 인쇄해도 보낼 곳이 없다.
    for label, value in (
        ("recipient_name", recipient_name),
        ("recipient_phone", recipient_phone),
        ("postal_code", postal_code),
        ("address_line1", address_line1),
    ):
        if not (value or "").strip():
            raise CheckoutError("SHIPPING_INCOMPLETE", f"{label} 이(가) 필요합니다.")

    # 편지 연결. 두 제품 모두 Soul Trace 편지를 인쇄하므로 필수다.
    letter_id: Optional[str] = (soul_trace_letter_id or "").strip() or None
    #: 주문 시점 귀속 스냅샷. 편지에서 **서버가** 읽는다 — 요청 바디에 partner
    #: 필드는 없고, 브라우저가 귀속을 정할 방법도 없다.
    partner_id: Optional[str] = None
    partner_type: Optional[str] = None
    partner_name: Optional[str] = None
    partner_code: Optional[str] = None
    partner_track: Optional[str] = None
    partner_share_rate: Optional[float] = None

    if product.includes_letter:
        if not letter_id:
            raise CheckoutError(
                "LETTER_REQUIRED",
                "Soul Trace 편지를 먼저 연결해 주세요.",
                status=409,
            )
        try:
            # 남의 편지를 자기 주문에 붙일 수 없다 — 실물이라 되돌릴 수 없다.
            letter = await soul_trace_letter.assert_owned(uid, letter_id)
        except soul_trace_letter.LetterError as e:
            raise _as_checkout_error(e) from e
        # 귀속을 편지에서 복사한다. 주문이 정산 단위이므로 **이 시점의 사실**을
        # 남긴다 — 나중에 편지 쪽이 바뀌어도 결제된 주문은 흔들리지 않는다.
        partner_id = letter.partner_id
        partner_type = letter.partner_type
        partner_name = letter.partner_name
        # 비율까지 함께 얼린다. 계약은 바뀌고, 파트너의 **현재** 비율로 과거 주문을
        # 계산하면 이미 정산이 끝난 달의 숫자가 조용히 움직인다.
        partner_code = letter.partner_code
        partner_track = letter.partner_track
        partner_share_rate = letter.partner_share_rate

    share_id = await _reusable_share_id(uid, pid)

    try:
        order = await physical_order.create(
            order_id=toss_billing.new_order_id("order"),
            user_id=uid,
            pet_id=pid,
            product_type=product.product_type,
            amount=product.price_krw,
            soul_trace_letter_id=letter_id,
            recipient_name=recipient_name.strip(),
            recipient_phone=recipient_phone.strip(),
            postal_code=postal_code.strip(),
            address_line1=address_line1.strip(),
            address_line2=(address_line2 or "").strip() or None,
            shaker_share_id=share_id,
            currency=physical_product.CURRENCY,
            partner_id=partner_id,
            partner_type=partner_type,
            partner_name=partner_name,
            partner_code=partner_code,
            partner_track=partner_track,
            partner_share_rate=partner_share_rate,
        )
    except physical_order.OrderError as e:
        raise _as_checkout_error(e) from e

    return OrderCheckout(
        order_id=order.order_id,
        product_type=order.product_type,
        amount=order.amount,
        order_name=f"Eternal Beam · {order.product_type}",
        currency=order.currency,
        client_key=toss_billing.client_key(),
        pet_id=order.pet_id,
        soul_trace_letter_id=order.soul_trace_letter_id,
    )


@dataclass
class ConfirmOutcome:
    order_id: str
    product_type: str
    payment_status: str
    #: **이번 호출이 실제로 승인한 금액.** 멱등 호출이면 0.
    charged: int
    already_paid: bool


async def confirm(
    *, user_id: str, payment_key: str, order_id: str, amount: int | None = None
) -> ConfirmOutcome:
    """
    결제창 승인 → 서버 검증 → 주문 PAID.

    성공이 하는 일은 **주문 한 행의 payment_status 를 바꾸는 것**이 전부다.
    구독도 테마도 크레딧도 생성도 일어나지 않는다.
    """
    uid = (user_id or "").strip()
    oid = (order_id or "").strip()
    if not uid or not oid:
        raise CheckoutError("ORDER_INVALID", "order_id 가 필요합니다.")

    try:
        order = await physical_order.get(oid)
    except physical_order.OrderError as e:
        raise _as_checkout_error(e) from e

    if not order:
        raise CheckoutError("ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.", status=404)

    # 남의 주문을 확인할 수 없다. order_id 는 리다이렉트 URL 에 노출되므로,
    # 검사가 없으면 남의 결제로 자기 주문을 PAID 로 만들 수 있다.
    if order.user_id != uid:
        raise CheckoutError("ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.", status=404)

    if order.paid:
        return ConfirmOutcome(
            order_id=order.order_id, product_type=order.product_type,
            payment_status=physical_order.PAYMENT_PAID, charged=0, already_paid=True,
        )
    if not order.pending:
        raise CheckoutError("ORDER_NOT_PENDING", "이미 종료된 주문입니다.", status=409)

    # 리다이렉트 금액은 **대조용**이다. 주문을 죽이지는 않는다 — 틀린 금액으로는
    # 어차피 승인되지 않고, 죽이면 정당한 재시도만 막힌다(Phase 11 에서 실측).
    if amount is not None and int(amount) != order.amount:
        logger.warning(
            "물리 주문 금액 불일치 — user=%s order=%s 기대=%s 수신=%s",
            uid, oid, order.amount, amount,
        )
        raise CheckoutError(
            "ORDER_AMOUNT_MISMATCH", "주문 금액이 일치하지 않습니다.", status=400
        )

    result = await toss_billing.confirm_payment(
        payment_key=payment_key, order_id=order.order_id, amount=order.amount
    )
    if not result.ok:
        await physical_order.mark_failed(order_id=oid, failure_code=result.failure_code)
        logger.warning(
            "물리 주문 결제 실패 — user=%s order=%s code=%s", uid, oid, result.failure_code
        )
        raise CheckoutError(
            "ORDER_PAYMENT_FAILED",
            result.failure_message or "결제가 완료되지 않았습니다.",
            status=402,
        )

    try:
        await physical_order.mark_paid(
            order_id=oid, payment_key=result.payment_key, amount=result.amount
        )
    except physical_order.OrderError as e:
        # 돈은 나갔는데 주문 갱신이 실패했다. 조용히 성공으로 답하지 않는다 —
        # order_id/payment_key 가 로그에 남아야 수동 복구가 가능하다.
        logger.error(
            "물리 주문 결제는 성공했으나 상태 갱신 실패 — order=%s payment=%s",
            oid, result.payment_key,
        )
        raise _as_checkout_error(e) from e

    logger.warning(
        "물리 주문 결제 완료 — user=%s order=%s product=%s amount=%s",
        uid, oid, order.product_type, result.amount,
    )

    # ── 결제 다음 칸 ──────────────────────────────────────────────────────────
    # 여기까지가 예전의 끝이었다. PAID 로 바꾸고 끝났고, Shaker 공유·QR·생산
    # 패키지는 **아무도 만들지 않았다** — 운영이 손으로 누르기 전까지.
    #
    # finalize_quietly 는 실패해도 예외를 올리지 않는다. 이미 돈을 받았으므로
    # 여기서 실패를 던지면 고객이 결제 실패 화면을 보고 재결제를 시도한다.
    # 실패하면 주문은 PAID·production_status=pending 으로 남아 재시도 가능하다.
    from . import order_finalization

    await order_finalization.finalize_quietly(order_id=oid)

    # ── 획득 보너스 (Phase 9) ────────────────────────────────────────────────
    # LETTER +3 / MEMORY BOX +10. 고객이 실물을 받고 **다시 돌아오게** 하는 고리다.
    #
    # 멱등 키가 주문 id 라, 결제 확인 화면을 새로고침해도 다시 지급되지 않는다.
    # (confirm 은 이미 already_paid 로 조기 반환하지만, 방어를 한 겹에만 두지 않는다.)
    #
    # finalize_quietly 와 같은 규약: 실패해도 주문을 뒤집지 않는다. 고객은 돈을
    # 냈고 주문은 성사됐다 — 덤을 못 줬다고 그것을 취소할 수는 없다.
    await acquisition_bonus.grant_physical(
        user_id=uid, order_id=oid, product_type=order.product_type
    )

    return ConfirmOutcome(
        order_id=order.order_id, product_type=order.product_type,
        payment_status=physical_order.PAYMENT_PAID,
        charged=result.amount, already_paid=False,
    )


# ── 재조정 (브라우저가 돌아오지 못한 경우) ───────────────────────────────────
#
# 실패 양상: 결제창에서 승인이 끝난 **직후** 사용자가 브라우저를 닫거나 네트워크가
# 끊기면 successUrl 로 돌아오지 못한다. 그러면 Toss 에는 승인된 결제가 있는데
# 우리 주문은 영원히 pending 이다 — **돈은 받았고 물건은 만들지 않는 상태**다.
# 실물이라 고객은 결제 문자만 받고 아무것도 오지 않는다.
#
# 재조정은 그 간극을 메운다: 미결 주문을 Toss 에 물어보고, 승인돼 있으면 PAID 로
# 만든다. 결제를 **새로 만들지 않는다** — 이미 일어난 일을 우리 쪽에 반영할 뿐이다.


async def _reconcile_one(order: physical_order.PhysicalOrder) -> Optional[str]:
    """
    미결 주문 하나를 Toss 에 확인. 확정했으면 order_id, 아니면 None.

    예외를 밖으로 내지 않는다 — 한 건 실패로 스윕 전체가 멈추면 안 된다.
    """
    try:
        found = await toss_billing.lookup_payment_by_order(order.order_id)
    except Exception:  # noqa: BLE001
        logger.warning("재조정 조회 실패 — order=%s", order.order_id)
        return None

    if not found or not found.ok:
        # 결제한 적 없거나 아직 승인되지 않았다. **주문을 실패로 만들지 않는다** —
        # 사용자가 결제창을 다시 열어 끝낼 수 있어야 한다.
        return None

    # 금액이 다르면 손대지 않는다. 자동 경로가 금액 불일치를 덮으면,
    # 사람이 알아차릴 기회 없이 잘못된 주문이 생산으로 넘어간다.
    if int(found.amount) != int(order.amount):
        logger.error(
            "재조정 금액 불일치 — order=%s 기대=%s 승인=%s (수동 확인 필요)",
            order.order_id, order.amount, found.amount,
        )
        return None

    try:
        await physical_order.mark_paid(
            order_id=order.order_id, payment_key=found.payment_key, amount=found.amount,
        )
    except physical_order.OrderError:
        logger.exception("재조정 상태 갱신 실패 — order=%s", order.order_id)
        return None

    logger.warning(
        "재조정으로 주문 확정 — order=%s user=%s amount=%s (브라우저가 돌아오지 못한 결제)",
        order.order_id, order.user_id, found.amount,
    )

    # 재조정으로 확정된 주문도 **같은 마무리**를 타야 한다. 여기서 빼먹으면
    # "브라우저가 돌아오지 못한 결제"만 생산 준비가 안 된 채 남는다 —
    # 정확히 가장 눈에 안 띄는 주문들이다.
    from . import order_finalization

    await order_finalization.finalize_quietly(order_id=order.order_id)
    return order.order_id


async def reconcile_user(*, user_id: str) -> list[str]:
    """
    이 사용자의 미결 주문을 Toss 와 맞춘다. 확정된 order_id 목록.

    앱이 다시 열릴 때 부르는 **안전망**이다. 사용자가 결제 직후 브라우저를 닫았다가
    나중에 돌아오면, 그때 주문이 조용히 PAID 로 정리된다.
    """
    uid = (user_id or "").strip()
    if not uid:
        return []
    try:
        pending = await physical_order.list_pending(user_id=uid)
    except physical_order.OrderError:
        logger.warning("재조정 대상 조회 실패 (user=%s)", uid)
        return []

    confirmed: list[str] = []
    for order in pending:
        oid = await _reconcile_one(order)
        if oid:
            confirmed.append(oid)
    return confirmed


async def reconcile_due(*, limit: int = 100) -> list[str]:
    """
    전체 미결 주문 스윕 — 배치/크론용.

    사용자가 **영영 돌아오지 않는** 경우를 위한 마지막 보루다. 앱 재방문에만
    기대면 그 사용자는 결제만 하고 물건을 못 받는다.
    """
    try:
        pending = await physical_order.list_pending(limit=limit)
    except physical_order.OrderError:
        logger.warning("재조정 스윕 대상 조회 실패")
        return []

    confirmed: list[str] = []
    for order in pending:
        oid = await _reconcile_one(order)
        if oid:
            confirmed.append(oid)
    return confirmed
