"""
테마 구매 — 결제 1건 → 소유권 1건. **크레딧도 구독도 건드리지 않는다.**

기존 Toss 인프라를 그대로 쓴다: `toss_billing.charge()` 는 이미 목업 모드,
Idempotency-Key, 실패를 예외가 아닌 결과로 다루는 규약을 갖고 있다. 새 결제
클라이언트를 만들면 그 규약을 다시 구현하게 되고, 반드시 한 군데가 어긋난다.

── 경로가 둘이다. 저장된 카드는 **선택**이다 ────────────────────────────────

    1) 일회성 결제 (기본)   구독한 적 없는 사용자도 쓸 수 있다
       checkout → 결제창 승인 → confirm(서버 검증) → 소유권

    2) 저장된 카드 (단축)   이미 카드가 있으면 결제창 없이 즉시 청구
       purchase() → charge() → 소유권

예전에는 2번이 **유일한** 경로였고, 그래서 테마를 사려면 먼저 멤버십 체크아웃으로
카드를 등록해야 했다. 그건 "테마 = 일회성 구매"라는 성격과 맞지 않고, 사실상
구독 흐름을 테마 구매의 전제로 만든다. 이제 1번이 기본이고 2번은 단축키다.

    구독 자격  ≠  결제 수단  ≠  테마 소유권

세 축 모두 독립이다. 이 모듈은 premium_entitlement / subscription_store_service 를
import 하지 않으며, 어느 경로로 사든 만들어지는 것은 user_theme_entitlements
한 줄뿐이다 — 구독도 크레딧도 생기거나 바뀌지 않는다.

── 생성하지 않는다 ──────────────────────────────────────────────────────────
테마를 사도 BREATHING 도 프리미엄 행동도 다시 만들어지지 않는다. 이 모듈에
생성 모듈로 가는 import 가 없다 — 경로가 없으면 실수로도 갈 수 없다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from . import theme_catalog, theme_entitlement, theme_order, toss_billing

logger = logging.getLogger(__name__)

#: 결제 수단을 보관하는 provider. billing_store 의 키와 같다.
PROVIDER = "toss"


class ThemePurchaseError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class PurchaseOutcome:
    theme_key: str
    #: "owned" — 이번에 샀거나 이미 갖고 있다.
    status: str
    #: **이번 호출이 실제로 청구한 금액.** 멱등 호출이면 0.
    charged: int
    #: 이전부터 갖고 있었는가.
    already_owned: bool
    order_id: Optional[str] = None
    currency: str = theme_catalog.CURRENCY


async def saved_payment_method(user_id: str) -> Optional[tuple[str, str]]:
    """
    (billing_key, customer_key) — 저장된 카드가 **있으면**. 없으면 None.

    없는 것은 오류가 아니다. 일회성 결제 경로가 기본이고 이건 단축키일 뿐이다.

    ⚠️ 여기서 읽는 것은 **카드**이지 구독 상태가 아니다. 같은 테이블에 있다는 것이
    두 개념을 섞어도 된다는 뜻은 아니다 — status 필드는 쳐다보지 않는다.
    조회에 실패해도 None 으로 떨어뜨린다: 단축키가 안 되면 결제창으로 가면 된다.
    """
    try:
        from . import billing_store

        sub = await billing_store.get_subscription(user_id, PROVIDER)
    except Exception:  # noqa: BLE001 — 단축키 실패가 구매를 막지 않는다
        logger.warning("저장된 결제 수단 조회 실패 — 결제창 경로로 진행한다 (user=%s)", user_id)
        return None

    billing_key = (getattr(sub, "billing_key", None) or "") if sub else ""
    customer_key = (getattr(sub, "customer_key", None) or "") if sub else ""
    if not billing_key or not customer_key:
        return None
    return billing_key, customer_key


async def _guard_purchasable(user_id: str, theme_key: str) -> tuple[str, int]:
    """
    (canonical theme_key, 가격) — 살 수 있는 상태인지 확인한다.

    두 경로(결제창 / 저장된 카드)가 **같은 규칙**을 통과하도록 한 곳에 모은다.
    나눠 두면 한쪽만 고쳐져 "결제창으로는 무료 테마도 살 수 있는" 상태가 된다.
    """
    try:
        tk = theme_catalog.normalize_theme_key(theme_key)
    except theme_catalog.ThemeCatalogError as e:
        raise ThemePurchaseError(e.code, e.message, status=e.status) from e

    if theme_catalog.is_free(tk):
        raise ThemePurchaseError(
            "THEME_IS_FREE", "무료 테마는 구매할 필요가 없습니다.", status=409
        )

    try:
        if await theme_entitlement.is_owned(user_id, tk):
            raise ThemePurchaseError(
                "THEME_ALREADY_OWNED", "이미 보유한 테마입니다.", status=409
            )
    except theme_entitlement.ThemeEntitlementError as e:
        raise ThemePurchaseError(e.code, e.message, status=e.status) from e

    price = theme_catalog.price_krw(tk)
    if price is None:
        raise ThemePurchaseError(
            "THEME_PRICE_NOT_SET", "이 테마의 가격이 아직 정해지지 않았습니다.", status=409
        )
    return tk, price


@dataclass
class ThemeCheckout:
    """결제창을 띄우는 데 필요한 값. **아직 아무 돈도 움직이지 않았다.**"""

    order_id: str
    theme_key: str
    amount: int
    order_name: str
    currency: str
    #: Toss 결제창용 **공개** 키. 시크릿이 아니다.
    client_key: str


async def start_checkout(*, user_id: str, theme_key: str) -> ThemeCheckout:
    """
    일회성 결제 체크아웃. **카드 등록도 구독도 요구하지 않는다.**

    금액을 서버가 확정해 주문에 적어 둔다 — 확인 단계에서 리다이렉트 쿼리의
    금액을 믿지 않기 위해서다.
    """
    uid = (user_id or "").strip()
    if not uid:
        raise ThemePurchaseError("THEME_PURCHASE_INVALID", "user_id 가 필요합니다.")

    tk, price = await _guard_purchasable(uid, theme_key)

    try:
        # 같은 테마의 미결 주문이 있으면 재사용한다 — 체크아웃을 두 번 눌렀다고
        # 주문이 쌓이면 어느 결제창이 유효한지 모호해진다.
        existing = await theme_order.find_reusable(user_id=uid, theme_key=tk)
        if existing and existing.amount == price:
            order = existing
        else:
            order = await theme_order.create(
                order_id=toss_billing.new_order_id("theme"),
                user_id=uid, theme_key=tk, amount=price,
                currency=theme_catalog.CURRENCY,
            )
    except theme_order.ThemeOrderError as e:
        raise ThemePurchaseError(e.code, e.message, status=e.status) from e

    return ThemeCheckout(
        order_id=order.order_id,
        theme_key=tk,
        amount=order.amount,
        order_name=f"Eternal Beam 테마 · {tk}",
        currency=theme_catalog.CURRENCY,
        client_key=toss_billing.client_key(),
    )


async def confirm_checkout(
    *, user_id: str, payment_key: str, order_id: str, amount: int | None = None
) -> PurchaseOutcome:
    """
    결제창 승인 → **서버 검증** → 소유권.

    amount 인자는 리다이렉트가 들고 온 값이며 **대조에만** 쓴다. Toss 에 물을 때는
    저장된 주문 금액을 쓴다 — URL 을 고쳐도 금액이 바뀌지 않는다.

    멱등: 같은 주문으로 다시 들어오면(새로고침·뒤로가기) 재승인하지 않는다.
    """
    uid = (user_id or "").strip()
    oid = (order_id or "").strip()
    if not uid or not oid:
        raise ThemePurchaseError("THEME_PURCHASE_INVALID", "order_id 가 필요합니다.")

    try:
        order = await theme_order.get(oid)
    except theme_order.ThemeOrderError as e:
        raise ThemePurchaseError(e.code, e.message, status=e.status) from e

    if not order:
        raise ThemePurchaseError("THEME_ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.", status=404)

    # 남의 주문을 확인할 수 없다. 주문 id 는 리다이렉트 URL 에 노출되므로
    # 소유자 검사가 없으면 남의 결제로 내 소유권을 만들 수 있다.
    if order.user_id != uid:
        raise ThemePurchaseError("THEME_ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.", status=404)

    # 이미 처리된 주문 — 재승인하지 않는다.
    if order.paid:
        return PurchaseOutcome(
            theme_key=order.theme_key, status="owned", charged=0,
            already_owned=True, order_id=order.order_id,
        )
    if not order.pending:
        raise ThemePurchaseError(
            "THEME_ORDER_NOT_PENDING", "이미 종료된 주문입니다.", status=409
        )

    # 리다이렉트가 들고 온 금액이 주문과 다르면 **거절한다** (위조 시도이거나
    # 잘못된 주문을 확인하려는 것이다). Toss 도 막지만 우리가 먼저 거른다.
    #
    # ⚠️ 주문을 failed 로 **죽이지 않는다.** 죽여도 보안상 얻는 것이 없고 —
    #    틀린 금액으로는 어차피 승인되지 않는다 — 정당한 재시도만 막힌다.
    #    (실측: 위조 시도 뒤 올바른 confirm 이 THEME_ORDER_NOT_PENDING 으로 막혔다.)
    #    주문은 pending 으로 남아 올바른 확인이 여전히 성공한다.
    if amount is not None and int(amount) != order.amount:
        logger.warning(
            "테마 결제 금액 불일치 — user=%s order=%s 기대=%s 수신=%s",
            uid, oid, order.amount, amount,
        )
        raise ThemePurchaseError(
            "THEME_AMOUNT_MISMATCH", "주문 금액이 일치하지 않습니다.", status=400
        )

    result = await toss_billing.confirm_payment(
        payment_key=payment_key, order_id=order.order_id, amount=order.amount
    )
    if not result.ok:
        await theme_order.mark_failed(order_id=oid, failure_code=result.failure_code)
        logger.warning(
            "테마 결제 확인 실패 — user=%s theme=%s order=%s code=%s",
            uid, order.theme_key, oid, result.failure_code,
        )
        raise ThemePurchaseError(
            "THEME_PAYMENT_FAILED",
            result.failure_message or "결제가 완료되지 않았습니다.",
            status=402,
        )

    await theme_order.mark_paid(
        order_id=oid, payment_key=result.payment_key, amount=result.amount
    )
    await _grant(
        user_id=uid, theme_key=order.theme_key, order_id=order.order_id,
        payment_key=result.payment_key, amount=result.amount,
    )
    logger.warning(
        "테마 구매(결제창) — user=%s theme=%s order=%s amount=%s",
        uid, order.theme_key, oid, result.amount,
    )
    return PurchaseOutcome(
        theme_key=order.theme_key, status="owned", charged=result.amount,
        already_owned=False, order_id=order.order_id,
    )


async def _grant(
    *, user_id: str, theme_key: str, order_id: str, payment_key: str | None, amount: int
) -> None:
    """
    검증된 결제 → 소유권. **두 경로가 같은 부여 함수를 쓴다.**

    나눠 두면 한쪽만 TTL 규칙이 바뀌거나 provider 가 빠지는 식으로 어긋난다.
    """
    try:
        await theme_entitlement.grant(
            user_id=user_id,
            theme_key=theme_key,
            order_id=order_id,
            provider=PROVIDER,
            payment_key=payment_key,
            amount=amount,
            currency=theme_catalog.CURRENCY,
            ttl_days=theme_catalog.entitlement_ttl_days(),
        )
    except theme_entitlement.ThemeEntitlementError as e:
        # 돈은 나갔는데 소유권 저장이 실패했다. **조용히 성공으로 답하지 않는다** —
        # order_id 가 로그에 남아 있어야 수동 복구가 가능하다.
        logger.error(
            "테마 결제는 성공했으나 소유권 저장 실패 — user=%s theme=%s order=%s payment=%s",
            user_id, theme_key, order_id, payment_key,
        )
        raise ThemePurchaseError(e.code, e.message, status=e.status) from e


async def purchase(*, user_id: str, theme_key: str) -> PurchaseOutcome:
    """
    저장된 카드로 즉시 구매 — **단축 경로다. 필수가 아니다.**

    카드가 없으면 PAYMENT_METHOD_UNAVAILABLE 로 답한다. 이것은 "살 수 없다"가
    아니라 **"결제창 경로로 가라"**는 신호다 — 호출부는 start_checkout 으로
    넘어간다. 예전처럼 여기서 막으면 구독한 적 없는 사용자가 테마를 살 수 없다.
    """
    uid = (user_id or "").strip()
    if not uid:
        raise ThemePurchaseError("THEME_PURCHASE_INVALID", "user_id 가 필요합니다.")

    # 이미 보유는 오류가 아니라 멱등 성공이다 — 두 번 눌러도 청구되지 않는다.
    try:
        tk = theme_catalog.normalize_theme_key(theme_key)
    except theme_catalog.ThemeCatalogError as e:
        raise ThemePurchaseError(e.code, e.message, status=e.status) from e
    try:
        if await theme_entitlement.is_owned(uid, tk):
            return PurchaseOutcome(
                theme_key=tk, status="owned", charged=0, already_owned=True
            )
    except theme_entitlement.ThemeEntitlementError as e:
        raise ThemePurchaseError(e.code, e.message, status=e.status) from e

    tk, price = await _guard_purchasable(uid, theme_key)

    saved = await saved_payment_method(uid)
    if not saved:
        raise ThemePurchaseError(
            "PAYMENT_METHOD_UNAVAILABLE",
            "저장된 결제 수단이 없습니다. 결제창으로 진행하세요.",
            status=409,
        )
    billing_key, customer_key = saved

    order_id = toss_billing.new_order_id("theme")
    try:
        result = await toss_billing.charge(
            billing_key=billing_key,
            customer_key=customer_key,
            amount=price,
            order_id=order_id,
            order_name=f"Eternal Beam 테마 · {tk}",
        )
    except toss_billing.TossError as e:
        raise ThemePurchaseError("THEME_PAYMENT_FAILED", e.message, status=502) from e

    if not result.ok:
        # 실패는 예외가 아니라 결과다(toss_billing 규약). 소유권은 만들지 않는다.
        logger.warning(
            "테마 결제 실패 — user=%s theme=%s order=%s code=%s",
            uid, tk, order_id, result.failure_code,
        )
        raise ThemePurchaseError(
            "THEME_PAYMENT_FAILED",
            result.failure_message or "결제가 완료되지 않았습니다.",
            status=402,
        )

    await _grant(
        user_id=uid, theme_key=tk, order_id=result.order_id,
        payment_key=result.payment_key, amount=result.amount,
    )
    logger.warning(
        "테마 구매(저장된 카드) — user=%s theme=%s order=%s amount=%s",
        uid, tk, result.order_id, result.amount,
    )
    return PurchaseOutcome(
        theme_key=tk, status="owned", charged=result.amount,
        already_owned=False, order_id=result.order_id,
    )
