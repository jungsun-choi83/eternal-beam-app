"""Stored-video QA retries: real run/QA persistence, simulated media and publication."""
from copy import deepcopy
from types import SimpleNamespace
import asyncio

import pytest

from backend.services import motion_spec, motion_video_qa, vlm_identity
from backend.services import motion_video_service as mv, pet_generation_run_service as runs
from backend.services import durable_provider_jobs as jobs, motion_delivery_service, premium_motion_finalization
from .test_motion_acceptance import classify
from .test_motion_video_generation import (
    _mock_backend, storage, _prepare_pipeline, _build_motion, _run,
    FakeVideoProvider, GOOD, USER, PET,
)


def current_qa(verdict="PASS"):
    changes = {"vlm_motion": verdict} if verdict != "PASS" else {}
    result = classify(**changes)
    result["decision"] = verdict
    result["business_qa"]["legacy_decision"] = verdict
    result.update(qa_version=motion_video_qa.active_qa_version(),
                  sampling_version=motion_video_qa.FRAME_SAMPLING_VERSION,
                  vlm={"source": vlm_identity.VLM_MOTION_ACCEPTANCE_QA_VERSION})
    assert result["decision"] == verdict
    return result


@pytest.mark.parametrize("field", ["qa_version", "sampling_version", "vlm", "motion_business_contract", "business_qa"])
def test_missing_version_evidence_is_stale(field):
    qa = current_qa()
    assert mv.candidate_qa_is_current(qa, "COME_CLOSER")
    del qa[field]
    assert not mv.candidate_qa_is_current(qa, "COME_CLOSER")


@pytest.mark.parametrize("change", ["contract", "acceptance", "evaluator", "description"])
def test_obsolete_acceptance_or_evaluator_is_stale(change):
    qa = current_qa()
    if change == "contract":
        qa["motion_business_contract"]["version"] = "motion-qa-contract-v2"
    elif change == "evaluator":
        qa["vlm"]["source"] = "vlm-targeted-qa-v1"
    else:
        qa["motion_business_contract"]["acceptance"]["version" if change == "acceptance" else "description"] = "old"
    assert not mv.candidate_qa_is_current(qa, "COME_CLOSER")


@pytest.fixture
def retry_case(storage, monkeypatch):
    runs.__reset_for_tests()
    jobs.__reset_for_tests()
    h, _ = _prepare_pipeline(monkeypatch, storage)
    monkeypatch.setenv("PHASE6_MAX_PRIMARY", "1")
    provider = FakeVideoProvider("seedance", [GOOD()])
    version = _build_motion(h, "COME_CLOSER", [provider])
    candidate = mv._MOCK_CANDIDATES[0]
    version_row = mv._MOCK_VERSIONS[0]
    old_qa = current_qa("FAIL")
    old_qa["motion_business_contract"]["version"] = "motion-qa-contract-v2"
    old_qa["vlm"] = {"source": "vlm-targeted-qa-v1", "notes": "old running-gait rejection"}
    candidate.update(qa_result=old_qa, decision="FAIL", selected=False)
    version_row.update(status=mv.STATUS_FAILED, selected_candidate_id=None)
    version_row["analyzer_versions"]["motion_qa_contract"] = "motion-qa-contract-v2"
    row = dict(id="retry-run", user_id=USER, pet_id=PET, content_id=PET.removeprefix("pet_"),
               motion_id="COME_CLOSER", request_kind="PREMIUM_PRODUCT", idempotency_key="fixed-key",
               status=runs.STATUS_FAILED, current_stage=runs.STAGE_QA,
               identity_profile_id="identity", identity_profile_version=1,
               reference_set_id="references", reference_set_version=1,
               canonical_version_id=version.canonical_version_id, canonical_version=1,
               keyframes={"STAND_READY": {"id": version.start_keyframe_id, "version": version.start_keyframe_version}},
               motion_spec_version=version.motion_spec_version, motion_version_id=version.id,
               motion_version=version.version, selected_candidate_id=None, publication_id=None,
               provider_state={}, last_error={"code": "MOTION_QA_FAILED"},
               retry_count=0, credits_reserved=7, product_key="action:COME_CLOSER")
    runs._MOCK_RUNS.append(row)
    jobs._MOCK_JOBS.append(dict(id="existing-operation", run_id=row["id"],
                              submission_status="COLLECTED", external_job_id=candidate["provider_job_id"]))
    calls = {"qa": 0, "publication": 0, "package": 0}
    case = SimpleNamespace(row=row, candidate=candidate, version_row=version_row, old_qa=deepcopy(old_qa),
                           provider=provider, calls=calls, verdict="PASS", entered=None, release=None,
                           assets=deepcopy(storage), jobs=deepcopy(jobs._MOCK_JOBS))

    async def intake(*args, **kwargs):
        return row["content_id"]

    async def evaluate(**kwargs):
        calls["qa"] += 1
        if case.entered:
            case.entered.set()
            await case.release.wait()
        return current_qa(case.verdict)

    async def package(**kwargs):
        calls["package"] += 1

    async def publish(**kwargs):
        # Selection and persisted QA must have been updated before publication.
        assert candidate["decision"] == case.verdict
        assert candidate["selected"] is True
        assert version_row["selected_candidate_id"] == candidate["id"]
        assert kwargs["candidate_id"] == candidate["id"]
        calls["publication"] += 1
        return SimpleNamespace(publication_id="publication")

    async def no_companion(*args, **kwargs):
        return None

    def no_generation(*args, **kwargs):
        pytest.fail("QA-only retry crossed a video-generation boundary")

    monkeypatch.setattr(runs, "_validate_intake", intake)
    monkeypatch.setattr(runs, "_maybe_enqueue_system_companion", no_companion)
    monkeypatch.setattr(runs, "_reconcile_premium_after_stop", no_companion)
    monkeypatch.setattr(mv, "_evaluate_candidate_qa", evaluate)
    from backend.services import pet_identity_service, supabase_assets
    monkeypatch.setattr(supabase_assets, "get_client", lambda: SimpleNamespace(
        storage=SimpleNamespace(from_=lambda bucket: SimpleNamespace(download=lambda path: storage[path]))))
    monkeypatch.setattr(pet_identity_service, "_default_fetch_bytes", lambda obj: storage.get(obj.object_path))
    monkeypatch.setattr(mv, "build_motion_video", no_generation)
    monkeypatch.setattr(runs, "_video_providers", no_generation)
    monkeypatch.setattr(motion_delivery_service, "package_breathing_for_delivery", package)
    monkeypatch.setattr(premium_motion_finalization, "finalize_premium_motion", publish)
    yield case
    assert provider.calls == 1
    assert jobs._MOCK_JOBS == case.jobs
    assert storage == case.assets
    assert row["credits_reserved"] == 7
    assert len(mv._MOCK_VERSIONS) == len(mv._MOCK_CANDIDATES) == 1
    runs.__reset_for_tests()
    jobs.__reset_for_tests()


async def retry_and_work():
    await runs.retry_generation_run(user_id=USER, run_id="retry-run")
    return await runs.process_next_generation_run(worker_id="worker-1")


@pytest.mark.parametrize("verdict", ["PASS", "REVIEW", "FAIL"])
def test_stale_retry_persists_verdict_and_uses_existing_publication_gate(retry_case, verdict):
    case = retry_case
    case.verdict = verdict
    result = _run(retry_and_work())
    assert case.calls["qa"] == 1, result.last_error
    assert case.candidate["qa_result"]["decision"] == verdict
    assert case.candidate["generation_metadata"]["qa_history"][0]["qa_result"] == case.old_qa
    assert mv.candidate_qa_is_current(case.candidate["qa_result"], "COME_CLOSER")
    assert case.version_row["analyzer_versions"]["motion_qa_contract"] == motion_spec.MOTION_QA_CONTRACT_VERSION
    assert result.motion_version_id == case.version_row["id"]
    assert result.retry_count == 1
    if verdict in {"PASS", "REVIEW"}:
        assert result.status == runs.STATUS_PUBLISHED
        assert result.selected_candidate_id == case.candidate["id"]
        assert result.last_error is None
        assert case.calls["publication"] == 1
    else:
        assert result.status == runs.STATUS_FAILED
        assert result.last_error["code"] in {"MOTION_QA_REVIEW", "MOTION_QA_FAILED"}
        assert result.publication_id is None
        assert case.calls["publication"] == 0
    # Repeated retries reuse the current verdict; no second QA call.
    _run(retry_and_work())
    assert case.calls["qa"] == 1, result.last_error
    assert len(case.candidate["generation_metadata"]["qa_history"]) == 1


def test_retry_refreshes_regressed_review_block_without_generation(retry_case):
    case = retry_case
    stale = current_qa("REVIEW")
    stale["business_qa"].update(
        integrity_status="REVIEW", delivery_action="BLOCK", retry_action="STOP",
        terminal_state=None,
    )
    case.candidate.update(qa_result=stale, decision="REVIEW", selected=False)
    case.version_row.update(status=mv.STATUS_REVIEW, selected_candidate_id=None)
    case.row["last_error"] = {"code": "MOTION_QA_REVIEW"}
    case.verdict = "REVIEW"

    assert not mv.candidate_qa_is_current(stale, "COME_CLOSER")
    result = _run(retry_and_work())

    assert result.status == runs.STATUS_PUBLISHED
    assert result.motion_version_id == case.version_row["id"]
    assert result.selected_candidate_id == case.candidate["id"]
    assert case.candidate["decision"] == "REVIEW"
    assert case.candidate["qa_result"]["business_qa"]["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert case.calls == {"qa": 1, "publication": 1, "package": 1}


def test_current_candidate_reuses_verdict_without_qa(retry_case):
    case = retry_case
    case.candidate["qa_result"] = current_qa("FAIL")
    result = _run(retry_and_work())
    assert result.status == runs.STATUS_FAILED
    assert case.calls["qa"] == 0
    assert "qa_history" not in case.candidate["generation_metadata"]


def test_duplicate_retries_and_workers_share_one_qa_evaluation(retry_case):
    case = retry_case
    async def race():
        case.entered, case.release = asyncio.Event(), asyncio.Event()
        await asyncio.gather(*(runs.retry_generation_run(user_id=USER, run_id="retry-run") for _ in range(3)))
        worker = asyncio.create_task(runs.process_next_generation_run(worker_id="worker-1"))
        await asyncio.wait_for(case.entered.wait(), timeout=5)
        try:
            duplicate = await runs.retry_generation_run(user_id=USER, run_id="retry-run")
            assert duplicate.status == runs.STATUS_RUNNING
            assert await runs.process_next_generation_run(worker_id="worker-2") is None
        finally:
            case.release.set()
        return await worker
    result = _run(race())
    assert result.status == runs.STATUS_PUBLISHED
    assert result.retry_count == 1
    assert case.calls["qa"] == case.calls["publication"] == 1
    assert len(case.candidate["generation_metadata"]["qa_history"]) == 1


def test_missing_legacy_metadata_is_reevaluated(retry_case):
    case = retry_case
    case.candidate["qa_result"] = {"decision": "FAIL", "vlm": {"notes": "legacy evidence"}}
    old = deepcopy(case.candidate["qa_result"])
    result = _run(retry_and_work())
    assert result.status == runs.STATUS_PUBLISHED
    assert case.calls["qa"] == 1
    assert case.candidate["generation_metadata"]["qa_history"][0]["qa_result"] == old


def test_failed_reevaluation_keeps_pins_and_evidence_and_can_retry(retry_case, monkeypatch):
    case = retry_case
    evaluate = mv._evaluate_candidate_qa
    async def unavailable(**kwargs):
        raise mv.MotionVideoError("QA_TEMPORARY", "temporary QA failure", status=503)
    monkeypatch.setattr(mv, "_evaluate_candidate_qa", unavailable)
    result = _run(retry_and_work())
    assert result.status == runs.STATUS_FAILED
    assert result.current_stage == runs.STAGE_QA
    assert result.last_error["code"] == "QA_TEMPORARY"
    assert result.motion_version_id == case.version_row["id"]
    assert case.candidate["qa_result"] == case.old_qa
    assert case.calls["publication"] == 0
    monkeypatch.setattr(mv, "_evaluate_candidate_qa", evaluate)
    result = _run(retry_and_work())
    assert result.status == runs.STATUS_PUBLISHED
    assert result.retry_count == 2
    assert case.calls["qa"] == 1


def test_lost_run_claim_cannot_start_reevaluation(retry_case):
    case = retry_case
    async def lost_claim():
        await runs.retry_generation_run(user_id=USER, run_id="retry-run")
        claimed = await runs._claim_next("old-worker")
        case.row["execution_token"] = "new-owner"
        motion = await mv.get_motion_version(user_id=USER, pet_id=PET, motion_id="COME_CLOSER", version=1)
        with pytest.raises(runs.PetGenerationRunError, match="lease") as exc:
            await runs._refresh_stale_motion_qa(claimed, motion)
        assert exc.value.code == "WORKER_LEASE_LOST"
    _run(lost_claim())
    assert case.calls["qa"] == 0


def test_retry_after_publication_failure_reuses_persisted_new_qa(retry_case, monkeypatch):
    case = retry_case
    publish = premium_motion_finalization.finalize_premium_motion
    async def unavailable(**kwargs):
        raise premium_motion_finalization.PremiumFinalizationError("PUBLICATION_TEMPORARY", "try again", status=503)
    monkeypatch.setattr(premium_motion_finalization, "finalize_premium_motion", unavailable)
    failed = _run(retry_and_work())
    assert failed.status == runs.STATUS_FAILED
    assert failed.current_stage == runs.STAGE_PUBLICATION
    assert case.candidate["decision"] == "PASS"
    assert case.calls["qa"] == 1
    monkeypatch.setattr(premium_motion_finalization, "finalize_premium_motion", publish)
    recovered = _run(retry_and_work())
    assert recovered.status == runs.STATUS_PUBLISHED
    assert recovered.retry_count == 2
    assert case.calls["qa"] == 1
    assert len(case.candidate["generation_metadata"]["qa_history"]) == 1
