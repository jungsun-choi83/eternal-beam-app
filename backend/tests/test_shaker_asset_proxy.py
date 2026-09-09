"""
재생 URL 프록시 — **공개 응답에서 고객 이메일이 사라진다.**

발견 경위: 운영(ops) 발급 테스트가 잡았다. 스토리지 객체 경로가
`{user_id}/{content_id}/idle_loop.mp4` 이고 이 저장소의 user_id 는 **이메일**이다.
서명 URL 을 그대로 응답에 실으면 로그인하지 않은 방문자가 받는 JSON 에 고객
이메일이 들어간다 — "계정/개인정보를 절대 노출하지 않는다"와 정면으로 어긋난다.

기존 누출 테스트가 놓친 이유: 픽스처 URL 이 `https://cdn.test/goya/idle_loop.mp4`
라 이메일이 애초에 없었다. **실제 경로 형태로 테스트하지 않으면 잡히지 않는 종류**다.

해결: 공개 응답은 `/api/v1/shaker/asset?share=…&k=…` 만 싣고, 그 엔드포인트가
302 로 갓 서명한 URL 을 가리킨다. 바이트를 흘려보내지 않으므로 대역폭 비용이 없다.

⚠️ 프록시는 **정책을 다시 검사한다.** 검사하지 않으면 /asset 이 멤버십 게이트를
통째로 우회하는 구멍이 된다 — 이 파일의 절반이 그 회귀를 막는다.
"""

from __future__ import annotations

import functools

import anyio
import pytest
from fastapi import FastAPI

from backend.models.hybrid_business import GeneratedMotion
from backend.routers import shaker_v1
from backend.services import behavior_preferences
from backend.services import generated_motions_service as motions_svc
from backend.services import premium_entitlement, premium_purchase
from backend.services import shaker_rate_limit, shaker_share

from .conftest import ASGITestClient

#: 실제 저장소와 같은 형태 — user_id 가 이메일이고 경로에 그대로 들어간다.
CUSTOMER = "customer@example.com"
CONTENT = "abc123"
PET = f"pet_{CONTENT}"
BUCKET = "user-assets"
BREATH_OBJ = f"{CUSTOMER}/{CONTENT}/idle_loop.mp4"
BREATH = f"https://proj.supabase.co/storage/v1/object/sign/{BUCKET}/{BREATH_OBJ}?token=OLD"
CC_OBJ = f"{CUSTOMER}/{CONTENT}/any_COME_CLOSER.mp4"
CC = f"https://proj.supabase.co/storage/v1/object/sign/{BUCKET}/{CC_OBJ}?token=OLD"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("SHAKER_RATE_LIMIT_ENABLED", "0")
    monkeypatch.delenv("SHAKER_DOUBLE_TAP_POLICY", raising=False)
    monkeypatch.delenv("SHAKER_PROXY_ASSET_URLS", raising=False)
    shaker_share.__reset_for_tests()
    shaker_rate_limit.__reset_for_tests()
    behavior_preferences.__reset_for_tests()
    motions_svc._MOCK_MOTIONS.clear()
    premium_purchase.__reset_for_tests()
    yield
    shaker_share.__reset_for_tests()
    shaker_rate_limit.__reset_for_tests()
    behavior_preferences.__reset_for_tests()
    motions_svc._MOCK_MOTIONS.clear()
    premium_purchase.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(shaker_v1.router, prefix="/api")
    return ASGITestClient(app)


class FakeStorage:
    def __init__(self, bucket: str):
        self.bucket = bucket

    def create_signed_url(self, path: str, seconds: int):
        return {
            "signedURL": f"https://proj.supabase.co/storage/v1/object/sign/"
                         f"{self.bucket}/{path}?token=FRESH"
        }


class FakeClient:
    def __init__(self):
        self.storage = self

    def from_(self, bucket: str) -> FakeStorage:
        return FakeStorage(bucket)


@pytest.fixture(autouse=True)
def _storage(monkeypatch: pytest.MonkeyPatch):
    from backend.services import supabase_assets

    monkeypatch.setattr(supabase_assets, "get_client", lambda: FakeClient())


def _sync(afn, *args, **kwargs):
    return anyio.run(functools.partial(afn, *args, **kwargs))


def _mint() -> str:
    _sid, token = _sync(
        shaker_share.create_share,
        user_id=CUSTOMER, pet_id=PET, breathing_url=BREATH, pet_name="고야",
    )
    return token


def _ready_come_closer() -> None:
    key = motions_svc._motion_key(CUSTOMER, PET, "any", "COME_CLOSER")
    motions_svc._MOCK_MOTIONS[key] = GeneratedMotion(
        user_id=CUSTOMER, pet_id=PET, place_id="any", action_id="COME_CLOSER", video_url=CC,
    )


def _member(monkeypatch, entitled: bool = True) -> None:
    async def _get(_uid):
        return premium_entitlement.EntitlementState(
            entitled=entitled, status="active" if entitled else "expired", enforced=True
        )

    monkeypatch.setattr(premium_entitlement, "get_entitlement", _get)


# ── 누출 회귀 ────────────────────────────────────────────────────────────────


def test_public_payload_never_contains_customer_email(client: ASGITestClient, monkeypatch):
    """**핵심 회귀**: 실제 저장소 경로 형태에서도 이메일이 나가지 않는다."""
    token = _mint()
    _ready_come_closer()
    _member(monkeypatch)

    r = client.get("/api/v1/shaker/pet", params={"share": token})
    assert r.status_code == 200
    assert CUSTOMER not in r.text
    assert "@example.com" not in r.text
    # 스토리지 호스트·객체 경로도 본문에 없다.
    assert "supabase.co" not in r.text
    assert "/storage/v1/" not in r.text


def test_payload_urls_are_proxy_paths(client: ASGITestClient, monkeypatch):
    token = _mint()
    _ready_come_closer()
    _member(monkeypatch)

    body = client.get("/api/v1/shaker/pet", params={"share": token}).json()
    assert body["breathing_url"].startswith("/api/v1/shaker/asset?")
    assert "k=breathing" in body["breathing_url"]
    assert body["actions"][0]["url"].startswith("/api/v1/shaker/asset?")
    assert "k=COME_CLOSER" in body["actions"][0]["url"]


def test_proxy_redirects_to_a_freshly_signed_url(client: ASGITestClient):
    token = _mint()
    r = client.get("/api/v1/shaker/asset", params={"share": token, "k": "breathing"})
    assert r.status_code == 302
    loc = r.headers["location"]
    assert BREATH_OBJ in loc
    assert "token=FRESH" in loc      # 저장된 OLD 서명이 아니다
    assert "token=OLD" not in loc
    assert r.headers["cache-control"] == "no-store"


# ── 프록시가 정책을 우회하지 않는다 ─────────────────────────────────────────


def test_non_member_cannot_fetch_action_through_proxy(client: ASGITestClient, monkeypatch):
    """
    **핵심 회귀**: /asset 이 멤버십 게이트를 우회하면 정책 전체가 무의미해진다.

    액션 id 를 알아도(고정된 5개다) 자격이 없으면 404 다.
    """
    token = _mint()
    _ready_come_closer()
    _member(monkeypatch, entitled=False)

    r = client.get("/api/v1/shaker/asset", params={"share": token, "k": "COME_CLOSER"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "ASSET_NOT_AVAILABLE"


def test_member_can_fetch_action_through_proxy(client: ASGITestClient, monkeypatch):
    token = _mint()
    _ready_come_closer()
    _member(monkeypatch)

    r = client.get("/api/v1/shaker/asset", params={"share": token, "k": "COME_CLOSER"})
    assert r.status_code == 302
    assert CC_OBJ in r.headers["location"]


def test_preference_off_blocks_the_proxy_too(client: ASGITestClient, monkeypatch):
    """소유자가 끈 행동은 프록시로도 받을 수 없다."""
    token = _mint()
    _ready_come_closer()
    _member(monkeypatch)
    _sync(
        behavior_preferences.set_preference,
        user_id=CUSTOMER, pet_id=PET, action_id="COME_CLOSER", enabled=False,
    )

    assert client.get(
        "/api/v1/shaker/asset", params={"share": token, "k": "COME_CLOSER"}
    ).status_code == 404


def test_unknown_kind_is_rejected(client: ASGITestClient):
    token = _mint()
    for k in ("", "secret", "../../etc/passwd", "BLINKING"):
        r = client.get("/api/v1/shaker/asset", params={"share": token, "k": k})
        assert r.status_code == 404, k


# ── 토큰 검증은 그대로다 ─────────────────────────────────────────────────────


def test_proxy_respects_revoked_shares(client: ASGITestClient):
    sid, token = _sync(
        shaker_share.create_share,
        user_id=CUSTOMER, pet_id=PET, breathing_url=BREATH,
    )
    assert client.get(
        "/api/v1/shaker/asset", params={"share": token, "k": "breathing"}
    ).status_code == 302

    _sync(shaker_share.revoke_share, user_id=CUSTOMER, share_id=sid)
    r = client.get("/api/v1/shaker/asset", params={"share": token, "k": "breathing"})
    assert r.status_code == 410
    assert r.json()["detail"]["code"] == "SHARE_REVOKED"


def test_proxy_respects_expiry_and_bad_tokens(client: ASGITestClient):
    token = _mint()
    shaker_share._MOCK_SHARES[shaker_share.hash_token(token)]["expires_at"] = (
        "2020-01-01T00:00:00+00:00"
    )
    assert client.get(
        "/api/v1/shaker/asset", params={"share": token, "k": "breathing"}
    ).status_code == 410

    for bad in ("a" * 43, "short", ""):
        r = client.get("/api/v1/shaker/asset", params={"share": bad, "k": "breathing"})
        assert r.status_code in (400, 404), bad


def test_proxy_respects_pet_id_mismatch(client: ASGITestClient):
    token = _mint()
    r = client.get(
        "/api/v1/shaker/asset",
        params={"share": token, "k": "breathing", "pet_id": "pet_other"},
    )
    assert r.status_code == 404


def test_proxy_is_rate_limited(client: ASGITestClient, monkeypatch):
    monkeypatch.setenv("SHAKER_RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("SHAKER_PUBLIC_RATE_LIMIT", "2")
    shaker_rate_limit.__reset_for_tests()

    token = _mint()
    headers = {"X-Forwarded-For": "203.0.113.200"}
    for _ in range(2):
        assert client.get(
            "/api/v1/shaker/asset", params={"share": token, "k": "breathing"}, headers=headers
        ).status_code == 302
    assert client.get(
        "/api/v1/shaker/asset", params={"share": token, "k": "breathing"}, headers=headers
    ).status_code == 429


# ── 되돌리기 스위치 ──────────────────────────────────────────────────────────


def test_proxy_can_be_disabled(client: ASGITestClient, monkeypatch):
    """
    끄면 서명 URL 이 그대로 실린다 — 브라우저 QA 에서 문제가 나면 한 줄로 되돌린다.

    ⚠️ 끄면 이메일 노출이 돌아온다. 되돌리기 스위치이지 권장 설정이 아니다.
    """
    monkeypatch.setenv("SHAKER_PROXY_ASSET_URLS", "0")
    token = _mint()

    body = client.get("/api/v1/shaker/pet", params={"share": token}).json()
    assert body["breathing_url"].startswith("https://proj.supabase.co/")
    assert "token=FRESH" in body["breathing_url"]


def test_proxy_never_generates(client: ASGITestClient, monkeypatch):
    from backend.services import generation_queue, premium_generation

    async def _boom(*_a, **_k):
        raise AssertionError("프록시가 생성을 호출했다")

    for mod, attr in (
        (premium_purchase, "purchase"),
        (premium_generation, "submit_action"),
        (generation_queue, "enqueue_generation"),
    ):
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, _boom)

    token = _mint()
    for _ in range(3):
        client.get("/api/v1/shaker/asset", params={"share": token, "k": "breathing"})
        client.get("/api/v1/shaker/asset", params={"share": token, "k": "COME_CLOSER"})
