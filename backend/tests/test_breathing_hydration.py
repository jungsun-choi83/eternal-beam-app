"""Phase 7F: 발행된 BREATHING 하이드레이션 — 새 서명 URL + 명시 전달 포맷."""

from __future__ import annotations

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import motion_videos_v1
from backend.services import asset_url_refresh
from backend.services import motion_publication_service as publication
from backend.services import motion_video_service as motions
from backend.services import pet_registry

from .conftest import ASGITestClient

USER = "hydration7f@example.com"
OTHER = "other@example.com"
CONTENT = "0a1b2a13-671c-4f67-a842-66ab777bd888"
PET = f"pet_{CONTENT}"
VERSION_ID = "7f100000-0000-4000-8000-000000000001"
CANDIDATE_ID = "7f100000-0000-4000-8000-000000000002"
BUCKET = "user-assets"
RAW_PATH = f"{USER}/{CONTENT}/motions/breathing/v1/seedance_a1_raw.mp4"
PACKED_PATH = f"{USER}/{CONTENT}/motions/breathing/v1/seedance_a1_packed.mp4"


def _run(coro):
    return anyio.run(lambda: coro)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    motions.__reset_for_tests()
    publication.__reset_for_tests()
    pet_registry.__reset_for_tests()
    sign_calls: list[str] = []
    existing = {RAW_PATH, PACKED_PATH}

    def fresh_sign(obj):
        if obj.path not in existing:
            return None
        sign_calls.append(obj.path)
        return f"https://storage.test/{obj.bucket}/{obj.path}?token=fresh-{len(sign_calls)}"

    monkeypatch.setattr(asset_url_refresh, "sign_object", fresh_sign)
    yield {"sign_calls": sign_calls, "existing": existing}
    motions.__reset_for_tests()
    publication.__reset_for_tests()
    pet_registry.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(motion_videos_v1.router, prefix="/api")
    return ASGITestClient(app)


def _auth(user: str = USER) -> dict[str, str]:
    return {"Authorization": f"Bearer test:{user}"}


def _seed_published(*, delivery_format: str | None = "packed_alpha"):
    """Phase 6 PASS + (옵션) 포장 완료 후보를 만들고 Phase 7A 로 발행한다."""
    motions._MOCK_VERSIONS.append(
        {
            "id": VERSION_ID,
            "pet_id": PET,
            "user_id": USER,
            "motion_id": "BREATHING",
            "motion_class": "MICRO",
            "version": 1,
            "status": "complete",
            "selected_candidate_id": CANDIDATE_ID,
            "created_at": "2026-09-03T00:00:00+00:00",
        }
    )
    candidate = {
        "id": CANDIDATE_ID,
        "motion_version_id": VERSION_ID,
        "pet_id": PET,
        "user_id": USER,
        "motion_id": "BREATHING",
        "provider": "seedance",
        "attempt": 1,
        "raw_bucket": BUCKET,
        "raw_video_path": RAW_PATH,
        "decision": "PASS",
        "selected": True,
        "created_at": "2026-09-03T00:00:00+00:00",
    }
    if delivery_format:
        candidate["derived_video_path"] = PACKED_PATH
        candidate["delivery_format"] = delivery_format
    motions._MOCK_CANDIDATES.append(candidate)
    return _run(
        publication.publish_breathing(user_id=USER, pet_id=PET, motion_version_id=VERSION_ID)
    )


def _get(client: ASGITestClient, *, user: str = USER, pet: str = PET):
    return client.get(
        f"/api/v1/pet/motions/{pet}/BREATHING/published", headers=_auth(user)
    )


def test_hydration_returns_fresh_url_and_explicit_format(client: ASGITestClient, _isolated):
    published = _seed_published()
    assert published.breathing_object_path == PACKED_PATH

    response = _get(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pet_id"] == PET
    assert body["motion_id"] == "BREATHING"
    assert body["motion_version_id"] == VERSION_ID
    assert body["breathing_bucket"] == BUCKET
    assert body["breathing_object_path"] == PACKED_PATH
    assert body["delivery_format"] == "packed_alpha"
    assert body["background_baked"] is False
    assert body["publication_id"] == published.publication_id

    # 저장된 서명이 아니라 **호출 시점** 서명이다 — 두 번 부르면 서명이 다르다.
    second = _get(client).json()
    assert body["url"].startswith(f"https://storage.test/{BUCKET}/{PACKED_PATH}?token=fresh-")
    assert second["url"] != body["url"]
    assert second["breathing_object_path"] == body["breathing_object_path"]


def test_hydration_rejects_wrong_user(client: ASGITestClient):
    _seed_published()
    response = _get(client, user=OTHER)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "PET_NOT_OWNED"


def test_hydration_404_when_no_pet(client: ASGITestClient):
    response = _get(client)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PET_NOT_FOUND"


def test_hydration_404_when_pet_has_no_breathing(client: ASGITestClient):
    pet_registry._MOCK_PETS[PET] = {
        "pet_id": PET,
        "user_id": USER,
        "content_id": CONTENT,
        "source": "app",
    }
    response = _get(client)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "BREATHING_NOT_PUBLISHED"


def test_hydration_serves_legacy_pointer_without_format(client: ASGITestClient, _isolated):
    """레거시(Luma) 포인터 — delivery_format 없음, background_baked 는 pets 행 값."""
    legacy_path = f"{USER}/{CONTENT}/idle_loop.mp4"
    _isolated["existing"].add(legacy_path)
    pet_registry._MOCK_PETS[PET] = {
        "pet_id": PET,
        "user_id": USER,
        "content_id": CONTENT,
        "breathing_bucket": BUCKET,
        "breathing_object_path": legacy_path,
        "source": "app",
        "background_baked": True,
    }
    response = _get(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["breathing_object_path"] == legacy_path
    assert body["delivery_format"] is None
    assert body["background_baked"] is True
    assert body["motion_version_id"] is None


def test_hydration_409_when_object_gone(client: ASGITestClient, _isolated):
    _seed_published()
    _isolated["existing"].clear()  # 스토리지 객체 소실 — 죽은 URL 을 주지 않는다
    response = _get(client)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PUBLISHED_ASSET_UNAVAILABLE"


def test_hydration_unpackaged_publication_reports_no_format(client: ASGITestClient):
    """포장 없이 발행된 raw — 포맷 선언 없음(브라우저 레거시 규칙으로 재생)."""
    _seed_published(delivery_format=None)
    response = _get(client)
    assert response.status_code == 200
    body = response.json()
    assert body["breathing_object_path"] == RAW_PATH
    assert body["delivery_format"] is None
