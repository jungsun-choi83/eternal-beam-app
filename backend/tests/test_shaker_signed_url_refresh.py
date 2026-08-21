"""
서명 URL 재발급 — **인쇄된 QR 이 서명보다 오래 산다.**

문제: 업로드 시점 서명은 7일짜리다(services/supabase_assets.py). QR 은 편지·
메모리 박스에 인쇄되어 나간다. 8일째에 QR 을 찍은 사람은 유효한 공유 토큰을
들고 있는데도 영상이 재생되지 않는다 — 링크도 자산도 살아 있고 그 사이의 서명만
죽은 상태다.

해결: 해석할 때마다 **저장된 객체 경로**에서 새 서명을 만든다. 경로는 만료되지
않으므로 공유 링크의 수명이 서명 수명과 분리된다.

여기서 고정하는 것:
  * 저장된(만료된) URL 이 응답에 **그대로 나가지 않는다**.
  * 토큰 검증·폐기는 **하나도 바뀌지 않았다** — 재서명은 검증을 통과한 뒤의 일이다.
  * 재서명은 읽기 서명 생성일 뿐 업로드도 생성도 아니다.
  * 재서명이 불가능한 환경에서는 원본으로 폴백해 재생이 멈추지 않는다.
"""

from __future__ import annotations

import functools

import anyio
import pytest
from fastapi import FastAPI

from backend.models.hybrid_business import GeneratedMotion
from backend.routers import shaker_v1
from backend.services import asset_url_refresh, behavior_preferences
from backend.services import generated_motions_service as motions_svc
from backend.services import premium_entitlement, premium_purchase
from backend.services import shaker_rate_limit, shaker_share

from .conftest import ASGITestClient, follow_shaker_asset

OWNER = "owner@example.com"
PET = "pet_goya"
BUCKET = "user-assets"
OBJ = f"{OWNER}/{PET}/idle_loop.mp4"

#: 업로드 시점에 저장된 서명 URL — 토큰이 이미 만료됐다고 가정한다.
STALE = (
    f"https://proj.supabase.co/storage/v1/object/sign/{BUCKET}/{OBJ}"
    "?token=EXPIRED_SEVEN_DAYS_AGO"
)
FRESH = (
    f"https://proj.supabase.co/storage/v1/object/sign/{BUCKET}/{OBJ}"
    "?token=FRESHLY_SIGNED"
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("SHAKER_RATE_LIMIT_ENABLED", "0")
    monkeypatch.delenv("SHAKER_DOUBLE_TAP_POLICY", raising=False)
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


def _sync(afn, *args, **kwargs):
    return anyio.run(functools.partial(afn, *args, **kwargs))


class FakeStorage:
    """create_signed_url 호출을 기록하는 최소 스토리지. 업로드 경로는 **없다**."""

    def __init__(self, sink: list[tuple[str, str, int]], bucket: str):
        self.sink = sink
        self.bucket = bucket

    def create_signed_url(self, path: str, seconds: int):
        self.sink.append((self.bucket, path, seconds))
        return {"signedURL": f"https://proj.supabase.co/storage/v1/object/sign/"
                             f"{self.bucket}/{path}?token=FRESHLY_SIGNED"}


class FakeClient:
    def __init__(self, sink: list[tuple[str, str, int]]):
        self.sink = sink
        self.storage = self

    def from_(self, bucket: str) -> FakeStorage:
        return FakeStorage(self.sink, bucket)


@pytest.fixture
def signings(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, int]]:
    """서명 호출 기록. Supabase 가 설정된 것처럼 보이게 한다."""
    sink: list[tuple[str, str, int]] = []
    from backend.services import supabase_assets

    monkeypatch.setattr(supabase_assets, "get_client", lambda: FakeClient(sink))
    return sink


def _mint(breathing: str = STALE, poster: str | None = None) -> str:
    _sid, token = _sync(
        shaker_share.create_share,
        user_id=OWNER, pet_id=PET, breathing_url=breathing,
        pet_name="고야", poster_url=poster,
    )
    return token


def _member(monkeypatch) -> None:
    async def _get(_uid):
        return premium_entitlement.EntitlementState(
            entitled=True, status="active", enforced=True
        )

    monkeypatch.setattr(premium_entitlement, "get_entitlement", _get)


def _body(client: ASGITestClient, token: str) -> dict:
    r = client.get("/api/v1/shaker/pet", params={"share": token})
    assert r.status_code == 200, r.text
    return r.json()


def _played(client: ASGITestClient, url: str | None) -> str:
    """
    브라우저가 **실제로 받게 되는** URL.

    공개 응답은 /api/v1/shaker/asset 프록시를 싣는다(스토리지 경로에 고객 이메일이
    들어 있어 그대로 노출할 수 없다). 재서명 검증은 그 프록시가 가리키는 최종
    대상을 봐야 의미가 있다.
    """
    return follow_shaker_asset(client, url)


# ── URL 파싱 ─────────────────────────────────────────────────────────────────


def test_parses_supabase_storage_urls():
    for url in (
        f"https://proj.supabase.co/storage/v1/object/sign/{BUCKET}/{OBJ}?token=x",
        f"https://proj.supabase.co/storage/v1/object/public/{BUCKET}/{OBJ}",
        f"https://proj.supabase.co/storage/v1/object/authenticated/{BUCKET}/{OBJ}",
        f"/storage/v1/object/sign/{BUCKET}/{OBJ}?token=x",
    ):
        obj = asset_url_refresh.parse_storage_object(url)
        assert obj is not None, url
        assert obj.bucket == BUCKET
        assert obj.path == OBJ


def test_ignores_urls_it_cannot_resign():
    """외부 CDN 등은 파싱되지 않는다 — 재서명 대상이 아니다."""
    for url in ("", None, "https://cdn.example.com/a/b.mp4", "data:video/mp4;base64,AA"):
        assert asset_url_refresh.parse_storage_object(url) is None


def test_rejects_path_traversal():
    bad = f"https://proj.supabase.co/storage/v1/object/sign/{BUCKET}/../../secret.mp4?token=x"
    assert asset_url_refresh.parse_storage_object(bad) is None


def test_query_string_is_dropped():
    """만료된 토큰이 바로 쿼리스트링이다 — 경로만 남겨야 한다."""
    obj = asset_url_refresh.parse_storage_object(STALE)
    assert obj is not None
    assert "token" not in obj.path
    assert "EXPIRED" not in obj.path


# ── 해석 시 재서명 ───────────────────────────────────────────────────────────


def test_expired_breathing_url_is_replaced_with_fresh_url(client, signings):
    """**핵심 회귀**: 저장된 만료 URL 이 응답에 그대로 나가지 않는다."""
    token = _mint()

    body = _body(client, token)
    played = _played(client, body["breathing_url"])
    assert played == FRESH
    assert played != STALE
    assert "EXPIRED_SEVEN_DAYS_AGO" not in played
    assert (BUCKET, OBJ, asset_url_refresh.DEFAULT_TTL_SECONDS) in signings


def test_every_resolve_signs_again(client, signings):
    """
    한 번 서명하고 캐시하지 않는다 — 캐시하면 만료 문제가 그대로 돌아온다.

    /pet 해석마다 최소 1회. 프록시(/asset)를 따라가면 그 시점에 또 서명하므로
    정확한 횟수가 아니라 **매번 늘어나는가**를 본다.
    """
    token = _mint()
    counts = []
    for _ in range(3):
        _body(client, token)
        counts.append(len(signings))
    assert counts == sorted(counts) and counts[0] >= 1
    assert len(set(counts)) == 3, f"해석마다 서명이 늘지 않았다: {counts}"


def test_poster_url_is_refreshed_too(client, signings):
    poster_obj = f"{OWNER}/{PET}/poster.png"
    poster = f"https://proj.supabase.co/storage/v1/object/sign/{BUCKET}/{poster_obj}?token=OLD"
    token = _mint(poster=poster)

    body = _body(client, token)
    assert "OLD" not in body["poster_url"]
    assert any(p == poster_obj for _b, p, _s in signings)


def test_action_urls_are_refreshed(client, signings, monkeypatch):
    """generated_motions 에 저장된 액션 URL 도 같은 문제를 갖는다."""
    action_obj = f"{OWNER}/{PET}/any_COME_CLOSER.mp4"
    key = motions_svc._motion_key(OWNER, PET, "any", "COME_CLOSER")
    motions_svc._MOCK_MOTIONS[key] = GeneratedMotion(
        user_id=OWNER, pet_id=PET, place_id="any", action_id="COME_CLOSER",
        video_url=f"https://proj.supabase.co/storage/v1/object/sign/{BUCKET}/{action_obj}?token=OLD",
    )
    _member(monkeypatch)
    token = _mint()

    body = _body(client, token)
    assert body["double_tap_action_id"] == "COME_CLOSER"
    url = _played(client, body["actions"][0]["url"])
    assert "OLD" not in url
    assert "FRESHLY_SIGNED" in url
    assert any(p == action_obj for _b, p, _s in signings)


def test_ttl_is_configurable(client, signings, monkeypatch):
    monkeypatch.setenv("SHAKER_SIGNED_URL_TTL_SECONDS", "120")
    token = _mint()
    _body(client, token)
    assert signings[0][2] == 120


def test_bad_ttl_falls_back_to_default(monkeypatch):
    for bad in ("0", "-5", "abc", ""):
        monkeypatch.setenv("SHAKER_SIGNED_URL_TTL_SECONDS", bad)
        assert asset_url_refresh.ttl_seconds() == asset_url_refresh.DEFAULT_TTL_SECONDS


# ── 폴백 ─────────────────────────────────────────────────────────────────────


def test_falls_back_to_stored_url_when_signing_unavailable(client):
    """
    Supabase 미설정(로컬/테스트)에서는 원본을 그대로 쓴다.

    재서명은 개선이지 전제가 아니다 — 못 한다고 재생을 막으면 지금 잘 돌아가는
    자산까지 죽인다.
    """
    token = _mint()
    body = _body(client, token)  # signings 픽스처를 쓰지 않는다 = 클라이언트 없음
    assert _played(client, body["breathing_url"]) == STALE


def test_external_cdn_url_passes_through(client, signings):
    """재서명 대상이 아닌 URL 은 건드리지 않는다."""
    external = "https://cdn.example.com/goya/idle.mp4"
    token = _mint(breathing=external)
    assert _played(client, _body(client, token)["breathing_url"]) == external
    # 프록시 경로도 1회 해석하므로 서명 시도 자체가 없었는지만 본다.
    assert signings == []


def test_signing_failure_falls_back_to_stored_url(client, monkeypatch):
    """서명 API 가 죽어도 재생 시도는 계속된다."""
    from backend.services import supabase_assets

    class Boom:
        storage = None

        def from_(self, _b):
            raise RuntimeError("스토리지 장애")

    boom = Boom()
    boom.storage = boom
    monkeypatch.setattr(supabase_assets, "get_client", lambda: boom)

    token = _mint()
    assert _played(client, _body(client, token)["breathing_url"]) == STALE


# ── 검증·폐기는 바뀌지 않았다 ────────────────────────────────────────────────


def test_revoked_share_is_still_blocked_and_signs_nothing(client, signings):
    """
    **핵심 회귀**: 재서명이 토큰 검증을 우회하지 않는다.

    폐기된 링크는 서명 호출조차 일어나지 않아야 한다 — 검증 뒤에 재서명이 오는
    순서가 지켜지고 있다는 뜻이다.
    """
    sid, token = _sync(
        shaker_share.create_share, user_id=OWNER, pet_id=PET, breathing_url=STALE
    )
    _sync(shaker_share.revoke_share, user_id=OWNER, share_id=sid)

    r = client.get("/api/v1/shaker/pet", params={"share": token})
    assert r.status_code == 410
    assert r.json()["detail"]["code"] == "SHARE_REVOKED"
    assert signings == []


def test_invalid_and_mismatched_shares_sign_nothing(client, signings):
    token = _mint()
    for params in (
        {"share": "a" * 43},                       # 없는 토큰
        {"share": "short"},                        # 형식 오류
        {"share": token, "pet_id": "pet_other"},   # petId 바꿔치기
    ):
        r = client.get("/api/v1/shaker/pet", params=params)
        assert r.status_code == 404, params
    assert signings == []


def test_expired_share_is_still_blocked(client, signings):
    token = _mint()
    shaker_share._MOCK_SHARES[shaker_share.hash_token(token)]["expires_at"] = (
        "2020-01-01T00:00:00+00:00"
    )
    r = client.get("/api/v1/shaker/pet", params={"share": token})
    assert r.status_code == 410
    assert r.json()["detail"]["code"] == "SHARE_EXPIRED"
    assert signings == []


# ── 재서명은 생성이 아니다 ───────────────────────────────────────────────────


def test_refresh_never_uploads_or_generates(client, signings, monkeypatch):
    """
    재서명 경로가 스토리지에 **쓰지 않는다.**

    FakeClient 에는 upload 메서드가 아예 없다 — 호출하려 했다면 AttributeError 로
    죽는다. 생성 진입점도 함께 막아 둔다.
    """
    from backend.services import generation_queue, premium_generation, supabase_assets

    async def _boom(*_a, **_k):
        raise AssertionError("Shaker 재서명이 업로드/생성을 호출했다")

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", _boom)
    for mod, attr in (
        (premium_purchase, "purchase"),
        (premium_generation, "submit_action"),
        (generation_queue, "enqueue_generation"),
    ):
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, _boom)

    token = _mint()
    for _ in range(3):
        assert _played(client, _body(client, token)["breathing_url"]) == FRESH
