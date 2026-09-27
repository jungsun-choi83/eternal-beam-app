"""
/api/v1/credits — Beam Credit 지갑과 팩 (Phase 5).

    GET  /credits/wallet     지금 잔액 + 최근 내역
    GET  /credits/packs      판매 중인 팩 — **프론트는 가격을 하드코딩하지 않는다**
    POST /credits/checkout   Toss 결제창 값 발급 (아직 돈이 움직이지 않는다)
    POST /credits/confirm    승인 검증 → 지갑 충전 + 원장

── 이 라우터가 하는 일과 하지 않는 일 ───────────────────────────────────────
크레딧을 **넣기만** 한다. 쓰는 것은 각 상품의 구매 경로가 한다
(테마 → /v1/themes/purchase-with-credits, 프리미엄 → /v1/pet/premium/purchase).

생성을 일으키지 않는다. 구독을 건드리지 않는다. 소유권을 만들지 않는다.
그럴 수 있는 import 자체가 없다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import AuthedUser, require_user
from ..services import credit_pack_service, wallet_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/credits", tags=["credits"])


def _http(e: Exception) -> HTTPException:
    return HTTPException(
        status_code=getattr(e, "status", 400),
        detail={"code": getattr(e, "code", "ERROR"), "message": getattr(e, "message", str(e))},
    )


# ── 지갑 ─────────────────────────────────────────────────────────────────────


class LedgerEntryOut(BaseModel):
    delta: int
    balance_after: int
    reason: str
    product_key: str | None = None
    created_at: str | None = None


class WalletResponse(BaseModel):
    user_id: str
    balance: int
    #: 최근 움직임. **왜 이 잔액인지**를 사용자가 볼 수 있어야 한다.
    entries: list[LedgerEntryOut] = []


@router.get("/wallet", response_model=WalletResponse)
async def get_wallet(
    limit: int = Query(20, ge=1, le=100),
    user: AuthedUser = Depends(require_user),
):
    """
    잔액 + 최근 내역. **인증된 사용자 자신의 것만** 볼 수 있다.

    레거시 `GET /v1/pet/wallet/{user_id}` 와 다른 점이 이것이다 — 그쪽은 user_id 가
    경로 파라미터라 아무나 남의 잔액을 조회할 수 있었다. 이 경로는 토큰이 신원이다.
    """
    try:
        w = await wallet_service.get_wallet(user.user_id, create_if_missing=True)
    except wallet_service.WalletUnavailableError as e:
        raise HTTPException(
            status_code=503,
            detail={"code": "WALLET_UNAVAILABLE", "message": e.message},
        ) from e

    entries = await _recent_entries(user.user_id, limit)
    return WalletResponse(
        user_id=user.user_id,
        balance=(w.current_credits if w else 0),
        entries=entries,
    )


async def _recent_entries(user_id: str, limit: int) -> list[LedgerEntryOut]:
    """
    최근 원장. 읽지 못해도 **잔액 조회를 실패시키지 않는다** — 내역은 설명이고
    잔액은 사실이다. 설명을 못 붙인다고 사실을 숨길 이유가 없다.
    """
    from ..services import credit_ledger

    if not wallet_service._use_db():
        rows = list(reversed(credit_ledger.mock_entries(user_id)))[:limit]
        return [
            LedgerEntryOut(
                delta=e.delta,
                balance_after=e.balance_after,
                reason=e.reason,
                product_key=e.product_key,
                created_at=e.created_at.isoformat(),
            )
            for e in rows
        ]

    try:
        from ..models.content import _supabase_client

        sb = _supabase_client()
        if not sb:
            return []
        r = (
            sb.table("credit_ledger")
            .select("delta, balance_after, reason, product_key, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception:
        logger.warning("크레딧 내역 조회 실패 — 잔액만 돌려준다 (user=%s)", user_id)
        return []

    return [
        LedgerEntryOut(
            delta=int(row.get("delta") or 0),
            balance_after=int(row.get("balance_after") or 0),
            reason=str(row.get("reason") or ""),
            product_key=(row.get("product_key") or None),
            created_at=(str(row["created_at"]) if row.get("created_at") else None),
        )
        for row in (getattr(r, "data", None) or [])
    ]


# ── 팩 ───────────────────────────────────────────────────────────────────────


class PackOut(BaseModel):
    pack_key: str
    credits: int
    price_krw: int
    display_name: str | None = None
    currency: str = credit_pack_service.CURRENCY


class PacksResponse(BaseModel):
    packs: list[PackOut] = []
    currency: str = credit_pack_service.CURRENCY


@router.get("/packs", response_model=PacksResponse)
async def list_packs(user: AuthedUser = Depends(require_user)):
    """
    판매 중인 팩. **가격의 권위는 서버다** — 프론트는 이 목록을 그대로 그린다.

    화면에 가격이 박혀 있으면 바꾸는 데 배포가 필요하고, 서버와 어긋나면 눌러도
    거절당하는 버튼이 생긴다. themes.ts 의 "$2.99" 가 정확히 그 문제였다.
    """
    try:
        packs = await credit_pack_service.list_packs()
    except credit_pack_service.CreditPackError as e:
        raise _http(e) from e

    return PacksResponse(
        packs=[
            PackOut(
                pack_key=p.pack_key,
                credits=p.credits,
                price_krw=p.price_krw,
                display_name=p.display_name,
            )
            for p in packs
        ]
    )


# ── 체크아웃 / 확인 ──────────────────────────────────────────────────────────


class CheckoutRequest(BaseModel):
    pack_key: str


class CheckoutResponse(BaseModel):
    order_id: str
    pack_key: str
    amount: int
    credits: int
    order_name: str
    currency: str = credit_pack_service.CURRENCY
    #: Toss 결제창용 **공개** 키. 시크릿은 백엔드를 떠나지 않는다.
    client_key: str


@router.post("/checkout", response_model=CheckoutResponse)
async def post_checkout(
    body: CheckoutRequest,
    user: AuthedUser = Depends(require_user),
):
    """
    결제창에 필요한 값 발급. **아직 아무 돈도 움직이지 않는다.**

    금액과 크레딧을 주문에 적어 둔다 — 확인 단계에서 리다이렉트 쿼리의 값을
    믿지 않기 위해서다.
    """
    try:
        s = await credit_pack_service.start_checkout(
            user_id=user.user_id, pack_key=body.pack_key
        )
    except credit_pack_service.CreditPackError as e:
        raise _http(e) from e

    return CheckoutResponse(
        order_id=s.order_id, pack_key=s.pack_key, amount=s.amount,
        credits=s.credits, order_name=s.order_name, currency=s.currency,
        client_key=s.client_key,
    )


class ConfirmRequest(BaseModel):
    payment_key: str
    order_id: str
    #: 리다이렉트가 들고 온 금액. **대조용일 뿐** — 승인 기준은 저장된 주문 금액이다.
    amount: int | None = None


class ConfirmResponse(BaseModel):
    order_id: str
    pack_key: str
    #: **이번 호출이 실제로 지급한 크레딧.** 재확인이면 0.
    credits_added: int
    credits_remaining: int
    amount: int
    already_confirmed: bool = False
    currency: str = credit_pack_service.CURRENCY


@router.post("/confirm", response_model=ConfirmResponse)
async def post_confirm(
    body: ConfirmRequest,
    user: AuthedUser = Depends(require_user),
):
    """
    결제창 승인 → 서버 검증 → **지갑 충전 + 원장을 한 트랜잭션으로.**

    같은 주문으로 다시 들어오면(새로고침·뒤로가기) 재승인하지 않고 0 을 돌려준다.
    """
    try:
        r = await credit_pack_service.confirm(
            user_id=user.user_id,
            order_id=body.order_id,
            payment_key=body.payment_key,
            amount=body.amount,
        )
    except credit_pack_service.CreditPackError as e:
        raise _http(e) from e

    return ConfirmResponse(
        order_id=r.order_id, pack_key=r.pack_key,
        credits_added=r.credits_added, credits_remaining=r.credits_remaining,
        amount=r.amount, already_confirmed=r.replayed,
    )
