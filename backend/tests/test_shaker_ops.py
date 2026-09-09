"""
판매자/운영 Shaker 도구 — 인가 · canonical petId · QR 안전성.

소유 모델:
    ETERNAL BEAM 이 소유  Shaker 앱 · API · 펫 조회 · 영상 접근 · 토큰 · **QR 생성** · 운영 도구
    사용자가 소유          펫 프로필 · 펫 콘텐츠 · 생성된 경험 · 물리 편지/카드 · **그 펫으로 가는 개인 링크**

여기서 고정하는 것:
  * 운영 경로는 인증 **위에** allowlist 를 요구한다. 미설정이면 전원 403.
  * 운영자가 펫을 **만들지 않는다** — 이미 있는 canonical petId 를 가리킬 뿐이다.
  * 공유의 소유자는 **고객**이다. 운영자가 만들어도 고객 소유로 기록된다.
  * QR 은 Shaker URL 만 인코딩한다 — Supabase/영상 주소는 거절된다.
  * 운영 경로도 생성하지 않는다.
"""

from __future__ import annotations

import functools

import anyio
import pytest
from fastapi import FastAPI

from backend.models.hybrid_business import GeneratedMotion
from backend.routers import shaker_ops_v1, shaker_v1
from backend.services import behavior_preferences
from backend.services import generated_motions_service as motions_svc
from backend.services import premium_purchase, qr_service, shaker_ops
from backend.services import shaker_rate_limit, shaker_share

from .conftest import ASGITestClient

OPS = "ops@eternalbeam.com"
CUSTOMER = "customer@example.com"
OTHER_CUSTOMER = "other@example.com"
CONTENT_ID = "abc123"
PET = f"pet_{CONTENT_ID}"
BUCKET = "user-assets"
OBJ = f"{CUSTOMER}/{CONTENT_ID}/idle_loop.mp4"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("SHAKER_RATE_LIMIT_ENABLED", "0")
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    monkeypatch.setenv("SHAKER_OPS_USER_IDS", OPS)
    monkeypatch.setenv("PUBLIC_WEB_BASE_URL", "https://eternalbeam.com")
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
    app.include_router(shaker_ops_v1.router, prefix="/api")
    app.include_router(shaker_v1.router, prefix="/api")
    return ASGITestClient(app)


class FakeStorage:
    def __init__(self, bucket: str, existing: set[str]):
        self.bucket = bucket
        self.existing = existing

    def create_signed_url(self, path: str, seconds: int):
        # 없는 객체에는 서명이 만들어지지 않는다 — 실제 Supabase 와 같은 동작.
        if path not in self.existing:
            raise RuntimeError("object not found")
        return {
            "signedURL": f"https://proj.supabase.co/storage/v1/object/sign/"
                         f"{self.bucket}/{path}?token=FRESH"
        }


class FakeClient:
    def __init__(self, existing: set[str]):
        self.existing = existing
        self.storage = self

    def from_(self, bucket: str) -> FakeStorage:
        return FakeStorage(bucket, self.existing)


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """규약 경로에 BREATHING 이 있는 것처럼 보이게 한다."""
    existing = {OBJ}
    from backend.services import supabase_assets

    monkeypatch.setattr(supabase_assets, "get_client", lambda: FakeClient(existing))
    return existing


def _sync(afn, *args, **kwargs):
    return anyio.run(functools.partial(afn, *args, **kwargs))


def _auth(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer test:{user}"}


def _seed_pet(user: str = CUSTOMER, pet: str = PET) -> None:
    """고객이 이미 만든 펫 경험 (운영은 이것을 찾을 뿐 만들지 않는다)."""
    key = motions_svc._motion_key(user, pet, "any", "COME_CLOSER")
    motions_svc._MOCK_MOTIONS[key] = GeneratedMotion(
        user_id=user, pet_id=pet, place_id="any", action_id="COME_CLOSER",
        video_url=f"https://proj.supabase.co/storage/v1/object/sign/{BUCKET}/{user}/{CONTENT_ID}/cc.mp4?token=OLD",
    )


# ── 인가 ─────────────────────────────────────────────────────────────────────


def test_ops_routes_require_auth(client: ASGITestClient):
    for method, path in (
        ("GET", "/api/v1/shaker/ops/pets"),
        ("GET", "/api/v1/shaker/ops/shares?pet_id=x"),
        ("POST", "/api/v1/shaker/ops/share"),
    ):
        r = client.request(method, path, json={"pet_id": "x"})
        assert r.status_code == 401, path


def test_normal_customer_is_not_ops(client: ASGITestClient):
    """**핵심 회귀**: 로그인한 고객이 운영 도구를 열 수 없다."""
    r = client.get("/api/v1/shaker/ops/pets", headers=_auth(CUSTOMER))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "OPS_FORBIDDEN"


def test_ops_allowlist_unset_locks_everyone_out(client: ASGITestClient, monkeypatch):
    """fail closed — 설정을 빠뜨리면 열리는 게 아니라 닫힌다."""
    monkeypatch.delenv("SHAKER_OPS_USER_IDS", raising=False)
    assert shaker_ops.ops_user_ids() == set()
    r = client.get(
        "/api/v1/shaker/ops/pets", params={"includeLegacy": True}, headers=_auth(OPS)
    )
    assert r.status_code == 403


def test_ops_allowlist_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("SHAKER_OPS_USER_IDS", "Ops@EternalBeam.com , other@x.com")
    assert shaker_ops.is_ops_user("ops@eternalbeam.com") is True
    assert shaker_ops.is_ops_user("OPS@ETERNALBEAM.COM") is True
    assert shaker_ops.is_ops_user("nobody@x.com") is False
    assert shaker_ops.is_ops_user("") is False


# ── 펫 찾기: 만들지 않고 찾는다 ──────────────────────────────────────────────


def test_ops_can_find_customer_pet(client: ASGITestClient):
    _seed_pet()
    r = client.get(
        "/api/v1/shaker/ops/pets", params={"includeLegacy": True}, headers=_auth(OPS)
    )
    assert r.status_code == 200
    pets = r.json()["pets"]
    assert len(pets) == 1
    assert pets[0]["pet_id"] == PET
    assert pets[0]["owner_user_id"] == CUSTOMER


def test_ops_search_filters_by_pet_or_user(client: ASGITestClient):
    _seed_pet()
    _seed_pet(user=OTHER_CUSTOMER, pet="pet_zzz")

    only = client.get(
        "/api/v1/shaker/ops/pets",
        params={"query": CONTENT_ID, "includeLegacy": True}, headers=_auth(OPS),
    )
    assert [p["pet_id"] for p in only.json()["pets"]] == [PET]

    by_user = client.get(
        "/api/v1/shaker/ops/pets",
        params={"query": "other@", "includeLegacy": True}, headers=_auth(OPS),
    )
    assert [p["pet_id"] for p in by_user.json()["pets"]] == ["pet_zzz"]


def test_search_returns_nothing_when_no_pet_exists(client: ASGITestClient):
    """운영이 없는 펫을 만들어 내지 않는다 — 그냥 빈 목록이다."""
    r = client.get("/api/v1/shaker/ops/pets", headers=_auth(OPS))
    assert r.json()["pets"] == []


# ── canonical petId 하나 ─────────────────────────────────────────────────────


def test_ops_share_uses_the_existing_pet_id_verbatim(client: ASGITestClient, storage):
    """
    **핵심 회귀**: QR·편지·메모리 박스가 전부 같은 petId 를 가리킨다.

    운영 발급이 새 pet_id 를 만들거나 변형하면 그 순간 사슬이 갈라진다.
    """
    _seed_pet()
    before = dict(motions_svc._MOCK_MOTIONS)

    r = client.post(
        "/api/v1/shaker/ops/share",
        json={"pet_id": PET, "pet_name": "고야", "purpose": "LETTER"},
        headers=_auth(OPS),
    )
    assert r.status_code == 200, r.text
    b = r.json()

    assert b["pet_id"] == PET                      # 그대로다
    assert b["owner_user_id"] == CUSTOMER          # 소유자는 고객
    assert motions_svc._MOCK_MOTIONS == before     # 펫 데이터가 늘지 않았다


def test_two_products_share_one_pet(client: ASGITestClient, storage):
    """편지용과 메모리 박스용을 따로 발급해도 펫은 하나다."""
    _seed_pet()
    letter = client.post(
        "/api/v1/shaker/ops/share",
        json={"pet_id": PET, "purpose": "LETTER"}, headers=_auth(OPS),
    ).json()
    box = client.post(
        "/api/v1/shaker/ops/share",
        json={"pet_id": PET, "purpose": "MEMORY_BOX"}, headers=_auth(OPS),
    ).json()

    assert letter["pet_id"] == box["pet_id"] == PET
    assert letter["token"] != box["token"]
    assert letter["share_id"] != box["share_id"]

    rows = _sync(shaker_share.list_shares, user_id=CUSTOMER, pet_id=PET)
    assert {r.purpose for r in rows} == {"LETTER", "MEMORY_BOX"}
    assert {r.pet_id for r in rows} == {PET}


def test_share_is_owned_by_customer_not_ops(client: ASGITestClient, storage):
    """
    **소유 모델**: 운영자가 만들어도 링크가 가리키는 것은 고객의 펫이고,
    공유 행의 소유자도 고객이다. created_by 에만 운영자가 남는다(감사 추적).
    """
    _seed_pet()
    client.post(
        "/api/v1/shaker/ops/share", json={"pet_id": PET}, headers=_auth(OPS)
    )
    rows = _sync(shaker_share.list_shares, user_id=CUSTOMER, pet_id=PET)
    assert len(rows) == 1
    assert rows[0].user_id == CUSTOMER
    assert rows[0].created_by == OPS

    # 운영자 자신의 목록에는 없다 — 운영자가 소유자가 아니기 때문이다.
    assert _sync(shaker_share.list_shares, user_id=OPS) == []


def test_ops_cannot_share_a_pet_that_does_not_exist(client: ASGITestClient, storage):
    r = client.post(
        "/api/v1/shaker/ops/share", json={"pet_id": "pet_nope"}, headers=_auth(OPS)
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "PET_NOT_FOUND"


def test_ops_share_rejects_pet_without_breathing(client: ASGITestClient, monkeypatch):
    """
    BREATHING 이 없으면 **거절한다 — 만들지 않는다.**

    자산이 없다는 것은 아직 QR 을 붙일 단계가 아니라는 뜻이다.
    """
    _seed_pet()
    from backend.services import supabase_assets

    monkeypatch.setattr(supabase_assets, "get_client", lambda: FakeClient(set()))

    r = client.post("/api/v1/shaker/ops/share", json={"pet_id": PET}, headers=_auth(OPS))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "BREATHING_NOT_FOUND"


def test_ops_share_resolves_breathing_from_convention(client: ASGITestClient, storage):
    """
    고객 브라우저 상태 없이 서버만으로 BREATHING 을 찾는다.

    운영자는 pipeline.idle_video_url 을 볼 수 없다 — 규약 경로가 유일한 단서다.
    """
    _seed_pet()
    r = client.post("/api/v1/shaker/ops/share", json={"pet_id": PET}, headers=_auth(OPS))
    assert r.status_code == 200

    rows = _sync(shaker_share.list_shares, user_id=CUSTOMER, pet_id=PET)
    assert rows[0].breathing_object_path == OBJ
    assert rows[0].breathing_bucket == BUCKET


def test_content_id_derivation():
    assert shaker_ops.content_id_from_pet_id("pet_abc123") == "abc123"
    # 규약을 따르지 않는 id 는 유도하지 않는다 (수동 지정이 필요하다).
    assert shaker_ops.content_id_from_pet_id("goya_pet") is None
    assert shaker_ops.content_id_from_pet_id("pet_") is None
    assert shaker_ops.content_id_from_pet_id("") is None


# ── 발급한 링크가 실제로 열린다 ─────────────────────────────────────────────


def test_ops_created_link_opens_publicly(client: ASGITestClient, storage):
    """운영 발급 → 고객이 QR 을 찍는다 → 같은 펫이 열린다."""
    _seed_pet()
    created = client.post(
        "/api/v1/shaker/ops/share",
        json={"pet_id": PET, "pet_name": "고야"}, headers=_auth(OPS),
    ).json()

    r = client.get("/api/v1/shaker/pet", params={"share": created["token"]})
    assert r.status_code == 200
    body = r.json()
    assert body["pet_id"] == PET
    assert body["pet_name"] == "고야"
    assert body["breathing_url"]
    # 공개 응답은 여전히 허용 목록이다 — 운영 경로가 열어 준 것이 없다.
    assert "owner_user_id" not in body
    assert CUSTOMER not in r.text


def test_ops_revoke_closes_the_printed_link(client: ASGITestClient, storage):
    _seed_pet()
    created = client.post(
        "/api/v1/shaker/ops/share", json={"pet_id": PET}, headers=_auth(OPS)
    ).json()

    r = client.post(
        f"/api/v1/shaker/ops/share/{created['share_id']}/revoke",
        json={"pet_id": PET}, headers=_auth(OPS),
    )
    assert r.json()["revoked"] is True

    closed = client.get("/api/v1/shaker/pet", params={"share": created["token"]})
    assert closed.status_code == 410


def test_ops_can_view_shares_for_any_customer_pet(client: ASGITestClient, storage):
    _seed_pet()
    client.post("/api/v1/shaker/ops/share", json={"pet_id": PET, "purpose": "LETTER"},
                headers=_auth(OPS))

    r = client.get("/api/v1/shaker/ops/shares", params={"pet_id": PET}, headers=_auth(OPS))
    assert r.status_code == 200
    shares = r.json()["shares"]
    assert len(shares) == 1
    assert shares[0]["purpose"] == "LETTER"
    assert shares[0]["created_by"] == OPS
    assert shares[0]["active"] is True
    # 목록에도 토큰은 없다.
    assert "token" not in shares[0]


# ── Phase 12–13 연결 지점 ────────────────────────────────────────────────────


def test_order_ref_is_stored_for_later_fulfilment(client: ASGITestClient, storage):
    """
    주문 → petId → 공유 → QR 사슬의 연결 지점만 예약한다.
    이행 파이프라인은 이 단계에서 만들지 않는다.
    """
    _seed_pet()
    client.post(
        "/api/v1/shaker/ops/share",
        json={"pet_id": PET, "purpose": "LETTER", "order_ref": "EB1234"},
        headers=_auth(OPS),
    )
    rows = _sync(shaker_share.list_shares, user_id=CUSTOMER, pet_id=PET)
    assert rows[0].order_ref == "EB1234"
    assert rows[0].purpose == "LETTER"


def test_invalid_purpose_is_rejected(client: ASGITestClient, storage):
    _seed_pet()
    r = client.post(
        "/api/v1/shaker/ops/share",
        json={"pet_id": PET, "purpose": "WHATEVER"}, headers=_auth(OPS),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "PURPOSE_INVALID"


# ── QR: Shaker URL 만 ────────────────────────────────────────────────────────


SHAKER_URL = "https://eternalbeam.com/shaker?petId=pet_abc123&share=" + "a" * 43


def test_qr_encodes_a_shaker_url(client: ASGITestClient):
    r = client.get(
        "/api/v1/shaker/ops/qr",
        params={"share_url": SHAKER_URL, "kind": "svg"},
        headers=_auth(OPS),
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in r.content
    assert r.headers["cache-control"] == "no-store"


def test_qr_supports_png_for_preview(client: ASGITestClient):
    r = client.get(
        "/api/v1/shaker/ops/qr",
        params={"share_url": SHAKER_URL, "kind": "png"},
        headers=_auth(OPS),
    )
    assert r.status_code == 200
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_qr_refuses_storage_and_video_urls(client: ASGITestClient):
    """
    **핵심 회귀**: 인쇄되어 나가면 회수할 수 없다.

    스토리지 URL 이 인쇄되면 (1) 7일 뒤 죽고 (2) 토큰 검증·폐기·리밋을 전부
    우회하며 (3) 폐기할 방법이 없다.
    """
    # ⚠️ share 파라미터를 **일부러 붙인다.** 없으면 "share 누락"만으로 거절돼,
    # 정작 검증하려는 규칙(스토리지/영상 금지)이 꺼져 있어도 테스트가 통과한다.
    # 실제로 돌연변이 검사에서 이 구멍이 드러났다.
    share = "a" * 43
    forbidden = [
        f"https://proj.supabase.co/storage/v1/object/sign/{BUCKET}/{OBJ}?share={share}",
        f"https://proj.supabase.co/storage/v1/object/public/user-assets/a/b.mp4?share={share}",
        f"https://cdn.example.com/goya/idle_loop.mp4?share={share}",
        f"https://eternalbeam.com/shaker/../storage/v1/object/sign/b/o.mp4?share={share}",
        f"https://eternalbeam.com/shaker.mp4?share={share}",
    ]
    for url in forbidden:
        r = client.get(
            "/api/v1/shaker/ops/qr", params={"share_url": url}, headers=_auth(OPS)
        )
        assert r.status_code == 400, url
        assert r.json()["detail"]["code"] == "QR_URL_NOT_SHAKER", url


def test_qr_refuses_non_shaker_paths(client: ASGITestClient):
    for url in (
        "https://eternalbeam.com/",
        "https://eternalbeam.com/billing/success?share=abc",
        "https://eternalbeam.com/shakerx?share=abc",
    ):
        r = client.get(
            "/api/v1/shaker/ops/qr", params={"share_url": url}, headers=_auth(OPS)
        )
        assert r.status_code == 400, url


def test_qr_refuses_shaker_url_without_share_token(client: ASGITestClient):
    """토큰 없는 /shaker 는 열어도 '링크가 없습니다'다 — 인쇄 전에 잡는다."""
    r = client.get(
        "/api/v1/shaker/ops/qr",
        params={"share_url": "https://eternalbeam.com/shaker?petId=pet_abc123"},
        headers=_auth(OPS),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "QR_URL_NO_SHARE"


def test_qr_requires_ops(client: ASGITestClient):
    r = client.get(
        "/api/v1/shaker/ops/qr", params={"share_url": SHAKER_URL}, headers=_auth(CUSTOMER)
    )
    assert r.status_code == 403


def test_qr_scale_is_bounded():
    """운영 실수 한 번으로 수십 MB 응답이 나오지 않게."""
    big = qr_service.render_qr(SHAKER_URL, kind="png", scale=10_000)
    modest = qr_service.render_qr(SHAKER_URL, kind="png", scale=40)
    assert len(big.data) == len(modest.data)


def test_qr_url_validator_is_pure_and_strict():
    for bad in ("", None, "not a url", "ftp://x/shaker?share=a", "/shaker?share=a"):
        with pytest.raises(qr_service.QrError):
            qr_service.assert_shaker_url(bad)
    assert qr_service.assert_shaker_url(SHAKER_URL) == SHAKER_URL


# ── 운영 경로도 생성하지 않는다 ─────────────────────────────────────────────


def test_ops_paths_never_generate(client: ASGITestClient, storage, monkeypatch):
    from backend.services import generation_queue, premium_generation

    fired: list[str] = []

    def _explode(name: str):
        async def _boom(*_a, **_k):
            fired.append(name)
            raise AssertionError(f"{name} 호출됨 — 운영 경로도 생성 금지다")

        return _boom

    for mod, attr in (
        (premium_purchase, "purchase"),
        (premium_generation, "submit_action"),
        (generation_queue, "enqueue_generation"),
        (generation_queue, "advance_generation_queue"),
    ):
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, _explode(f"{mod.__name__}.{attr}"))

    _seed_pet()
    client.get("/api/v1/shaker/ops/pets", headers=_auth(OPS))
    created = client.post(
        "/api/v1/shaker/ops/share", json={"pet_id": PET}, headers=_auth(OPS)
    ).json()
    client.get("/api/v1/shaker/ops/shares", params={"pet_id": PET}, headers=_auth(OPS))
    client.get(
        "/api/v1/shaker/ops/qr", params={"share_url": created["share_url"]}, headers=_auth(OPS)
    )
    client.post(
        f"/api/v1/shaker/ops/share/{created['share_id']}/revoke",
        json={"pet_id": PET}, headers=_auth(OPS),
    )
    assert fired == []


def test_ops_share_url_points_at_the_web_app_not_the_api(client: ASGITestClient, storage):
    """
    QR 이 API 도메인을 가리키면 아무것도 열리지 않는다.
    PUBLIC_WEB_BASE_URL 을 먼저 보는 이유가 이것이다.
    """
    _seed_pet()
    b = client.post(
        "/api/v1/shaker/ops/share", json={"pet_id": PET}, headers=_auth(OPS)
    ).json()
    assert b["share_url"].startswith("https://eternalbeam.com/shaker?")
    assert "testserver" not in b["share_url"]
    # 그리고 그 URL 은 QR 검증을 통과한다.
    assert qr_service.assert_shaker_url(b["share_url"]) == b["share_url"]
