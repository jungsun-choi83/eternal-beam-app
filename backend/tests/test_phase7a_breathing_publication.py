"""Phase 7A: publish an existing Phase 6 BREATHING PASS without generating anything."""

from __future__ import annotations

from pathlib import Path

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import motion_videos_v1
from backend.services import asset_url_refresh
from backend.services import motion_publication_service as publication
from backend.services import motion_video_service as motions
from backend.services import pet_registry

from .conftest import ASGITestClient

USER = "phase7a@example.com"
OTHER = "other@example.com"
CONTENT = "798e2a13-671c-4f67-a842-66ab777bd890"
PET = f"pet_{CONTENT}"
VERSION_ID = "7a000000-0000-4000-8000-000000000001"
CANDIDATE_ID = "7a000000-0000-4000-8000-000000000002"
BUCKET = "user-assets"
OBJECT_PATH = f"{USER}/{CONTENT}/motions/breathing/v1/seedance_a1_raw.mp4"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    motions.__reset_for_tests()
    publication.__reset_for_tests()
    pet_registry.__reset_for_tests()
    existing = {OBJECT_PATH}
    monkeypatch.setattr(
        asset_url_refresh,
        "sign_object",
        lambda obj: (
            f"https://storage.test/{obj.bucket}/{obj.path}?token=fresh"
            if obj.path in existing
            else None
        ),
    )
    yield existing
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


def _seed(*, status: str = "complete", decision: str = "PASS", candidate: bool = True):
    motions._MOCK_VERSIONS.append(
        {
            "id": VERSION_ID,
            "pet_id": PET,
            "user_id": USER,
            "motion_id": "BREATHING",
            "motion_class": "MICRO",
            "version": 1,
            "status": status,
            "selected_candidate_id": CANDIDATE_ID,
            "created_at": "2026-09-03T00:00:00+00:00",
        }
    )
    if candidate:
        motions._MOCK_CANDIDATES.append(
            {
                "id": CANDIDATE_ID,
                "motion_version_id": VERSION_ID,
                "pet_id": PET,
                "user_id": USER,
                "motion_id": "BREATHING",
                "provider": "seedance",
                "attempt": 1,
                "raw_bucket": BUCKET,
                "raw_video_path": OBJECT_PATH,
                "decision": decision,
                "selected": True,
                "created_at": "2026-09-03T00:00:00+00:00",
            }
        )


def _post(client: ASGITestClient, *, user: str = USER):
    return client.post(
        f"/api/v1/pet/motions/{PET}/BREATHING/publish",
        json={"motion_version_id": VERSION_ID},
        headers=_auth(user),
    )


def _run(coro):
    return anyio.run(lambda: coro)


def test_pass_publication_projects_existing_asset_for_browser(client: ASGITestClient):
    _seed()
    # An existing baked legacy pointer is replaced; the source Phase 6 rows remain untouched.
    pet_registry._MOCK_PETS[PET] = {
        "pet_id": PET,
        "user_id": USER,
        "content_id": CONTENT,
        "breathing_bucket": BUCKET,
        "breathing_object_path": f"{USER}/{CONTENT}/legacy-baked.mp4",
        "source": "app",
        "background_baked": True,
    }

    response = _post(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["motion_version_id"] == VERSION_ID
    assert body["selected_candidate_id"] == CANDIDATE_ID
    assert body["idle_video_url"].startswith("https://storage.test/")
    assert body["breathing_bucket"] == BUCKET
    assert body["breathing_object_path"] == OBJECT_PATH
    assert body["background_baked"] is False
    assert body["deduplicated"] is False

    pet = _run(pet_registry.get(PET))
    assert pet and pet.user_id == USER
    assert pet.breathing_bucket == BUCKET
    assert pet.breathing_object_path == OBJECT_PATH
    assert pet.background_baked is False
    assert motions._MOCK_VERSIONS[0]["status"] == "complete"
    assert motions._MOCK_CANDIDATES[0]["decision"] == "PASS"


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        ("failed", "MOTION_FAILED_NOT_PUBLISHABLE"),
        ("review", "MOTION_REVIEW_NOT_PUBLISHABLE"),
    ],
)
def test_failed_or_review_version_is_not_published(
    client: ASGITestClient, status: str, expected_code: str
):
    _seed(status=status)
    response = _post(client)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == expected_code
    assert publication._MOCK_PUBLICATIONS == []
    assert _run(pet_registry.get(PET)) is None


def test_selected_candidate_must_itself_be_qa_pass(client: ASGITestClient):
    _seed(decision="FAIL")
    response = _post(client)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CANDIDATE_NOT_PASS"
    assert publication._MOCK_PUBLICATIONS == []


def test_missing_selected_candidate_is_not_published(client: ASGITestClient):
    _seed(candidate=False)
    response = _post(client)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SELECTED_CANDIDATE_MISSING"
    assert publication._MOCK_PUBLICATIONS == []


def test_wrong_user_cannot_publish(client: ASGITestClient):
    _seed()
    response = _post(client, user=OTHER)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "PET_NOT_OWNED"
    assert publication._MOCK_PUBLICATIONS == []


def test_storage_object_must_exist(client: ASGITestClient, _isolated: set[str]):
    _seed()
    _isolated.clear()
    response = _post(client)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CANDIDATE_ASSET_NOT_FOUND"
    assert publication._MOCK_PUBLICATIONS == []
    assert _run(pet_registry.get(PET)) is None


def test_duplicate_publication_is_idempotent(client: ASGITestClient):
    _seed()
    first = _post(client)
    second = _post(client)

    assert first.status_code == second.status_code == 200
    assert first.json()["publication_id"] == second.json()["publication_id"]
    assert first.json()["deduplicated"] is False
    assert second.json()["deduplicated"] is True
    assert len(publication._MOCK_PUBLICATIONS) == 1
    assert pet_registry._MOCK_PETS[PET]["breathing_motion_version_id"] == VERSION_ID


def test_migration_makes_publication_atomic_and_service_only():
    root = Path(__file__).resolve().parents[2]
    migration = root / "supabase/migrations/20261017000000_phase7a_breathing_publication.sql"
    sql = migration.read_text()
    assert "motion_version_id uuid not null unique" in sql
    assert "create or replace function public.publish_phase6_breathing" in sql
    assert "v_candidate.decision <> 'PASS'" in sql
    assert "background_baked = false" in sql
    assert "for update" in sql
    assert "revoke all on function public.publish_phase6_breathing" in sql
    assert "to service_role" in sql
