"""
/api/v1/themes — 유료 테마 스토어 (Phase 11).

    GET  /catalog   무엇이 무료/보유/구매가능인가
    POST /purchase  구매 (결제 1건 → 소유권 1건)

── 이 라우터가 **하지 않는** 것 ─────────────────────────────────────────────
구독을 읽지 않는다. 크레딧을 건드리지 않는다. 생성을 일으키지 않는다.
테마를 바꾼다고 BREATHING 이나 프리미엄 행동이 다시 만들어지지 않는다 —
그럴 수 있는 import 자체가 없다.

기존 테마 **선택** 경로(theme-selection-store / place_id / 기기 동기화)는 한 줄도
바뀌지 않는다. 이 라우터는 그 위에 "살 수 있는가 / 샀는가"만 얹는다.

── 레거시 PayPal 경로와의 관계 ──────────────────────────────────────────────
services/theme_prices.py 와 purchased_slots(PayPal, USD)는 **그대로 남는다.**
이번 단계는 그것을 대체하지 않고 KRW/Toss 경로를 새로 추가한다 — 기존 구매자의
소유권을 옮기는 것은 데이터 마이그레이션이고 별도 결정이다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import AuthedUser, require_user
from ..services import theme_catalog, theme_entitlement, theme_purchase

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
    price_krw: int | None = None
    currency: str = theme_catalog.CURRENCY
    #: 지금 [Buy] 를 눌러도 되는가. 무료이거나 이미 보유면 false.
    purchasable: bool = False


class CatalogResponse(BaseModel):
    themes: list[ThemeOfferOut] = []
    #: 소유 테마가 구독과 무관함을 프론트가 오해하지 않도록 명시한다.
    #: ⚠️ 구독 상태는 이 응답에 **없다** — 다른 축이다.
    currency: str = theme_catalog.CURRENCY


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

    out: list[ThemeOfferOut] = []
    for off in theme_catalog.catalog():
        is_owned = off.free or off.theme_key in owned
        out.append(
            ThemeOfferOut(
                theme_key=off.theme_key,
                free=off.free,
                owned=is_owned,
                price_krw=off.price_krw,
                purchasable=off.purchasable and not is_owned,
            )
        )
    return CatalogResponse(themes=out)


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


@router.post("/purchase", response_model=PurchaseResponse)
async def post_purchase(
    body: PurchaseRequest,
    user: AuthedUser = Depends(require_user),
):
    """
    저장된 카드로 즉시 구매 — **단축 경로. 카드가 없으면 실패가 아니라 안내다.**

    카드가 없으면 409 PAYMENT_METHOD_UNAVAILABLE 이고, 프론트는 그때
    /checkout 으로 넘어간다. 구독한 적 없는 사용자도 테마를 살 수 있어야 하므로
    이 경로가 전제가 되어서는 안 된다.

    이미 갖고 있으면 charged=0 으로 돌아온다.
    """
    try:
        result = await theme_purchase.purchase(user_id=user.user_id, theme_key=body.theme_key)
    except theme_purchase.ThemePurchaseError as e:
        raise _http(e) from e

    return PurchaseResponse(
        theme_key=result.theme_key,
        status=result.status,
        charged=result.charged,
        already_owned=result.already_owned,
        order_id=result.order_id,
    )


class CheckoutRequest(BaseModel):
    theme_key: str


class CheckoutResponse(BaseModel):
    order_id: str
    theme_key: str
    amount: int
    order_name: str
    currency: str = theme_catalog.CURRENCY
    #: Toss 결제창용 **공개** 키. 시크릿은 백엔드를 떠나지 않는다.
    client_key: str


@router.post("/checkout", response_model=CheckoutResponse)
async def post_checkout(
    body: CheckoutRequest,
    user: AuthedUser = Depends(require_user),
):
    """
    일회성 결제 체크아웃. **아직 아무 돈도 움직이지 않는다.**

    구독도 카드 등록도 요구하지 않는다 — 인증된 사용자면 누구나 부를 수 있다.
    금액은 서버가 확정해 주문에 적어 두고, 확인 단계에서 그 값을 쓴다.
    """
    try:
        s = await theme_purchase.start_checkout(
            user_id=user.user_id, theme_key=body.theme_key
        )
    except theme_purchase.ThemePurchaseError as e:
        raise _http(e) from e

    return CheckoutResponse(
        order_id=s.order_id, theme_key=s.theme_key, amount=s.amount,
        order_name=s.order_name, currency=s.currency, client_key=s.client_key,
    )


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
