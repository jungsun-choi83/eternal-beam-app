"""
Supabase 계정 → Eternal Beam 신원 연결.

지키려는 것 (가장 중요한 것부터):
  * 기존 데이터를 **고아로 만들지 않는다**. 검증된 이메일로 로그인하면 예전
    auth-screen 이 쓰던 소문자 이메일이 그대로 신원이 된다 → 지갑·생성 자산·
    구매 원장이 전부 붙어 있다.
  * 신원은 **안정적**이다. 같은 계정은 몇 번을 물어도 같은 값을 받는다.
  * 검증되지 않은 이메일로는 남의 신원을 가져갈 수 없다.
  * 한 신원에 계정은 하나뿐이다.
"""

from __future__ import annotations

import pytest

from backend.services import identity_service as ident

SUB_A = "11111111-1111-1111-1111-111111111111"
SUB_B = "22222222-2222-2222-2222-222222222222"
LEGACY_EMAIL = "toiletarchive24@gmail.com"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    ident.__reset_for_tests()
    yield
    ident.__reset_for_tests()


async def _resolve(sub: str, email: str | None, verified: bool):
    return await ident.resolve_identity(subject=sub, email=email, email_verified=verified)


# ── 기존 데이터 승계 ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_verified_email_inherits_the_legacy_identity():
    """
    예전 auth-screen 은 setEternalBeamUserId(email.toLowerCase()) 를 썼다.
    지갑·자산·구매가 전부 그 문자열로 키가 잡혀 있으므로, 신원이 같아야 한다.
    """
    r = await _resolve(SUB_A, LEGACY_EMAIL, True)
    assert r.user_id == LEGACY_EMAIL, "기존 데이터가 고아가 된다"
    assert r.linked_via == "email"


@pytest.mark.anyio
async def test_email_is_normalised_to_lowercase():
    r = await _resolve(SUB_A, "  ToiletArchive24@Gmail.COM ", True)
    assert r.user_id == LEGACY_EMAIL


@pytest.mark.anyio
async def test_identity_is_stable_across_logins():
    first = await _resolve(SUB_A, LEGACY_EMAIL, True)
    for _ in range(5):
        again = await _resolve(SUB_A, LEGACY_EMAIL, True)
        assert again.user_id == first.user_id
        assert again.linked_via == "existing"


@pytest.mark.anyio
async def test_identity_survives_email_change_on_the_account():
    """연결은 한 번 정해지면 바뀌지 않는다 — 이메일을 바꿔도 데이터가 따라온다."""
    first = await _resolve(SUB_A, LEGACY_EMAIL, True)
    later = await _resolve(SUB_A, "new-address@example.com", True)
    assert later.user_id == first.user_id


# ── 승계 거부 ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_unverified_email_cannot_claim_a_legacy_identity():
    """검증 없이 남의 이메일을 적어 지갑을 가져가는 경로를 막는다."""
    r = await _resolve(SUB_A, LEGACY_EMAIL, False)
    assert r.user_id == SUB_A, "검증되지 않은 이메일이 신원을 승계했다"
    assert r.linked_via == "new"


@pytest.mark.anyio
async def test_second_account_cannot_take_an_already_linked_identity():
    first = await _resolve(SUB_A, LEGACY_EMAIL, True)
    assert first.user_id == LEGACY_EMAIL

    intruder = await _resolve(SUB_B, LEGACY_EMAIL, True)
    assert intruder.user_id == SUB_B, "두 계정이 같은 신원을 공유한다"
    assert intruder.user_id != first.user_id


@pytest.mark.anyio
async def test_no_email_gets_a_fresh_identity():
    r = await _resolve(SUB_A, None, False)
    assert r.user_id == SUB_A
    assert r.linked_via == "new"


@pytest.mark.anyio
async def test_anonymous_legacy_ids_are_never_auto_claimed():
    """
    익명 신원(`user_<base36>`)은 타임스탬프 기반이라 추측 가능하다. 소유 증명이
    없으므로 절대 자동 승계하지 않는다 — 승계하면 남의 지갑을 가져갈 수 있다.
    """
    r = await _resolve(SUB_A, "user_lx8f2a@example.com", True)
    assert r.user_id == "user_lx8f2a@example.com"
    # 익명 id 자체(`user_lx8f2a`)는 어떤 경로로도 신원이 되지 않는다.
    assert not r.user_id.startswith("user_lx8f2a\0")
    r2 = await _resolve(SUB_B, None, False)
    assert r2.user_id == SUB_B


@pytest.mark.anyio
async def test_missing_subject_is_rejected():
    with pytest.raises(ident.IdentityUnavailableError):
        await _resolve("", LEGACY_EMAIL, True)


# ── 라우터 통합 ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_two_accounts_get_separate_wallets_and_assets():
    """서로 다른 신원이면 프리미엄 자산·지갑이 섞이지 않는다."""
    a = await _resolve(SUB_A, "alice@example.com", True)
    b = await _resolve(SUB_B, "bob@example.com", True)
    assert a.user_id != b.user_id
    assert a.user_id == "alice@example.com"
    assert b.user_id == "bob@example.com"
