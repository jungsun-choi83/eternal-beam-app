"""
크레딧 **예약 → 확정/해제** (Phase 7).

    선택 → reserve(5) → 생성 작업 → 프로바이더 → 검증
        PASS → commit()   확정. 잔액은 이미 빠져 있으므로 상태만 바뀐다.
        FAIL → release()  상태 전이 + reservation_release 보상 행.

── 왜 "차감 후 환불"이 아닌가 ───────────────────────────────────────────────
잔액 결과는 같다. 다른 것은 **중간 상태를 설명할 수 있는가**이다. 예약은 원장에
RESERVED 로 남아 "생성 중이라 잡혀 있는 크레딧"이 정확히 조회된다. 그리고 해제는
상태 전이 + 보상 행이 한 트랜잭션이라, Phase 1 이 고친 "환불 표시만 남고 크레딧은
안 돌아온 상태"가 구조적으로 생기지 않는다.

── 잔액은 예약 시점에 실제로 줄어든다 ──────────────────────────────────────
"예약"이지만 지갑에서는 즉시 빠진다. 그래야 5 크레딧으로 두 건을 동시에 시작할 수
없다 — 잔액이 그대로면 두 요청이 각각 통과하고, 하나는 나중에 낼 수 없는 돈이 된다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from . import credit_ledger

logger = logging.getLogger(__name__)


class ReservationError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class InsufficientCreditsError(ReservationError):
    def __init__(self, message: str = "크레딧이 부족합니다."):
        super().__init__("INSUFFICIENT_CREDITS", message, status=402)


@dataclass(frozen=True)
class Reservation:
    ledger_id: str
    credits: int
    balance_after: int
    #: 같은 키로 이미 예약돼 있었는가. **재시도·새로고침이 두 번 잡지 않는다.**
    replayed: bool


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


_RPC_ERRORS: dict[str, tuple[str, str, int]] = {
    "insufficient_credits": ("INSUFFICIENT_CREDITS", "크레딧이 부족합니다.", 402),
    "reservation_not_found": ("RESERVATION_NOT_FOUND", "예약을 찾을 수 없습니다.", 404),
    "reservation_not_open": (
        "RESERVATION_NOT_OPEN",
        "이미 확정되었거나 해제된 예약입니다.",
        409,
    ),
    "invalid_amount": ("RESERVATION_INVALID", "예약 금액이 올바르지 않습니다.", 400),
}


def _raise_for(e: Exception) -> None:
    msg = f"{e}".lower()
    for needle, (code, message, status) in _RPC_ERRORS.items():
        if needle in msg:
            if code == "INSUFFICIENT_CREDITS":
                raise InsufficientCreditsError() from e
            raise ReservationError(code, message, status=status) from e
    raise ReservationError(
        "RESERVATION_UNAVAILABLE",
        "크레딧 예약을 처리하지 못했습니다. 크레딧은 차감되지 않았습니다.",
        status=503,
    ) from e


# ── 인메모리 (HYBRID_USE_SUPABASE=0 전용) ────────────────────────────────────
#
# DB 경로와 **같은 순서·같은 판정**을 흉내 낸다. 목업이 실제와 다르게 동작하면
# 그 차이는 프로덕션에서만 드러난다.
_MOCK_STATE: dict[str, str] = {}  # ledger_id → RESERVED | COMMITTED | RELEASED


def __reset_for_tests() -> None:
    _MOCK_STATE.clear()


async def reserve(
    *,
    user_id: str,
    credits: int,
    idempotency_key: str,
    product_key: Optional[str] = None,
    reason: str = credit_ledger.REASON_IDLE_GENERATION,
    ref_type: Optional[str] = None,
    ref_id: Optional[str] = None,
) -> Reservation:
    """
    크레딧을 잡는다. **같은 키는 다시 잡지 않는다** (재시도·새로고침 방어).

    Raises:
        InsufficientCreditsError: 잔액 부족 — 아무것도 잡히지 않았다
        ReservationError:         예약을 DB 로 확정하지 못함 (차감 없음)
    """
    if credits <= 0:
        raise ReservationError("RESERVATION_INVALID", "예약 크레딧은 1 이상이어야 합니다.")

    if not _use_db():
        from . import wallet_service

        try:
            wallet = await wallet_service.deduct_credits(
                user_id, credits, strict=True, reason=reason,
                idempotency_key=idempotency_key, product_key=product_key,
                unit_price=credits, ref_type=ref_type, ref_id=ref_id,
            )
        except wallet_service.InsufficientCreditsError as e:
            raise InsufficientCreditsError() from e
        except wallet_service.WalletUnavailableError as e:
            raise ReservationError("RESERVATION_UNAVAILABLE", e.message, status=503) from e

        entry = credit_ledger._MOCK_BY_KEY.get(idempotency_key)
        lid = entry.ledger_id if entry else idempotency_key
        replayed = _MOCK_STATE.get(lid) is not None
        if not replayed:
            _MOCK_STATE[lid] = credit_ledger.STATE_RESERVED
            if entry is not None:
                entry.state = credit_ledger.STATE_RESERVED
                entry.settled_at = None
        return Reservation(
            ledger_id=lid, credits=credits,
            balance_after=wallet.current_credits, replayed=replayed,
        )

    try:
        r = _supabase().rpc(
            "reserve_credits",
            {
                "p_user_id": user_id,
                "p_credits": credits,
                "p_reason": reason,
                "p_idempotency_key": idempotency_key,
                "p_product_key": product_key,
                "p_ref_type": ref_type,
                "p_ref_id": ref_id,
            },
        ).execute()
    except Exception as e:
        _raise_for(e)

    data = r.data
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict) or not data.get("ledger_id"):
        raise ReservationError(
            "RESERVATION_UNAVAILABLE", "예약 결과를 확인하지 못했습니다.", status=503
        )
    return Reservation(
        ledger_id=str(data["ledger_id"]),
        credits=credits,
        balance_after=int(data.get("balance_after") or 0),
        replayed=bool(data.get("replayed")),
    )


async def commit(ledger_id: str) -> None:
    """
    예약 확정. **잔액은 건드리지 않는다** — 예약 시점에 이미 빠졌다.

    여러 번 불려도 안전하다(웹훅 재전송).
    """
    if not ledger_id:
        return

    if not _use_db():
        state = _MOCK_STATE.get(ledger_id)
        if state == credit_ledger.STATE_COMMITTED:
            return
        if state != credit_ledger.STATE_RESERVED:
            raise ReservationError(
                "RESERVATION_NOT_OPEN", "이미 확정되었거나 해제된 예약입니다.", status=409
            )
        _MOCK_STATE[ledger_id] = credit_ledger.STATE_COMMITTED
        for e in credit_ledger.mock_entries():
            if e.ledger_id == ledger_id:
                e.state = credit_ledger.STATE_COMMITTED
        return

    try:
        _supabase().rpc("commit_reservation", {"p_ledger_id": ledger_id}).execute()
    except Exception as e:
        _raise_for(e)


async def release(ledger_id: str) -> None:
    """
    예약 해제 — 잡혀 있던 크레딧을 되돌린다.

    ⚠️ 확정된 예약은 해제할 수 없다(RESERVATION_NOT_OPEN). 그건 환불이지
    해제가 아니며, 되돌리려면 자산을 회수하는 결정이 함께 필요하다.
    """
    if not ledger_id:
        return

    if not _use_db():
        state = _MOCK_STATE.get(ledger_id)
        if state == credit_ledger.STATE_RELEASED:
            return
        if state != credit_ledger.STATE_RESERVED:
            raise ReservationError(
                "RESERVATION_NOT_OPEN", "이미 확정되었거나 해제된 예약입니다.", status=409
            )
        _MOCK_STATE[ledger_id] = credit_ledger.STATE_RELEASED

        credits = 0
        for e in credit_ledger.mock_entries():
            if e.ledger_id == ledger_id:
                e.state = credit_ledger.STATE_RELEASED
                credits = -e.delta
        if credits > 0:
            from . import wallet_service

            await wallet_service.refund_credits(
                _owner_of(ledger_id), credits,
                reason=credit_ledger.REASON_RESERVATION_RELEASE,
                idempotency_key=f"release:{ledger_id}",
            )
        return

    try:
        _supabase().rpc(
            "release_reservation", {"p_ledger_id": ledger_id, "p_reason": None}
        ).execute()
    except Exception as e:
        _raise_for(e)


def _owner_of(ledger_id: str) -> str:
    for e in credit_ledger.mock_entries():
        if e.ledger_id == ledger_id:
            return e.user_id
    return ""
