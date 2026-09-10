"""
/api/v1/orders — 물리 제품 주문 (Phase 12).

    GET  /products                  카탈로그 (LETTER / MEMORY_BOX)
    POST /letter/claim              Soul Trace 핸드오프 교환 (**서버가 본문을 가져온다**)
    POST /letter/link-pet           가져온 편지에 canonical petId 연결
    GET  /letters                   내가 연결한 편지들
    POST /checkout                  주문 생성 + 결제창 값
    POST /confirm                   결제 검증 → 주문 PAID
    GET  /                          내 주문 목록
    GET  /ops/search                운영: 결제된 주문 찾기 (allowlist 필요)

── 이 라우터가 하지 않는 것 ────────────────────────────────────────────────
편지를 만들지 않는다. 펫을 만들지 않는다. Shaker 공유를 새로 발급하지 않는다.
구독·테마·크레딧을 건드리지 않는다. 생성 모듈로 가는 import 가 없다.

결제 성공이 하는 일은 **주문 한 행을 PAID 로 바꾸는 것**이 전부이며,
BREATHING 은 이 주문과 무관하게 언제나 무료다.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from ..auth import AuthedUser, require_user
from ..services import (
    acquisition_bonus,
    letter_background,
    order_attention,
    physical_checkout,
    physical_order,
    physical_product,
    production_package,
    shaker_ops,
    soul_trace_import,
    soul_trace_letter,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/orders", tags=["physical-orders"])


def _http(e: Exception) -> HTTPException:
    return HTTPException(
        status_code=getattr(e, "status", 400),
        detail={"code": getattr(e, "code", "ERROR"), "message": getattr(e, "message", str(e))},
    )


# ── 카탈로그 ──────────────────────────────────────────────────────────────────


class ProductOut(BaseModel):
    product_type: str
    price_krw: int
    currency: str
    contents: list[str] = []


class ProductsResponse(BaseModel):
    products: list[ProductOut] = []


@router.get("/products", response_model=ProductsResponse)
async def list_products():
    """
    판매 중인 실물. **NFC 는 없다** — 핸드오프가 나중으로 미뤘고, 카탈로그에
    넣으면 만들 수 없는 것을 팔게 된다.

    인증이 필요 없다: 가격은 공개 정보이고 로그인 전에도 보여 줘야 한다.
    """
    return ProductsResponse(
        products=[
            ProductOut(
                product_type=p.product_type, price_krw=p.price_krw,
                currency=p.currency, contents=list(p.contents),
            )
            for p in physical_product.catalog()
        ]
    )


# ── Soul Trace 편지 연결 ─────────────────────────────────────────────────────


class ClaimLetterRequest(BaseModel):
    """
    핸드오프 교환에 필요한 **두 값뿐**이다.

    ⚠️ letter_body 를 받는 자리가 **없다.** 예전 /letter/link 는 브라우저가 보낸
    본문을 그대로 저장했고, 그 본문은 A5 로 인쇄되어 배송됐다 — 인증된 사용자
    누구나 아무 문장이나 찍어 받을 수 있었다는 뜻이다. 본문은 이제 서버가
    Soul Trace 에서 직접 가져온다(services/soul_trace_import.py).
    """

    trace_id: str
    handoff: str


class LinkPetRequest(BaseModel):
    """이미 가져온 편지에 **이미 존재하는** canonical petId 를 붙인다."""

    letter_id: str
    pet_id: str


class LetterOut(BaseModel):
    letter_id: str
    pet_id: str | None = None
    source_letter_id: str | None = None
    source: str
    child_name: str | None = None
    letter_excerpt: str | None = None


class LettersResponse(BaseModel):
    letters: list[LetterOut] = []


def _letter_out(l: soul_trace_letter.SoulTraceLetter) -> LetterOut:
    # 본문(letter_body)은 목록 응답에 싣지 않는다 — 인쇄용이지 화면용이 아니고,
    # 길다. 필요하면 Soul Trace 쪽 화면이 원본을 갖고 있다.
    return LetterOut(
        letter_id=l.letter_id, pet_id=l.pet_id, source_letter_id=l.source_letter_id,
        source=l.source, child_name=l.child_name, letter_excerpt=l.letter_excerpt,
    )


@router.post("/letter/claim", response_model=LetterOut)
async def claim_letter(
    body: ClaimLetterRequest,
    user: AuthedUser = Depends(require_user),
):
    """
    Soul Trace 핸드오프를 교환해 편지를 이 계정으로 가져온다.

        브라우저: traceId + 불투명 토큰
        우리 서버 → Soul Trace: 토큰 제시 → 정본 본문 수령 (서버 대 서버)
        우리 DB: soul_trace_letters 한 행으로 수렴

    **생성이 아니라 가져오기다.** 본문은 Soul Trace 가 만든 것이고, 이 경로에는
    문장을 만들어 내는 코드가 없다. 같은 편지를 두 번 가져와도 letter_id 가
    결정적이라 한 행으로 수렴한다.

    pet_id 는 여기서 붙이지 않는다 — Soul Trace 만 마친 사용자는 아직 펫이
    없다. 펫이 생긴 뒤 /letter/link-pet 으로 연결한다.
    """
    try:
        source = await soul_trace_import.fetch_source_letter(
            trace_id=body.trace_id,
            handoff=body.handoff,
            consumed_by=user.user_id,
        )
    except soul_trace_import.ImportError_ as e:
        raise _http(e) from e

    # ── 배경(히어로) 사본 (Phase 22 → 24) ───────────────────────────────────
    # **지금** 복사한다. Soul Trace 가 준 주소는 수명이 짧다 — 자기 버킷에서 방금
    # 발급한 서명(약 10분)이거나, 레거시 편지라면 DALL·E 임시 URL 이다. 인쇄는
    # 결제·생산 이후 며칠 뒤일 수 있으므로 그때 다시 받는 설계는 성립하지 않는다.
    # 저장하는 것은 서명 URL 이 아니라 우리 스토리지의 **경로**다.
    #
    # 실패해도 편지 가져오기를 막지 않는다: 배경이 없으면 인쇄가 기존 스크림으로
    # 떨어질 뿐이고(레거시와 같은 경로), 배경 한 장 때문에 편지를 잃는 것이 훨씬 나쁘다.
    background_ref = None
    try:
        background_ref = await letter_background.import_from_source(
            source_url=source.hero_image_url,
            user_id=user.user_id,
            letter_id=soul_trace_letter.derive_letter_id(user.user_id, source.letter_id),
        )
    except Exception:  # noqa: BLE001 — 배경은 편지를 막지 않는다
        logger.warning("편지 배경 복사 실패 — 배경 없이 진행", exc_info=True)

    # ── 획득 보너스 (Phase 9) ────────────────────────────────────────────
    # 편지를 가져온 고객에게 크레딧을 준다 — 이것이 Soul Trace → Eternal Beam
    # 유입의 연결 고리다.
    #
    # 멱등 키는 **Soul Trace 원본 편지 id** 다. 임시 핸드오프 토큰이 아니다:
    # 그 토큰은 편지 하나에 대해 몇 번이든 새로 발급되므로(재시도를 위해 그것이
    # 옳다), 토큰을 키로 삼으면 토큰을 다시 받는 것만으로 보너스를 다시 받는다.
    #
    # 실패해도 편지 가져오기를 막지 않는다 — 보너스는 덤이다.
    await acquisition_bonus.grant_soultrace(
        user_id=user.user_id, source_letter_id=source.letter_id
    )

    try:
        letter = await soul_trace_letter.link_letter(
            user_id=user.user_id,
            # Soul Trace 쪽 letter_id 가 우리 source_letter_id 다. 새 식별자를
            # 만들지 않는다 — 그러면 같은 편지가 두 이름을 갖는다.
            source_letter_id=source.letter_id,
            pet_id=None,
            child_name=source.pet_name or None,
            letter_body=source.letter_body,
            letter_excerpt=soul_trace_import.excerpt_of(source.letter_body),
            # 귀속은 Soul Trace 가 서버에서 확정한 값이다 — 요청 바디에는
            # partner 필드가 없고, 있어도 쓰지 않는다.
            partner_id=source.partner_id,
            partner_type=source.partner_type,
            partner_name=source.partner_name,
            partner_code=source.partner_code,
            partner_track=source.partner_track,
            partner_share_rate=source.partner_share_rate,
            letter_background_ref=background_ref,
        )
    except soul_trace_letter.LetterError as e:
        raise _http(e) from e
    return _letter_out(letter)


@router.post("/letter/link-pet", response_model=LetterOut)
async def link_letter_pet(
    body: LinkPetRequest,
    user: AuthedUser = Depends(require_user),
):
    """
    가져온 편지에 canonical petId 를 붙인다. **펫을 만들지 않는다.**

    편지 소유권과 펫 소유권을 **모두** 확인한다 — 둘 중 하나만 보면 남의 펫에
    내 편지를 붙이거나 그 반대가 가능하다.
    """
    try:
        letter = await soul_trace_letter.link_pet(
            user_id=user.user_id,
            letter_id=body.letter_id,
            pet_id=body.pet_id,
        )
    except soul_trace_letter.LetterError as e:
        raise _http(e) from e
    return _letter_out(letter)


@router.get("/letters", response_model=LettersResponse)
async def list_letters(user: AuthedUser = Depends(require_user)):
    try:
        rows = await soul_trace_letter.list_letters(user.user_id)
    except soul_trace_letter.LetterError as e:
        raise _http(e) from e
    return LettersResponse(letters=[_letter_out(l) for l in rows])


# ── 체크아웃 · 확인 ───────────────────────────────────────────────────────────


class OrderCheckoutRequest(BaseModel):
    #: **이미 존재하는** canonical petId. 주문용 펫을 만들지 않는다.
    pet_id: str
    product_type: str
    soul_trace_letter_id: str | None = None
    recipient_name: str
    recipient_phone: str
    postal_code: str
    address_line1: str
    address_line2: str | None = None


class OrderCheckoutResponse(BaseModel):
    order_id: str
    product_type: str
    amount: int
    order_name: str
    currency: str
    client_key: str
    pet_id: str
    soul_trace_letter_id: str | None = None


@router.post("/checkout", response_model=OrderCheckoutResponse)
async def post_checkout(
    body: OrderCheckoutRequest,
    user: AuthedUser = Depends(require_user),
):
    """
    주문 생성 + 결제창 값. **멤버십도 저장된 카드도 요구하지 않는다.**

    금액은 서버가 확정해 주문에 적어 두고, 확인 단계에서 그 값을 쓴다.
    """
    try:
        s = await physical_checkout.start_checkout(
            user_id=user.user_id,
            pet_id=body.pet_id,
            product_type=body.product_type,
            soul_trace_letter_id=body.soul_trace_letter_id,
            recipient_name=body.recipient_name,
            recipient_phone=body.recipient_phone,
            postal_code=body.postal_code,
            address_line1=body.address_line1,
            address_line2=body.address_line2,
        )
    except physical_checkout.CheckoutError as e:
        raise _http(e) from e

    return OrderCheckoutResponse(
        order_id=s.order_id, product_type=s.product_type, amount=s.amount,
        order_name=s.order_name, currency=s.currency, client_key=s.client_key,
        pet_id=s.pet_id, soul_trace_letter_id=s.soul_trace_letter_id,
    )


class OrderConfirmRequest(BaseModel):
    payment_key: str
    order_id: str
    #: 리다이렉트가 들고 온 금액. **대조용** — 승인 기준은 저장된 주문 금액이다.
    amount: int | None = None


class OrderConfirmResponse(BaseModel):
    order_id: str
    product_type: str
    payment_status: str
    charged: int
    already_paid: bool


@router.post("/confirm", response_model=OrderConfirmResponse)
async def post_confirm(
    body: OrderConfirmRequest,
    user: AuthedUser = Depends(require_user),
):
    """결제 검증 → 주문 PAID. 같은 주문을 다시 확인해도 재승인하지 않는다."""
    try:
        r = await physical_checkout.confirm(
            user_id=user.user_id, payment_key=body.payment_key,
            order_id=body.order_id, amount=body.amount,
        )
    except physical_checkout.CheckoutError as e:
        raise _http(e) from e

    return OrderConfirmResponse(
        order_id=r.order_id, product_type=r.product_type,
        payment_status=r.payment_status, charged=r.charged, already_paid=r.already_paid,
    )


# ── 주문 조회 ─────────────────────────────────────────────────────────────────


class OrderOut(BaseModel):
    order_id: str
    pet_id: str
    soul_trace_letter_id: str | None = None
    product_type: str
    amount: int
    currency: str
    payment_status: str
    production_status: str
    shipping_status: str
    tracking_number: str | None = None
    shaker_share_id: str | None = None
    created_at: str | None = None


class OrdersResponse(BaseModel):
    orders: list[OrderOut] = []


def _order_out(o: physical_order.PhysicalOrder, *, with_recipient: bool = False) -> OrderOut:
    # 고객 자신의 목록에는 배송지를 싣지 않는다 — 이미 아는 값이고, 응답에
    # 개인정보를 담을수록 로그·캐시로 새어 나갈 표면이 넓어진다.
    return OrderOut(
        order_id=o.order_id, pet_id=o.pet_id,
        soul_trace_letter_id=o.soul_trace_letter_id,
        product_type=o.product_type, amount=o.amount, currency=o.currency,
        payment_status=o.payment_status, production_status=o.production_status,
        shipping_status=o.shipping_status, tracking_number=o.tracking_number,
        shaker_share_id=o.shaker_share_id, created_at=o.created_at,
    )


@router.get("", response_model=OrdersResponse)
@router.get("/", response_model=OrdersResponse)
async def list_my_orders(user: AuthedUser = Depends(require_user)):
    """내 주문만. 남의 주문은 조회되지 않는다(user_id 로 좁힌다)."""
    try:
        rows = await physical_order.list_for_user(user.user_id)
    except physical_order.OrderError as e:
        raise _http(e) from e
    return OrdersResponse(orders=[_order_out(o) for o in rows])


# ── 재조정 (브라우저가 돌아오지 못한 결제) ──────────────────────────────────


class ReconcileResponse(BaseModel):
    #: 이번 호출이 PAID 로 확정한 주문들. 없으면 빈 배열(정상이다).
    confirmed_order_ids: list[str] = []


@router.post("/reconcile", response_model=ReconcileResponse)
async def post_reconcile(user: AuthedUser = Depends(require_user)):
    """
    내 미결 주문을 Toss 와 맞춘다. **자기 것만** 본다.

    결제창에서 승인이 끝난 직후 브라우저가 닫히면 successUrl 로 돌아오지 못하고,
    Toss 에는 승인된 결제가 있는데 우리 주문은 pending 으로 남는다 — 돈은 받고
    물건은 만들지 않는 상태다. 앱이 다시 열릴 때 이 호출이 그것을 정리한다.

    **결제를 새로 만들지 않는다.** 이미 일어난 승인을 우리 쪽에 반영할 뿐이다.
    """
    ids = await physical_checkout.reconcile_user(user_id=user.user_id)
    return ReconcileResponse(confirmed_order_ids=ids)


@router.post("/reconcile-due", response_model=ReconcileResponse)
async def post_reconcile_due(x_cron_secret: str = Header(default="")):
    """
    전체 미결 주문 스윕 — 크론용.

    사용자가 **영영 돌아오지 않는** 경우의 마지막 보루다. 앱 재방문에만 기대면
    그 사람은 결제만 하고 아무것도 받지 못한다.

    구독 갱신 배치와 **같은 시크릿**(BILLING_CRON_SECRET)을 쓴다 — 크론 자격을
    두 벌 만들면 하나가 갱신되고 다른 하나가 잊힌다. 미설정이면 503 으로 닫는다.
    """
    secret = (os.getenv("BILLING_CRON_SECRET") or "").strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail={"code": "CRON_NOT_CONFIGURED", "message": "BILLING_CRON_SECRET 이 없습니다."},
        )
    if (x_cron_secret or "").strip() != secret:
        raise HTTPException(
            status_code=403, detail={"code": "CRON_FORBIDDEN", "message": "크론 시크릿이 다릅니다."}
        )

    ids = await physical_checkout.reconcile_due()
    if ids:
        logger.warning("재조정 스윕이 주문 %d건을 확정했다: %s", len(ids), ids)
    return ReconcileResponse(confirmed_order_ids=ids)


# ── 운영 ──────────────────────────────────────────────────────────────────────


class OpsOrderOut(OrderOut):
    """운영은 배송지를 봐야 인쇄·발송할 수 있다."""

    user_id: str
    recipient_name: str | None = None
    recipient_phone: str | None = None
    postal_code: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    #: 주문 시점 파트너 귀속. 전부 None 이면 직접 유입이다.
    partner_id: str | None = None
    partner_type: str | None = None
    partner_name: str | None = None
    partner_code: str | None = None
    partner_track: str | None = None
    #: ⚠️ **주문 시점 스냅샷**이다. 파트너의 현재 비율이 아니다 — 계약이 바뀌어도
    #: 이미 결제된 주문의 정산 근거는 움직이지 않는다.
    partner_share_rate: float | None = None

    # ── 목록에서 바로 보이는 처리 필요 여부 ──────────────────────────────
    # 목록이 주문마다 상세를 부르지 않도록 서버가 판정해 준다. pendingFiles
    # 전체를 복제하지 않는다 — 목록에 필요한 것은 "손댈 일이 있는가"뿐이다.
    needs_attention: bool = False
    attention_code: str | None = None
    attention_reason: str | None = None


class OpsOrdersResponse(BaseModel):
    orders: list[OpsOrderOut] = []


@router.get("/ops/search", response_model=OpsOrdersResponse)
async def ops_search_orders(
    query: str | None = None,
    paid_only: bool = True,
    #: 정확 일치. 'all' 은 필터 없음과 같다(값을 비우면 된다).
    partner_id: str | None = None,
    #: HOSPITAL | FUNERAL. 그 외 값은 아무것도 매칭하지 않는다.
    partner_type: str | None = None,
    _ops: AuthedUser = Depends(shaker_ops.require_ops),
):
    """
    운영: 결제된 주문 찾기 — 고객 / 펫 / 주문번호 / 수령인 부분 일치.

    Phase 10 과 **같은 allowlist**(SHAKER_OPS_USER_IDS)를 쓴다. 운영 권한을 두 벌
    만들지 않는다 — 하나가 갱신되고 다른 하나가 잊히는 것이 가장 흔한 사고다.
    """
    try:
        rows = await physical_order.search(query=query, paid_only=paid_only, partner_id=partner_id, partner_type=partner_type)
    except physical_order.OrderError as e:
        raise _http(e) from e

    # 패키지는 **한 번에** 읽는다. 주문마다 상세를 부르면 목록 한 번에 N 개의
    # 요청이 나가고, 느린 목록은 아무도 보지 않는다.
    packages = await production_package.get_packages([o.order_id for o in rows])

    out: list[OpsOrderOut] = []
    for o in rows:
        att = order_attention.evaluate(o, packages.get(o.order_id))
        out.append(
            OpsOrderOut(
                **_order_out(o).model_dump(),
                user_id=o.user_id,
                recipient_name=o.recipient_name,
                recipient_phone=o.recipient_phone,
                postal_code=o.postal_code,
                address_line1=o.address_line1,
                address_line2=o.address_line2,
                partner_id=o.partner_id,
                partner_type=o.partner_type,
                partner_name=o.partner_name,
                partner_code=o.partner_code,
                partner_track=o.partner_track,
                partner_share_rate=o.partner_share_rate,
                needs_attention=att.needs_attention,
                attention_code=att.reason_code,
                attention_reason=att.reason,
            )
        )
    return OpsOrdersResponse(orders=out)
