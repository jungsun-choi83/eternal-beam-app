"""
결제 완료 → 생산 준비까지의 **한 걸음**. Phase 14 가 메우는 구멍이다.

── 지금까지 무엇이 비어 있었나 ─────────────────────────────────────────────
physical_checkout.confirm() 은 주문을 PAID 로 바꾸는 것으로 끝났다. 그 뒤의

    Shaker 공유 확보 → 주문에 붙이기 → 인쇄용 QR → 생산 패키지 → READY

는 **아무도 부르지 않았다.** production_package.prepare 로 가는 유일한 경로는
운영 콘솔의 수동 버튼이었다. 그래서 고객이 결제를 마쳐도 서버에는 "돈은 받았고
만들 준비는 되지 않은" 주문만 남았고, 누군가 손으로 누르기 전까지 아무 일도
일어나지 않았다.

── 왜 결제 확인 안에 인라인하지 않는가 ─────────────────────────────────────
결제 확인은 **돈**을 다루고, 이 함수는 **물건**을 다룬다. 둘의 실패는 의미가
다르다: 결제 실패는 주문을 죽이지만, 생산 준비 실패는 죽이면 안 된다 —
이미 받은 돈이 있기 때문이다. 그래서 여기서 나는 예외는 절대 위로 올라가
결제를 되돌리지 않는다(finalize_quietly 참고).

── 멱등성 ──────────────────────────────────────────────────────────────────
Toss 콜백은 중복으로 온다(재시도·새로고침·재조정 스윕). 세 단계 모두 두 번
불려도 한 번과 같아야 한다:

    공유   이미 붙어 있으면 재사용, 없으면 (user, pet) 의 기존 공유를 찾고,
           그것도 없을 때만 발급. 주문에 붙이는 것은 compare-and-set 이라
           동시 콜백 중 하나만 이긴다.
    QR     공유당 산출물 1개(shaker_qr_artifact). 이미 있으면 그 바이트를 쓴다.
    패키지 order_id 가 기본키라 upsert 로 수렴한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from . import (
    physical_order,
    physical_product,
    production_package,
    qr_service,
    shaker_qr_artifact,
    shaker_share,
)

logger = logging.getLogger(__name__)

#: 인쇄물용 공유의 용도 표시. 화면 공유와 구분해 운영이 알아볼 수 있게 한다.
PRINT_PURPOSE = "print"


class FinalizationError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class FinalizationOutcome:
    order_id: str
    shaker_share_id: Optional[str]
    qr_share_url: Optional[str]
    production_status: str
    package_ready: bool
    #: 실패했을 때만 채워진다. 주문은 그대로 PAID 로 남는다.
    error_code: Optional[str] = None
    error_message: Optional[str] = None


async def _existing_share_id(user_id: str, pet_id: str) -> Optional[str]:
    """
    이 펫의 **살아 있는** 공유. 없으면 None.

    새로 만들기 전에 반드시 먼저 본다 — 주문마다 공유를 찍어 내면 한 아이에게
    여러 개의 "펫 경험"이 생기고, 그건 요구사항이 금지한 중복이다.
    """
    try:
        rows = await shaker_share.list_shares(user_id=user_id, pet_id=pet_id)
    except Exception:  # noqa: BLE001 — 조회 실패로 새 공유를 만들지 않는다
        logger.warning("기존 Shaker 공유 조회 실패 (pet=%s)", pet_id)
        raise FinalizationError(
            "SHARE_LOOKUP_FAILED",
            "기존 Shaker 공유를 확인하지 못했습니다.",
            status=503,
        )
    for r in rows:
        if not r.revoked_at:
            return r.share_id
    return None


async def _ensure_share(
    order: physical_order.PhysicalOrder,
) -> tuple[str, Optional[str]]:
    """
    이 주문이 인쇄할 공유 하나를 확정한다. **재사용이 기본, 발급은 최후다.**

    돌려주는 URL 이 None 일 수 있는 이유: 원문 토큰은 **발급 순간에만** 존재한다
    (저장하지 않는다). 그래서 기존 공유를 재사용할 때는 URL 을 다시 만들 수 없고,
    보관된 QR 산출물(shaker_qr_artifact)이 그 자리를 대신한다 — 그래야 재인쇄가
    이미 배송된 종이와 **같은 바이트**가 된다.
    """
    if order.shaker_share_id:
        return order.shaker_share_id, None

    found = await _existing_share_id(order.user_id, order.pet_id)
    if found:
        await physical_order.attach_share(order_id=order.order_id, shaker_share_id=found)
        return found, None

    # 발급해야 한다. BREATHING 위치는 **운영 콘솔과 같은 규약 탐색**을 쓴다
    # (shaker_ops.locate_breathing) — 여기서 따로 구현하면 두 경로가 서로 다른
    # 영상을 가리키기 시작한다.
    from . import shaker_ops

    located = await shaker_ops.locate_breathing(order.user_id, order.pet_id)
    if not located:
        raise FinalizationError(
            "PET_BREATHING_MISSING",
            (
                "이 펫의 BREATHING 영상을 찾을 수 없어 Shaker 공유를 만들 수 없습니다. "
                "운영 콘솔에서 확인한 뒤 다시 시도해 주세요."
            ),
            status=409,
        )
    loc, breathing_url = located

    # ⚠️ **발급 전에** 인쇄용 base 를 확인한다.
    #
    # 예전에는 create_share 다음에 확인했다. 그래서 PUBLIC_WEB_BASE_URL 이 없으면
    # 공유는 이미 발급된 뒤에 실패했고, 그 토큰은 **영원히 사라졌다** —
    # shaker_shares 는 token_hash 만 저장하므로 URL 을 복원할 수 없다.
    # 결과는 QR 을 만들 수 없는 고아 공유였다(주문 eb_order_d78a1b1e... 가 그것이다).
    #
    # 설정 문제로 되돌릴 수 없는 자원을 태우지 않는다.
    base = qr_service.assert_printable_base()

    # 인쇄물의 QR 은 **오래 살아야 하므로** 만료를 두지 않는다(ttl_days=None).
    # 짧은 수명 토큰을 종이에 찍으면 며칠 뒤 죽은 QR 이 된다.
    share_id, token = await shaker_share.create_share(
        user_id=order.user_id,
        pet_id=order.pet_id,
        breathing_url=breathing_url,
        breathing_bucket=loc.bucket,
        breathing_object_path=loc.object_path,
        ttl_days=None,
        created_by="order_finalization",
        purpose=PRINT_PURPOSE,
        order_ref=order.order_id,
    )
    await physical_order.attach_share(order_id=order.order_id, shaker_share_id=share_id)

    # 경합 방어: 동시 콜백 둘이 각각 공유를 만들었을 수 있다. 주문에 실제로
    # 붙은 값을 다시 읽어 **그것**을 정본으로 삼는다 — 인쇄되는 것은 그 값이다.
    refreshed = await physical_order.get(order.order_id)
    winning = (refreshed.shaker_share_id if refreshed else None) or share_id
    if winning != share_id:
        # 우리가 만든 공유는 졌다. 그 URL 을 쓰면 인쇄물이 주문과 어긋난다.
        return winning, None

    # 경로 모양은 운영 콘솔이 발급하는 것과 **같아야** 한다 — 두 경로가 다른
    # 모양의 링크를 찍으면 같은 제품에 서로 다른 QR 이 인쇄된다.
    share_url = f"{base}{qr_service.SHAKER_PATH}?petId={order.pet_id}&share={token}"

    # 산출물을 **반드시** 보관한다. 원문 토큰은 이 함수를 벗어나는 순간 사라지므로,
    # 보관에 실패한 공유로는 이후 어떤 방법으로도 QR 을 만들 수 없다 — 재시도해도,
    # 운영이 손으로 URL 을 넣으려 해도 토큰을 아는 사람이 없다.
    #
    # 그래서 여기서는 **삼키지 않는다.** 조용히 넘어가면 고아 공유가 생기고,
    # 주문은 영영 생산으로 넘어가지 못한 채 PAID 로 남는다.
    try:
        await shaker_qr_artifact.store(
            share_id=share_id,
            token_hash=shaker_share.hash_token(token),
            pet_id=order.pet_id,
            share_url=share_url,
            purpose=PRINT_PURPOSE,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("QR 산출물 보관 실패 — share=%s. 이 공유로는 QR 을 만들 수 없다.", share_id)
        raise FinalizationError(
            "QR_ARTIFACT_STORE_FAILED",
            "QR 산출물을 보관하지 못했습니다. 이 공유로는 인쇄용 QR 을 만들 수 없습니다.",
            status=503,
        ) from e
    return winning, share_url


async def finalize(*, order_id: str) -> FinalizationOutcome:
    """
    PAID 주문 → 공유·QR·생산 패키지 → production_status = ready.

    결제되지 않은 주문에는 아무 것도 만들지 않는다 — 돈을 받기 전에 QR 을
    발급하면 취소된 주문의 공유가 세상에 남는다.
    """
    order = await physical_order.get(order_id)
    if not order:
        raise FinalizationError("ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.", status=404)
    if not order.paid:
        raise FinalizationError(
            "ORDER_NOT_PAID", "결제된 주문만 생산 준비할 수 있습니다.", status=409
        )

    share_id, minted_url = await _ensure_share(order)

    # 인쇄용 QR 은 **웹앱 도메인**을 가리켜야 한다. API 도메인이 찍히면 스캔한
    # 사람이 JSON 을 보게 된다. assert_printable_base 가 그것을 막는다.
    try:
        qr_service.assert_printable_base()
    except qr_service.QrError as e:
        raise FinalizationError(e.code, e.message, status=e.status) from e

    # ── 사진 카드 원본 (Phase 17) ────────────────────────────────────────────
    # MEMORY BOX 는 85×55mm 사진 카드를 포함한다. 예전에는 여기서 사진을 전혀
    # 찾지 않아 photo_image_url 이 None 으로 굳었고, 그러면 사진 카드도 패키지
    # ZIP 도 만들어지지 않았다 — 결제는 끝났는데 인쇄소에 넘길 것이 없는 상태였다.
    #
    # 찾지 못해도 **완결을 막지 않는다.** 편지와 QR 은 이미 준비돼 있고, 사진은
    # 운영이 나중에 붙일 수 있다(attach_photo). 사진 하나 때문에 결제된 주문을
    # pending 에 묶어 두면 그 주문은 아무도 손대지 않는 한 영영 멈춘다.
    photo_url: Optional[str] = None
    if physical_product.get_product(order.product_type).needs_photo_card:
        from . import shaker_ops

        try:
            photo_url = await shaker_ops.locate_pet_photo(order.user_id, order.pet_id)
        except Exception:  # noqa: BLE001 — 조회 실패가 완결을 막지 않는다
            logger.warning("사진 카드 원본 조회 실패 — pet=%s (운영이 나중에 붙인다)", order.pet_id)

    # 패키지 준비. prepare 는 order_id 기본키로 upsert 하므로 재실행이 안전하고,
    # QR 은 이미 보관된 산출물이 있으면 **같은 바이트**를 다시 쓴다.
    try:
        pkg = await production_package.prepare(
            order_id=order_id, qr_share_url=minted_url, photo_image_url=photo_url
        )
    except production_package.ProductionError as e:
        raise FinalizationError(e.code, e.message, status=e.status) from e

    updated = await physical_order.advance_production(
        order_id=order_id, to=physical_order.PRODUCTION_READY
    )

    logger.warning(
        "주문 생산 준비 완료 — order=%s pet=%s share=%s status=%s",
        order_id, order.pet_id, share_id, updated.production_status,
    )
    return FinalizationOutcome(
        order_id=order_id,
        shaker_share_id=share_id,
        qr_share_url=pkg.qr_share_url or None,
        production_status=updated.production_status,
        package_ready=True,
    )


async def finalize_quietly(*, order_id: str) -> FinalizationOutcome:
    """
    결제 확인 경로에서 부르는 버전. **절대 예외를 위로 올리지 않는다.**

    이미 돈을 받았다. 여기서 예외가 결제 확인을 실패로 만들면 고객은 결제
    실패 화면을 보고 다시 결제하려 하고, 우리는 이중 청구를 만든다. 생산 준비는
    나중에 다시 시도할 수 있지만 이중 청구는 되돌리기 어렵다.

    실패해도 주문은 PAID · production_status=pending 으로 남는다 —
    운영 콘솔의 prepare 나 이 함수 재호출로 **그대로 재시도할 수 있는 상태**다.
    """
    try:
        return await finalize(order_id=order_id)
    except Exception as e:  # noqa: BLE001 — 결제를 되돌리지 않는다
        code = getattr(e, "code", "FINALIZATION_FAILED")
        message = getattr(e, "message", str(e))
        logger.error(
            "결제는 완료됐으나 생산 준비 실패 — order=%s code=%s message=%s "
            "(주문은 PAID 로 남는다. 운영 콘솔에서 재시도 가능)",
            order_id, code, message,
        )
        order = await physical_order.get(order_id)
        return FinalizationOutcome(
            order_id=order_id,
            shaker_share_id=(order.shaker_share_id if order else None),
            qr_share_url=None,
            production_status=(
                order.production_status if order else physical_order.PRODUCTION_PENDING
            ),
            package_ready=False,
            error_code=code,
            error_message=message,
        )
