"""
물리 주문 저장소 — 결제 / 생산 / 배송 **세 상태를 따로** 들고 있다.

셋을 한 컬럼에 섞지 않는 이유: 서로 다른 속도로 움직이고 담당도 다르다.
섞으면 "결제됐지만 아직 인쇄 전"과 "인쇄됐지만 미발송"을 구분할 수 없고,
운영이 무엇을 해야 하는지 알 수 없다.

    payment_status     pending | paid | failed        Toss
    production_status  pending | ready | printed      Phase 13 (운영)
    shipping_status    pending | shipped | delivered  운영

이 모듈은 구독·테마·크레딧·생성 모듈을 **import 하지 않는다.** 실물 주문은
네 번째 축이고, 성공해도 만들어지는 것은 이 테이블의 한 행뿐이다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

PAYMENT_PENDING = "pending"
PAYMENT_PAID = "paid"
PAYMENT_FAILED = "failed"

PRODUCTION_PENDING = "pending"
SHIPPING_PENDING = "pending"


class OrderError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _table() -> str:
    return os.getenv("PHYSICAL_ORDERS_TABLE", "physical_orders")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


_MOCK_ORDERS: dict[str, dict[str, Any]] = {}


def __reset_for_tests() -> None:
    _MOCK_ORDERS.clear()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class PhysicalOrder:
    order_id: str
    user_id: str
    pet_id: str
    soul_trace_letter_id: Optional[str]
    product_type: str
    amount: int
    currency: str
    payment_status: str
    production_status: str
    shipping_status: str
    recipient_name: Optional[str] = None
    recipient_phone: Optional[str] = None
    postal_code: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    tracking_number: Optional[str] = None
    shaker_share_id: Optional[str] = None
    payment_key: Optional[str] = None
    failure_code: Optional[str] = None
    created_at: Optional[str] = None
    paid_at: Optional[str] = None

    @property
    def pending(self) -> bool:
        return self.payment_status == PAYMENT_PENDING

    @property
    def paid(self) -> bool:
        return self.payment_status == PAYMENT_PAID


_SELECT = (
    "order_id, user_id, pet_id, soul_trace_letter_id, product_type, amount, currency, "
    "payment_status, production_status, shipping_status, recipient_name, recipient_phone, "
    "postal_code, address_line1, address_line2, tracking_number, shaker_share_id, "
    "payment_key, failure_code, created_at, paid_at"
)


def _to_order(row: dict[str, Any]) -> PhysicalOrder:
    return PhysicalOrder(
        order_id=str(row.get("order_id") or ""),
        user_id=str(row.get("user_id") or ""),
        pet_id=str(row.get("pet_id") or ""),
        soul_trace_letter_id=(row.get("soul_trace_letter_id") or None),
        product_type=str(row.get("product_type") or ""),
        amount=int(row.get("amount") or 0),
        currency=str(row.get("currency") or "KRW"),
        payment_status=str(row.get("payment_status") or PAYMENT_PENDING),
        production_status=str(row.get("production_status") or PRODUCTION_PENDING),
        shipping_status=str(row.get("shipping_status") or SHIPPING_PENDING),
        recipient_name=(row.get("recipient_name") or None),
        recipient_phone=(row.get("recipient_phone") or None),
        postal_code=(row.get("postal_code") or None),
        address_line1=(row.get("address_line1") or None),
        address_line2=(row.get("address_line2") or None),
        tracking_number=(row.get("tracking_number") or None),
        shaker_share_id=(row.get("shaker_share_id") or None),
        payment_key=(row.get("payment_key") or None),
        failure_code=(row.get("failure_code") or None),
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
        paid_at=(str(row["paid_at"]) if row.get("paid_at") else None),
    )


async def create(
    *,
    order_id: str,
    user_id: str,
    pet_id: str,
    product_type: str,
    amount: int,
    soul_trace_letter_id: str | None,
    recipient_name: str,
    recipient_phone: str,
    postal_code: str,
    address_line1: str,
    address_line2: str | None = None,
    shaker_share_id: str | None = None,
    currency: str = "KRW",
) -> PhysicalOrder:
    """주문 생성. **아직 결제되지 않았다** — payment_status=pending."""
    row: dict[str, Any] = {
        "order_id": order_id,
        "user_id": user_id,
        "pet_id": pet_id,
        "soul_trace_letter_id": soul_trace_letter_id,
        "product_type": product_type,
        "amount": int(amount),
        "currency": currency,
        "payment_status": PAYMENT_PENDING,
        "provider": "toss",
        "production_status": PRODUCTION_PENDING,
        "shipping_status": SHIPPING_PENDING,
        "recipient_name": recipient_name,
        "recipient_phone": recipient_phone,
        "postal_code": postal_code,
        "address_line1": address_line1,
        "address_line2": address_line2,
        "shaker_share_id": shaker_share_id,
        "created_at": _now().isoformat(),
    }

    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).insert(row).execute()
        except Exception as e:
            logger.exception("물리 주문 생성 실패 (user=%s pet=%s)", user_id, pet_id)
            raise OrderError(
                "ORDER_STORE_UNAVAILABLE", "주문을 생성하지 못했습니다.", status=503
            ) from e
        return _to_order(row)

    _MOCK_ORDERS[order_id] = row
    return _to_order(row)


async def get(order_id: str) -> Optional[PhysicalOrder]:
    oid = (order_id or "").strip()
    if not oid:
        return None

    if _use_db() and _supabase():
        try:
            r = _supabase().table(_table()).select(_SELECT).eq("order_id", oid).limit(1).execute()
            data = getattr(r, "data", None) or []
            return _to_order(data[0]) if data else None
        except Exception as e:
            logger.exception("물리 주문 조회 실패 (order=%s)", oid)
            raise OrderError(
                "ORDER_STORE_UNAVAILABLE", "주문을 확인하지 못했습니다.", status=503
            ) from e

    row = _MOCK_ORDERS.get(oid)
    return _to_order(row) if row else None


async def mark_paid(*, order_id: str, payment_key: str | None, amount: int) -> None:
    """
    pending → paid. **결제 상태만 바꾼다.**

    생산·배송은 건드리지 않는다 — 결제됐다고 인쇄가 시작되는 것이 아니다.
    그 전이는 Phase 13 운영의 몫이다.
    """
    patch = {
        "payment_status": PAYMENT_PAID,
        "payment_key": payment_key,
        "amount": int(amount),
        "paid_at": _now().isoformat(),
    }
    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).update(patch).eq("order_id", order_id).eq(
                "payment_status", PAYMENT_PENDING
            ).execute()
        except Exception as e:
            logger.exception("물리 주문 결제 확정 실패 (order=%s)", order_id)
            raise OrderError(
                "ORDER_STORE_UNAVAILABLE", "주문 상태를 갱신하지 못했습니다.", status=503
            ) from e
        return

    row = _MOCK_ORDERS.get(order_id)
    if row and row.get("payment_status") == PAYMENT_PENDING:
        row.update(patch)


async def mark_failed(*, order_id: str, failure_code: str | None) -> None:
    patch = {"payment_status": PAYMENT_FAILED, "failure_code": failure_code}
    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).update(patch).eq("order_id", order_id).eq(
                "payment_status", PAYMENT_PENDING
            ).execute()
        except Exception:
            logger.warning("물리 주문 실패 기록 실패 (order=%s)", order_id)
        return

    row = _MOCK_ORDERS.get(order_id)
    if row and row.get("payment_status") == PAYMENT_PENDING:
        row.update(patch)


async def list_for_user(user_id: str) -> list[PhysicalOrder]:
    uid = (user_id or "").strip()
    if not uid:
        return []

    if _use_db() and _supabase():
        try:
            r = _supabase().table(_table()).select(_SELECT).eq("user_id", uid).execute()
            rows = getattr(r, "data", None) or []
        except Exception as e:
            logger.exception("물리 주문 목록 조회 실패 (user=%s)", uid)
            raise OrderError(
                "ORDER_STORE_UNAVAILABLE", "주문 목록을 불러오지 못했습니다.", status=503
            ) from e
    else:
        rows = [r for r in _MOCK_ORDERS.values() if r.get("user_id") == uid]

    out = [_to_order(r) for r in rows]
    out.sort(key=lambda o: o.created_at or "", reverse=True)
    return out


async def search(
    *, query: str | None = None, paid_only: bool = True, limit: int = 50
) -> list[PhysicalOrder]:
    """
    운영 검색 — 고객 / 펫 / 주문번호 부분 일치.

    기본이 paid_only 인 이유: 운영이 처리해야 하는 것은 **결제된** 주문이다.
    미결제 주문까지 섞이면 목록이 결제창을 열었다 닫은 흔적으로 가득 찬다.
    """
    q = (query or "").strip().lower()

    if _use_db() and _supabase():
        try:
            sel = _supabase().table(_table()).select(_SELECT)
            if paid_only:
                sel = sel.eq("payment_status", PAYMENT_PAID)
            r = sel.limit(2000).execute()
            rows = getattr(r, "data", None) or []
        except Exception as e:
            logger.exception("물리 주문 검색 실패")
            raise OrderError(
                "ORDER_STORE_UNAVAILABLE", "주문을 조회하지 못했습니다.", status=503
            ) from e
    else:
        rows = [
            r for r in _MOCK_ORDERS.values()
            if (not paid_only) or r.get("payment_status") == PAYMENT_PAID
        ]

    out: list[PhysicalOrder] = []
    for row in rows:
        o = _to_order(row)
        if q and not (
            q in o.order_id.lower()
            or q in o.user_id.lower()
            or q in o.pet_id.lower()
            or q in (o.recipient_name or "").lower()
        ):
            continue
        out.append(o)
    out.sort(key=lambda o: o.created_at or "", reverse=True)
    return out[: max(1, min(limit, 500))]


async def list_pending(*, user_id: str | None = None, limit: int = 200) -> list[PhysicalOrder]:
    """
    아직 결제 확인되지 않은 주문들 — **재조정 대상**.

    user_id 를 주면 그 사용자 것만(앱이 돌아왔을 때의 안전망), 주지 않으면 전체
    (배치 스윕). 두 경로가 같은 조회를 쓰므로 한쪽만 조건이 어긋날 일이 없다.
    """
    uid = (user_id or "").strip()

    if _use_db() and _supabase():
        try:
            sel = _supabase().table(_table()).select(_SELECT).eq(
                "payment_status", PAYMENT_PENDING
            )
            if uid:
                sel = sel.eq("user_id", uid)
            r = sel.limit(max(1, min(limit, 500))).execute()
            rows = getattr(r, "data", None) or []
        except Exception as e:
            logger.exception("미결 주문 조회 실패")
            raise OrderError(
                "ORDER_STORE_UNAVAILABLE", "주문을 조회하지 못했습니다.", status=503
            ) from e
    else:
        rows = [
            r for r in _MOCK_ORDERS.values()
            if r.get("payment_status") == PAYMENT_PENDING
            and (not uid or r.get("user_id") == uid)
        ]

    out = [_to_order(r) for r in rows]
    out.sort(key=lambda o: o.created_at or "")
    return out[: max(1, min(limit, 500))]


# ── 생산 · 배송 상태 (Phase 13) ───────────────────────────────────────────────

PRODUCTION_READY = "ready"
PRODUCTION_IN_PRODUCTION = "in_production"
PRODUCTION_PRODUCED = "produced"

SHIPPING_SHIPPED = "shipped"
SHIPPING_DELIVERED = "delivered"

#: 허용된 전이만 일어난다. 임의 상태 쓰기를 열어 두면 운영 실수 한 번으로
#: "배송 완료 → 생산 대기" 같은 불가능한 이력이 남는다.
PRODUCTION_FLOW: dict[str, tuple[str, ...]] = {
    PRODUCTION_PENDING: (PRODUCTION_READY,),
    PRODUCTION_READY: (PRODUCTION_IN_PRODUCTION,),
    PRODUCTION_IN_PRODUCTION: (PRODUCTION_PRODUCED,),
    PRODUCTION_PRODUCED: (),
}

SHIPPING_FLOW: dict[str, tuple[str, ...]] = {
    SHIPPING_PENDING: (SHIPPING_SHIPPED,),
    SHIPPING_SHIPPED: (SHIPPING_DELIVERED,),
    SHIPPING_DELIVERED: (),
}


def _patch(order_id: str, patch: dict[str, Any], *, where: dict[str, str]) -> None:
    if _use_db() and _supabase():
        q = _supabase().table(_table()).update(patch).eq("order_id", order_id)
        for k, v in where.items():
            q = q.eq(k, v)
        q.execute()
        return
    row = _MOCK_ORDERS.get(order_id)
    if row and all(row.get(k) == v for k, v in where.items()):
        row.update(patch)


async def advance_production(*, order_id: str, to: str) -> PhysicalOrder:
    """
    생산 상태 전이. **허용된 다음 단계로만** 간다.

    현재 상태를 조건에 함께 걸어(compare-and-set) 두 요청이 동시에 같은 전이를
    수행해도 한 번만 반영되게 한다.
    """
    order = await get(order_id)
    if not order:
        raise OrderError("ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.", status=404)
    if not order.paid:
        raise OrderError("ORDER_NOT_PAID", "결제된 주문만 생산할 수 있습니다.", status=409)

    target = (to or "").strip().lower()
    allowed = PRODUCTION_FLOW.get(order.production_status, ())
    if target == order.production_status:
        return order  # 멱등 — 같은 상태로의 전이는 성공으로 본다
    if target not in allowed:
        raise OrderError(
            "PRODUCTION_TRANSITION_INVALID",
            f"{order.production_status} → {target} 전이는 허용되지 않습니다.",
            status=409,
        )

    _patch(order_id, {"production_status": target},
           where={"production_status": order.production_status})
    return await get(order_id) or order


async def advance_shipping(
    *, order_id: str, to: str, tracking_number: str | None = None
) -> PhysicalOrder:
    """
    배송 상태 전이. 발송은 **송장 없이 할 수 없다** — 송장 없는 '배송 중'은
    고객에게 아무것도 알려 주지 못하고 문의만 만든다.
    """
    order = await get(order_id)
    if not order:
        raise OrderError("ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.", status=404)

    target = (to or "").strip().lower()
    if target == order.shipping_status and not tracking_number:
        return order
    allowed = SHIPPING_FLOW.get(order.shipping_status, ())
    if target != order.shipping_status and target not in allowed:
        raise OrderError(
            "SHIPPING_TRANSITION_INVALID",
            f"{order.shipping_status} → {target} 전이는 허용되지 않습니다.",
            status=409,
        )

    tn = (tracking_number or "").strip() or order.tracking_number
    if target == SHIPPING_SHIPPED and not tn:
        raise OrderError(
            "TRACKING_REQUIRED", "송장 번호 없이 발송 처리할 수 없습니다.", status=409
        )
    # 생산이 끝나기 전에 발송될 수 없다 — 만들지 않은 것을 보낼 수는 없다.
    if target == SHIPPING_SHIPPED and order.production_status != PRODUCTION_PRODUCED:
        raise OrderError(
            "NOT_PRODUCED", "생산이 완료되지 않은 주문은 발송할 수 없습니다.", status=409
        )

    patch: dict[str, Any] = {"shipping_status": target}
    if tn:
        patch["tracking_number"] = tn
    _patch(order_id, patch, where={"shipping_status": order.shipping_status})
    return await get(order_id) or order


async def set_tracking(*, order_id: str, tracking_number: str) -> PhysicalOrder:
    """송장만 기록한다 (발송 처리와 분리 — 먼저 등록하고 나중에 발송할 수 있다)."""
    tn = (tracking_number or "").strip()
    if not tn:
        raise OrderError("TRACKING_REQUIRED", "송장 번호가 필요합니다.")
    order = await get(order_id)
    if not order:
        raise OrderError("ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.", status=404)
    _patch(order_id, {"tracking_number": tn}, where={})
    return await get(order_id) or order
