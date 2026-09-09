"""
canonical 펫 레지스트리 (Phase 13.2).

── 고치는 결함 ─────────────────────────────────────────────────────────────
운영 검색이 generated_motions 를 펫 목록으로 썼다. 그 테이블은 **프리미엄 모션이
승격됐을 때만** 채워지므로, 무료 BREATHING 펫은 운영 콘솔에 아예 나타나지 않았다.
QR 제품의 주 고객이 정확히 그 사람들이라, 제품의 핵심 경로가 막혀 있었다.

여기서 고정하는 계약:
  * **BREATHING 하나만 있어도** 펫이 등록되고 운영이 찾을 수 있다 (멤버십·프리미엄 불필요).
  * 등록은 멱등하다.
  * BREATHING 이 없으면 등록하지 않는다 (열어도 아무것도 없는 QR 방지).
  * 남의 펫을 가로챌 수 없다.
  * 레거시(프리미엄) 펫은 generated_motions 폴백으로 계속 보인다.
  * **generated_motions 에 가짜 행을 만들지 않는다.**
"""

from __future__ import annotations

import functools

import anyio
import pytest
from fastapi import FastAPI

from backend.models.hybrid_business import GeneratedMotion
from backend.routers import pet_registry_v1, shaker_ops_v1
from backend.services import generated_motions_service as motions_svc
from backend.services import pet_registry, premium_purchase, shaker_qr_artifact, shaker_share

from .conftest import ASGITestClient

#: 구독도, 프리미엄 모션도, 크레딧도 없는 **새 사용자**.
NEWCOMER = "newcomer@example.com"
OTHER = "other@example.com"
OPS = "ops@eternalbeam.com"
CONTENT = "5da0d31f-33d8-4735-8e60-0c2a532ed358"
PET = f"pet_{CONTENT}"
BUCKET = "user-assets"
OBJ = f"{NEWCOMER}/{CONTENT}/idle_loop.mp4"
BREATHING_URL = (
    f"https://proj.supabase.co/storage/v1/object/sign/{BUCKET}/{OBJ}?token=OLD"
)
WEB = "https://eternalbeam.com"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    monkeypatch.setenv("SHAKER_OPS_USER_IDS", OPS)
    monkeypatch.setenv("PUBLIC_WEB_BASE_URL", WEB)
    pet_registry.__reset_for_tests()
    shaker_share.__reset_for_tests()
    shaker_qr_artifact.__reset_for_tests()
    motions_svc._MOCK_MOTIONS.clear()
    premium_purchase.__reset_for_tests()
    yield
    pet_registry.__reset_for_tests()
    shaker_share.__reset_for_tests()
    shaker_qr_artifact.__reset_for_tests()
    motions_svc._MOCK_MOTIONS.clear()
    premium_purchase.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(pet_registry_v1.router, prefix="/api")
    app.include_router(shaker_ops_v1.router, prefix="/api")
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


@pytest.fixture(autouse=True)
def storage(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """BREATHING 이 스토리지에 실제로 있는 상태."""
    existing = {OBJ}
    from backend.services import supabase_assets

    monkeypatch.setattr(supabase_assets, "get_client", lambda: FakeClient(existing))
    return existing


def _sync(afn, *args, **kwargs):
    return anyio.run(functools.partial(afn, *args, **kwargs))


def _auth(u: str) -> dict[str, str]:
    return {"Authorization": f"Bearer test:{u}"}


def _register(client: ASGITestClient, user: str = NEWCOMER, pet: str = PET, **body):
    payload = {"pet_id": pet, "content_id": CONTENT, "breathing_url": BREATHING_URL, **body}
    return client.post("/api/v1/pet/registry/register", json=payload, headers=_auth(user))


# ── 회귀: 새 사용자 → BREATHING → 운영이 찾는다 → 공유까지 ─────────────────


def test_free_breathing_only_pet_is_discoverable_end_to_end(client: ASGITestClient):
    """
    **핵심 회귀 (이 단계의 이유)**:
      새 사용자 → BREATHING READY → 멤버십 없음 → 프리미엄 모션 없음
      → 펫 등록됨 → 운영이 정확한 petId 로 찾음 → Shaker 공유 생성 가능
    """
    # 프리미엄 모션이 하나도 없다 — 예전에는 이 상태에서 운영이 펫을 볼 수 없었다.
    assert motions_svc._MOCK_MOTIONS == {}

    assert _register(client).status_code == 200

    # 1) 정확한 petId 로 검색된다.
    found = client.get(
        "/api/v1/shaker/ops/pets", params={"query": PET}, headers=_auth(OPS)
    ).json()["pets"]
    assert [p["pet_id"] for p in found] == [PET]
    assert found[0]["owner_user_id"] == NEWCOMER
    assert found[0]["ready_count"] == 0  # 프리미엄 모션은 0 이다 — 그래도 보인다

    # 2) 고객으로도 찾을 수 있다.
    by_customer = client.get(
        "/api/v1/shaker/ops/pets", params={"query": "newcomer@"}, headers=_auth(OPS)
    ).json()["pets"]
    assert [p["pet_id"] for p in by_customer] == [PET]

    # 3) Shaker 공유를 만들 수 있다 — 여기까지가 QR 생산의 입구다.
    share = client.post(
        "/api/v1/shaker/ops/share",
        json={"pet_id": PET, "pet_name": "고야", "purpose": "LETTER"},
        headers=_auth(OPS),
    )
    assert share.status_code == 200, share.text
    assert share.json()["pet_id"] == PET
    assert share.json()["owner_user_id"] == NEWCOMER
    assert share.json()["share_url"].startswith(f"{WEB}/shaker?")


def test_discovery_requires_no_membership_or_premium(client: ASGITestClient, monkeypatch):
    """발견 경로가 구독을 조회하지 않는다 — 건드리지 않으면 얽힐 수도 없다."""
    from backend.services import premium_entitlement

    async def _forbidden(*_a, **_k):
        raise AssertionError("펫 발견이 구독을 조회했다")

    monkeypatch.setattr(premium_entitlement, "get_entitlement", _forbidden)

    _register(client)
    assert client.get("/api/v1/shaker/ops/pets", headers=_auth(OPS)).status_code == 200


def test_registration_never_writes_generated_motions(client: ASGITestClient):
    """
    **핵심 회귀**: 가짜 generated_motions 행을 만들지 않는다.

    만들면 asset_state 가 오염되어 없는 프리미엄 행동이 READY 로 보이고,
    membership 정책 아래 공개 Shaker 응답에까지 새어 나갈 수 있다.
    """
    _register(client)
    assert motions_svc._MOCK_MOTIONS == {}


# ── 멱등성 ───────────────────────────────────────────────────────────────────


def test_registration_is_idempotent(client: ASGITestClient):
    first = _register(client)
    assert first.status_code == 200

    for _ in range(3):
        again = _register(client)
        assert again.status_code == 200
        assert again.json()["pet_id"] == PET

    assert len(pet_registry._MOCK_PETS) == 1
    # 검색 결과도 하나뿐이다.
    pets = client.get("/api/v1/shaker/ops/pets", headers=_auth(OPS)).json()["pets"]
    assert len(pets) == 1


def test_repeat_registration_does_not_reverify_storage(client: ASGITestClient, storage):
    """
    이미 등록됐으면 스토리지를 다시 확인하지 않는다.

    자산이 나중에 옮겨지거나 서명이 잠시 실패해도, 등록된 펫이 갑자기 사라지면 안 된다.
    """
    _register(client)
    storage.clear()  # BREATHING 이 조회되지 않는 상태
    assert _register(client).status_code == 200


# ── BREATHING 검증 ───────────────────────────────────────────────────────────


def test_missing_breathing_is_rejected(client: ASGITestClient, storage):
    """
    **핵심 회귀**: BREATHING 이 없으면 등록하지 않는다.

    등록해 두면 운영이 QR 을 붙일 수 있게 되고, 열어도 아무것도 재생되지 않는
    링크가 인쇄돼 나간다 — 인쇄물은 회수할 수 없다.
    """
    storage.clear()
    r = _register(client)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "BREATHING_NOT_FOUND"
    assert pet_registry._MOCK_PETS == {}

    # 등록되지 않았으므로 운영 검색에도 나오지 않는다.
    assert client.get("/api/v1/shaker/ops/pets", headers=_auth(OPS)).json()["pets"] == []


def test_unparseable_breathing_url_is_rejected(client: ASGITestClient):
    r = _register(client, breathing_url="data:video/mp4;base64,AAAA")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "BREATHING_LOCATION_UNKNOWN"


def test_registration_requires_auth(client: ASGITestClient):
    assert client.post(
        "/api/v1/pet/registry/register",
        json={"pet_id": PET, "breathing_url": BREATHING_URL},
    ).status_code == 401


# ── 사용자 간 보호 ───────────────────────────────────────────────────────────


def test_another_user_cannot_claim_a_registered_pet(client: ASGITestClient, storage):
    """**핵심 회귀**: 이미 등록된 펫을 다른 사용자가 가로챌 수 없다."""
    assert _register(client, user=NEWCOMER).status_code == 200

    storage.add(f"{OTHER}/{CONTENT}/idle_loop.mp4")
    r = _register(client, user=OTHER)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "PET_NOT_OWNED"

    # 소유자는 그대로다.
    assert _sync(pet_registry.owner_of, PET) == NEWCOMER


def test_my_pets_lists_only_mine(client: ASGITestClient):
    _register(client, user=NEWCOMER)
    r = client.get("/api/v1/pet/registry/mine", headers=_auth(OTHER))
    assert r.json()["pets"] == []
    mine = client.get("/api/v1/pet/registry/mine", headers=_auth(NEWCOMER)).json()["pets"]
    assert [p["pet_id"] for p in mine] == [PET]


def test_owner_is_taken_from_the_token_not_the_body(client: ASGITestClient):
    """바디로 소유자를 주장할 수 없다 — 신원은 토큰이 확정한다."""
    r = client.post(
        "/api/v1/pet/registry/register",
        json={
            "pet_id": PET, "content_id": CONTENT, "breathing_url": BREATHING_URL,
            "user_id": OTHER,  # 무시돼야 한다
        },
        headers=_auth(NEWCOMER),
    )
    assert r.status_code == 200
    assert _sync(pet_registry.owner_of, PET) == NEWCOMER


# ── 레거시 폴백 ──────────────────────────────────────────────────────────────


def _seed_legacy(user: str = OTHER, pet: str = "pet_legacy") -> None:
    """레지스트리 이전에 만들어진 프리미엄 펫 (generated_motions 에만 있다)."""
    key = motions_svc._motion_key(user, pet, "any", "COME_CLOSER")
    motions_svc._MOCK_MOTIONS[key] = GeneratedMotion(
        user_id=user, pet_id=pet, place_id="any", action_id="COME_CLOSER",
        video_url="https://cdn.test/cc.mp4",
    )


def test_legacy_premium_pets_are_still_found(client: ASGITestClient):
    """레거시는 명시적으로 요청했을 때만 보인다."""
    _seed_legacy()
    assert client.get("/api/v1/shaker/ops/pets", headers=_auth(OPS)).json()["pets"] == []
    pets = client.get(
        "/api/v1/shaker/ops/pets", params={"includeLegacy": True}, headers=_auth(OPS)
    ).json()["pets"]
    assert [p["pet_id"] for p in pets] == ["pet_legacy"]
    assert pets[0]["ready_count"] == 1
    assert pets[0]["source"] == "LEGACY"


def test_registry_and_legacy_appear_together(client: ASGITestClient):
    _register(client)
    _seed_legacy()
    pets = client.get(
        "/api/v1/shaker/ops/pets", params={"includeLegacy": True}, headers=_auth(OPS)
    ).json()["pets"]
    assert {p["pet_id"] for p in pets} == {PET, "pet_legacy"}
    assert {p["source"] for p in pets} == {"REGISTRY", "LEGACY"}


def test_default_registry_list_is_newest_first_and_not_lost_to_legacy_limit(
    client: ASGITestClient,
):
    _seed_legacy(pet="pet_0000_legacy")
    _register(client, pet="pet_old", content_id="old")
    pet_registry._MOCK_PETS["pet_old"]["created_at"] = "2026-01-01T00:00:00+00:00"
    _register(client, pet="pet_new", content_id="new")
    pet_registry._MOCK_PETS["pet_new"]["created_at"] = "2026-08-21T00:00:00+00:00"

    body = client.get("/api/v1/shaker/ops/pets", headers=_auth(OPS)).json()
    assert [p["pet_id"] for p in body["pets"]] == ["pet_new", "pet_old"]
    assert all(p["source"] == "REGISTRY" for p in body["pets"])
    assert body["degraded"] is False


def test_registry_failure_is_not_silently_reported_as_default_success(
    client: ASGITestClient, monkeypatch,
):
    async def _broken(*_a, **_k):
        raise pet_registry.PetRegistryError("PET_REGISTRY_UNAVAILABLE", "down", status=503)

    monkeypatch.setattr(pet_registry, "search", _broken)
    default = client.get("/api/v1/shaker/ops/pets", headers=_auth(OPS))
    assert default.status_code == 503
    assert default.json()["detail"]["code"] == "PET_REGISTRY_UNAVAILABLE"

    _seed_legacy()
    fallback = client.get(
        "/api/v1/shaker/ops/pets", params={"includeLegacy": True}, headers=_auth(OPS)
    )
    assert fallback.status_code == 200
    assert fallback.json()["degraded"] is True
    assert fallback.json()["registry_available"] is False
    assert fallback.json()["pets"][0]["source"] == "LEGACY"


def test_registry_wins_for_owner_resolution(client: ASGITestClient):
    """
    같은 펫이 양쪽에 있으면 **레지스트리**가 소유자를 정한다.

    레지스트리는 인증된 신원이고, generated_motions 의 user_id 는 생성 시점의
    (때로 localStorage 유래) 값이라 신뢰도가 낮다.
    """
    _register(client, user=NEWCOMER)
    _seed_legacy(user=OTHER, pet=PET)  # 같은 펫에 다른 신원이 붙어 있다
    from backend.services import shaker_ops

    assert _sync(shaker_ops.resolve_pet_owner, PET) == NEWCOMER


def test_unregistered_unknown_pet_still_rejected(client: ASGITestClient):
    from backend.services import shaker_ops

    with pytest.raises(shaker_ops.OpsError) as ei:
        _sync(shaker_ops.resolve_pet_owner, "pet_nope")
    assert ei.value.code == "PET_NOT_FOUND"


# ── 운영 백필 ────────────────────────────────────────────────────────────────


def test_ops_can_backfill_an_existing_pet(client: ASGITestClient):
    """레지스트리 이전 펫을 운영이 수동 등록한다."""
    r = client.post(
        "/api/v1/shaker/ops/pets/register",
        json={
            "pet_id": PET, "user_id": NEWCOMER, "content_id": CONTENT,
            "breathing_url": BREATHING_URL,
        },
        headers=_auth(OPS),
    )
    assert r.status_code == 200, r.text
    assert r.json()["owner_user_id"] == NEWCOMER
    assert r.json()["source"] == "ops"

    pets = client.get(
        "/api/v1/shaker/ops/pets", params={"query": PET}, headers=_auth(OPS)
    ).json()["pets"]
    assert [p["pet_id"] for p in pets] == [PET]


def test_ops_backfill_accepts_an_object_path(client: ASGITestClient):
    """URL 이 없어도 경로만으로 백필할 수 있다 (스토리지를 직접 훑는 경우)."""
    r = client.post(
        "/api/v1/shaker/ops/pets/register",
        json={
            "pet_id": PET, "user_id": NEWCOMER,
            "breathing_bucket": BUCKET, "breathing_object_path": OBJ,
        },
        headers=_auth(OPS),
    )
    assert r.status_code == 200
    assert _sync(pet_registry.get, PET).breathing_object_path == OBJ


def test_ops_backfill_verifies_breathing_too(client: ASGITestClient, storage):
    """백필도 같은 검증을 받는다 — 없는 펫을 등록하면 빈 QR 이 인쇄된다."""
    storage.clear()
    r = client.post(
        "/api/v1/shaker/ops/pets/register",
        json={"pet_id": PET, "user_id": NEWCOMER, "breathing_url": BREATHING_URL},
        headers=_auth(OPS),
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "BREATHING_NOT_FOUND"


def test_ops_backfill_requires_ops(client: ASGITestClient):
    body = {"pet_id": PET, "user_id": NEWCOMER, "breathing_url": BREATHING_URL}
    assert client.post("/api/v1/shaker/ops/pets/register", json=body).status_code == 401
    assert client.post(
        "/api/v1/shaker/ops/pets/register", json=body, headers=_auth(NEWCOMER)
    ).status_code == 403


# ── 저장 내용 ────────────────────────────────────────────────────────────────


def test_registry_stores_path_not_signed_url(client: ASGITestClient):
    """
    만료되는 서명 URL 을 정본으로 두지 않는다 — Phase 10·11 에서 겪은 실패다.
    """
    _register(client)
    pet = _sync(pet_registry.get, PET)
    assert pet.breathing_object_path == OBJ
    assert pet.breathing_bucket == BUCKET
    assert "token=" not in (pet.breathing_object_path or "")
    assert BREATHING_URL not in repr(pet_registry._MOCK_PETS)


def test_content_id_derivation():
    assert pet_registry.content_id_of(PET) == CONTENT
    assert pet_registry.content_id_of("goya_pet") is None
    assert pet_registry.content_id_of("") is None


def test_registry_module_is_independent():
    """구조로 고정 — 레지스트리가 생성·구독·결제 모듈을 import 하지 않는다."""
    import ast

    forbidden = {
        "premium_entitlement", "subscription_store_service", "premium_generation",
        "generation_queue", "credit_generation_service", "wallet_service",
        "premium_purchase", "luma_service", "wan_service", "video_generation",
        "toss_billing", "theme_purchase", "generated_motions_service",
    }
    tree = ast.parse(open("backend/services/pet_registry.py", encoding="utf-8").read())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add(a.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                imported.add(a.name)
            if node.module:
                imported.add(node.module.rsplit(".", 1)[-1])
    assert not (imported & forbidden), imported & forbidden


def test_registration_never_generates(client: ASGITestClient, monkeypatch):
    from backend.services import generation_queue, premium_generation

    fired: list[str] = []

    def _explode(name: str):
        async def _boom(*_a, **_k):
            fired.append(name)
            raise AssertionError(f"{name} 호출됨 — 등록은 생성하지 않는다")

        return _boom

    for mod, attr in (
        (premium_purchase, "purchase"),
        (premium_generation, "submit_action"),
        (generation_queue, "enqueue_generation"),
    ):
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, _explode(f"{mod.__name__}.{attr}"))

    _register(client)
    client.get("/api/v1/shaker/ops/pets", headers=_auth(OPS))
    assert fired == []
