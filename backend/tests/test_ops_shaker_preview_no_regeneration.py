"""
운영 QR → Shaker 미리보기는 **이미 만든 펫을 그대로 연다** (Phase 13.2 후속).

── 고치는 결함 ─────────────────────────────────────────────────────────────
고객이 BREATHING 을 한 번 만들고 펫이 레지스트리에 등록된 뒤에도, 운영 콘솔에서
공유를 만들면 409 BREATHING_NOT_FOUND("아직 생성되지 않았습니다")가 났다.

원인은 **해석기가 레지스트리를 보지 않은 것**이다:

    소유자   pets 레지스트리에서 읽는다        → canonical user_id  ✅
    BREATHING `{user_id}/{content_id}/idle_loop.mp4` 규약으로 유도  ❌

레지스트리의 소유자는 **인증 토큰의 canonical id** 인데, 스토리지 객체 경로는
**생성 시점 신원**을 접두사로 갖는다. 둘이 다르면(알려진 한계 #8) 규약 유도는
존재하지 않는 경로를 만들고, 서명이 실패하고, 운영은 "생성되지 않았다"를 본다 —
이미 있는 강아지를 다시 만들라는 안내가 된다.

두 번째 결함은 **미리보기 링크의 목적지**였다. PUBLIC_WEB_BASE_URL 이 없으면
화면용 share_url 이 request.base_url(= API 오리진)로 떨어졌다. 거기에는 /shaker
라우트가 없어서, 운영자는 Shaker 대신 404 를 만나고 웹앱 루트로 되돌아가면
고객 앱 업로드 화면이 열린다.

── 여기서 고정하는 계약 ────────────────────────────────────────────────────
    BREATHING READY → 펫 등록 → 운영 검색 → 공유 생성 → /shaker 미리보기
    → **이미 있는** idle_loop.mp4 가 재생된다 → 생성 호출 0 회

그리고 그 경로 어디에서도:
    * 새 펫이 만들어지지 않는다
    * 새 BREATHING 자산이 만들어지지 않는다
    * 미리보기 링크가 API 오리진을 가리키지 않는다
"""

from __future__ import annotations

import functools
from urllib.parse import parse_qs, urlsplit

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import pet_registry_v1, shaker_ops_v1, shaker_v1
from backend.services import generated_motions_service as motions_svc
from backend.services import (
    pet_registry,
    premium_purchase,
    shaker_qr_artifact,
    shaker_rate_limit,
    shaker_share,
)

from .conftest import ASGITestClient, follow_shaker_asset

CUSTOMER = "newcomer@example.com"
OPS = "ops@eternalbeam.com"
CONTENT = "5da0d31f-33d8-4735-8e60-0c2a532ed358"
PET = f"pet_{CONTENT}"
BUCKET = "user-assets"

#: ⚠️ 생성 시점 신원이 canonical user_id 와 **다르다** — 이것이 결함의 조건이다.
#: 규약 유도(`{CUSTOMER}/{CONTENT}/idle_loop.mp4`)로는 절대 찾을 수 없는 경로다.
GEN_PREFIX = "anon-4f21bd90"
OBJ = f"{GEN_PREFIX}/{CONTENT}/idle_loop.mp4"
BREATHING_URL = f"https://proj.supabase.co/storage/v1/object/sign/{BUCKET}/{OBJ}?token=OLD"

#: 운영 콘솔이 도는 웹앱 오리진 — /shaker 가 사는 곳.
WEB_ORIGIN = "https://app.eternalbeam.test"
#: 테스트 클라이언트의 base_url = API 오리진. 미리보기가 여기로 가면 안 된다.
API_ORIGIN = "http://testserver"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    monkeypatch.setenv("SHAKER_OPS_USER_IDS", OPS)
    monkeypatch.setenv("SHAKER_RATE_LIMIT_ENABLED", "0")
    # ⚠️ 일부러 **설정하지 않는다** — 화면용 폴백이 어디로 가는지가 회귀 대상이다.
    monkeypatch.delenv("PUBLIC_WEB_BASE_URL", raising=False)
    monkeypatch.delenv("VITE_PUBLIC_WEB_URL", raising=False)
    for reset in (
        pet_registry.__reset_for_tests,
        shaker_share.__reset_for_tests,
        shaker_qr_artifact.__reset_for_tests,
        shaker_rate_limit.__reset_for_tests,
        premium_purchase.__reset_for_tests,
    ):
        reset()
    motions_svc._MOCK_MOTIONS.clear()
    yield
    for reset in (
        pet_registry.__reset_for_tests,
        shaker_share.__reset_for_tests,
        shaker_qr_artifact.__reset_for_tests,
        shaker_rate_limit.__reset_for_tests,
        premium_purchase.__reset_for_tests,
    ):
        reset()
    motions_svc._MOCK_MOTIONS.clear()


@pytest.fixture
def client() -> ASGITestClient:
    """고객 등록 · 운영 콘솔 · 공개 Shaker 를 한 앱에 올린다 (실제 배포와 같다)."""
    app = FastAPI()
    app.include_router(pet_registry_v1.router, prefix="/api")
    app.include_router(shaker_ops_v1.router, prefix="/api")
    app.include_router(shaker_v1.router, prefix="/api")
    return ASGITestClient(app, base_url=API_ORIGIN)


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
    """BREATHING 이 **이미** 스토리지에 있는 상태 — 고객이 한 번 만들었다."""
    existing = {OBJ}
    from backend.services import supabase_assets

    monkeypatch.setattr(supabase_assets, "get_client", lambda: FakeClient(existing))
    return existing


@pytest.fixture
def armed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """
    생성·과금 진입점을 전부 폭탄으로 갈아 끼운다 (test_shaker_no_generation 과 같은 방식).

    하나라도 불리면 이름이 남는다 — 실제 프로바이더까지 가지 않아도 **의도만으로** 잡힌다.
    """
    fired: list[str] = []

    def _explode(name: str):
        async def _boom(*_a, **_k):
            fired.append(name)
            raise AssertionError(f"운영/미리보기 경로가 {name} 을 호출했다 — 생성 금지 위반")

        return _boom

    from backend.services import (
        credit_generation_service,
        generation_queue,
        premium_generation,
        video_generation,
        wallet_service,
    )

    targets = [
        (premium_purchase, "purchase"),
        (premium_generation, "submit_action"),
        (generation_queue, "enqueue_generation"),
        (generation_queue, "advance_generation_queue"),
        (credit_generation_service, "generate_with_credit"),
        (video_generation, "submit_generation"),
        (wallet_service, "deduct_credits"),
    ]
    for mod, attr in targets:
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, _explode(f"{mod.__name__}.{attr}"))
    return fired


def _auth(u: str) -> dict[str, str]:
    return {"Authorization": f"Bearer test:{u}"}


def _from_ops_console() -> dict[str, str]:
    """운영자의 브라우저가 보내는 헤더 — 콘솔은 웹앱 안의 화면이다."""
    return {**_auth(OPS), "Origin": WEB_ORIGIN, "Referer": f"{WEB_ORIGIN}/ops/shaker"}


def _sync(afn, *args, **kwargs):
    return anyio.run(functools.partial(afn, *args, **kwargs))


def _customer_generated_breathing_once(client: ASGITestClient) -> None:
    """고객 앱이 BREATHING 파이프라인 완료 후 하는 일 — 결과를 등록할 뿐이다."""
    r = client.post(
        "/api/v1/pet/registry/register",
        json={"pet_id": PET, "content_id": CONTENT, "breathing_url": BREATHING_URL},
        headers=_auth(CUSTOMER),
    )
    assert r.status_code == 200, r.text


# ── 핵심 회귀 ────────────────────────────────────────────────────────────────


def test_registered_pet_previews_existing_breathing_with_zero_generation(
    client: ASGITestClient, armed: list[str], storage: set[str]
):
    """
    **이 파일의 이유** — 전 구간을 한 번에 걷는다:

        BREATHING READY → 펫 등록 → 운영 검색 → 공유 생성 → Shaker 미리보기
        → 이미 있는 idle_loop.mp4 재생 → 생성 호출 0 회
    """
    _customer_generated_breathing_once(client)

    # ── 1. 운영이 **같은** 펫을 찾는다 (프리미엄 모션 0 개, 멤버십 없음) ──────
    assert motions_svc._MOCK_MOTIONS == {}
    found = client.get(
        "/api/v1/shaker/ops/pets", params={"query": PET}, headers=_from_ops_console()
    ).json()["pets"]
    assert [p["pet_id"] for p in found] == [PET]
    assert found[0]["owner_user_id"] == CUSTOMER

    # ── 2. 공유를 만든다 — 여기가 예전에 409 BREATHING_NOT_FOUND 였다 ────────
    created = client.post(
        "/api/v1/shaker/ops/share",
        json={"pet_id": PET, "pet_name": "고야", "purpose": "OPS"},
        headers=_from_ops_console(),
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["pet_id"] == PET                      # 같은 canonical petId
    assert body["owner_user_id"] == CUSTOMER          # 소유자는 고객이다

    # ── 3. 미리보기 링크가 **웹앱의 /shaker** 를 가리킨다 ────────────────────
    parts = urlsplit(body["share_url"])
    assert f"{parts.scheme}://{parts.netloc}" == WEB_ORIGIN
    assert parts.path == "/shaker"
    q = parse_qs(parts.query)
    assert q["petId"] == [PET]
    assert q["share"] == [body["token"]]

    # ── 4. 그 링크를 연다 — 공개 Shaker 가 **이미 있는** BREATHING 을 준다 ───
    pet = client.get(
        "/api/v1/shaker/pet", params={"share": body["token"], "pet_id": PET}
    )
    assert pet.status_code == 200, pet.text
    played = follow_shaker_asset(client, pet.json()["breathing_url"])
    #: 재생되는 것은 고객이 한 번 만든 그 객체다 — 새 자산이 아니다.
    assert f"{BUCKET}/{OBJ}" in played
    assert played.endswith("token=FRESH")             # 만료된 저장 URL 이 아니라 재서명본
    assert pet.json()["pet_id"] == PET
    assert pet.json()["actions"] == []                # 프리미엄 없음 — BREATHING 만

    # ── 5. 생성 호출 0 회 ───────────────────────────────────────────────────
    assert armed == []

    # ── 6. 펫도 자산도 새로 생기지 않았다 ───────────────────────────────────
    assert len(pet_registry._MOCK_PETS) == 1
    assert _sync(pet_registry.get, PET).breathing_object_path == OBJ
    assert storage == {OBJ}
    assert motions_svc._MOCK_MOTIONS == {}


# ── 결함 조각별 고정 ─────────────────────────────────────────────────────────


def test_ops_share_uses_the_registry_path_not_the_derived_convention(client: ASGITestClient):
    """
    **핵심 회귀**: BREATHING 위치는 레지스트리가 정한다.

    canonical 소유자(newcomer@…)와 객체 접두사(anon-…)가 다르다. 규약 유도만 하면
    존재하지 않는 경로가 나오고, 이미 만든 펫에 대해 "생성되지 않았다"가 나온다.
    """
    _customer_generated_breathing_once(client)

    # 규약 유도 경로는 실제로 스토리지에 없다 — 이 전제가 깨지면 테스트가 무의미하다.
    derived = _sync(pet_registry.get, PET)
    assert derived.breathing_object_path == OBJ
    assert not OBJ.startswith(CUSTOMER)

    from backend.services import shaker_ops

    located = _sync(shaker_ops.locate_breathing, CUSTOMER, PET)
    assert located is not None, "레지스트리에 저장된 BREATHING 위치를 찾지 못했다"
    loc, url = located
    assert (loc.bucket, loc.object_path) == (BUCKET, OBJ)
    assert url.endswith("token=FRESH")


def test_share_creation_does_not_fall_back_to_the_api_origin(client: ASGITestClient):
    """
    **핵심 회귀**: 미리보기 링크가 API 도메인을 가리키면 /shaker 가 없어 404 다.

    거기서 웹앱 루트로 되돌아가면 고객 앱 업로드 화면이 열린다 — 이미 만든 펫을
    다시 만들게 되는 입구다.
    """
    _customer_generated_breathing_once(client)
    url = client.post(
        "/api/v1/shaker/ops/share",
        json={"pet_id": PET, "purpose": "OPS"},
        headers=_from_ops_console(),
    ).json()["share_url"]

    assert API_ORIGIN not in url
    assert "testserver" not in url
    assert url.startswith(f"{WEB_ORIGIN}/shaker?")


def test_print_purpose_still_fails_closed_without_a_configured_base(client: ASGITestClient):
    """
    화면용 폴백이 인쇄 안전을 느슨하게 만들지 않는다.

    인쇄된 QR 은 회수할 수 없다. 헤더는 위조 가능하므로 인쇄용은 여전히
    PUBLIC_WEB_BASE_URL 만 본다 (Phase 13.1 계약을 그대로 유지한다).
    """
    _customer_generated_breathing_once(client)
    for purpose in ("LETTER", "MEMORY_BOX"):
        r = client.post(
            "/api/v1/shaker/ops/share",
            json={"pet_id": PET, "purpose": purpose},
            headers=_from_ops_console(),
        )
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "PRINT_BASE_URL_MISSING"


def test_configured_base_still_wins_over_the_request_origin(
    client: ASGITestClient, monkeypatch: pytest.MonkeyPatch
):
    """설정값이 있으면 그것이 정본이다 — 헤더가 이기지 못한다."""
    monkeypatch.setenv("PUBLIC_WEB_BASE_URL", "https://eternalbeam.com")
    _customer_generated_breathing_once(client)
    url = client.post(
        "/api/v1/shaker/ops/share",
        json={"pet_id": PET, "purpose": "OPS"},
        headers={**_auth(OPS), "Origin": "https://evil.example"},
    ).json()["share_url"]
    assert url.startswith("https://eternalbeam.com/shaker?")


def test_missing_breathing_is_still_rejected(client: ASGITestClient, storage: set[str]):
    """
    레지스트리 우선이 **없는 자산을 있는 것으로 만들지 않는다.**

    등록 후 객체가 사라지면(이동·삭제) 공유는 여전히 거절된다 — 열어도 아무것도
    재생되지 않는 QR 이 인쇄되는 것을 막는 기존 게이트다.
    """
    _customer_generated_breathing_once(client)
    storage.clear()

    r = client.post(
        "/api/v1/shaker/ops/share",
        json={"pet_id": PET, "purpose": "OPS"},
        headers=_from_ops_console(),
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "BREATHING_NOT_FOUND"


def test_legacy_convention_pets_still_resolve(client: ASGITestClient, storage: set[str]):
    """
    레지스트리에 없는 레거시 펫은 **규약 유도 폴백**이 계속 처리한다.

    백필이 끝날 때까지 이 경로가 살아 있어야 한다.
    """
    from backend.services import shaker_ops

    legacy_obj = f"{CUSTOMER}/{CONTENT}/idle_loop.mp4"
    storage.add(legacy_obj)
    pet_registry.__reset_for_tests()  # 레지스트리에 아무것도 없다

    located = _sync(shaker_ops.locate_breathing, CUSTOMER, PET)
    assert located is not None
    assert located[0].object_path == legacy_obj
