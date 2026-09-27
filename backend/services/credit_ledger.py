"""
크레딧 원장 — 사유 어휘와 멱등 키 규약.

    user_wallets   지금 잔액이 **얼마인가**
    credit_ledger  그 잔액이 **왜 그런가**

DB 쪽 정의는 supabase/migrations/20261001000000_credit_ledger.sql 에 있고, 실제
쓰기는 wallet_apply() 안에서 지갑 변경과 **함께** 일어난다. 이 모듈이 하는 일은 셋:

  1. 사유 문자열을 상수로 고정한다 (오타 방지 — DB CHECK 의 앞단 방어)
  2. 멱등 키를 만드는 규약을 한곳에 모은다
  3. 인메모리 모드(HYBRID_USE_SUPABASE=0)에서 원장을 흉내 낸다

3번이 있는 이유: 목업 모드에서 원장이 사라지면 "모든 움직임이 기록된다"는 성질을
테스트로 확인할 수 없다. 목업이 실제와 다르게 동작하면, 그 차이가 곧 프로덕션에서만
드러나는 결함이 된다 — 이 저장소가 Phase 8 에서 이미 겪은 일이다(구독 0크레딧 갱신이
Python 목업에서는 통과하고 SQL 에서는 실패했다).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

# ── 사유 ──────────────────────────────────────────────────────────────────────
# 값은 DB CHECK(credit_ledger_reason_check)와 **정확히** 같아야 한다.
# 테스트(test_credit_ledger.py)가 두 목록의 일치를 강제한다.

#: 들어오는 것
REASON_CREDIT_PACK_TOPUP = "credit_pack_topup"
REASON_STARTER_BONUS = "starter_bonus"
REASON_SOULTRACE_BONUS = "soultrace_bonus"
REASON_MEMBERSHIP_GRANT = "membership_grant"
REASON_PHYSICAL_PRODUCT_BONUS = "physical_product_bonus"
REASON_REFUND = "refund"
REASON_RESERVATION_RELEASE = "reservation_release"
REASON_LEGACY_MIGRATION = "legacy_migration"

#: 나가는 것
REASON_THEME_PURCHASE = "theme_purchase"
REASON_IDLE_GENERATION = "idle_generation"
REASON_ACTION_GENERATION = "action_generation"
REASON_AI_BACKGROUND_GENERATION = "ai_background_generation"

#: 양방향
REASON_ADMIN_ADJUSTMENT = "admin_adjustment"

CREDIT_REASONS: frozenset[str] = frozenset({
    REASON_CREDIT_PACK_TOPUP,
    REASON_STARTER_BONUS,
    REASON_SOULTRACE_BONUS,
    REASON_MEMBERSHIP_GRANT,
    REASON_PHYSICAL_PRODUCT_BONUS,
    REASON_REFUND,
    REASON_RESERVATION_RELEASE,
})

DEBIT_REASONS: frozenset[str] = frozenset({
    REASON_THEME_PURCHASE,
    REASON_IDLE_GENERATION,
    REASON_ACTION_GENERATION,
    REASON_AI_BACKGROUND_GENERATION,
})

ALL_REASONS: frozenset[str] = (
    CREDIT_REASONS | DEBIT_REASONS | {REASON_LEGACY_MIGRATION, REASON_ADMIN_ADJUSTMENT}
)

#: 상태
STATE_RESERVED = "RESERVED"
STATE_COMMITTED = "COMMITTED"
STATE_RELEASED = "RELEASED"
ALL_STATES: frozenset[str] = frozenset({STATE_RESERVED, STATE_COMMITTED, STATE_RELEASED})


def direction_ok(reason: str, delta: int) -> bool:
    """
    사유와 부호가 맞는가. DB CHECK 와 같은 규칙의 Python 사본.

    두 벌인 것이 낫다: DB 는 최종 방어선이고, 여기서 걸리면 스택 트레이스가
    호출부를 가리킨다. DB 에서만 걸리면 오류 메시지가 제약 이름뿐이다.
    """
    if reason in CREDIT_REASONS:
        return delta > 0
    if reason in DEBIT_REASONS:
        return delta < 0
    if reason == REASON_LEGACY_MIGRATION:
        return delta >= 0
    if reason == REASON_ADMIN_ADJUSTMENT:
        return delta != 0
    return False


# ── 멱등 키 ───────────────────────────────────────────────────────────────────
#
# 규약: "<출처>:<그 출처에서 유일한 값>"
#
# 출처마다 이미 unique 인 것을 그대로 쓴다. 새 식별자를 만들면 그것이 유일한지
# 다시 증명해야 하지만, 기존 unique 키를 재사용하면 증명이 이미 끝나 있다.


def iap_key(receipt_fingerprint: str) -> str:
    """payment_history.receipt_fingerprint 와 같은 축."""
    return f"iap:{receipt_fingerprint}"


def membership_key(event_fingerprint: str) -> str:
    """subscription_webhook_events.event_fingerprint 와 같은 축."""
    return f"membership:{event_fingerprint}"


def starter_key(user_id: str) -> str:
    """사용자당 한 번. 지갑을 지웠다 만들어도 다시 지급되지 않는다."""
    return f"starter:{user_id}"


def purchase_key(purchase_id: str) -> str:
    """premium_purchases.purchase_id — 구매 원장의 선점 행 하나당 차감 하나."""
    return f"purchase:{purchase_id}"


def refund_key(purchase_id: str) -> str:
    """같은 구매에 대한 환불은 한 번뿐이다."""
    return f"refund:{purchase_id}"


def session_key(session_id: str) -> str:
    """레거시 4코인 세션 — 세션당 차감 하나."""
    return f"session:{session_id}"


def session_refund_key(session_id: str) -> str:
    return f"session-refund:{session_id}"


def theme_purchase_key(user_id: str, theme_key: str) -> str:
    """
    테마 크레딧 구매 — **(사용자, 테마) 당 하나.**

    이 키가 곧 user_theme_entitlements.order_id 가 된다(그 컬럼에 부분 unique
    인덱스가 있다). 그래서 재플레이 방어가 원장과 소유권 **양쪽**에서 같은 값으로
    걸린다 — 더블탭·다중 탭·재시도가 두 번 청구할 수 없다.

    ⚠️ 테마 환불이 생기면 이 규약을 고쳐야 한다. 지금은 재구매 개념이 없어서
    (테마는 영구 소유) 사용자·테마 조합이 곧 유일한 구매지만, 환불 후 재구매를
    허용하는 순간 이 키로는 두 번째 구매가 재플레이로 보인다. 그때는 환불 횟수를
    키에 넣어야 한다.
    """
    return f"theme:{user_id}:{(theme_key or '').strip().lower()}"


def auto_key(prefix: str = "auto") -> str:
    """
    멱등 키가 없는 경로용 임시 키.

    ⚠️ 재플레이를 막지 못한다 — 매번 다른 값이기 때문이다. 이것을 쓰는 경로는
    **예전과 같은 수준의 방어**를 갖는다(원래 멱등성이 없었다). 다만 기록은
    남으므로, 이중 적용이 일어나면 원장에 두 줄로 드러난다. 예전에는 잔액만
    늘고 흔적이 없었다.

    새 경로에는 쓰지 말 것. 출처의 unique 값을 찾아 위 헬퍼처럼 규약을 추가하라.
    """
    return f"{prefix}:{uuid.uuid4()}"


# ── 인메모리 원장 (HYBRID_USE_SUPABASE=0 전용) ────────────────────────────────


@dataclass
class LedgerEntry:
    user_id: str
    delta: int
    balance_after: int
    reason: str
    idempotency_key: str
    product_key: Optional[str] = None
    unit_price: Optional[int] = None
    state: str = STATE_COMMITTED
    ref_type: Optional[str] = None
    ref_id: Optional[str] = None
    ledger_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=datetime.utcnow)
    settled_at: Optional[datetime] = field(default_factory=datetime.utcnow)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ledger_id": self.ledger_id,
            "user_id": self.user_id,
            "delta": self.delta,
            "balance_after": self.balance_after,
            "reason": self.reason,
            "product_key": self.product_key,
            "unit_price": self.unit_price,
            "state": self.state,
            "idempotency_key": self.idempotency_key,
            "ref_type": self.ref_type,
            "ref_id": self.ref_id,
        }


#: 삽입 순서대로. 목업 모드에서만 채워진다.
_MOCK_LEDGER: list[LedgerEntry] = []
_MOCK_BY_KEY: dict[str, LedgerEntry] = {}


def record_mock(entry: LedgerEntry) -> tuple[LedgerEntry, bool]:
    """
    인메모리 원장에 기록한다. 반환: (행, replayed).

    DB 의 unique(idempotency_key) 와 같은 판정을 한다 — 같은 키면 기록하지 않고
    기존 행을 돌려준다. 그래야 목업 테스트가 실제 재플레이 동작을 확인한다.
    """
    prior = _MOCK_BY_KEY.get(entry.idempotency_key)
    if prior is not None:
        return prior, True
    _MOCK_LEDGER.append(entry)
    _MOCK_BY_KEY[entry.idempotency_key] = entry
    return entry, False


def mock_entries(user_id: Optional[str] = None) -> list[LedgerEntry]:
    if user_id is None:
        return list(_MOCK_LEDGER)
    uid = user_id.strip()
    return [e for e in _MOCK_LEDGER if e.user_id == uid]


def mock_balance(user_id: str) -> int:
    """원장 합계. 지갑 잔액과 같아야 한다 — 그 대조가 이 함수의 용도다."""
    return sum(e.delta for e in mock_entries(user_id))


def __reset_for_tests() -> None:
    _MOCK_LEDGER.clear()
    _MOCK_BY_KEY.clear()
