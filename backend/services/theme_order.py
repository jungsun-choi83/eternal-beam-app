"""
테마 일회성 결제 주문 저장소 — **금액의 정본.**

이 모듈이 존재하는 이유는 하나다: 결제 확인 시점에 **클라이언트가 보낸 금액을
믿지 않기 위해서**다.

    체크아웃  서버가 (주문, 사용자, 테마, 금액)을 적는다
    결제창    사용자가 승인한다
    리다이렉트 /themes/success?paymentKey=…&orderId=…&amount=…   ← 주소창이다
    확인      **저장된 금액**으로 Toss 에 묻는다

리다이렉트의 amount 를 그대로 쓰면 URL 을 고쳐 1원 결제로 유료 테마를 살 수 있다.
Toss 도 주문 금액이 다르면 거절하지만, 방어를 결제사에 위임하지 않는다.

구독도 크레딧도 건드리지 않는다 — 그런 import 가 없다.

── 레거시: **새 주문을 만드는 프로덕션 경로는 없다** (Phase 11) ─────────────
테마는 이제 Beam Credit 으로만 판다. create() / find_reusable() 을 부르던
theme_purchase.start_checkout 은 삭제됐다.

    남은 것    theme_purchase.confirm_checkout  — 배포 시점에 결제창에 머물러
               있던 고객의 승인을 받아 주는 드레인 경로 (get / mark_paid /
               mark_failed 만 쓴다)

    create()   프로덕션 호출부 없음. 지우지 않는 이유는 드레인 경로를 시험하려면
               "배포 전에 만들어진 미결 주문"을 재현해야 하기 때문이다.
               프로덕션이 다시 부르지 못하도록
               backend/tests/test_theme_legacy_retired.py 가 고정한다.

미결 주문이 0 건이 되면 표를 동결한다 —
supabase/migrations/20261009000000_freeze_legacy_purchase_tables.sql 참고.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_PAID = "paid"
STATUS_FAILED = "failed"


class ThemeOrderError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _table() -> str:
    return os.getenv("THEME_ORDERS_TABLE", "theme_purchase_orders")


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
class ThemeOrder:
    order_id: str
    user_id: str
    theme_key: str
    amount: int
    currency: str
    status: str
    provider: str = "toss"
    payment_key: Optional[str] = None
    failure_code: Optional[str] = None

    @property
    def pending(self) -> bool:
        return self.status == STATUS_PENDING

    @property
    def paid(self) -> bool:
        return self.status == STATUS_PAID


_SELECT = (
    "order_id, user_id, theme_key, amount, currency, status, provider, "
    "payment_key, failure_code"
)


def _to_order(row: dict[str, Any]) -> ThemeOrder:
    return ThemeOrder(
        order_id=str(row.get("order_id") or ""),
        user_id=str(row.get("user_id") or ""),
        theme_key=str(row.get("theme_key") or ""),
        amount=int(row.get("amount") or 0),
        currency=str(row.get("currency") or "KRW"),
        status=str(row.get("status") or STATUS_PENDING),
        provider=str(row.get("provider") or "toss"),
        payment_key=(row.get("payment_key") or None),
        failure_code=(row.get("failure_code") or None),
    )


async def create(
    *, order_id: str, user_id: str, theme_key: str, amount: int, currency: str = "KRW"
) -> ThemeOrder:
    """체크아웃 — 아직 **아무 돈도 움직이지 않는다.** 금액만 확정해 적어 둔다."""
    row: dict[str, Any] = {
        "order_id": order_id,
        "user_id": user_id,
        "theme_key": theme_key,
        "amount": int(amount),
        "currency": currency,
        "status": STATUS_PENDING,
        "provider": "toss",
        "created_at": _now().isoformat(),
    }

    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).insert(row).execute()
        except Exception as e:
            logger.exception("테마 주문 생성 실패 (user=%s theme=%s)", user_id, theme_key)
            raise ThemeOrderError(
                "THEME_ORDER_UNAVAILABLE", "주문을 생성하지 못했습니다.", status=503
            ) from e
        return _to_order(row)

    _MOCK_ORDERS[order_id] = row
    return _to_order(row)


async def get(order_id: str) -> Optional[ThemeOrder]:
    oid = (order_id or "").strip()
    if not oid:
        return None

    if _use_db() and _supabase():
        try:
            r = _supabase().table(_table()).select(_SELECT).eq("order_id", oid).limit(1).execute()
            data = getattr(r, "data", None) or []
            return _to_order(data[0]) if data else None
        except Exception as e:
            logger.exception("테마 주문 조회 실패 (order=%s)", oid)
            raise ThemeOrderError(
                "THEME_ORDER_UNAVAILABLE", "주문을 확인하지 못했습니다.", status=503
            ) from e

    row = _MOCK_ORDERS.get(oid)
    return _to_order(row) if row else None


async def mark_paid(*, order_id: str, payment_key: str | None, amount: int) -> None:
    """
    pending → paid. **한 번만 일어나야 하는 전이다.**

    조건에 status='pending' 을 함께 건다 — 조회 후 갱신하는 방식이면 두 요청이
    동시에 통과할 수 있고, 그러면 소유권이 두 번 부여된다(소유권 쪽 unique
    인덱스가 다시 막지만, 여기서 먼저 좁히는 편이 낫다).
    """
    patch = {
        "status": STATUS_PAID,
        "payment_key": payment_key,
        "amount": int(amount),
        "confirmed_at": _now().isoformat(),
    }
    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).update(patch).eq("order_id", order_id).eq(
                "status", STATUS_PENDING
            ).execute()
        except Exception as e:
            logger.exception("테마 주문 확정 실패 (order=%s)", order_id)
            raise ThemeOrderError(
                "THEME_ORDER_UNAVAILABLE", "주문 상태를 갱신하지 못했습니다.", status=503
            ) from e
        return

    row = _MOCK_ORDERS.get(order_id)
    if row and row.get("status") == STATUS_PENDING:
        row.update(patch)


async def mark_failed(*, order_id: str, failure_code: str | None) -> None:
    """실패도 기록한다 — "결제창까지 갔다가 실패"와 "시도한 적 없음"은 다르다."""
    patch = {"status": STATUS_FAILED, "failure_code": failure_code}
    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).update(patch).eq("order_id", order_id).eq(
                "status", STATUS_PENDING
            ).execute()
        except Exception:
            # 실패 기록에 실패해도 호출부의 결론(결제 실패)은 바뀌지 않는다.
            logger.warning("테마 주문 실패 기록 실패 (order=%s)", order_id)
        return

    row = _MOCK_ORDERS.get(order_id)
    if row and row.get("status") == STATUS_PENDING:
        row.update(patch)


async def find_reusable(*, user_id: str, theme_key: str) -> Optional[ThemeOrder]:
    """
    아직 결제되지 않은 같은 주문이 있으면 재사용한다.

    체크아웃을 두 번 눌렀다고 주문이 쌓이면, 사용자가 예전 탭의 결제창을 승인했을
    때 어느 주문이 유효한지 모호해진다. 하나로 좁힌다.
    """
    uid = (user_id or "").strip()
    tk = (theme_key or "").strip()

    if _use_db() and _supabase():
        try:
            r = (
                _supabase().table(_table()).select(_SELECT)
                .eq("user_id", uid).eq("theme_key", tk).eq("status", STATUS_PENDING)
                .limit(1).execute()
            )
            data = getattr(r, "data", None) or []
            return _to_order(data[0]) if data else None
        except Exception:
            # 재사용은 편의다. 실패하면 새 주문을 만들면 된다.
            logger.warning("재사용 가능한 테마 주문 조회 실패 (user=%s)", uid)
            return None

    for row in _MOCK_ORDERS.values():
        if (
            row.get("user_id") == uid
            and row.get("theme_key") == tk
            and row.get("status") == STATUS_PENDING
        ):
            return _to_order(row)
    return None
