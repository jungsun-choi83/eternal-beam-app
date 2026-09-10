"""
/api/v1/themes — 유료 테마 스토어 (Phase 11).

    GET  /catalog                무엇이 무료/보유/구매가능인가 (+ 크레딧 잔액)
    POST /purchase-with-credits  구매 (크레딧 차감 1건 → 소유권 1건)
    POST /confirm                레거시 KRW 결제 승인 수신 (드레인 — 아래 참고)

── 이 라우터가 **하지 않는** 것 ─────────────────────────────────────────────
구독을 읽지 않는다. 크레딧을 건드리지 않는다. 생성을 일으키지 않는다.
테마를 바꾼다고 BREATHING 이나 프리미엄 행동이 다시 만들어지지 않는다 —
그럴 수 있는 import 자체가 없다.

기존 테마 **선택** 경로(theme-selection-store / place_id / 기기 동기화)는 한 줄도
바뀌지 않는다. 이 라우터는 그 위에 "살 수 있는가 / 샀는가"만 얹는다.

── KRW 직접 구매는 은퇴했다 (Phase 11) ──────────────────────────────────────
테마는 이제 **Beam Credit** 으로만 산다 (POST /purchase-with-credits).
새 KRW 주문을 만들던 두 경로는 삭제됐다:

    POST /purchase   저장 카드 즉시 청구
    POST /checkout   결제창용 주문 발급

**POST /confirm 은 남는다.** 배포 시점에 결제창에 머물러 있던 고객이 있을 수 있고,
그 사람의 승인을 받아 줄 곳이 없으면 돈만 나가고 테마는 못 받는다. 새 주문이
만들어지지 않으므로 미결 주문은 시간이 지나면 자연히 0 이 된다 —
그때 이 경로도 삭제한다(조건: theme_purchase_orders 에 pending 행 0건).

theme_purchase_orders 표는 **남긴다.** 과거 결제 증거는 새 아키텍처가 생겼다는
이유로 버리지 않는다.

── 결제 제공자 ──────────────────────────────────────────────────────────────
신규 테마 구매는 **크레딧 경로만** 사용한다. 레거시 PayPal 은 Phase 11 에서
코드까지 삭제됐다(표는 읽기 전용으로 남는다 — docs/PAYPAL_LEGACY.md).

purchased_slots 데이터는 **legacy/dev-only 로 분류되어 이관하지 않는다**
(docs/PAYPAL_LEGACY.md). PayPal 은 개발 중에만 쓰였고 — 라우터가 마운트된 적이
없어 실 결제가 코드 배치상 불가능했다 — 그 표에는 실 고객 구매가 없다.
따라서 이 카탈로그는 user_theme_entitlements **하나만** 본다. 레거시 표를 함께
읽기 시작하면 개발용 행이 실 소유권처럼 보이고, 그건 이관과 같은 효과다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import AuthedUser, require_user
from ..services import product_catalog, theme_catalog, theme_entitlement, theme_purchase
from ..services import wallet_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/themes", tags=["theme-store"])


def _http(e: Exception) -> HTTPException:
    return HTTPException(
        status_code=getattr(e, "status", 400),
        detail={"code": getattr(e, "code", "ERROR"), "message": getattr(e, "message", str(e))},
    )


class ThemeOfferOut(BaseModel):
    theme_key: str
    #: 무료 테마는 결제 없이 언제나 쓸 수 있다.
    free: bool
    #: 이 사용자가 지금 쓸 수 있는가 (무료면 항상 true).
    owned: bool
    #: 유료인데 가격 미설정이면 null — 화면은 "준비 중"으로 그린다.
    #: ⚠️ 레거시 KRW 경로 전용. 크레딧 전환 후 화면은 credit_price 를 쓴다.
    price_krw: int | None = None
    currency: str = theme_catalog.CURRENCY
    #: **Beam Credit 가격** (digital_products). null 이면 크레딧으로 팔지 않는다.
    #: 0 은 명시적 무료다 — null 과 다르다.
    credit_price: int | None = None
    #: 지금 [Buy] 를 눌러도 되는가. 무료이거나 이미 보유면 false.
    purchasable: bool = False


class CatalogResponse(BaseModel):
    themes: list[ThemeOfferOut] = []
    #: 소유 테마가 구독과 무관함을 프론트가 오해하지 않도록 명시한다.
    #: ⚠️ 구독 상태는 이 응답에 **없다** — 다른 축이다.
    currency: str = theme_catalog.CURRENCY
    #: 지금 잔액. 화면이 "잔액 12 / 가격 5" 를 한 번의 조회로 그릴 수 있게 함께 싣는다.
    #: 조회하지 못하면 null — 0 으로 떨어뜨리면 "잔액 없음"과 구분되지 않는다.
    credit_balance: int | None = None


@router.get("/catalog", response_model=CatalogResponse)
async def get_catalog(user: AuthedUser = Depends(require_user)):
    """
    카탈로그 + 이 사용자의 보유 상태.

    구독 상태를 조회하지 않는다 — 테마 소유권과 무관하기 때문이다.
    """
    try:
        owned = await theme_entitlement.owned_theme_keys(user.user_id)
    except theme_entitlement.ThemeEntitlementError as e:
        raise _http(e) from e

    # 크레딧 가격은 **카탈로그가 권위다** (Phase 3). 읽지 못하면 가격을 비운 채로
    # 내보낸다 — 무료 테마 사용과 소유 표시는 가격과 무관하게 계속돼야 하고,
    # 실제 과금은 POST /purchase-with-credits 가 fail-closed 로 다시 판정한다.
    credit_prices: dict[str, int] = {}
    try:
        for p in await product_catalog.list_products():
            if p.product_key.startswith(product_catalog.PREFIX_THEME):
                credit_prices[p.product_key] = p.credit_price
    except product_catalog.CatalogUnavailableError:
        logger.warning("테마 카탈로그 — 크레딧 가격 조회 실패 (user=%s)", user.user_id)

    out: list[ThemeOfferOut] = []
    for off in theme_catalog.catalog():
        is_owned = off.free or off.theme_key in owned
        cp = credit_prices.get(product_catalog.theme_key(off.theme_key))
        out.append(
            ThemeOfferOut(
                theme_key=off.theme_key,
                free=off.free,
                owned=is_owned,
                price_krw=off.price_krw,
                credit_price=cp,
                # 크레딧으로 살 수 있으면 그것으로 충분하다 — KRW 가격이 없어도
                # 구매 가능하다. 예전에는 KRW 가격만 이 판정을 했다.
                purchasable=(not is_owned) and (off.purchasable or bool(cp and cp > 0)),
            )
        )

    balance: int | None = None
    try:
        w = await wallet_service.get_wallet(user.user_id, create_if_missing=True)
        balance = w.current_credits if w else None
    except Exception:
        logger.warning("테마 카탈로그 — 잔액 조회 실패 (user=%s)", user.user_id)

    return CatalogResponse(themes=out, credit_balance=balance)


class PurchaseRequest(BaseModel):
    theme_key: str


class PurchaseResponse(BaseModel):
    theme_key: str
    status: str
    #: **이번 호출이 실제로 청구한 금액.** 멱등 호출이면 0.
    charged: int
    already_owned: bool
    order_id: str | None = None
    currency: str = theme_catalog.CURRENCY


class ConfirmRequest(BaseModel):
    payment_key: str
    order_id: str
    #: 리다이렉트가 들고 온 금액. **대조용일 뿐** — 승인 기준은 저장된 주문 금액이다.
    amount: int | None = None


@router.post("/confirm", response_model=PurchaseResponse)
async def post_confirm(
    body: ConfirmRequest,
    user: AuthedUser = Depends(require_user),
):
    """
    결제창 승인 → 서버 검증 → 소유권.

    같은 주문으로 다시 들어오면(새로고침·뒤로가기) 재승인하지 않고 charged=0 이다.
    성공하면 만들어지는 것은 **테마 소유권 한 줄뿐**이다 — 구독도 크레딧도 아니다.
    """
    try:
        result = await theme_purchase.confirm_checkout(
            user_id=user.user_id,
            payment_key=body.payment_key,
            order_id=body.order_id,
            amount=body.amount,
        )
    except theme_purchase.ThemePurchaseError as e:
        raise _http(e) from e

    return PurchaseResponse(
        theme_key=result.theme_key,
        status=result.status,
        charged=result.charged,
        already_owned=result.already_owned,
        order_id=result.order_id,
    )


# ── 크레딧 구매 (Phase 4) ─────────────────────────────────────────────────────


class CreditPurchaseResponse(BaseModel):
    theme_key: str
    status: str
    #: **이번 호출이 실제로 차감한 크레딧.** 멱등 호출·이미 보유면 0.
    charged: int
    already_owned: bool
    #: 차감 후 잔액 — 화면이 "잔액 7" 을 재조회 없이 그린다.
    credits_remaining: int | None = None
    order_id: str | None = None
    currency: str = "CREDIT"


@router.post("/purchase-with-credits", response_model=CreditPurchaseResponse)
async def post_purchase_with_credits(
    body: PurchaseRequest,
    user: AuthedUser = Depends(require_user),
):
    """
    Beam Credit 으로 테마를 산다 — **차감 · 원장 · 소유권이 한 트랜잭션이다.**

        선택 → 잔액 12 / 가격 5 → [5 크레딧으로 잠금 해제]
             → -5 · 원장 · 소유권  (전부 또는 아무것도)
             → 잔액 7 · Aurora OWNED (영구)

    KRW 경로(/checkout → /confirm)와 **나란히** 산다. 둘 다 만드는 결과물은
    user_theme_entitlements 한 줄로 같다 — 새 소유권 테이블은 없다.

    이미 보유하면 charged=0 이고 **기존 소유권을 덮어쓰지 않는다.** 덮어쓰면
    Toss 로 산 기록(provider/amount/currency)이 크레딧 기록으로 조용히 바뀐다.
    """
    try:
        result = await theme_purchase.purchase_with_credits(
            user_id=user.user_id, theme_key=body.theme_key
        )
    except theme_purchase.ThemePurchaseError as e:
        raise _http(e) from e

    return CreditPurchaseResponse(
        theme_key=result.theme_key,
        status=result.status,
        charged=result.charged,
        already_owned=result.already_owned,
        credits_remaining=result.credits_remaining,
        order_id=result.order_id,
    )
