"""
지갑 연산의 **fail-closed 계약** (Phase 1 — 재무 안전).

지키려는 것 하나:

    DB 장애 ≠ 성공한 크레딧 연산

예전에는 Supabase 가 실패하면 지갑 변경이 프로세스 메모리에만 적용되고 **성공이
반환**됐다. Render 가 인스턴스를 재활용하면(무료·starter 플랜에서는 일상이다)
그 변경은 사라진다. 방향에 따라 결과가 다르다:

    충전 실패 → 고객은 돈을 냈는데 크레딧이 없다. 영수증은 이미 소비됐다.
    환불 실패 → **고객이 크레딧을 영구히 잃는다.** 원장에는 '환불됨' 도장이
                찍혀 있으므로 재시도도, 발견도 되지 않는다.

두 번째가 이 파일이 존재하는 이유다.

여기서 고정하는 계약:
  * HYBRID_USE_SUPABASE=0 (명시적 목업 모드) → 인메모리로 동작한다. 이건 폴백이
    아니라 그 환경의 정답이다.
  * DB 를 쓰기로 해 놓고 실패 → WalletUnavailableError. 절대 성공을 반환하지 않는다.
  * 환불 표시를 찍었는데 지갑 환불이 확정되지 않으면 → **표시를 되돌린다.**
"""

from __future__ import annotations

import inspect

import pytest

from backend.services import (
    credit_generation_service,
    generated_motions_service,
    premium_purchase,
    wallet_service,
)
from backend.services.wallet_service import (
    InsufficientCreditsError,
    WalletUnavailableError,
)

USER = "user_wallet_safety"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("STARTER_CREDITS", "0")
    wallet_service._MOCK_WALLETS.clear()
    premium_purchase.__reset_for_tests()
    yield
    wallet_service._MOCK_WALLETS.clear()
    premium_purchase.__reset_for_tests()


class _BrokenSupabase:
    """모든 호출이 터지는 클라이언트 — Supabase 장애를 흉내 낸다."""

    def __init__(self) -> None:
        self.rpc_calls: list[tuple[str, dict]] = []

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        raise RuntimeError("connection reset by peer")

    def table(self, _name):
        raise RuntimeError("connection reset by peer")


class _SilentSupabase:
    """죽지는 않지만 **잔액을 돌려주지 않는** 클라이언트 (스키마 드리프트 등)."""

    class _Q:
        data = None

        def execute(self):
            return self

        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        def insert(self, *_a, **_k):
            return self

        def update(self, *_a, **_k):
            return self

    def rpc(self, _name, _params):
        return self._Q()

    def table(self, _name):
        return self._Q()


@pytest.fixture
def db_mode(monkeypatch: pytest.MonkeyPatch):
    """'DB 를 쓰기로 했다' 상태. 클라이언트는 테스트가 주입한다."""
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "1")


@pytest.fixture
def memory_mode(monkeypatch: pytest.MonkeyPatch):
    """운영자가 명시적으로 DB 를 끈 상태."""
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")


def _install(monkeypatch: pytest.MonkeyPatch, client) -> None:
    monkeypatch.setattr(wallet_service, "_supabase", lambda: client)


# ── 충전 ─────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_add_credits_fails_closed_when_the_db_is_down(db_mode, monkeypatch):
    """장애 중 충전은 **성공을 반환하지 않는다.**"""
    _install(monkeypatch, _BrokenSupabase())

    with pytest.raises(WalletUnavailableError):
        await wallet_service.add_credits(USER, 5)


@pytest.mark.anyio
async def test_add_credits_fails_closed_when_the_rpc_returns_no_balance(db_mode, monkeypatch):
    """
    잔액을 못 받았으면 충전됐는지 알 수 없다 → 닫는다.

    예전에는 이때 직접 UPDATE 로 덮어썼는데, 그 UPDATE 는 읽은 값 기준이라
    동시에 일어난 차감을 통째로 지운다(lost update).
    """
    _install(monkeypatch, _SilentSupabase())

    with pytest.raises(WalletUnavailableError):
        await wallet_service.add_credits(USER, 5)


@pytest.mark.anyio
async def test_add_credits_does_not_leave_a_phantom_memory_balance(db_mode, monkeypatch):
    """실패한 충전이 인메모리 잔액을 만들어 두지 않는다."""
    _install(monkeypatch, _BrokenSupabase())

    with pytest.raises(WalletUnavailableError):
        await wallet_service.add_credits(USER, 7)

    assert USER not in wallet_service._MOCK_WALLETS


@pytest.mark.anyio
async def test_add_credits_still_works_in_explicit_memory_mode(memory_mode):
    """HYBRID_USE_SUPABASE=0 은 폴백이 아니라 선언이다 — 예전 그대로 동작한다."""
    w = await wallet_service.add_credits(USER, 5)
    assert w.current_credits == 5


# ── 환불 ─────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_refund_fails_closed_when_the_db_is_down(db_mode, monkeypatch):
    """감사에서 나온 최악의 경로 — 이제 닫힌다."""
    _install(monkeypatch, _BrokenSupabase())

    with pytest.raises(WalletUnavailableError):
        await wallet_service.refund_credits(USER, 3)


@pytest.mark.anyio
async def test_refund_fails_closed_when_the_rpc_returns_no_balance(db_mode, monkeypatch):
    _install(monkeypatch, _SilentSupabase())

    with pytest.raises(WalletUnavailableError):
        await wallet_service.refund_credits(USER, 3)


@pytest.mark.anyio
async def test_refund_still_works_in_explicit_memory_mode(memory_mode):
    await wallet_service.add_credits(USER, 2)
    w = await wallet_service.refund_credits(USER, 3)
    assert w.current_credits == 5


def test_refund_no_longer_offers_a_loose_mode():
    """
    `strict` 인자를 되살리지 못하게 못박는다.

    예전에는 기본값이 False 였고 호출부 **다섯 곳이 전부** False 를 넘겼다. 즉
    실전에서 환불은 언제나 "실패해도 성공을 반환하는" 모드로 돌았다. 인자가
    남아 있으면 다음 호출부가 또 False 를 넘긴다.
    """
    params = inspect.signature(wallet_service.refund_credits).parameters
    assert "strict" not in params


# ── 차감 ─────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_strict_deduct_fails_closed(db_mode, monkeypatch):
    _install(monkeypatch, _BrokenSupabase())

    with pytest.raises(WalletUnavailableError):
        await wallet_service.deduct_credits(USER, 1, strict=True)


@pytest.mark.anyio
async def test_legacy_deduct_also_fails_closed(db_mode, monkeypatch):
    """
    레거시(비-strict) 경로도 닫는다.

    예전에는 여기서 인메모리로 폴백해 차감을 성공으로 보고했다 — 그러면 요청은
    유료 생성을 제출하면서 실제로는 아무도 과금되지 않았고, 인스턴스가 재활용되면
    그 흔적조차 남지 않았다.
    """
    _install(monkeypatch, _BrokenSupabase())

    with pytest.raises(WalletUnavailableError):
        await wallet_service.deduct_credits(USER, 1)


@pytest.mark.anyio
async def test_deduct_still_works_in_explicit_memory_mode(memory_mode):
    await wallet_service.add_credits(USER, 4)
    w = await wallet_service.deduct_credits(USER, 3)
    assert w.current_credits == 1


@pytest.mark.anyio
async def test_insufficient_credits_is_not_reported_as_unavailable(memory_mode):
    """잔액 부족과 DB 장애는 **다른 답**이다 — 고객 안내가 달라진다."""
    await wallet_service.add_credits(USER, 1)
    with pytest.raises(InsufficientCreditsError):
        await wallet_service.deduct_credits(USER, 5)


# ── 조회 ─────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_strict_get_wallet_does_not_invent_a_wallet(db_mode, monkeypatch):
    """
    돈이 움직이는 경로 안의 조회는 폴백하지 않는다.

    폴백하면 DB 에는 행이 없는 채로 차감 RPC 가 돌아 insufficient_credits 가 되고,
    잔액이 충분한 사용자가 원인 불명의 "크레딧 부족"을 본다.
    """
    _install(monkeypatch, _BrokenSupabase())

    with pytest.raises(WalletUnavailableError):
        await wallet_service.get_wallet(USER, create_if_missing=True, strict=True)


@pytest.mark.anyio
async def test_non_strict_get_wallet_still_falls_back(db_mode, monkeypatch):
    """표시용 조회는 예전 그대로 — 잔액 표시가 잠깐 틀리는 것은 돈을 잃는 게 아니다."""
    _install(monkeypatch, _BrokenSupabase())

    w = await wallet_service.get_wallet(USER, create_if_missing=True)
    assert w is not None


# ── 환불 표시 되돌리기 ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_failed_refund_restores_the_purchase_ledger_stamp(memory_mode, monkeypatch):
    """
    **고객이 크레딧을 잃던 바로 그 시나리오.**

    도장은 찍혔는데 지갑 환불이 확정되지 않으면, 표시를 되돌려 다음 종료
    이벤트가 같은 판정을 다시 내리게 해야 한다. 되돌리지 않으면 그 구매는
    영원히 '환불됨' 이고 크레딧은 돌아오지 않는다.
    """
    # ⚠️ kind 는 reconcile_after_terminal 이 실제로 조회하는 것과 **같아야** 한다.
    # COME_CLOSER 는 IDLE_EVENTS 가 아니므로 그쪽은 ACTION:<ID> 만 본다. 여기서
    # IDLE_BUNDLE 을 선점하면 판정 대상에 아예 들어가지 않아, 테스트가 아무것도
    # 검사하지 않은 채 통과한다(처음에 실제로 그렇게 썼다가 잡혔다).
    kind = premium_purchase.action_kind("COME_CLOSER")
    pid = await premium_purchase._claim_purchase(USER, "pet1", kind, 1)
    assert pid

    async def _boom(*_a, **_k):
        raise WalletUnavailableError("db down")

    async def _no_assets(*_a, **_k):
        return premium_purchase.AssetState(ready={}, active=[], missing=["BREATHING"])

    monkeypatch.setattr(premium_purchase, "refund_credits", _boom)
    monkeypatch.setattr(premium_purchase, "asset_state", _no_assets)

    refunded = await premium_purchase.reconcile_after_terminal(USER, "pet1", "COME_CLOSER")

    assert refunded is False
    # 표시가 되돌아왔으므로 구매는 여전히 '활성' 이고 재시도가 가능하다.
    still_active = await premium_purchase.find_active_purchase(USER, "pet1", kind)
    assert still_active is not None, "환불 표시가 되돌아오지 않았다 — 크레딧이 갇힌다"
    assert not still_active.get("refunded_at")


@pytest.mark.anyio
async def test_successful_refund_keeps_the_stamp(memory_mode, monkeypatch):
    """환불이 확정되면 도장은 남는다 — 이중 환불을 막는 것이 그 도장의 일이다."""
    kind = premium_purchase.action_kind("COME_CLOSER")
    assert await premium_purchase._claim_purchase(USER, "pet2", kind, 1)

    async def _ok(*_a, **_k):
        return None

    async def _no_assets(*_a, **_k):
        return premium_purchase.AssetState(ready={}, active=[], missing=["BREATHING"])

    monkeypatch.setattr(premium_purchase, "refund_credits", _ok)
    monkeypatch.setattr(premium_purchase, "asset_state", _no_assets)

    assert await premium_purchase.reconcile_after_terminal(USER, "pet2", "COME_CLOSER") is True
    assert await premium_purchase.find_active_purchase(USER, "pet2", kind) is None


@pytest.mark.anyio
async def test_failed_session_refund_restores_the_session_stamp(memory_mode, monkeypatch):
    """레거시 4코인 세션도 같은 규칙을 따른다."""
    sid = "sess_safety_1"
    generated_motions_service._MOCK_SESSIONS[sid] = {
        "session_id": sid,
        "user_id": USER,
        "credits_charged": 4,
        "status": "processing",
        "refunded_at": None,
    }
    try:
        assert await generated_motions_service.mark_session_refunded(sid) is True
        assert generated_motions_service._MOCK_SESSIONS[sid]["refunded_at"]

        assert await generated_motions_service.unmark_session_refunded(sid) is True
        assert not generated_motions_service._MOCK_SESSIONS[sid]["refunded_at"]

        # 되돌린 뒤에는 다시 선점할 수 있다 = 재시도가 살아 있다.
        assert await generated_motions_service.mark_session_refunded(sid) is True
    finally:
        generated_motions_service._MOCK_SESSIONS.pop(sid, None)


@pytest.mark.anyio
async def test_finalize_unmarks_when_the_wallet_refund_is_unconfirmed(memory_mode, monkeypatch):
    """세션 종료 경로 전체를 통과시켜 확인한다."""
    sid = "sess_safety_2"
    generated_motions_service._MOCK_SESSIONS[sid] = {
        "session_id": sid,
        "user_id": USER,
        "credits_charged": 4,
        "status": "processing",
        "refunded_at": None,
    }

    async def _boom(*_a, **_k):
        raise WalletUnavailableError("db down")

    async def _no_jobs(*_a, **_k):
        return []

    monkeypatch.setattr(credit_generation_service, "refund_credits", _boom)
    monkeypatch.setattr(generated_motions_service, "list_jobs_for_session", _no_jobs)

    try:
        out = await credit_generation_service._finalize_session_if_terminal(sid)
        assert out["refunded"] is False
        assert not generated_motions_service._MOCK_SESSIONS[sid]["refunded_at"], (
            "환불 표시가 되돌아오지 않았다 — 다음 웹훅이 재시도하지 못한다"
        )
    finally:
        generated_motions_service._MOCK_SESSIONS.pop(sid, None)
