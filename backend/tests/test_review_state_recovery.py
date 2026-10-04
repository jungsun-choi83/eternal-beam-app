"""REVIEW-state orchestration fix.

Bug: `_execute()`'s Phase 7G packaging block advances the `stage` closure
variable to STAGE_DELIVERY (a progress-tracking side effect) whenever a
REVIEW candidate exists to package — which is always true for a real REVIEW
motion version. It then raised MOTION_QA_REVIEW with that stale `stage`
value, so the persisted `current_stage` became "DELIVERY" instead of "QA".
`request_replacement_generation` requires `current_stage == STAGE_QA`, so the
one paid-replacement path was permanently unreachable for every real REVIEW
run that got as far as packaging (see `test_phase7g_cutover.py::
test_review_run_packages_but_never_publishes` for the packaging shape this
regression test builds on).

Fix: `stage` is reset to STAGE_QA immediately before the MOTION_QA_REVIEW
raise, so the failure durably records the stage QA actually originated from.
Packaging itself, and the playback resolver it feeds, are untouched.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import anyio
import pytest

from backend.services import (
    business_qa,
    customer_fallback_service,
    motion_spec,
    motion_video_service,
    pet_generation_run_service as runs,
    pet_reference_service,
    pet_registry,
)

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


def _run(awaitable):
    return anyio.run(lambda: awaitable)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("SEEDANCE_TRANSPORT", "runway")
    runs.__reset_for_tests()
    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()
    yield
    runs.__reset_for_tests()
    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()


@pytest.fixture
def storage(monkeypatch):
    from backend.services import supabase_assets

    async def upload(path, data, content_type):
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", upload)
    return None


# 1. motion REVIEW remains recoverable ────────────────────────────────────


def test_review_run_stays_recoverable_current_stage_is_qa_not_delivery(storage, monkeypatch):
    seed_intake()
    harness = review_harness(monkeypatch)

    start(key="review:stage")
    result = work()

    assert result.status == runs.STATUS_FAILED
    assert result.current_stage == runs.STAGE_QA  # not STAGE_DELIVERY
    assert result.last_error["code"] == "MOTION_QA_REVIEW"
    # the REVIEW candidate is still packaged for dev/current-run playback
    assert harness.counts["delivery"] == 1
    assert result.selected_candidate_id == harness.review_candidate.id


# 2. QA rerun uses the existing candidate, no new generation ──────────────


def test_qa_rerun_reuses_existing_candidate_no_new_generation(storage, monkeypatch):
    seed_intake()
    harness = review_harness(monkeypatch)

    started = start(key="review:rerun")
    first = work()
    assert first.last_error["code"] == "MOTION_QA_REVIEW"
    assert harness.counts["motion_build"] == 1

    resumed = _run(runs.retry_generation_run(user_id=started.user_id, run_id=started.id))
    assert resumed.status == runs.STATUS_QUEUED
    assert resumed.motion_version_id == harness.motion.id  # pin preserved, not cleared

    second = work()
    assert second.current_stage == runs.STAGE_QA
    assert second.last_error["code"] == "MOTION_QA_REVIEW"
    assert harness.counts["motion_build"] == 1  # no new provider spend
    assert harness.counts["delivery"] == 2  # re-packaged (idempotent), not re-generated


# 3. Explicit replacement works from REVIEW ────────────────────────────────


def test_explicit_replacement_works_from_review(storage, monkeypatch):
    seed_intake()
    harness = review_harness(monkeypatch)

    started = start(key="review:replace")
    failed = work()
    assert failed.current_stage == runs.STAGE_QA
    assert failed.last_error["code"] == "MOTION_QA_REVIEW"

    # Before the fix this raised REPLACEMENT_NOT_JUSTIFIED because
    # current_stage had drifted to STAGE_DELIVERY during packaging.
    queued = _run(
        runs.request_replacement_generation(
            user_id=started.user_id,
            run_id=started.id,
            idempotency_key="replace:one",
            reason="identity drift on the packaged REVIEW candidate",
        )
    )
    assert queued.status == runs.STATUS_QUEUED
    assert queued.motion_version_id is None
    request = queued.provider_state["_operator"]["replacement_request"]
    assert request["source_motion_version_id"] == harness.motion.id
    assert request["status"] == "QUEUED"
    assert harness.counts["motion_build"] == 1  # API only persisted intent, no spend yet

    # idempotent on the same key
    duplicate = _run(
        runs.request_replacement_generation(
            user_id=started.user_id,
            run_id=started.id,
            idempotency_key="replace:one",
            reason="same request",
        )
    )
    assert duplicate.provider_state == queued.provider_state
    assert harness.counts["motion_build"] == 1

    with pytest.raises(runs.PetGenerationRunError) as second_key:
        _run(
            runs.request_replacement_generation(
                user_id=started.user_id,
                run_id=started.id,
                idempotency_key="replace:two",
                reason="must not buy twice",
            )
        )
    assert second_key.value.code == "REPLACEMENT_ALREADY_REQUESTED"


# 4. Replacement submits at most one new paid candidate ───────────────────


def test_replacement_worker_builds_exactly_one_new_version_then_reuses_it(monkeypatch):
    source = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000901",
        pet_id=PET,
        user_id=USER,
        motion_id="BREATHING",
        motion_spec_version=motion_spec.MOTION_SPEC_VERSION,
        start_keyframe_id="00000000-0000-0000-0000-000000000501",
        start_keyframe_version=1,
        canonical_version_id="00000000-0000-0000-0000-000000000401",
        version=1,
        status=motion_video_service.STATUS_REVIEW,
    )
    replacement = SimpleNamespace(
        **{
            **source.__dict__,
            "id": "00000000-0000-0000-0000-000000000902",
            "version": 2,
            "status": motion_video_service.STATUS_REVIEW,
        }
    )
    build_calls = []

    async def get_motion(**kwargs):
        version = kwargs.get("version")
        if version == replacement.version:
            return replacement
        if version == source.version:
            return source
        return None  # an unversioned "latest" lookup — nothing reusable

    async def build_motion(**kwargs):
        build_calls.append(kwargs)
        return replacement

    monkeypatch.setattr(motion_video_service, "get_motion_version", get_motion)
    monkeypatch.setattr(motion_video_service, "build_motion_video", build_motion)
    monkeypatch.setattr(runs, "_video_providers", lambda *args: [object()])

    run = runs.PetGenerationRun(
        id="00000000-0000-0000-0000-000000000900",
        user_id=USER,
        pet_id=PET,
        content_id=CID,
        motion_id="BREATHING",
        request_kind="FREE_HOME",
        idempotency_key="replacement-worker",
        status=runs.STATUS_RUNNING,
        current_stage=runs.STAGE_MOTION_GENERATION,
        canonical_version_id=source.canonical_version_id,
        keyframes={"NEUTRAL_IDLE": {"id": source.start_keyframe_id, "version": 1}},
        motion_spec_version=motion_spec.MOTION_SPEC_VERSION,
        provider_state={"_operator": {"replacement_request": {"source_motion_version_id": source.id}}},
    )

    built, run = _run(runs._motion(run))
    assert built.id == replacement.id
    assert len(build_calls) == 1  # exactly one new paid candidate submitted

    # simulate `_execute` pinning the run to the freshly built version
    run = replace(run, motion_version_id=replacement.id, motion_version=replacement.version)
    again, run = _run(runs._motion(run))
    assert again.id == replacement.id
    assert len(build_calls) == 1  # resuming never buys a second candidate


# 5. PASS still publishes normally ─────────────────────────────────────────


def test_pass_still_publishes_normally(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)

    start(key="pass:unaffected")
    result = work()

    assert result.status == runs.STATUS_PUBLISHED
    assert result.current_stage == runs.STAGE_PUBLISHED
    assert harness.counts["publication"] == 1


# 6. FAIL still remains terminal, never replacement-eligible ───────────────


def test_fail_still_terminal_and_not_replacement_eligible(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch, motion_status=motion_video_service.STATUS_FAILED)

    started = start(key="fail:terminal")
    result = work()

    assert result.status == runs.STATUS_FAILED
    assert result.current_stage == runs.STAGE_QA
    assert result.last_error["code"] == "MOTION_QA_FAILED"
    assert harness.counts["delivery"] == 0

    with pytest.raises(runs.PetGenerationRunError) as error:
        _run(
            runs.request_replacement_generation(
                user_id=started.user_id,
                run_id=started.id,
                idempotency_key="fail:replace",
                reason="should be rejected",
            )
        )
    assert error.value.code == "REPLACEMENT_NOT_JUSTIFIED"


def test_two_hard_failures_return_business_fallback_not_motion_qa_failed(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch, motion_status=motion_video_service.STATUS_REVIEW)
    qa_result = {
        "qa_version": "motion-test-v1",
        "decision": "FAIL",
        "checks": {"vlm_anatomy": "FAIL"},
        "reasons": ["severe_anatomy_corruption"],
    }
    business_qa.attach_business_result(
        qa_result,
        attempt_number=2,
        request_kind="MICRO",
        fallback_available=True,
    )
    harness.motion.selected_candidate_id = None
    harness.motion.candidates = [
        SimpleNamespace(
            id="00000000-0000-0000-0000-000000000699",
            selected=False,
            decision="FAIL",
            qa_result=qa_result,
        )
    ]

    async def resolve(**kwargs):
        return customer_fallback_service.CustomerFallbackAsset(
            tier=customer_fallback_service.TIER_PREVIOUS_MOTION,
            asset_kind="motion_video",
            user_id=kwargs["user_id"],
            pet_id=kwargs["pet_id"],
            requested_motion_id=kwargs["motion_id"],
            delivery_format="packed_alpha",
            bucket="user-assets",
            object_path="motions/previous_packed.mp4",
            publication_id="publication-previous",
            provenance={"source": "pet_motion_publications"},
        )

    monkeypatch.setattr(customer_fallback_service, "resolve_best_safe_fallback", resolve)

    start(key="fail:business-fallback")
    result = work()

    assert result.status == runs.STATUS_PUBLISHED  # fallback delivery is success
    assert result.publication_id is None  # ...without claiming a publication
    assert result.current_stage == runs.STAGE_DELIVERY
    assert result.last_error is None
    receipt = result.provider_state["_business_qa"]
    assert receipt["terminal_state"] == business_qa.DELIVERED_FALLBACK
    assert receipt["decision"]["retry_action"] == "FALLBACK"
    assert receipt["fallback_resolution"] == "RESOLVED"
    assert receipt["fallback_asset"]["tier"] == customer_fallback_service.TIER_PREVIOUS_MOTION
    assert receipt["fallback_asset"]["publication_id"] == "publication-previous"
    assert harness.counts["delivery"] == 0
    assert harness.counts["publication"] == 0


def _hard_fail_twice_harness(monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch, motion_status=motion_video_service.STATUS_REVIEW)
    qa_result = {
        "qa_version": "motion-test-v1",
        "decision": "FAIL",
        "checks": {"vlm_anatomy": "FAIL"},
    }
    business_qa.attach_business_result(
        qa_result, attempt_number=2, request_kind="MICRO", fallback_available=True
    )
    harness.motion.selected_candidate_id = None
    harness.motion.candidates = [
        SimpleNamespace(id="hard-2", selected=False, decision="FAIL", qa_result=qa_result)
    ]
    return harness


def _canonical_idle_fallback(monkeypatch):
    async def resolve(**kwargs):
        return customer_fallback_service.CustomerFallbackAsset(
            tier=customer_fallback_service.TIER_CANONICAL_IDLE,
            asset_kind="canonical_image",
            user_id=kwargs["user_id"],
            pet_id=kwargs["pet_id"],
            requested_motion_id=kwargs["motion_id"],
            delivery_format=customer_fallback_service.FORMAT_CANONICAL_IDLE,
            bucket="user-assets",
            object_path="canonical/safe_cutout.png",
            provenance={"source": "pet_canonical_candidates"},
        )

    monkeypatch.setattr(customer_fallback_service, "resolve_best_safe_fallback", resolve)


def test_canonical_local_idle_fallback_completes_run_successfully(storage, monkeypatch):
    harness = _hard_fail_twice_harness(monkeypatch)
    _canonical_idle_fallback(monkeypatch)

    start(key="fallback:canonical-idle")
    result = work()

    assert result.status == runs.STATUS_PUBLISHED
    assert result.last_error is None
    assert result.publication_id is None
    assert runs.is_delivered_fallback(result)
    receipt = result.provider_state["_business_qa"]
    assert receipt["terminal_state"] == business_qa.DELIVERED_FALLBACK
    assert (
        receipt["fallback_asset"]["delivery_format"]
        == customer_fallback_service.FORMAT_CANONICAL_IDLE
    )
    assert harness.counts["publication"] == 0


def test_no_generated_asset_and_no_fallback_is_failed(storage, monkeypatch):
    _hard_fail_twice_harness(monkeypatch)

    async def none(**kwargs):
        return None

    monkeypatch.setattr(customer_fallback_service, "resolve_best_safe_fallback", none)

    start(key="fallback:none")
    result = work()

    assert result.status == runs.STATUS_FAILED
    assert result.last_error["code"] == "NO_SAFE_FALLBACK"
    assert not runs.is_delivered_fallback(result)
    receipt = result.provider_state["_business_qa"]
    assert receipt["terminal_state"] is None
    assert receipt["fallback_resolution"] == "UNAVAILABLE"


def test_delivered_fallback_run_stays_retryable(storage, monkeypatch):
    _hard_fail_twice_harness(monkeypatch)
    _canonical_idle_fallback(monkeypatch)

    start(key="fallback:retry")
    delivered = work()
    assert delivered.status == runs.STATUS_PUBLISHED

    retried = _run(runs.retry_generation_run(user_id=delivered.user_id, run_id=delivered.id))
    assert retried.status == runs.STATUS_QUEUED


def test_fallback_store_outage_is_true_infrastructure_failure(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch, motion_status=motion_video_service.STATUS_REVIEW)
    qa_result = {
        "qa_version": "motion-test-v1",
        "decision": "FAIL",
        "checks": {"vlm_anatomy": "FAIL"},
    }
    business_qa.attach_business_result(
        qa_result, attempt_number=2, request_kind="MICRO", fallback_available=True
    )
    harness.motion.selected_candidate_id = None
    harness.motion.candidates = [
        SimpleNamespace(id="hard-2", selected=False, decision="FAIL", qa_result=qa_result)
    ]

    async def unavailable(**kwargs):
        raise customer_fallback_service.FallbackInfrastructureError(
            "FALLBACK_STORE_DOWN", "fallback storage unavailable"
        )

    monkeypatch.setattr(customer_fallback_service, "resolve_best_safe_fallback", unavailable)

    start(key="fail:true-infrastructure")
    result = work()

    assert result.last_error["code"] == "TRUE_INFRASTRUCTURE_FAILURE"
    receipt = result.provider_state["_business_qa"]
    assert receipt["terminal_state"] == business_qa.TRUE_INFRASTRUCTURE_FAILURE
    assert receipt["fallback_resolution"] == "ERROR"
    assert harness.counts["publication"] == 0
