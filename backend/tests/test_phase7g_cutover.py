"""Phase 7G: 실행 파이프라인의 packed-alpha 포장 단계 + 발행 없는 재생 리졸버.

QA 결정은 절대 가공되지 않는다 — REVIEW 는 데이터베이스에 REVIEW 로 남고,
재생은 발행이 아니라 명시적 리졸버(GET /generation-runs/{id}/playback)로만 된다.
"""

from __future__ import annotations

from types import SimpleNamespace

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import generation_runs_v1
from backend.services import (
    asset_url_refresh,
    motion_delivery_service as delivery,
    motion_publication_service,
    motion_video_service as motions,
    pet_generation_run_service as runs,
    pet_reference_service,
    pet_registry,
)

from .conftest import ASGITestClient
from .test_phase7g_helpers import review_harness
from .test_phase7c_generation_runs import (
    CID,
    PET,
    USER,
    PipelineHarness,
    seed_intake,
    start,
    work,
)

OTHER = "someone-else@test"


def _run(awaitable):
    return anyio.run(lambda: awaitable)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    monkeypatch.setenv("SEEDANCE_TRANSPORT", "runway")
    runs.__reset_for_tests()
    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()
    motions.__reset_for_tests()
    motion_publication_service.__reset_for_tests()
    delivery.__reset_for_tests()
    yield
    runs.__reset_for_tests()
    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()
    motions.__reset_for_tests()
    motion_publication_service.__reset_for_tests()
    delivery.__reset_for_tests()


@pytest.fixture
def storage(monkeypatch):
    from backend.services import supabase_assets

    async def upload(path, data, content_type):
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", upload)
    return None


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(generation_runs_v1.router, prefix="/api")
    return ASGITestClient(app)


def _auth(user: str = USER) -> dict[str, str]:
    return {"Authorization": f"Bearer test:{user}"}


# ══════════════════════════════════════════════════════════════════════════
# 코디네이터 — DELIVERY 단계
# ══════════════════════════════════════════════════════════════════════════


def test_pass_run_packages_before_publication(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)

    start()
    result = work()

    assert result.status == runs.STATUS_PUBLISHED
    assert harness.counts["delivery"] == 1
    assert harness.counts["publication"] == 1
    # 포장이 발행보다 먼저다 — 발행은 derived(packed) 경로를 집어 든다.
    assert harness.calls.index("delivery") < harness.calls.index("publication")


def test_review_run_packages_but_never_publishes(storage, monkeypatch):
    seed_intake()
    harness = review_harness(monkeypatch)

    start()
    result = work()

    # QA 상태는 진실 그대로: REVIEW → 실행은 실패로 끝나고 발행은 없다.
    assert result.status == runs.STATUS_FAILED
    assert (result.last_error or {}).get("code") == "MOTION_QA_REVIEW"
    assert harness.counts["publication"] == 0
    # 그래도 REVIEW 후보는 포장된다 — 개발 재생 리졸버가 쓸 수 있게.
    assert harness.counts["delivery"] == 1
    assert result.selected_candidate_id == harness.review_candidate.id


def test_review_without_candidates_skips_delivery(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch, motion_status="review")

    start()
    result = work()

    assert result.status == runs.STATUS_FAILED
    assert (result.last_error or {}).get("code") == "MOTION_QA_REVIEW"
    assert harness.counts["delivery"] == 0


# ══════════════════════════════════════════════════════════════════════════
# 재생 리졸버 — GET /generation-runs/{id}/playback
# ══════════════════════════════════════════════════════════════════════════

VERSION_ID = "00000000-0000-0000-0000-000000000601"
CANDIDATE_ID = "00000000-0000-0000-0000-000000000699"
DERIVED = f"{USER}/{CID}/motions/breathing/v1/seedance_a1_packed.mp4"


def _seed_packaged_candidate(*, decision: str = "REVIEW", packaged: bool = True):
    motions._MOCK_VERSIONS.append(
        {
            "id": VERSION_ID,
            "pet_id": PET,
            "user_id": USER,
            "motion_id": "BREATHING",
            "motion_class": "MICRO",
            "version": 1,
            "status": "review" if decision == "REVIEW" else "complete",
            "selected_candidate_id": CANDIDATE_ID,
        }
    )
    motions._MOCK_CANDIDATES.append(
        {
            "id": CANDIDATE_ID,
            "motion_version_id": VERSION_ID,
            "pet_id": PET,
            "user_id": USER,
            "motion_id": "BREATHING",
            "provider": "seedance",
            "attempt": 1,
            "raw_bucket": "user-assets",
            "raw_video_path": f"{USER}/{CID}/motions/breathing/v1/seedance_a1_raw.mp4",
            "derived_video_path": DERIVED if packaged else None,
            "delivery_format": "packed_alpha" if packaged else None,
            "decision": decision,
            "selected": False,
        }
    )


def _review_run_via_worker(monkeypatch):
    seed_intake()
    harness = review_harness(
        monkeypatch, version_id=VERSION_ID, candidate_id=CANDIDATE_ID
    )
    start()
    return work(), harness


def test_playback_serves_packaged_review_without_publication(
    storage, monkeypatch, client: ASGITestClient
):
    result, _ = _review_run_via_worker(monkeypatch)
    _seed_packaged_candidate(decision="REVIEW", packaged=True)
    monkeypatch.setattr(
        asset_url_refresh,
        "sign_object",
        lambda obj: f"https://storage.test/{obj.bucket}/{obj.path}?token=fresh",
    )

    response = client.get(
        f"/api/v1/pet/generation-runs/{result.id}/playback", headers=_auth()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["published"] is False
    assert body["qa_decision"] == "REVIEW"  # 데이터베이스 결정 그대로
    assert body["delivery_format"] == "packed_alpha"
    assert body["url"].endswith("_packed.mp4?token=fresh")
    assert body["candidate_id"] == CANDIDATE_ID
    # 발행 흔적이 없다 — pets 포인터도, 발행 원장도 만들지 않았다.
    assert pet_registry._MOCK_PETS.get(PET) is None
    assert motion_publication_service._MOCK_PUBLICATIONS == []
    # 데이터베이스 QA 상태도 그대로다.
    assert motions._MOCK_CANDIDATES[0]["decision"] == "REVIEW"
    assert motions._MOCK_VERSIONS[0]["status"] == "review"


def test_playback_refuses_unpackaged_candidate(storage, monkeypatch, client: ASGITestClient):
    result, _ = _review_run_via_worker(monkeypatch)
    _seed_packaged_candidate(decision="REVIEW", packaged=False)

    response = client.get(
        f"/api/v1/pet/generation-runs/{result.id}/playback", headers=_auth()
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLAYBACK_NOT_PACKAGED"


def test_playback_refuses_fail_candidate(storage, monkeypatch, client: ASGITestClient):
    result, _ = _review_run_via_worker(monkeypatch)
    _seed_packaged_candidate(decision="FAIL", packaged=True)

    response = client.get(
        f"/api/v1/pet/generation-runs/{result.id}/playback", headers=_auth()
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLAYBACK_UNAVAILABLE"


def test_playback_rejects_other_user(storage, monkeypatch, client: ASGITestClient):
    result, _ = _review_run_via_worker(monkeypatch)
    _seed_packaged_candidate()

    response = client.get(
        f"/api/v1/pet/generation-runs/{result.id}/playback", headers=_auth(OTHER)
    )
    assert response.status_code in (403, 404)


def test_playback_for_published_run_uses_publication_pointer(
    storage, monkeypatch, client: ASGITestClient
):
    seed_intake()
    PipelineHarness(monkeypatch)
    start()
    result = work()
    assert result.status == runs.STATUS_PUBLISHED

    async def published(**kwargs):
        assert kwargs["user_id"] == USER
        assert kwargs["pet_id"] == PET
        return SimpleNamespace(
            url="https://storage.test/user-assets/pub_packed.mp4?token=fresh",
            delivery_format="packed_alpha",
            background_baked=False,
            motion_version_id="00000000-0000-0000-0000-000000000601",
            breathing_object_path="pub_packed.mp4",
        )

    monkeypatch.setattr(motion_publication_service, "get_published_breathing", published)
    response = client.get(
        f"/api/v1/pet/generation-runs/{result.id}/playback", headers=_auth()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["published"] is True
    assert body["qa_decision"] == "PASS"
    assert body["delivery_format"] == "packed_alpha"
