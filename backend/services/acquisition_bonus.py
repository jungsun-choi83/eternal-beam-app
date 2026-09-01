"""
획득 보너스 크레딧 (Phase 9).

    Soul Trace 핸드오프  → +5   고객을 데려온다
    LETTER   ₩14,900     → +3   다시 오게 한다
    MEMORY BOX ₩49,000   → +10

── 보너스는 결제 경로를 막지 않는다 ────────────────────────────────────────
이 모듈의 모든 실패는 **삼켜진다.** 보너스를 못 줬다고 편지 가져오기나 실물 결제가
실패하면, 고객은 돈을 냈는데 주문이 실패한 것처럼 보인다. 보너스는 덤이고, 덤 때문에
본체를 잃어서는 안 된다.

대신 실패는 크게 로그로 남는다. 지급되지 않은 보너스는 나중에 같은 멱등 키로 다시
시도해도 안전하다(중복 지급되지 않는다).

── 멱등 키가 방어의 전부다 ─────────────────────────────────────────────────
    soultrace:{source_letter_id}   편지 하나당 한 번 — **전역으로**
    physical_bonus:{order_id}      주문 하나당 한 번

두 번째 것이 특히 중요하다: 결제 확인 화면을 새로고침하면 confirm 이 다시 불린다.
키가 없으면 새로고침 한 번이 10 크레딧이다.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

BONUS_SOULTRACE = "soultrace_handoff"


def physical_bonus_key(product_type: str) -> str:
    """'LETTER' → 'physical:LETTER'."""
    return f"physical:{(product_type or '').strip().upper()}"


def soultrace_idempotency_key(source_letter_id: str) -> str:
    """
    **Soul Trace 원본 편지 id** 로 잡는다. 두 가지를 쓰지 않는 이유:

      임시 핸드오프 토큰:  편지 하나에 대해 몇 번이든 새로 발급된다
                           (POST /api/handoff 에 횟수 제한이 없고, 실패한 핸드오프를
                           다시 시도할 수 있어야 하므로 그것이 옳다). 토큰을 키로
                           삼으면 토큰을 다시 받는 것만으로 보너스를 다시 받는다.

      우리 쪽 파생 letter_id: 안에 user_id 가 들어 있다. 같은 편지를 여러 계정으로
                           가져가면 계정마다 보너스가 나간다.

    원본 id 로 잡으면 **편지 하나에 보너스 하나**다.
    """
    return f"soultrace:{(source_letter_id or '').strip()}"


def physical_idempotency_key(order_id: str) -> str:
    """주문 하나당 한 번. 결제 확인 새로고침이 다시 지급하지 못한다."""
    return f"physical_bonus:{(order_id or '').strip()}"


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


#: 인메모리 규칙 (HYBRID_USE_SUPABASE=0). 마이그레이션 시드와 같은 값이어야 한다.
_SEED: dict[str, int] = {
    BONUS_SOULTRACE: 5,
    "physical:LETTER": 3,
    "physical:MEMORY_BOX": 10,
}

_MOCK_RULES: dict[str, int] = {}


def _rules() -> dict[str, int]:
    if not _MOCK_RULES:
        _MOCK_RULES.update(_SEED)
    return _MOCK_RULES


def __reset_for_tests() -> None:
    _MOCK_RULES.clear()


def set_bonus_for_tests(bonus_key: str, credits: int) -> None:
    _rules()[bonus_key] = credits


async def bonus_credits(bonus_key: str) -> Optional[int]:
    """이 보너스의 크레딧 수. 규칙이 없거나 꺼져 있으면 None."""
    if not _use_db():
        return _rules().get(bonus_key)
    try:
        r = (
            _supabase()
            .table(os.getenv("CREDIT_BONUS_RULES_TABLE", "credit_bonus_rules"))
            .select("credits")
            .eq("bonus_key", bonus_key)
            .eq("active", True)
            .limit(1)
            .execute()
        )
    except Exception:
        logger.warning("보너스 규칙 조회 실패 (key=%s)", bonus_key)
        return None
    rows = getattr(r, "data", None) or []
    return int(rows[0]["credits"]) if rows else None


async def grant(
    *,
    user_id: str,
    bonus_key: str,
    idempotency_key: str,
    ref_type: Optional[str] = None,
    ref_id: Optional[str] = None,
) -> int:
    """
    보너스를 지급한다. 반환값은 **이번에 실제로 지급된 크레딧** (재지급이면 0).

    ⚠️ **절대 예외를 올리지 않는다.** 호출부는 결제·편지 가져오기의 성공 경로이며,
    보너스 실패가 그것을 뒤집으면 안 된다. 실패는 로그로만 남는다.
    """
    uid = (user_id or "").strip()
    if not uid or not idempotency_key:
        return 0

    try:
        if not _use_db():
            credits = _rules().get(bonus_key)
            if not credits:
                return 0
            from . import credit_ledger, wallet_service

            before = await wallet_service.get_wallet(uid, create_if_missing=True)
            prior = credit_ledger._MOCK_BY_KEY.get(idempotency_key)
            await wallet_service.add_credits(
                uid, credits,
                reason=(
                    credit_ledger.REASON_SOULTRACE_BONUS
                    if bonus_key == BONUS_SOULTRACE
                    else credit_ledger.REASON_PHYSICAL_PRODUCT_BONUS
                ),
                idempotency_key=idempotency_key,
                product_key=bonus_key,
                ref_type=ref_type,
                ref_id=ref_id,
            )
            _ = before
            return 0 if prior is not None else credits

        r = _supabase().rpc(
            "grant_acquisition_bonus",
            {
                "p_user_id": uid,
                "p_bonus_key": bonus_key,
                "p_idempotency_key": idempotency_key,
                "p_ref_type": ref_type,
                "p_ref_id": ref_id,
            },
        ).execute()
        data = r.data
        if isinstance(data, list) and data:
            data = data[0]
        granted = int((data or {}).get("granted") or 0)
        if granted:
            logger.warning(
                "획득 보너스 지급 — user=%s bonus=%s credits=%s", uid, bonus_key, granted
            )
        return granted
    except Exception:
        # 보너스는 덤이다. 덤 때문에 본체(결제·편지)를 잃지 않는다.
        logger.exception(
            "획득 보너스 지급 실패 — 본 경로는 계속한다 (user=%s bonus=%s key=%s)",
            uid, bonus_key, idempotency_key,
        )
        return 0


async def grant_soultrace(*, user_id: str, source_letter_id: str) -> int:
    return await grant(
        user_id=user_id,
        bonus_key=BONUS_SOULTRACE,
        idempotency_key=soultrace_idempotency_key(source_letter_id),
        ref_type="soul_trace_letters",
        ref_id=source_letter_id,
    )


async def grant_physical(*, user_id: str, order_id: str, product_type: str) -> int:
    return await grant(
        user_id=user_id,
        bonus_key=physical_bonus_key(product_type),
        idempotency_key=physical_idempotency_key(order_id),
        ref_type="physical_orders",
        ref_id=order_id,
    )
