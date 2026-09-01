"""
**생성 크레딧 커머스** — 아이들과 액션이 함께 쓰는 단 하나의 서비스 (Phase 7–8).

    Paw Wave          4 크레딧        BLINKING       5 크레딧
    Special Greeting  8 크레딧        TAIL_WAGGING   3 크레딧

가격도 사유도 다르지만 **경로는 하나다.** 액션용 지갑도, 액션용 원장도, 액션용
멱등 모델도 만들지 않는다 — 두 벌이 생기면 서로 조금씩 어긋나고, 그 어긋남은
돈에서 드러난다.

갈라지는 것은 딱 두 가지이고 둘 다 **데이터**다:
    가격   digital_products 의 행       (상품마다 다르다)
    사유   product_key 접두사에서 파생   (idle_generation / action_generation)

코드 분기는 reason_for() 한 줄뿐이다.

── 예전 이름 ────────────────────────────────────────────────────────────────
이 파일은 idle_credit_generation.py 였다. 액션도 처음부터 같은 경로를 탔지만
이름이 그렇게 말하지 않아 "액션은 어디서 처리하지?" 를 묻게 만들었다.

    Sleeping 선택
        ↓  reserve(5)                    ← 예약 없이는 제출하지 않는다
    scene/motion 생성 작업
        ↓  프로바이더
    검증
      PASS → commit(-5) → owned_generated_asset  → Sleeping #1 영구 소유
      FAIL → release(+5)                          → 아무 일도 없던 것과 같다

── 다시 만들면 다시 낸다 ───────────────────────────────────────────────────
Sleeping 을 또 만들면 또 5 크레딧이고, Sleeping #2 가 **따로** 소유된다.
그래서 멱등 키는 (사용자, 상품) 이 될 수 없다 — 그러면 두 번째 생성이 재플레이로
보인다. 키는 **한 번의 사용자 조작**을 가리켜야 하고, 그 값은 호출부가 준다
(프론트가 버튼을 누를 때 만든다). 재시도·새로고침은 같은 키를 다시 보내므로
두 번 잡히지 않는다.

── 이미 만든 것을 재생하는 것은 0 크레딧이다 ───────────────────────────────
이 모듈은 **생성**에만 관여한다. 재생 권한은 owned_generated_assets 와
generated_motions 가 정하고, 거기에는 잔액이 등장하지 않는다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from . import credit_ledger, credit_reservation, owned_assets, product_catalog

logger = logging.getLogger(__name__)


class GenerationCreditError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class ReservedGeneration:
    reservation_ledger_id: str
    credits: int
    product_key: str
    balance_after: int
    replayed: bool


def reason_for(product_key: str) -> str:
    """상품 종류 → 원장 사유. 카탈로그 접두사가 그대로 사유를 정한다."""
    if product_key.startswith(product_catalog.PREFIX_IDLE):
        return credit_ledger.REASON_IDLE_GENERATION
    if product_key.startswith(product_catalog.PREFIX_ACTION):
        return credit_ledger.REASON_ACTION_GENERATION
    return credit_ledger.REASON_ACTION_GENERATION


async def reserve_for_action(
    *,
    user_id: str,
    action_id: str,
    idempotency_key: str,
    ref_type: Optional[str] = None,
    ref_id: Optional[str] = None,
) -> Optional[ReservedGeneration]:
    """
    이 행동을 만들기 위한 크레딧을 잡는다.

    **무료 상품(가격 0)은 None 을 돌려준다** — 예약할 것이 없다. BREATHING 이
    그 경우이고, 무료 경로가 예약을 요구하게 되면 무료가 아니게 된다.

    Raises:
        GenerationCreditError:
            PRODUCT_NOT_SOLD (409) / INSUFFICIENT_CREDITS (402) /
            RESERVATION_UNAVAILABLE (503)
    """
    product_key = owned_assets.product_key_for_action(action_id)

    try:
        price = await product_catalog.credit_price(product_key)
    except product_catalog.CatalogUnavailableError as e:
        # 가격을 모르면 **제출하지 않는다.** 0 으로 떨어뜨리면 장애 중에 유료
        # 생성이 공짜로 돌아간다.
        raise GenerationCreditError("CATALOG_UNAVAILABLE", e.message, status=503) from e

    if price is None:
        raise GenerationCreditError(
            "PRODUCT_NOT_SOLD",
            f"현재 판매하지 않는 상품입니다: {product_key}",
            status=409,
        )
    if price == 0:
        return None  # 무료 — 예약 없음

    try:
        res = await credit_reservation.reserve(
            user_id=user_id,
            credits=price,
            idempotency_key=idempotency_key,
            product_key=product_key,
            reason=reason_for(product_key),
            ref_type=ref_type,
            ref_id=ref_id,
        )
    except credit_reservation.InsufficientCreditsError as e:
        raise GenerationCreditError(
            "INSUFFICIENT_CREDITS",
            "크레딧이 부족합니다. 크레딧을 충전한 뒤 다시 시도해 주세요.",
            status=402,
        ) from e
    except credit_reservation.ReservationError as e:
        raise GenerationCreditError(e.code, e.message, status=e.status) from e

    return ReservedGeneration(
        reservation_ledger_id=res.ledger_id,
        credits=res.credits,
        product_key=product_key,
        balance_after=res.balance_after,
        replayed=res.replayed,
    )


async def commit_for_asset(
    *,
    reservation_ledger_id: Optional[str],
    credits: int,
    user_id: str,
    pet_id: str,
    action_id: str,
    video_url: str,
    object_path: Optional[str] = None,
    bucket: Optional[str] = None,
    scene_id: Optional[str] = None,
    source_job_id: Optional[str] = None,
) -> None:
    """
    검증 PASS → **예약 확정 + 영구 소유 자산 기록.**

    순서가 계약이다: 확정을 먼저 한다. 자산을 먼저 쓰고 확정이 실패하면
    "공짜로 준 자산"이 남고, 그 상태는 원장으로 설명되지 않는다. 반대로 확정만
    되고 자산 기록이 실패하면 **예외가 올라가** 승격이 중단되므로, 포인터가
    옮겨지지 않고 재시도가 같은 예약을 다시 확정한다(멱등).
    """
    if reservation_ledger_id:
        await credit_reservation.commit(reservation_ledger_id)

    await owned_assets.record(
        owned_assets.OwnedAsset(
            user_id=user_id,
            pet_id=pet_id,
            product_key=owned_assets.product_key_for_action(action_id),
            video_url=video_url,
            object_path=object_path,
            bucket=bucket,
            scene_id=scene_id,
            credits_spent=credits if reservation_ledger_id else 0,
            ledger_id=reservation_ledger_id,
            source=(
                owned_assets.SOURCE_PURCHASE
                if reservation_ledger_id
                else owned_assets.SOURCE_FREE
            ),
            source_job_id=source_job_id,
        )
    )


async def release_quietly(reservation_ledger_id: Optional[str]) -> bool:
    """
    검증 FAIL / 제출 실패 → 예약 해제.

    실패해도 **예외를 올리지 않는다.** 이 함수는 이미 실패한 경로에서 불리고,
    여기서 다시 던지면 원래 실패 원인이 가려진다. 대신 크게 남긴다 — 해제되지
    못한 예약은 고객 크레딧이 잡힌 채로 남는 유일한 경로다.
    """
    if not reservation_ledger_id:
        return True
    try:
        await credit_reservation.release(reservation_ledger_id)
        return True
    except credit_reservation.ReservationError as e:
        if e.code == "RESERVATION_NOT_OPEN":
            # 이미 확정됐거나 해제됐다 — 정상이다(재전송).
            return True
        logger.critical(
            "예약 해제 실패 — 고객 크레딧이 잡힌 채로 남는다 (ledger=%s): %s",
            reservation_ledger_id, e.message,
        )
        return False


# ── 공용 진입점: 아이들도 액션도 이 함수를 부른다 (Phase 8) ──────────────────


@dataclass(frozen=True)
class GenerationPurchase:
    action_id: str
    product_key: str
    #: **이번 호출이 실제로 잡은 크레딧.** 재시도(같은 키)면 0.
    credits_charged: int
    credits_remaining: int
    reservation_ledger_id: Optional[str]
    submitted: bool
    #: 이 펫이 이 상품으로 지금 갖고 있는 자산 수 (이번 생성 **전** 기준).
    owned_versions: int


async def purchase_generation(
    *,
    user_id: str,
    pet_id: str,
    action_id: str,
    idempotency_key: str,
    pet_image_url: str,
    api_base: str,
) -> GenerationPurchase:
    """
    **아이들·액션 공용 구매 진입점.** 예약 → 제출까지.

        Paw Wave → reserve 4 → 생성 → 검증 → commit → Paw Wave #1 영구 소유
        또 Paw Wave → 또 4    → …           → Paw Wave #2 영구 소유

    ── 멱등 키는 호출부가 준다 ──────────────────────────────────────────────
    "다시 만들기"가 성립하려면 키가 (사용자, 상품) 일 수 없다 — 그러면 두 번째
    생성이 재플레이로 보인다. 키는 **한 번의 사용자 조작**을 가리켜야 하고,
    그 값은 버튼을 누른 쪽이 만든다. 재시도·새로고침은 같은 키를 다시 보내므로
    두 번 잡히지 않고, 새 조작은 새 키라 새로 잡힌다.

    ── 이미 갖고 있어도 막지 않는다 ─────────────────────────────────────────
    Paw Wave 를 이미 하나 갖고 있어도 또 살 수 있다. 소유 개수는 표시용이지
    게이트가 아니다 — 그것이 "또 만들면 또 소유한다"는 모델의 핵심이다.

    ⚠️ 이 경로는 premium_purchases 를 쓰지 않는다. 그 표의 부분 unique
       (user, pet, kind) 는 "한 종류당 활성 구매 하나"를 뜻해서 재생성을 막는다.
       멱등성은 credit_ledger.idempotency_key 가, 소유는 owned_generated_assets
       가 이미 더 정확하게 담당한다.

    Raises:
        GenerationCreditError: PRODUCT_NOT_SOLD / INSUFFICIENT_CREDITS /
                               CATALOG_UNAVAILABLE / SUBMIT_FAILED
    """
    from . import owned_assets as _owned
    from . import premium_generation, wallet_service

    action = (action_id or "").strip().upper()
    product_key = _owned.product_key_for_action(action)
    owned_before = await _owned.count_for_product(user_id, pet_id, product_key)

    reservation = await reserve_for_action(
        user_id=user_id,
        action_id=action,
        idempotency_key=idempotency_key,
        ref_type="generation_request",
        ref_id=idempotency_key,
    )

    # 재플레이면 이미 제출돼 있다 — 다시 제출하면 프로바이더에 두 번 낸다.
    if reservation is not None and reservation.replayed:
        wallet = await wallet_service.get_wallet(user_id, create_if_missing=True)
        return GenerationPurchase(
            action_id=action, product_key=product_key, credits_charged=0,
            credits_remaining=(wallet.current_credits if wallet else 0),
            reservation_ledger_id=reservation.reservation_ledger_id,
            submitted=False, owned_versions=owned_before,
        )

    try:
        await premium_generation.submit_premium_action(
            user_id=user_id, pet_id=pet_id, action_id=action,
            pet_image_url=pet_image_url, api_base=api_base,
            reservation_ledger_id=(
                reservation.reservation_ledger_id if reservation else None
            ),
            credits_reserved=(reservation.credits if reservation else 0),
        )
    except premium_generation.PremiumSubmitError as e:
        # 제출이 실패했으면 잡아 둔 크레딧을 **즉시 돌려준다.** 세션이 만들어지지
        # 않았을 수도 있어 종료 경로가 이 예약을 발견하지 못한다.
        if reservation:
            await release_quietly(reservation.reservation_ledger_id)
        raise GenerationCreditError(
            "SUBMIT_FAILED",
            "생성을 시작하지 못했습니다. 크레딧은 차감되지 않았습니다.",
            status=502,
        ) from e

    wallet = await wallet_service.get_wallet(user_id, create_if_missing=True)
    return GenerationPurchase(
        action_id=action,
        product_key=product_key,
        credits_charged=(reservation.credits if reservation else 0),
        credits_remaining=(wallet.current_credits if wallet else 0),
        reservation_ledger_id=(
            reservation.reservation_ledger_id if reservation else None
        ),
        submitted=True,
        owned_versions=owned_before,
    )
