"""
소유자 공유 링크 발급 — 인증·소유권·중복 펫 금지.

소유자 UI(pet/Memorial 영역의 QR 카드)가 실제로 부르는 경로다.

여기서 고정하는 것:
  * 인증 없이는 발급할 수 없다.
  * 남의 펫으로 발급할 수 없다.
  * **펫 데이터를 새로 만들지 않는다** — 기존 pet_id 를 가리키기만 한다.
  * 구독을 요구하지 않는다 (BREATHING 은 무료이므로 공유도 무료다).
  * 토큰은 응답에서 한 번만 나오고 목록에는 없다.
  * 발급이 생성을 유발하지 않는다.
"""

from __future__ import annotations

import functools

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import shaker_v1
from backend.services import generated_motions_service as motions_svc
from backend.services import premium_purchase, shaker_rate_limit, shaker_share

from .conftest import ASGITestClient, follow_shaker_asset

OWNER = "owner@example.com"
STRANGER = "stranger@example.com"
PET = "pet_goya"
BREATH = "https://cdn.test/goya/idle_loop.mp4"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("SHAKER_RATE_LIMIT_ENABLED", "0")
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    shaker_share.__reset_for_tests()
    shaker_rate_limit.__reset_for_tests()
    motions_svc._MOCK_MOTIONS.clear()
    premium_purchase.__reset_for_tests()
    yield
    shaker_share.__reset_for_tests()
    shaker_rate_limit.__reset_for_tests()
    motions_svc._MOCK_MOTIONS.clear()
    premium_purchase.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(shaker_v1.router, prefix="/api")
    return ASGITestClient(app)


def _sync(afn, *args, **kwargs):
    return anyio.run(functools.partial(afn, *args, **kwargs))


def _auth(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer test:{user}"}


def _create(client: ASGITestClient, user: str = OWNER, **body):
    payload = {"pet_id": PET, "breathing_url": BREATH, "pet_name": "고야", **body}
    return client.post("/api/v1/shaker/share", json=payload, headers=_auth(user))


# ── 인증 · 소유권 ────────────────────────────────────────────────────────────


def test_creating_a_share_requires_auth(client: ASGITestClient):
    r = client.post(
        "/api/v1/shaker/share",
        json={"pet_id": PET, "breathing_url": BREATH},
    )
    assert r.status_code == 401


def test_owner_can_create_share(client: ASGITestClient):
    r = _create(client)
    assert r.status_code == 200, r.text
    b = r.json()

    assert b["pet_id"] == PET
    assert b["share_id"].startswith("shr_")
    assert len(b["token"]) >= 40
    # QR 에 그대로 넣을 경로 — petId 와 토큰이 모두 들어 있다.
    assert b["share_path"].startswith("/shaker?petId=")
    assert f"share={b['token']}" in b["share_path"]


def test_created_share_opens_publicly(client: ASGITestClient):
    """발급 → 로그인 없이 열린다. 이것이 전체 흐름의 계약이다."""
    token = _create(client).json()["token"]

    r = client.get("/api/v1/shaker/pet", params={"share": token})
    assert r.status_code == 200
    assert follow_shaker_asset(client, r.json()["breathing_url"]) == BREATH
    assert r.json()["pet_name"] == "고야"


def test_stranger_cannot_create_share_for_someone_elses_pet(client: ASGITestClient):
    """
    **핵심 회귀**: 남의 pet_id 로 공유 링크를 만들 수 없다.

    만들 수 있다면 남의 펫을 공개 인터넷에 올리는 것과 같다.
    """
    assert _create(client, user=OWNER).status_code == 200  # 소유권 확립(TOFU)
    r = _create(client, user=STRANGER)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "PET_NOT_OWNED"


def test_subscription_is_not_required_to_share(client: ASGITestClient):
    """BREATHING 은 무료다 — 그것을 공유하는 것도 무료다."""
    r = _create(client)
    assert r.status_code == 200


# ── 펫 데이터를 새로 만들지 않는다 ───────────────────────────────────────────


def test_share_creation_does_not_create_pet_data(client: ASGITestClient):
    """
    **핵심 회귀**: 공유는 **가리키기**일 뿐이다.

    물리 주문·QR 이 canonical petId 를 참조해야 한다는 규칙(핸드오프 Phase 13)이
    여기서 시작된다. 공유가 펫을 복제하면 그 규칙이 처음부터 깨진다.
    """
    before = dict(motions_svc._MOCK_MOTIONS)
    r = _create(client)
    assert r.status_code == 200

    # 생성물 저장소가 그대로다 — 새 펫도 새 모션도 만들지 않았다.
    assert motions_svc._MOCK_MOTIONS == before
    # 공유 행은 주어진 pet_id 를 그대로 가리킨다.
    rows = _sync(shaker_share.list_shares, user_id=OWNER)
    assert [row.pet_id for row in rows] == [PET]


def test_multiple_shares_point_at_the_same_pet(client: ASGITestClient):
    """편지 QR / 메모리 박스 QR 을 따로 발급해도 펫은 하나다."""
    a = _create(client).json()
    b = _create(client).json()

    assert a["token"] != b["token"]
    assert a["share_id"] != b["share_id"]
    assert a["pet_id"] == b["pet_id"] == PET

    rows = _sync(shaker_share.list_shares, user_id=OWNER)
    assert len(rows) == 2
    assert {row.pet_id for row in rows} == {PET}


# ── 입력 검증 ────────────────────────────────────────────────────────────────


def test_breathing_url_must_be_remote(client: ASGITestClient):
    """data: URL 을 통과시키면 공유는 성공하고 재생만 조용히 실패한다."""
    r = _create(client, breathing_url="data:video/mp4;base64,AAAA")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "BREATHING_URL_NOT_REMOTE"


def test_breathing_url_is_required(client: ASGITestClient):
    r = client.post(
        "/api/v1/shaker/share",
        json={"pet_id": PET, "breathing_url": ""},
        headers=_auth(OWNER),
    )
    assert r.status_code == 400


# ── 목록 · 폐기 ──────────────────────────────────────────────────────────────


def test_list_never_returns_tokens(client: ASGITestClient):
    """서버가 원문을 저장하지 않으므로 목록도 돌려줄 수 없다."""
    token = _create(client).json()["token"]

    r = client.get("/api/v1/shaker/shares", headers=_auth(OWNER))
    assert r.status_code == 200
    assert token not in r.text
    assert "token" not in r.json()["shares"][0]


def test_list_only_shows_my_shares(client: ASGITestClient):
    _create(client, user=OWNER)
    r = client.get("/api/v1/shaker/shares", headers=_auth(STRANGER))
    assert r.status_code == 200
    assert r.json()["shares"] == []


def test_owner_can_revoke_and_link_closes(client: ASGITestClient):
    b = _create(client).json()
    token, sid = b["token"], b["share_id"]

    assert client.get("/api/v1/shaker/pet", params={"share": token}).status_code == 200

    r = client.post(f"/api/v1/shaker/share/{sid}/revoke", headers=_auth(OWNER))
    assert r.status_code == 200
    assert r.json()["revoked"] is True

    closed = client.get("/api/v1/shaker/pet", params={"share": token})
    assert closed.status_code == 410
    assert closed.json()["detail"]["code"] == "SHARE_REVOKED"


def test_revoke_is_idempotent_over_http(client: ASGITestClient):
    sid = _create(client).json()["share_id"]
    assert client.post(f"/api/v1/shaker/share/{sid}/revoke", headers=_auth(OWNER)).json()[
        "revoked"
    ] is True
    assert client.post(f"/api/v1/shaker/share/{sid}/revoke", headers=_auth(OWNER)).json()[
        "revoked"
    ] is False


def test_stranger_cannot_revoke_my_share(client: ASGITestClient):
    b = _create(client).json()
    r = client.post(f"/api/v1/shaker/share/{b['share_id']}/revoke", headers=_auth(STRANGER))
    assert r.json()["revoked"] is False
    # 여전히 열린다.
    assert client.get("/api/v1/shaker/pet", params={"share": b["token"]}).status_code == 200


def test_revoke_requires_auth(client: ASGITestClient):
    sid = _create(client).json()["share_id"]
    assert client.post(f"/api/v1/shaker/share/{sid}/revoke").status_code == 401
