"""
Shaker 공유 토큰 — 추측 불가·폐기 가능·임의 petId 접근 불가.

이 파일이 지키는 계약:

  1. **임의의 petId 로는 아무것도 열 수 없다.** 공개 경로에 pet_id 조회가 없다.
  2. 토큰은 추측 불가하고, **원문이 저장되지 않는다**(해시만).
  3. 폐기하면 즉시 닫힌다. 만료도 마찬가지.
  4. 토큰 A 로 펫 B 를 열 수 없다 (petId 바꿔치기).
  5. 남의 링크를 폐기할 수 없다.
"""

from __future__ import annotations

import pytest

from backend.services import shaker_share

OWNER = "owner@example.com"
OTHER = "stranger@example.com"
PET = "pet_goya"
BREATH = "https://cdn.test/goya/idle_loop.mp4"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    shaker_share.__reset_for_tests()
    yield
    shaker_share.__reset_for_tests()


async def _mint(**kw) -> tuple[str, str]:
    return await shaker_share.create_share(
        user_id=kw.pop("user_id", OWNER),
        pet_id=kw.pop("pet_id", PET),
        breathing_url=kw.pop("breathing_url", BREATH),
        **kw,
    )


# ── 1. 임의 petId 접근 불가 ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_pet_id_alone_opens_nothing():
    """
    **핵심 회귀**: pet_id 를 아는 것만으로는 어떤 조회 경로도 없다.

    이 모듈에는 pet_id 를 받아 레코드를 돌려주는 공개 함수가 존재하지 않는다.
    resolve_share 는 토큰만 받는다 — 서명이 그것을 강제한다.
    """
    await _mint()

    import inspect

    sig = inspect.signature(shaker_share.resolve_share)
    params = list(sig.parameters)
    assert params[0] == "token", "resolve_share 의 첫 인자는 토큰이어야 한다"
    # pet_id 는 keyword-only 이고 이름이 expected_* 다 — 조회키가 아니라 검사값임을
    # 서명 수준에서 못 박는다.
    assert "expected_pet_id" in sig.parameters
    assert "pet_id" not in sig.parameters


@pytest.mark.anyio
async def test_guessed_tokens_are_rejected():
    """추측한 토큰은 전부 404. 형식이 그럴듯해도 마찬가지다."""
    await _mint()

    guesses = [
        "",
        "x",
        "pet_goya",
        "a" * 43,                       # 길이는 맞지만 값이 다르다
        "share",
        "0123456789abcdef0123456789",
        "../../etc/passwd",
        "'; drop table shaker_shares;--",
        "A" * 5000,
    ]
    for g in guesses:
        with pytest.raises(shaker_share.ShareError) as ei:
            await shaker_share.resolve_share(g)
        assert ei.value.status in (400, 404), g
        # 빈 토큰만 400(입력 누락), 나머지는 전부 "없는 링크"로 수렴한다.
        if g:
            assert ei.value.code == "SHARE_NOT_FOUND", g


@pytest.mark.anyio
async def test_token_for_one_pet_cannot_open_another():
    """토큰 A + petId B → 404. 토큰 하나로 pet_id 를 탐색할 수 없다."""
    _sid, token = await _mint(pet_id="pet_a")
    await _mint(pet_id="pet_b")

    ok = await shaker_share.resolve_share(token, expected_pet_id="pet_a")
    assert ok.pet_id == "pet_a"

    with pytest.raises(shaker_share.ShareError) as ei:
        await shaker_share.resolve_share(token, expected_pet_id="pet_b")
    # 불일치를 "유효하지만 다른 펫"이라고 알려 주지 않는다 — 없는 링크와 같은 답.
    assert ei.value.status == 404
    assert ei.value.code == "SHARE_NOT_FOUND"


# ── 2. 토큰 자체의 성질 ───────────────────────────────────────────────────────


def test_tokens_are_unguessable_and_unique():
    tokens = {shaker_share.mint_token() for _ in range(200)}
    assert len(tokens) == 200, "토큰이 충돌했다"
    for t in tokens:
        # 32바이트 → base64url 43자. 256비트 엔트로피.
        assert len(t) >= 40
        assert set(t) <= set(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        )


@pytest.mark.anyio
async def test_raw_token_is_never_stored():
    """
    저장소 어디에도 원문이 없다. DB 덤프가 유출돼도 링크를 열 수 없다.

    이것이 "해시만 저장한다"의 실제 검증이다 — 주석이 아니라 저장된 값을 본다.
    """
    _sid, token = await _mint()

    blob = repr(shaker_share._MOCK_SHARES)
    assert token not in blob, "원문 토큰이 저장소에 남아 있다"
    assert shaker_share.hash_token(token) in blob

    # 해시는 결정적이고, 다른 토큰은 다른 해시를 낸다.
    assert shaker_share.hash_token(token) == shaker_share.hash_token(token)
    assert shaker_share.hash_token(token) != shaker_share.hash_token(token + "x")


@pytest.mark.anyio
async def test_list_shares_never_returns_tokens():
    """소유자 목록에도 토큰이 없다 — 저장하지 않으므로 돌려줄 수가 없다."""
    _sid, token = await _mint()
    rows = await shaker_share.list_shares(user_id=OWNER)
    assert len(rows) == 1
    assert token not in repr(rows)
    assert not hasattr(rows[0], "token")


# ── 3. 폐기 · 만료 ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_revoked_share_stops_opening():
    sid, token = await _mint()
    assert (await shaker_share.resolve_share(token)).pet_id == PET

    assert await shaker_share.revoke_share(user_id=OWNER, share_id=sid) is True

    with pytest.raises(shaker_share.ShareError) as ei:
        await shaker_share.resolve_share(token)
    assert ei.value.status == 410
    assert ei.value.code == "SHARE_REVOKED"


@pytest.mark.anyio
async def test_revoke_is_idempotent():
    """두 번 눌러도 오류가 아니다 — 두 번째는 '바뀐 것 없음'."""
    sid, _token = await _mint()
    assert await shaker_share.revoke_share(user_id=OWNER, share_id=sid) is True
    assert await shaker_share.revoke_share(user_id=OWNER, share_id=sid) is False


@pytest.mark.anyio
async def test_stranger_cannot_revoke_someone_elses_share():
    sid, token = await _mint(user_id=OWNER)

    assert await shaker_share.revoke_share(user_id=OTHER, share_id=sid) is False
    # 여전히 열린다 — 남이 닫지 못했다.
    assert (await shaker_share.resolve_share(token)).pet_id == PET


@pytest.mark.anyio
async def test_expired_share_stops_opening():
    _sid, token = await _mint(ttl_days=7)
    assert (await shaker_share.resolve_share(token)).pet_id == PET

    # 만료 시각을 과거로 밀어 놓는다 (시계를 건드리지 않고 상태만 바꾼다).
    row = shaker_share._MOCK_SHARES[shaker_share.hash_token(token)]
    row["expires_at"] = "2020-01-01T00:00:00+00:00"

    with pytest.raises(shaker_share.ShareError) as ei:
        await shaker_share.resolve_share(token)
    assert ei.value.status == 410
    assert ei.value.code == "SHARE_EXPIRED"


@pytest.mark.anyio
async def test_unparseable_expiry_is_treated_as_expired_not_open():
    """
    손상된 만료 시각은 **열지 않는다**.

    조용히 통과시키면 만료된 링크가 영원히 열린다 — fail open 이다.
    """
    _sid, token = await _mint()
    row = shaker_share._MOCK_SHARES[shaker_share.hash_token(token)]
    row["expires_at"] = "쓰레기값"

    with pytest.raises(shaker_share.ShareError) as ei:
        await shaker_share.resolve_share(token)
    assert ei.value.code == "SHARE_EXPIRED"


@pytest.mark.anyio
async def test_no_expiry_stays_open():
    """인쇄된 QR 은 회수할 수 없으므로 기본은 무기한이다."""
    _sid, token = await _mint()
    row = shaker_share._MOCK_SHARES[shaker_share.hash_token(token)]
    assert row["expires_at"] is None
    assert (await shaker_share.resolve_share(token)).pet_id == PET


# ── 4. 발급 시 입력 검증 ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_breathing_url_must_be_remote_http():
    """
    data: URL 은 거절한다. 통과시키면 공유는 성공하고 재생만 조용히 실패한다.
    """
    for bad in ("data:video/mp4;base64,AAAA", "blob:https://x/y", "  ", "file:///tmp/a.mp4"):
        with pytest.raises(shaker_share.ShareError) as ei:
            await _mint(breathing_url=bad)
        assert ei.value.status == 400


@pytest.mark.anyio
async def test_poster_url_must_be_remote_when_given():
    with pytest.raises(shaker_share.ShareError) as ei:
        await _mint(poster_url="data:image/png;base64,AAAA")
    assert ei.value.code == "POSTER_URL_NOT_REMOTE"

    # 없으면 그냥 없는 것 — 포스터는 선택이다.
    _sid, token = await _mint(poster_url=None)
    assert (await shaker_share.resolve_share(token)).poster_url is None


@pytest.mark.anyio
async def test_owner_can_hold_multiple_shares_and_revoke_one():
    """
    링크를 여러 장 발급할 수 있고, 하나를 닫아도 나머지는 산다.
    (편지 QR / 메모리 박스 QR 을 따로 폐기할 수 있어야 한다.)
    """
    sid_a, token_a = await _mint()
    _sid_b, token_b = await _mint()

    await shaker_share.revoke_share(user_id=OWNER, share_id=sid_a)

    with pytest.raises(shaker_share.ShareError):
        await shaker_share.resolve_share(token_a)
    assert (await shaker_share.resolve_share(token_b)).pet_id == PET


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
