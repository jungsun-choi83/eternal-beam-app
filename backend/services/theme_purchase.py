"""
테마 구매 — **Beam Credit 으로 산다** (Phase 11 이후).

    Aurora → 5 Beam Credits → user_theme_entitlements 한 줄 → 영구 소유

── KRW 로 **새 구매를 시작하는 경로는 없다** (Phase 11) ─────────────────────
지워진 것:

    purchase()              저장된 카드로 즉시 청구
    start_checkout()        결제창용 주문 발급
    saved_payment_method()  카드 조회 (위 둘만 쓰던 헬퍼)
    _guard_purchasable()    위 둘의 공통 사전 검사

지운 이유는 "안 쓰니까"가 아니다. **주문을 만들 수 있는 코드가 남아 있으면
언젠가 다시 호출된다.** 라우터에서만 떼어 내면 "임시로 하나만 열자"가 한 줄로
가능하고, 그러면 KRW·크레딧 두 가격이 동시에 살아 있는 상태로 돌아간다.

── confirm_checkout 은 **남는다** ───────────────────────────────────────────
배포 시점에 Toss 결제창을 띄워 둔 고객이 있을 수 있다. 그 사람이 승인을 누르면
돈은 나간다 — 받아 줄 곳이 없으면 결제만 되고 테마는 못 받는다. 새 주문이
만들어지지 않으므로 미결 주문은 시간이 지나면 0 이 되고, 그때 이 함수도
theme_order 표 동결과 함께 은퇴한다
(supabase/migrations/20261009000000_freeze_legacy_purchase_tables.sql).

    구독 자격  ≠  결제 수단  ≠  테마 소유권

세 축 모두 독립이다. 이 모듈은 premium_entitlement / subscription_store_service 를
import 하지 않으며, 어느 경로로 사든 만들어지는 것은 user_theme_entitlements
한 줄뿐이다 — 구독은 생기거나 바뀌지 않는다.

── 생성하지 않는다 ──────────────────────────────────────────────────────────
테마를 사도 BREATHING 도 프리미엄 행동도 다시 만들어지지 않는다. 이 모듈에
생성 모듈로 가는 import 가 없다 — 경로가 없으면 실수로도 갈 수 없다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from . import credit_ledger, theme_catalog, theme_entitlement, theme_order, toss_billing

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
    #: 크레딧 구매에서만 채워진다 — 화면이 "잔액 7" 을 바로 그릴 수 있게.
    #: KRW 경로에서는 None 이다 (지갑을 건드리지 않으므로 보고할 잔액이 없다).
    credits_remaining: Optional[int] = None


async def confirm_checkout(
    *, user_id: str, payment_key: str, order_id: str, amount: int | None = None
) -> PurchaseOutcome:
    """
    결제창 승인 → **서버 검증** → 소유권. (레거시 KRW · 드레인 전용 — 위 참고)

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
    """검증된 KRW 결제 → 소유권 (드레인 경로 전용)."""
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


# ── 크레딧 구매 (Phase 4) — **테마를 사는 유일한 경로** ──────────────────────
#
#     Aurora → 5 Beam Credits → entitlement → 영구 소유
#
# 위 confirm_checkout 은 이미 시작된 KRW 결제를 받아 주는 드레인 경로일 뿐,
# 새 구매를 시작할 수 없다. 두 경로가 만드는 결과물은 **완전히 같다** —
# user_theme_entitlements 한 줄.


async def purchase_with_credits(*, user_id: str, theme_key: str) -> PurchaseOutcome:
    """
    Beam Credit 으로 테마를 산다. **차감·원장·소유권이 한 트랜잭션이다.**

        차감만 성공  → 고객은 크레딧을 잃고 테마는 못 쓴다
        소유권만 성공 → 공짜로 준 것이고 원장이 그것을 설명하지 못한다

    둘 다 불가능해야 한다. DB 모드에서는 plpgsql 함수 하나가 셋을 함께 하고
    (purchase_theme_with_credits), 부분 실패는 함수 전체 롤백으로 사라진다.

    멱등 키는 (사용자, 테마) 다. 더블탭·재시도·다중 탭이 두 번 청구하지 못한다.

    Raises:
        ThemePurchaseError:
            THEME_UNKNOWN / THEME_PRODUCT_NOT_SOLD / THEME_IS_FREE
            INSUFFICIENT_CREDITS (402) / THEME_PURCHASE_UNAVAILABLE (503)
    """
    uid = (user_id or "").strip()
    if not uid:
        raise ThemePurchaseError("THEME_PURCHASE_INVALID", "user_id 가 필요합니다.")

    try:
        tk = theme_catalog.normalize_theme_key(theme_key)
    except theme_catalog.ThemeCatalogError as e:
        raise ThemePurchaseError(e.code, e.message, status=e.status) from e

    key = credit_ledger.theme_purchase_key(uid, tk)

    if _use_db_for_credits():
        return await _purchase_with_credits_db(uid, tk, key)
    return await _purchase_with_credits_memory(uid, tk, key)


def _use_db_for_credits() -> bool:
    import os

    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _credit_supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


#: RPC 가 올리는 예외 문자열 → 사용자에게 보여 줄 오류.
#: 문자열로 판정하는 이유: PostgREST 는 plpgsql 의 RAISE 를 메시지로 실어 보낸다.
_RPC_ERRORS: dict[str, tuple[str, str, int]] = {
    "insufficient_credits": (
        "INSUFFICIENT_CREDITS",
        "크레딧이 부족합니다. 크레딧을 충전한 뒤 다시 시도해 주세요.",
        402,
    ),
    "product_not_sold": (
        "THEME_PRODUCT_NOT_SOLD",
        "현재 크레딧으로 판매하지 않는 테마입니다.",
        409,
    ),
    "theme_is_free": ("THEME_IS_FREE", "무료 테마는 구매할 필요가 없습니다.", 409),
}


async def _purchase_with_credits_db(uid: str, tk: str, key: str) -> PurchaseOutcome:
    try:
        sb = _credit_supabase()
    except Exception as e:
        raise ThemePurchaseError(
            "THEME_PURCHASE_UNAVAILABLE", "구매를 처리하지 못했습니다.", status=503
        ) from e
    if not sb:
        raise ThemePurchaseError(
            "THEME_PURCHASE_UNAVAILABLE", "구매를 처리하지 못했습니다.", status=503
        )

    try:
        r = sb.rpc(
            "purchase_theme_with_credits",
            {"p_user_id": uid, "p_theme_key": tk, "p_idempotency_key": key},
        ).execute()
    except Exception as e:
        msg = f"{e}".lower()
        for needle, (code, message, status) in _RPC_ERRORS.items():
            if needle in msg:
                raise ThemePurchaseError(code, message, status=status) from e
        # 모르는 실패는 **성공으로 처리하지 않는다.** 함수 전체가 롤백됐으므로
        # 차감도 소유권도 일어나지 않았다.
        logger.exception("테마 크레딧 구매 실패 — user=%s theme=%s", uid, tk)
        raise ThemePurchaseError(
            "THEME_PURCHASE_UNAVAILABLE",
            "구매를 처리하지 못했습니다. 크레딧은 차감되지 않았습니다.",
            status=503,
        ) from e

    data = r.data
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        # 결과를 못 읽으면 무엇이 일어났는지 모른다. 성공이라고 말하지 않는다.
        raise ThemePurchaseError(
            "THEME_PURCHASE_UNAVAILABLE", "구매 결과를 확인하지 못했습니다.", status=503
        )

    charged = int(data.get("charged") or 0)
    if charged:
        logger.warning(
            "테마 크레딧 구매 — user=%s theme=%s credits=%s 잔액=%s",
            uid, tk, charged, data.get("credits_remaining"),
        )
    return PurchaseOutcome(
        theme_key=str(data.get("theme_key") or tk),
        status="owned",
        charged=charged,
        already_owned=bool(data.get("already_owned")),
        order_id=str(data.get("order_id") or key),
    )


async def _purchase_with_credits_memory(uid: str, tk: str, key: str) -> PurchaseOutcome:
    """
    인메모리 경로 (HYBRID_USE_SUPABASE=0) — 로컬/테스트 전용.

    ⚠️ 여기에는 트랜잭션이 없다. 단일 프로세스라 중간에 끼어드는 것은 없지만,
    **차감 뒤 소유권 부여가 실패하면 되돌린다**(보상). 실제 원자성은 DB 경로가
    갖고, 이 경로는 같은 관찰 가능한 계약을 흉내 낼 뿐이다.
    """
    from . import product_catalog, wallet_service

    try:
        if await theme_entitlement.is_owned(uid, tk):
            wallet = await wallet_service.get_wallet(uid, create_if_missing=True)
            return PurchaseOutcome(
                theme_key=tk, status="owned", charged=0, already_owned=True,
                # DB 경로와 **같은 값**을 돌려준다. 여기서 None 을 주면 새로고침한
                # 화면만 주문을 잃고, 두 경로의 응답이 조용히 달라진다.
                order_id=key,
                credits_remaining=(wallet.current_credits if wallet else 0),
            )
    except theme_entitlement.ThemeEntitlementError as e:
        raise ThemePurchaseError(e.code, e.message, status=e.status) from e

    product_key = product_catalog.theme_key(tk)
    try:
        price = await product_catalog.credit_price(product_key)
    except product_catalog.CatalogUnavailableError as e:
        raise ThemePurchaseError("THEME_PURCHASE_UNAVAILABLE", e.message, status=503) from e
    if price is None:
        raise ThemePurchaseError(
            "THEME_PRODUCT_NOT_SOLD", "현재 크레딧으로 판매하지 않는 테마입니다.", status=409
        )
    if price <= 0:
        raise ThemePurchaseError("THEME_IS_FREE", "무료 테마는 구매할 필요가 없습니다.", status=409)

    try:
        wallet = await wallet_service.deduct_credits(
            uid, price, strict=True,
            reason=credit_ledger.REASON_THEME_PURCHASE,
            idempotency_key=key,
            product_key=product_key,
            unit_price=price,
            ref_type="user_theme_entitlements",
            ref_id=tk,
        )
    except wallet_service.InsufficientCreditsError as e:
        raise ThemePurchaseError(
            "INSUFFICIENT_CREDITS",
            "크레딧이 부족합니다. 크레딧을 충전한 뒤 다시 시도해 주세요.",
            status=402,
        ) from e
    except wallet_service.WalletUnavailableError as e:
        raise ThemePurchaseError("THEME_PURCHASE_UNAVAILABLE", e.message, status=503) from e

    try:
        await theme_entitlement.grant(
            user_id=uid, theme_key=tk, order_id=key,
            provider="credits", amount=price, currency="CREDIT",
            ttl_days=None,  # 영구 소유
        )
    except theme_entitlement.ThemeEntitlementError as e:
        # 보상: 소유권을 만들지 못했으면 차감도 되돌린다. 크레딧만 잃는 상태를
        # 남기지 않는 것이 이 블록의 유일한 목적이다.
        await wallet_service.refund_credits(
            uid, price,
            reason=credit_ledger.REASON_RESERVATION_RELEASE,
            idempotency_key=f"release:{key}",
            product_key=product_key,
            ref_type="user_theme_entitlements",
            ref_id=tk,
        )
        raise ThemePurchaseError(e.code, e.message, status=e.status) from e

    return PurchaseOutcome(
        theme_key=tk, status="owned", charged=price, already_owned=False,
        order_id=key, credits_remaining=wallet.current_credits,
    )
