"""
Beam Credit 팩 — **KRW 가 크레딧으로 들어오는 유일한 문** (Phase 5).

    packs → checkout → Toss 결제창 → confirm → credit_ledger + wallet

── Toss 의 역할이 바뀌었다 ──────────────────────────────────────────────────
Phase 4 까지 Toss 는 테마를 직접 팔았다(₩4,900 → Aurora). 이제 Toss 는 **크레딧
팩만** 판다. 테마는 크레딧으로 산다.

실제 돈이 오가는 지점이 하나로 줄어드는 것이 요점이다: 결제 검증·환불·세금·정산이
한 경로에만 있으면 된다. 디지털 상품이 늘어도 결제 코드는 늘지 않는다.

── 가격은 프론트에 없다 ─────────────────────────────────────────────────────
팩 구성과 가격은 credit_packs 표가 정한다. 화면은 목록을 받아 그대로 그린다.
digital_products 와 같은 원칙이다.

── 금액을 서버가 보관한다 ───────────────────────────────────────────────────
일회성 결제는 successUrl 로 돌아오고 그 파라미터는 **주소창에 있다.** amount 를
그대로 믿고 confirm 하면 URL 을 고쳐 1원짜리 승인으로 30 크레딧을 받을 수 있다.
그래서 체크아웃에서 (주문 → 사용자 → 팩 → 금액 → 크레딧)을 적어 두고, 확인은
**저장된 값**으로 한다. theme_purchase 가 이미 쓰는 규약과 같다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from . import toss_billing

logger = logging.getLogger(__name__)

CURRENCY = "KRW"


class CreditPackError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class CreditPack:
    pack_key: str
    credits: int
    price_krw: int
    display_name: Optional[str] = None
    sort_order: int = 0

    @property
    def price_per_credit(self) -> float:
        """화면이 "개당 ₩980" 같은 표시를 만들 때 쓴다. 서버가 계산해 내려 준다."""
        return self.price_krw / self.credits if self.credits else 0.0


@dataclass(frozen=True)
class CreditCheckout:
    order_id: str
    pack_key: str
    amount: int
    credits: int
    order_name: str
    currency: str
    #: Toss 결제창용 **공개** 키. 시크릿은 백엔드를 떠나지 않는다.
    client_key: str


@dataclass(frozen=True)
class CreditConfirmResult:
    order_id: str
    pack_key: str
    #: **이번 호출이 실제로 지급한 크레딧.** 재확인이면 0.
    credits_added: int
    credits_remaining: int
    amount: int
    replayed: bool


# ── 저장소 ───────────────────────────────────────────────────────────────────


def _packs_table() -> str:
    return os.getenv("CREDIT_PACKS_TABLE", "credit_packs")


def _orders_table() -> str:
    return os.getenv("CREDIT_PACK_ORDERS_TABLE", "credit_pack_orders")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


#: 인메모리 카탈로그 (HYBRID_USE_SUPABASE=0 전용).
#: 마이그레이션 시드와 **같은 값**이어야 한다 — test_credit_packs.py 가 강제한다.
_SEED: tuple[tuple[str, int, int, str, int], ...] = (
    ("pack_5", 5, 4900, "5 Beam Credits", 10),
    ("pack_12", 12, 9900, "12 Beam Credits", 20),
    ("pack_30", 30, 19900, "30 Beam Credits", 30),
)

_MOCK_PACKS: dict[str, CreditPack] = {}
_MOCK_ORDERS: dict[str, dict[str, Any]] = {}


def _mock_packs() -> dict[str, CreditPack]:
    if not _MOCK_PACKS:
        for key, credits, price, name, order in _SEED:
            _MOCK_PACKS[key] = CreditPack(key, credits, price, name, order)
    return _MOCK_PACKS


def __reset_for_tests() -> None:
    _MOCK_PACKS.clear()
    _MOCK_ORDERS.clear()


def _row_to_pack(row: dict[str, Any]) -> CreditPack:
    return CreditPack(
        pack_key=str(row.get("pack_key") or ""),
        credits=int(row.get("credits") or 0),
        price_krw=int(row.get("price_krw") or 0),
        display_name=(row.get("display_name") or None),
        sort_order=int(row.get("sort_order") or 0),
    )


# ── 카탈로그 ─────────────────────────────────────────────────────────────────


async def list_packs() -> list[CreditPack]:
    """
    판매 중인 팩. **프론트가 가격을 만들지 않게** 서버가 목록을 내려 준다.

    조회 실패는 빈 목록이 아니라 오류다 — 빈 목록은 "팩이 없다"로 보이고,
    사용자는 크레딧을 살 방법이 없다고 생각한다.
    """
    if not _use_db():
        return sorted(_mock_packs().values(), key=lambda p: (p.sort_order, p.pack_key))

    try:
        sb = _supabase()
    except Exception as e:
        raise CreditPackError("CREDIT_PACKS_UNAVAILABLE", "팩 목록을 불러오지 못했습니다.", status=503) from e
    if not sb:
        raise CreditPackError("CREDIT_PACKS_UNAVAILABLE", "팩 목록을 불러오지 못했습니다.", status=503)

    try:
        r = (
            sb.table(_packs_table())
            .select("pack_key, credits, price_krw, display_name, sort_order")
            .eq("active", True)
            .execute()
        )
    except Exception as e:
        logger.exception("크레딧 팩 목록 조회 실패")
        raise CreditPackError("CREDIT_PACKS_UNAVAILABLE", "팩 목록을 불러오지 못했습니다.", status=503) from e

    packs = [_row_to_pack(row) for row in (getattr(r, "data", None) or [])]
    return sorted(packs, key=lambda p: (p.sort_order, p.pack_key))


async def get_pack(pack_key: str) -> Optional[CreditPack]:
    key = (pack_key or "").strip()
    if not key:
        return None
    for p in await list_packs():
        if p.pack_key == key:
            return p
    return None


# ── 체크아웃 ─────────────────────────────────────────────────────────────────


async def start_checkout(*, user_id: str, pack_key: str) -> CreditCheckout:
    """
    결제창에 필요한 값 발급. **아직 아무 돈도 움직이지 않는다.**

    금액과 크레딧을 주문에 적어 둔다 — 확인 단계에서 리다이렉트 쿼리의 값을
    믿지 않기 위해서다. 팩 가격이 나중에 바뀌어도 이미 만든 주문은 그대로다.
    """
    uid = (user_id or "").strip()
    if not uid:
        raise CreditPackError("CREDIT_CHECKOUT_INVALID", "user_id 가 필요합니다.")

    pack = await get_pack(pack_key)
    if not pack:
        raise CreditPackError("CREDIT_PACK_UNKNOWN", "알 수 없는 크레딧 팩입니다.", status=404)

    order_id = toss_billing.new_order_id("credits")
    row = {
        "order_id": order_id,
        "user_id": uid,
        "pack_key": pack.pack_key,
        "amount": pack.price_krw,
        "credits": pack.credits,
        "currency": CURRENCY,
        "status": "pending",
        "provider": "toss",
    }

    if _use_db():
        try:
            _supabase().table(_orders_table()).insert(row).execute()
        except Exception as e:
            logger.exception("크레딧 주문 생성 실패 (user=%s pack=%s)", uid, pack.pack_key)
            raise CreditPackError(
                "CREDIT_ORDER_UNAVAILABLE", "주문을 만들지 못했습니다.", status=503
            ) from e
    else:
        _MOCK_ORDERS[order_id] = dict(row)

    return CreditCheckout(
        order_id=order_id,
        pack_key=pack.pack_key,
        amount=pack.price_krw,
        credits=pack.credits,
        order_name=f"Eternal Beam · {pack.display_name or pack.pack_key}",
        currency=CURRENCY,
        client_key=toss_billing.client_key(),
    )


# ── 확인 ─────────────────────────────────────────────────────────────────────

#: RPC 가 올리는 예외 → 사용자 오류.
_RPC_ERRORS: dict[str, tuple[str, str, int]] = {
    "order_not_found": ("CREDIT_ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.", 404),
    "order_not_pending": ("CREDIT_ORDER_NOT_PENDING", "이미 종료된 주문입니다.", 409),
    "order_required": ("CREDIT_CHECKOUT_INVALID", "order_id 가 필요합니다.", 400),
}


async def confirm(
    *, user_id: str, order_id: str, payment_key: str, amount: int | None = None
) -> CreditConfirmResult:
    """
    결제창 승인 → **서버 검증** → 지갑 충전.

    순서가 계약이다:

        1) 주문 조회 — 소유자·상태·금액을 **서버 기록**으로 확인
        2) Toss 승인 (네트워크) — 저장된 금액으로 묻는다
        3) 주문 paid + 지갑 충전 + 원장을 **한 트랜잭션**으로

    3번을 나누면 "주문은 성공인데 크레딧이 없다"나 "같은 주문으로 무한 충전"이
    생긴다. amount 인자는 리다이렉트가 들고 온 값이며 **대조에만** 쓴다.
    """
    uid = (user_id or "").strip()
    oid = (order_id or "").strip()
    if not uid or not oid:
        raise CreditPackError("CREDIT_CHECKOUT_INVALID", "order_id 가 필요합니다.")

    order = await _get_order(oid)
    if not order or order.get("user_id") != uid:
        # 없는 주문과 남의 주문을 구분해 주지 않는다 — 탐색 힌트를 주지 않는다.
        raise CreditPackError("CREDIT_ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.", status=404)

    if order.get("status") == "paid":
        # 재확인은 멱등 성공이다. RPC 를 부르면 같은 답을 주지만, Toss 에 다시
        # 묻지 않는 편이 빠르고 안전하다.
        return await _confirm_via_store(uid, oid, str(order.get("payment_key") or ""))

    if order.get("status") != "pending":
        raise CreditPackError("CREDIT_ORDER_NOT_PENDING", "이미 종료된 주문입니다.", status=409)

    stored_amount = int(order.get("amount") or 0)
    if amount is not None and int(amount) != stored_amount:
        # 리다이렉트가 다른 금액을 들고 왔다 = 위조 시도이거나 우리 버그다.
        # Toss 도 막지만 우리가 먼저 거른다 — 방어를 결제사에 위임하지 않는다.
        logger.warning(
            "크레딧 주문 금액 불일치 — order=%s 저장=%s 요청=%s", oid, stored_amount, amount
        )
        raise CreditPackError(
            "CREDIT_AMOUNT_MISMATCH", "주문 금액이 일치하지 않습니다.", status=400
        )

    try:
        result = await toss_billing.confirm_payment(
            payment_key=payment_key, order_id=oid, amount=stored_amount
        )
    except toss_billing.TossError as e:
        raise CreditPackError("CREDIT_PAYMENT_FAILED", e.message, status=502) from e

    if not result.ok:
        await _mark_failed(oid, result.failure_code)
        logger.warning(
            "크레딧 결제 실패 — user=%s order=%s code=%s", uid, oid, result.failure_code
        )
        raise CreditPackError(
            "CREDIT_PAYMENT_FAILED",
            result.failure_message or "결제가 완료되지 않았습니다.",
            status=402,
        )

    out = await _confirm_via_store(uid, oid, result.payment_key or payment_key)
    if out.credits_added:
        logger.warning(
            "크레딧 충전 — user=%s order=%s pack=%s credits=%s 잔액=%s",
            uid, oid, out.pack_key, out.credits_added, out.credits_remaining,
        )
    return out


async def _get_order(order_id: str) -> Optional[dict[str, Any]]:
    if not _use_db():
        return _MOCK_ORDERS.get(order_id)
    try:
        r = (
            _supabase()
            .table(_orders_table())
            .select("*")
            .eq("order_id", order_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.exception("크레딧 주문 조회 실패 (order=%s)", order_id)
        raise CreditPackError(
            "CREDIT_ORDER_UNAVAILABLE", "주문을 확인하지 못했습니다.", status=503
        ) from e
    rows = getattr(r, "data", None) or []
    return rows[0] if rows else None


async def _mark_failed(order_id: str, failure_code: str | None) -> None:
    patch = {"status": "failed", "failure_code": failure_code}
    if not _use_db():
        if order_id in _MOCK_ORDERS:
            _MOCK_ORDERS[order_id].update(patch)
        return
    try:
        _supabase().table(_orders_table()).update(patch).eq("order_id", order_id).eq(
            "status", "pending"
        ).execute()
    except Exception:
        # 실패 기록에 실패해도 결제는 이미 실패했다. 주문은 pending 으로 남아
        # 재시도할 수 있다 — 잘못된 상태로 굳히는 것보다 낫다.
        logger.exception("크레딧 주문 실패 기록 실패 (order=%s)", order_id)


async def _confirm_via_store(uid: str, oid: str, payment_key: str) -> CreditConfirmResult:
    """주문 확정 + 충전 + 원장. DB 모드는 RPC 하나, 목업은 같은 순서를 흉내 낸다."""
    if _use_db():
        try:
            r = _supabase().rpc(
                "confirm_credit_pack_order",
                {"p_order_id": oid, "p_user_id": uid, "p_payment_key": payment_key},
            ).execute()
        except Exception as e:
            msg = f"{e}".lower()
            for needle, (code, message, status) in _RPC_ERRORS.items():
                if needle in msg:
                    raise CreditPackError(code, message, status=status) from e
            logger.exception("크레딧 충전 확정 실패 (order=%s)", oid)
            raise CreditPackError(
                "CREDIT_CONFIRM_UNAVAILABLE",
                "결제는 승인됐지만 크레딧 지급을 확정하지 못했습니다. "
                "잠시 후 다시 확인해 주세요.",
                status=503,
            ) from e

        data = r.data
        if isinstance(data, list) and data:
            data = data[0]
        if not isinstance(data, dict):
            raise CreditPackError(
                "CREDIT_CONFIRM_UNAVAILABLE", "충전 결과를 확인하지 못했습니다.", status=503
            )
        return CreditConfirmResult(
            order_id=str(data.get("order_id") or oid),
            pack_key=str(data.get("pack_key") or ""),
            credits_added=int(data.get("credits_added") or 0),
            credits_remaining=int(data.get("credits_remaining") or 0),
            amount=int(data.get("amount") or 0),
            replayed=bool(data.get("replayed")),
        )

    # ── 인메모리 (로컬/테스트) ────────────────────────────────────────────────
    from . import credit_ledger, wallet_service

    order = _MOCK_ORDERS.get(oid)
    if not order:
        raise CreditPackError("CREDIT_ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.", status=404)

    already = order.get("status") == "paid"
    if not already:
        order["status"] = "paid"
        order["payment_key"] = payment_key

    # 멱등 키가 주문 id 라, 재확인이면 지갑이 움직이지 않는다 — DB 와 같은 판정이다.
    wallet = await wallet_service.add_credits(
        uid,
        int(order["credits"]),
        reason=credit_ledger.REASON_CREDIT_PACK_TOPUP,
        idempotency_key=f"pack:{oid}",
        product_key=str(order["pack_key"]),
        unit_price=int(order["amount"]),
        ref_type="credit_pack_orders",
        ref_id=oid,
    )
    return CreditConfirmResult(
        order_id=oid,
        pack_key=str(order["pack_key"]),
        credits_added=0 if already else int(order["credits"]),
        credits_remaining=wallet.current_credits,
        amount=int(order["amount"]),
        replayed=already,
    )
