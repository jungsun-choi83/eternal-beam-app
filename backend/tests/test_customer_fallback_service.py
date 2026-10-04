from __future__ import annotations

from datetime import datetime, timezone

import anyio
import pytest

from backend.services import (
    business_qa,
    canonical_pet_service,
    customer_fallback_service as fallback,
    generated_motions_service,
    motion_delivery_service,
    motion_publication_service,
    motion_video_service,
    owned_assets,
)


USER = "fallback@test"
PET = "fallback-pet"
VERSION = "00000000-0000-0000-0000-000000000101"
CANDIDATE = "00000000-0000-0000-0000-000000000102"


def _run(awaitable):
    return anyio.run(lambda: awaitable)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    canonical_pet_service.__reset_for_tests()
    motion_video_service.__reset_for_tests()
    motion_delivery_service.__reset_for_tests()
    motion_publication_service.__reset_for_tests()
    owned_assets.__reset_for_tests()
    generated_motions_service._MOCK_MOTIONS.clear()
    yield
    canonical_pet_service.__reset_for_tests()
    motion_video_service.__reset_for_tests()
    motion_delivery_service.__reset_for_tests()
    motion_publication_service.__reset_for_tests()
    owned_assets.__reset_for_tests()
    generated_motions_service._MOCK_MOTIONS.clear()


def _sign(obj):
    return f"https://storage.test/{obj.bucket}/{obj.path}?fresh=1"


def _qa(checks, *, attempt=1, request_kind="MICRO", decision="PASS"):
    result = {"qa_version": "fallback-test-v1", "decision": decision, "checks": checks}
    business_qa.attach_business_result(
        result,
        attempt_number=attempt,
        request_kind=request_kind,
        fallback_available=True,
    )
    return result


def _seed_canonical(*, cutout=True, raw=True):
    canonical_pet_service._MOCK_VERSIONS.append(
        {
            "id": VERSION,
            "user_id": USER,
            "pet_id": PET,
            "version": 3,
            "status": canonical_pet_service.STATUS_COMPLETE,
            "selected_candidate_id": CANDIDATE,
        }
    )
    canonical_pet_service._MOCK_CANDIDATES.append(
        {
            "id": CANDIDATE,
            "canonical_version_id": VERSION,
            "user_id": USER,
            "pet_id": PET,
            "attempt": 1,
            "decision": "PASS",
            "selected": True,
            "qa_result": _qa({}, request_kind="CANONICAL"),
            "cutout_bucket": "user-assets",
            "cutout_object_path": "pets/canonical_cutout.png" if cutout else None,
            "raw_bucket": "user-assets",
            "raw_object_path": "pets/canonical.png" if raw else None,
        }
    )


def test_safe_current_candidate_is_first_priority():
    current = {
        "id": "current-safe",
        "motion_version_id": "motion-current",
        "attempt": 1,
        "decision": "REVIEW",
        "derived_video_path": "motions/current_packed.mp4",
        "raw_bucket": "user-assets",
        "delivery_format": "packed_alpha",
        "qa_result": _qa({"loop_return": "REVIEW"}, decision="REVIEW"),
    }
    _seed_canonical()

    result = _run(
        fallback.resolve_best_safe_fallback(
            user_id=USER,
            pet_id=PET,
            motion_id="BREATHING",
            current_candidates=[current],
            sign_fn=_sign,
        )
    )

    assert result.tier == fallback.TIER_CURRENT_RUN
    assert result.candidate_id == "current-safe"


def test_previous_published_motion_wins_without_repointing_any_ledger():
    motion_publication_service._MOCK_PUBLICATIONS.append(
        {
            "publication_id": "publication-old",
            "motion_version_id": "motion-old",
            "selected_candidate_id": "legacy-candidate",
            "user_id": USER,
            "pet_id": PET,
            "motion_id": "BREATHING",
            "motion_version": 2,
            "bucket": "user-assets",
            "object_path": "motions/old_packed.mp4",
            "published_at": "2026-01-01T00:00:00+00:00",
        }
    )
    before = list(motion_publication_service._MOCK_PUBLICATIONS)

    result = _run(
        fallback.resolve_best_safe_fallback(
            user_id=USER, pet_id=PET, motion_id="BREATHING", sign_fn=_sign
        )
    )

    assert result.tier == fallback.TIER_PREVIOUS_MOTION
    assert result.publication_id == "publication-old"
    assert motion_publication_service._MOCK_PUBLICATIONS == before
    assert owned_assets._MOCK == []


def test_previous_owned_motion_is_reused_without_new_ownership():
    asset = owned_assets.OwnedAsset(
        asset_id="owned-old",
        user_id=USER,
        pet_id=PET,
        product_key="action:COME_CLOSER",
        video_url="https://old.example/packed.mp4",
        bucket="user-assets",
        object_path="motions/come_closer_packed.mp4",
        source_job_id="phase7:old-run",
        lineage={"delivery_format": "packed_alpha", "publication_id": "pub-premium"},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    _run(owned_assets.record(asset))
    _run(
        generated_motions_service.record_pointer(
            user_id=USER,
            pet_id=PET,
            place_id=generated_motions_service.THEME_INDEPENDENT_PLACE_ID,
            action_id="COME_CLOSER",
            video_url="https://current.example/current.mp4",
        )
    )
    pointers_before = dict(generated_motions_service._MOCK_MOTIONS)

    result = _run(
        fallback.resolve_best_safe_fallback(
            user_id=USER, pet_id=PET, motion_id="COME_CLOSER", sign_fn=_sign
        )
    )

    assert result.tier == fallback.TIER_PREVIOUS_MOTION
    assert result.ownership_asset_id == "owned-old"
    assert len(owned_assets._MOCK) == 1
    assert generated_motions_service._MOCK_MOTIONS == pointers_before


def test_no_motion_uses_approved_canonical_local_idle_then_still():
    _seed_canonical(cutout=True, raw=True)
    idle = _run(
        fallback.resolve_best_safe_fallback(
            user_id=USER, pet_id=PET, motion_id="BREATHING", sign_fn=_sign
        )
    )
    assert idle.tier == fallback.TIER_CANONICAL_IDLE
    assert idle.delivery_format == fallback.FORMAT_CANONICAL_IDLE

    canonical_pet_service._MOCK_CANDIDATES[0]["cutout_object_path"] = None
    still = _run(
        fallback.resolve_best_safe_fallback(
            user_id=USER, pet_id=PET, motion_id="BREATHING", sign_fn=_sign
        )
    )
    assert still.tier == fallback.TIER_CANONICAL_STILL
    assert still.object_path == "pets/canonical.png"


def test_hard_failed_motion_is_never_selected():
    hard_qa = _qa(
        {"vlm_anatomy": "FAIL"}, attempt=2, decision="FAIL"
    )
    motion_video_service._MOCK_CANDIDATES.append(
        {
            "id": "hard-candidate",
            "motion_version_id": "hard-version",
            "user_id": USER,
            "pet_id": PET,
            "motion_id": "BREATHING",
            "attempt": 2,
            "decision": "FAIL",
            "qa_result": hard_qa,
            "derived_video_path": "motions/hard_packed.mp4",
            "raw_bucket": "user-assets",
            "delivery_format": "packed_alpha",
        }
    )
    motion_publication_service._MOCK_PUBLICATIONS.append(
        {
            "publication_id": "hard-publication",
            "motion_version_id": "hard-version",
            "selected_candidate_id": "hard-candidate",
            "user_id": USER,
            "pet_id": PET,
            "motion_id": "BREATHING",
            "motion_version": 9,
            "bucket": "user-assets",
            "object_path": "motions/hard_packed.mp4",
        }
    )
    _seed_canonical()

    result = _run(
        fallback.resolve_best_safe_fallback(
            user_id=USER, pet_id=PET, motion_id="BREATHING", sign_fn=_sign
        )
    )

    assert result.tier == fallback.TIER_CANONICAL_IDLE
    assert result.publication_id is None
