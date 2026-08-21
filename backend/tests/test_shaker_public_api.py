"""
공개 Shaker API — 노출 범위·인가·레이트 리밋.

지키려는 것:
  * 응답이 **허용 목록**이다. 계정·지갑·구독·결제·주문·프로바이더가 절대 나가지 않는다.
  * 추측한 petId/토큰으로는 아무것도 열리지 않는다.
  * 폐기·만료된 링크는 즉시 닫힌다.
  * BREATHING 은 언제나 나간다 — 프리미엄 조회가 실패해도.
  * 더블탭 정책은 격리돼 있고 기본은 **membership** 이다 (PM 확정).
    세부 규칙(구독 ∩ READY ∩ 선호 ON)은 test_shaker_membership_policy.py 참고.
"""

from __future__ import annotations

import functools
import json

import anyio
import pytest
from fastapi import FastAPI

from backend.models.hybrid_business import GeneratedMotion
from backend.routers import shaker_v1
from backend.services import generated_motions_service as motions_svc
from backend.services import behavior_preferences, premium_purchase
from backend.services import shaker_policy, shaker_rate_limit, shaker_share

from .conftest import ASGITestClient, follow_shaker_asset

OWNER = "owner@example.com"
PET = "pet_goya"
BREATH = "https://cdn.test/goya/idle_loop.mp4"
POSTER = "https://cdn.test/goya/poster.png"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.delenv("SHAKER_DOUBLE_TAP_POLICY", raising=False)
    monkeypatch.delenv("SHAKER_PUBLIC_RATE_LIMIT", raising=False)
    monkeypatch.setenv("SHAKER_RATE_LIMIT_ENABLED", "0")  # 리밋 전용 테스트에서만 켠다
    shaker_share.__reset_for_tests()
    shaker_rate_limit.__reset_for_tests()
    motions_svc._MOCK_MOTIONS.clear()
    premium_purchase.__reset_for_tests()
    behavior_preferences.__reset_for_tests()
    yield
    shaker_share.__reset_for_tests()
    shaker_rate_limit.__reset_for_tests()
    motions_svc._MOCK_MOTIONS.clear()
    premium_purchase.__reset_for_tests()
    behavior_preferences.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(shaker_v1.router, prefix="/api")
    return ASGITestClient(app)


def _sync(afn, *args, **kwargs):
    """비동기 헬퍼를 동기 테스트에서 부른다 (ASGITestClient 가 자기 이벤트 루프를 돌린다)."""
    return anyio.run(functools.partial(afn, *args, **kwargs))


def _mint(**kw) -> str:
    _sid, token = _sync(
        shaker_share.create_share,
        user_id=kw.pop("user_id", OWNER),
        pet_id=kw.pop("pet_id", PET),
        breathing_url=kw.pop("breathing_url", BREATH),
        pet_name=kw.pop("pet_name", "고야"),
        poster_url=kw.pop("poster_url", POSTER),
        **kw,
    )
    return token


def _ready(action: str, *, user: str = OWNER, pet: str = PET) -> None:
    """이 액션을 READY 로 심는다 (생성 경로를 타지 않고 결과만 모사)."""
    key = motions_svc._motion_key(user, pet, "any", action)
    motions_svc._MOCK_MOTIONS[key] = GeneratedMotion(
        user_id=user, pet_id=pet, place_id="any", action_id=action,
        video_url=f"https://cdn.test/{action.lower()}.mp4",
    )


def _get(client: ASGITestClient, token: str | None = None, **params):
    qs = {**({"share": token} if token is not None else {}), **params}
    return client.get("/api/v1/shaker/pet", params=qs)


# ── 노출 범위 (허용 목록) ─────────────────────────────────────────────────────


#: 응답에 나와도 되는 최상위 키 — 이 목록 밖의 키가 생기면 테스트가 깨진다.
ALLOWED_KEYS = {
    "pet_id", "pet_name", "breathing_url", "poster_url", "actions", "double_tap_action_id",
}

#: 절대 나타나면 안 되는 문자열. 값과 키 양쪽을 본다.
FORBIDDEN_SUBSTRINGS = (
    "user_id", "email", "@example.com",
    "subscription", "subscribed", "entitled", "membership",
    "wallet", "credit", "balance",
    "order", "payment", "billing", "toss", "customer_key",
    # 프로바이더 식별자. 맨 "fal" 은 JSON 의 `false` 에 걸려 엉뚱한 이유로 실패하므로
    # 실제로 URL/식별자에 나타나는 형태만 본다.
    "luma", "fal-ai", "fal.media", "provider", "generation_id", "webhook",
    "generating", "missing",
    "token_hash", "share_id",
)


def test_public_payload_is_an_allowlist(client: ASGITestClient):
    token = _mint()
    _ready("COME_CLOSER")

    r = _get(client, token)
    assert r.status_code == 200
    body = r.json()

    assert set(body) == ALLOWED_KEYS, f"예상 밖 필드: {set(body) ^ ALLOWED_KEYS}"
    assert body["pet_id"] == PET
    assert body["pet_name"] == "고야"
    assert follow_shaker_asset(client, body["breathing_url"]) == BREATH
    assert follow_shaker_asset(client, body["poster_url"]) == POSTER


def test_response_never_leaks_private_fields(client: ASGITestClient):
    """
    **핵심 회귀**: 계정/지갑/구독/결제/주문/프로바이더가 응답 본문에 없다.

    키만 보지 않고 직렬화된 본문 전체를 본다 — 중첩 객체나 URL 안에 섞여 나가는
    경우까지 잡기 위해서다.
    """
    token = _mint()
    for a in ("COME_CLOSER", "BLINKING", "TAIL_WAGGING"):
        _ready(a)

    for policy in ("disabled", "free", "ready-only", "membership"):
        import os

        os.environ["SHAKER_DOUBLE_TAP_POLICY"] = policy
        try:
            r = _get(client, token)
            assert r.status_code == 200
            raw = json.dumps(r.json(), ensure_ascii=False).lower()
            for bad in FORBIDDEN_SUBSTRINGS:
                assert bad not in raw, f"정책 {policy} 응답에 {bad!r} 가 새어 나왔다: {raw}"
        finally:
            os.environ.pop("SHAKER_DOUBLE_TAP_POLICY", None)


def test_owner_generation_progress_is_not_exposed(client: ASGITestClient):
    """
    GENERATING/MISSING 은 나가지 않는다. 소유자가 지금 무엇을 만들고 있는지는
    링크를 받은 사람이 알 이유가 없다.
    """
    token = _mint()
    _ready("COME_CLOSER")

    r = _get(client, token)
    body = r.json()
    assert "generating" not in body
    assert "missing" not in body
    # actions 는 READY 인 것만 담는다 — 상태 필드가 따라오지 않는다.
    for a in body["actions"]:
        assert set(a) == {"id", "url"}


def test_response_is_not_cacheable(client: ASGITestClient):
    """폐기를 실효화하려면 중간 캐시가 응답을 들고 있으면 안 된다."""
    token = _mint()
    r = _get(client, token)
    assert r.headers.get("cache-control") == "no-store"


# ── 인가 ─────────────────────────────────────────────────────────────────────


def test_guessed_pet_id_without_token_is_rejected(client: ASGITestClient):
    """
    **핵심 회귀**: petId 만으로는 열 수 없다. share 는 필수 쿼리 파라미터다.
    """
    _mint()
    r = client.get("/api/v1/shaker/pet", params={"pet_id": PET})
    assert r.status_code == 422  # share 누락 — 라우트가 성립하지 않는다

    r = client.get("/api/v1/shaker/pet", params={"petId": PET})
    assert r.status_code == 422


def test_guessed_tokens_are_rejected(client: ASGITestClient):
    _mint()
    for guess in ("", "x", "pet_goya", "a" * 43, "%2e%2e%2f", "null", "undefined"):
        r = _get(client, guess)
        assert r.status_code in (400, 404), guess
        assert "breathing_url" not in r.text


def test_token_cannot_be_pointed_at_another_pet(client: ASGITestClient):
    """토큰 A + petId B → 404. petId 를 바꿔 넣어 얻을 수 있는 것이 없다."""
    token = _mint(pet_id="pet_a")
    _sync(
        shaker_share.create_share,
        user_id=OWNER, pet_id="pet_b", breathing_url="https://cdn.test/b.mp4",
    )

    assert _get(client, token, pet_id="pet_a").status_code == 200
    r = _get(client, token, pet_id="pet_b")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "SHARE_NOT_FOUND"


def test_revoked_and_expired_links_are_closed(client: ASGITestClient):
    sid, token = _sync(
        shaker_share.create_share, user_id=OWNER, pet_id=PET, breathing_url=BREATH
    )
    assert _get(client, token).status_code == 200

    _sync(shaker_share.revoke_share, user_id=OWNER, share_id=sid)
    r = _get(client, token)
    assert r.status_code == 410
    assert r.json()["detail"]["code"] == "SHARE_REVOKED"

    # 만료도 같은 방식으로 닫힌다.
    token2 = _mint()
    shaker_share._MOCK_SHARES[shaker_share.hash_token(token2)]["expires_at"] = (
        "2020-01-01T00:00:00+00:00"
    )
    r2 = _get(client, token2)
    assert r2.status_code == 410
    assert r2.json()["detail"]["code"] == "SHARE_EXPIRED"


def test_public_endpoint_needs_no_auth_header(client: ASGITestClient):
    """로그인 없이 열린다 — 공유 경험의 전제다."""
    token = _mint()
    r = _get(client, token)
    assert r.status_code == 200
    assert follow_shaker_asset(client, r.json()["breathing_url"]) == BREATH


# ── BREATHING 은 언제나 무료 ─────────────────────────────────────────────────


def test_breathing_survives_premium_lookup_failure(client: ASGITestClient, monkeypatch):
    """
    프리미엄 자산 조회가 죽어도 **BREATHING 은 나간다**.

    BREATHING 은 무료이고 프리미엄과 무관하다. 여기서 500 을 던지면 프리미엄
    조회 장애가 무료 경험을 통째로 죽인다.
    """
    token = _mint()

    async def _boom(*_a, **_k):
        raise RuntimeError("자산 저장소 장애")

    monkeypatch.setattr(premium_purchase, "asset_state", _boom)

    r = _get(client, token)
    assert r.status_code == 200
    assert follow_shaker_asset(client, r.json()["breathing_url"]) == BREATH
    assert r.json()["actions"] == []


def test_breathing_served_when_pet_has_no_premium_assets(client: ASGITestClient):
    """무료 사용자 — READY 액션이 하나도 없어도 BREATHING 은 돈다."""
    token = _mint()
    r = _get(client, token)
    assert r.status_code == 200
    assert follow_shaker_asset(client, r.json()["breathing_url"]) == BREATH
    assert r.json()["actions"] == []
    assert r.json()["double_tap_action_id"] is None


# ── 더블탭 정책 (PM 미결 — 격리 확인) ────────────────────────────────────────


def test_default_policy_is_membership(client: ASGITestClient):
    """PM 확정: 기본 정책은 membership 이다."""
    assert shaker_policy.current_policy() == shaker_policy.POLICY_MEMBERSHIP


def test_non_member_gets_no_actions_by_default(client: ASGITestClient):
    """
    **핵심 회귀**: 구독이 없는 펫의 액션은 READY 여도 노출되지 않는다.

    URL 조차 나가지 않는다 — "노출은 하되 재생만 막는다"로 만들면 URL 이 이미
    손에 들어간 뒤라 막은 것이 아니게 된다.
    """
    token = _mint()
    _ready("COME_CLOSER")
    _ready("BLINKING")

    body = _get(client, token).json()
    assert body["actions"] == []
    assert body["double_tap_action_id"] is None
    assert "come_closer" not in json.dumps(body).lower()
    # BREATHING 은 그대로 나간다 — 무료이기 때문이다.
    assert follow_shaker_asset(client, body["breathing_url"]) == BREATH


def test_policy_b_exposes_only_come_closer(client: ASGITestClient, monkeypatch):
    monkeypatch.setenv("SHAKER_DOUBLE_TAP_POLICY", "ready-only")
    token = _mint()
    _ready("COME_CLOSER")
    _ready("BLINKING")

    body = _get(client, token).json()
    assert [a["id"] for a in body["actions"]] == ["COME_CLOSER"]
    assert body["double_tap_action_id"] == "COME_CLOSER"


def test_policy_b_stays_closed_when_come_closer_absent(client: ASGITestClient, monkeypatch):
    monkeypatch.setenv("SHAKER_DOUBLE_TAP_POLICY", "ready-only")
    token = _mint()
    _ready("BLINKING")

    body = _get(client, token).json()
    assert body["actions"] == []
    assert body["double_tap_action_id"] is None


def test_policy_c_requires_owner_entitlement(client: ASGITestClient, monkeypatch):
    """C — 소유자 구독이 유효할 때만. 판정 불가는 거절이다(fail closed)."""
    monkeypatch.setenv("SHAKER_DOUBLE_TAP_POLICY", "membership")
    token = _mint()
    _ready("COME_CLOSER")

    from backend.services import premium_entitlement

    async def _entitled(_uid):
        return premium_entitlement.EntitlementState(entitled=True, status="active", enforced=True)

    monkeypatch.setattr(premium_entitlement, "get_entitlement", _entitled)
    assert _get(client, token).json()["double_tap_action_id"] == "COME_CLOSER"

    async def _expired(_uid):
        return premium_entitlement.EntitlementState(
            entitled=False, status="expired", enforced=True
        )

    monkeypatch.setattr(premium_entitlement, "get_entitlement", _expired)
    body = _get(client, token).json()
    assert body["double_tap_action_id"] is None
    assert body["actions"] == []
    # 구독 상태 자체는 여전히 응답에 없다.
    assert "expired" not in json.dumps(body).lower()

    async def _boom(_uid):
        raise RuntimeError("구독 조회 장애")

    monkeypatch.setattr(premium_entitlement, "get_entitlement", _boom)
    assert _get(client, token).json()["double_tap_action_id"] is None


@pytest.mark.parametrize("policy", ["disabled", "free", "ready-only"])
def test_non_membership_policies_never_read_subscription(
    client: ASGITestClient, monkeypatch, policy: str
):
    """
    멤버십 정책이 **아닐 때**는 구독·선호 테이블을 아예 조회하지 않는다.
    건드리지 않으면 샐 수도 없다.
    """
    from backend.services import premium_entitlement

    monkeypatch.setenv("SHAKER_DOUBLE_TAP_POLICY", policy)

    async def _forbidden(*_a, **_k):
        raise AssertionError(f"{policy} 정책은 구독/선호를 조회해선 안 된다")

    monkeypatch.setattr(premium_entitlement, "get_entitlement", _forbidden)
    monkeypatch.setattr(behavior_preferences, "get_preferences", _forbidden)

    token = _mint()
    _ready("COME_CLOSER")
    assert _get(client, token).status_code == 200


def test_unknown_policy_value_falls_back_to_membership(monkeypatch):
    """
    오타는 기본값(membership)으로 떨어진다.

    membership 은 세 조건을 모두 요구하므로, 여기로 떨어져도 자격 없는 방문자에게
    무언가가 열리지 않는다 — 되돌아가는 자리가 안전한 자리다.
    """
    monkeypatch.setenv("SHAKER_DOUBLE_TAP_POLICY", "reddy-only")
    assert shaker_policy.current_policy() == shaker_policy.POLICY_MEMBERSHIP
    # 자격 정보 없이는 아무것도 허용되지 않는다.
    assert shaker_policy.permitted_action_ids(["COME_CLOSER"]) == []


# ── 레이트 리밋 ──────────────────────────────────────────────────────────────


def test_rate_limit_blocks_repeated_scraping(client: ASGITestClient, monkeypatch):
    monkeypatch.setenv("SHAKER_RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("SHAKER_PUBLIC_RATE_LIMIT", "3")
    shaker_rate_limit.__reset_for_tests()

    token = _mint()
    headers = {"X-Forwarded-For": "203.0.113.9"}

    for _ in range(3):
        r = client.get("/api/v1/shaker/pet", params={"share": token}, headers=headers)
        assert r.status_code == 200

    blocked = client.get("/api/v1/shaker/pet", params={"share": token}, headers=headers)
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "RATE_LIMITED"
    assert int(blocked.headers["retry-after"]) > 0

    # 다른 IP 는 영향을 받지 않는다 — 남을 차단시킬 수 없다.
    other = client.get(
        "/api/v1/shaker/pet",
        params={"share": token},
        headers={"X-Forwarded-For": "198.51.100.4"},
    )
    assert other.status_code == 200


def test_rate_limit_counts_invalid_tokens_too(client: ASGITestClient, monkeypatch):
    """
    무효 토큰도 카운트한다. 안 그러면 실패 요청은 무제한이라 리밋이 무의미해진다.
    """
    monkeypatch.setenv("SHAKER_RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("SHAKER_PUBLIC_RATE_LIMIT", "2")
    shaker_rate_limit.__reset_for_tests()

    headers = {"X-Forwarded-For": "203.0.113.77"}
    for _ in range(2):
        assert client.get(
            "/api/v1/shaker/pet", params={"share": "a" * 43}, headers=headers
        ).status_code == 404
    assert client.get(
        "/api/v1/shaker/pet", params={"share": "a" * 43}, headers=headers
    ).status_code == 429

