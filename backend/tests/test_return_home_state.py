"""Keep the literal derived input through the real worker/spec/motion path."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.services import durable_provider_jobs as jobs
from backend.services import motion_spec as ms
from backend.services import motion_video_service as mv
from backend.services import pet_generation_run_service as runs
from backend.services import premium_run_fulfillment as fulfillment
from backend.services import supabase_assets
from .test_motion_video_generation import (
    FakeVideoProvider, _mock_backend, _prepare_pipeline, conformance_ok,
    sampler_identical, storage,  # noqa: F401
)
from .test_pet_reference_sets import PET, USER
from .test_return_home_flow import _png_bytes, _run


@pytest.fixture(autouse=True)
def clean_runs():
    runs.__reset_for_tests()
    jobs.__reset_for_tests()
    yield
    runs.__reset_for_tests()
    jobs.__reset_for_tests()


def override():
    return {
        "bucket": supabase_assets.BUCKET,
        "object_path": f"{USER}/{PET[4:]}/derived/close_position_cc-source.png",
        "reference_id": "derived-reference",
        "source": {"come_closer_run_id": "cc-source", "candidate_id": "cc-candidate"},
        "future_lineage": {"frame": "final", "version": 7},
    }


def test_provider_state_preserves_full_override_verbatim(monkeypatch):
    original = override()
    state = {"close_position_override": original, "_operator": {"note": "keep"},
             "_business_qa_cutover": {"mode": "off"}, "stale-job": {"status": "old"}}
    before = deepcopy(state)
    monkeypatch.setattr(jobs, "summary_for_run", lambda _: {"new-job": {"status": "SUBMITTED"}})
    result = runs._provider_state(SimpleNamespace(id="run-1", provider_state=state))
    assert result["close_position_override"] is original
    assert result["close_position_override"] == before["close_position_override"]
    assert result["_operator"] == before["_operator"]
    assert result["_business_qa_cutover"] == before["_business_qa_cutover"]
    assert "new-job" in result and "stale-job" not in result
    assert state == before


@pytest.mark.parametrize("revived", [False, True])
def test_override_survives_canonical_and_reaches_actual_motion_provider(monkeypatch, storage, revived):
    harness, _ = _prepare_pipeline(monkeypatch, storage)
    original = override()
    image = _png_bytes()
    storage[original["object_path"]] = image
    rid = _run(fulfillment.ensure_system_companion(
        user_id=USER, pet_id=PET, action_id="COME_CLOSER", source_run_id="cc-source",
        start_image_override=original,
    ))
    if revived:
        _run(runs._update(rid, {
            "status": runs.STATUS_FAILED, "current_stage": runs.STAGE_CANONICAL,
            "provider_state": {}, "last_error": {"code": "DERIVED_START_MISSING"},
        }))
        assert _run(fulfillment.ensure_system_companion(
            user_id=USER, pet_id=PET, action_id="COME_CLOSER", source_run_id="cc-source",
            start_image_override=original,
        )) == rid

    snapshots = []
    progress = runs._progress

    async def record_progress(run, fields):
        result = await progress(run, fields)
        snapshots.append((result.current_stage, deepcopy(result.provider_state), dict(fields)))
        return result

    monkeypatch.setattr(runs, "_progress", record_progress)
    resolve = ms.resolve_video_generation_spec
    resolved = []

    async def record_spec(**kwargs):
        assert kwargs["start_image_override"] == original
        contract = await resolve(**kwargs)
        resolved.append(contract)
        return contract

    monkeypatch.setattr(ms, "resolve_video_generation_spec", record_spec)

    class PendingVideo(FakeVideoProvider):
        durable_execution = True

        def generate(self, request):
            self.requests.append(request)
            raise jobs.ProviderWorkPending("fake-operation", "IN_QUEUE")

    provider = PendingVideo("kling", [], end_frame=True)
    monkeypatch.setattr(runs, "_video_providers", lambda *_: [provider])
    build = mv.build_motion_video

    async def build_with_fake_io(**kwargs):
        def fetch(ref):
            if ref.object_path in storage:
                return storage[ref.object_path]
            return harness.kf_fetch(ref)
        return await build(**kwargs, fetch_bytes=fetch, frame_sampler=sampler_identical,
                           conformance_fn=conformance_ok)

    monkeypatch.setattr(mv, "build_motion_video", build_with_fake_io)
    result = _run(runs.process_next_generation_run(worker_id="test-return-home-worker"))
    assert result.status == runs.STATUS_WAITING_PROVIDER, result.last_error
    assert result.current_stage == runs.STAGE_MOTION_GENERATION
    assert result.provider_state["close_position_override"] == original
    assert result.retry_count == int(revived)
    assert resolved[0]["start_keyframe"]["raw"]["object_path"] == original["object_path"]
    assert resolved[0]["start_keyframe"]["derived"] is True
    assert provider.requests[0].start_image_bytes == image
    assert provider.requests[0].end_image_bytes is not None
    assert any(stage == runs.STAGE_CANONICAL and "canonical_version_id" in fields
               for stage, _, fields in snapshots)
    for _, state, _ in snapshots:
        assert state["close_position_override"] == original


def test_old_worker_failure_resumes_submitted_job_and_original_frame(monkeypatch, storage):
    from backend.services import close_position_service, motion_delivery_service, premium_motion_finalization
    from backend.services.provider_job_contract import ProviderSubmission, ProviderJobCheck, SUCCEEDED
    from backend.services.video_motion_providers import MotionVideoResult
    from .test_canonical_pet_builder import GOOD

    harness, _ = _prepare_pipeline(monkeypatch, storage)
    saved = _run(close_position_service.persist_close_position(
        user_id=USER, content_id=PET[4:], pet_id=PET, come_closer_run_id="cc-source",
        come_closer_motion_version_id="cc-version", selected_candidate_id="cc-candidate",
        source_video_object_path="cc.mp4", png=_png_bytes(),
    ))
    original = {"bucket": saved.bucket, "object_path": saved.object_path, "reference_id": saved.reference_id}
    rid = _run(fulfillment.ensure_system_companion(
        user_id=USER, pet_id=PET, action_id="COME_CLOSER", source_run_id="cc-source",
        start_image_override=original,
    ))

    class DurableFake(FakeVideoProvider):
        supports_durable_jobs = True
        submits = 0
        collects = 0
        def submit(self, request):
            self.submits += 1
            self.requests.append(request)
            return ProviderSubmission("existing-return-job")
        def check(self, job_id):
            assert job_id == "existing-return-job"
            return ProviderJobCheck(SUCCEEDED, "COMPLETED")
        def collect(self, job_id):
            self.collects += 1
            return MotionVideoResult(video_bytes=GOOD(), provider=self.name,
                                     model=self.model_name(), external_job_id=job_id)

    provider = DurableFake("seedance", [])
    # Resume resolves the recorded adapter directly, independently of routing.
    monkeypatch.setattr(runs.video_motion_providers, "pinned_provider", lambda identity: provider)
    monkeypatch.setattr(runs, "_video_providers", lambda run, mid: [jobs.DurableVideoProvider(
        provider, run_id=run.id, user_id=run.user_id, pet_id=run.pet_id, provider_operation=jobs.OP_MOTION)])
    build = mv.build_motion_video
    async def build_with_io(**kwargs):
        return await build(**kwargs, fetch_bytes=lambda obj: storage.get(obj.object_path) or harness.kf_fetch(obj),
                           frame_sampler=sampler_identical, conformance_fn=conformance_ok)
    monkeypatch.setattr(mv, "build_motion_video", build_with_io)
    publications = []
    async def package(**kwargs):
        return None
    async def publish(**kwargs):
        publications.append(kwargs)
        if len(publications) == 1:
            raise premium_motion_finalization.PremiumFinalizationError("TEMPORARY", "retry publication", status=503)
        return SimpleNamespace(publication_id="return-home-publication")
    async def no_commerce(**kwargs):
        pytest.fail("RETURN_HOME entered commercial finalization")
    monkeypatch.setattr(motion_delivery_service, "package_breathing_for_delivery", package)
    monkeypatch.setattr(premium_motion_finalization, "finalize_system_motion", publish)
    monkeypatch.setattr(premium_motion_finalization, "finalize_premium_motion", no_commerce)

    waiting = _run(runs.process_next_generation_run(worker_id="new-worker"))
    assert waiting.status == runs.STATUS_WAITING_PROVIDER, waiting.last_error
    assert provider.submits == 1
    job = deepcopy(jobs.list_for_run(rid)[0])
    # Reproduce the older worker: reject on the next tick and drop the input.
    _run(runs._update(rid, {"status": runs.STATUS_FAILED, "last_error": {"code": "UNSUPPORTED_MOTION"},
                          "provider_state": {}, "next_attempt_at": None}))
    retried = _run(runs.retry_generation_run(user_id=USER, run_id=rid))
    assert retried.provider_state["close_position_override"] == original
    assert retried.credits_reserved == 0 and retried.product_key is None
    assert jobs.list_for_run(rid)[0] == job  # Retry itself never touches the job.
    completed = _run(runs.process_next_generation_run(worker_id="new-worker"))
    assert completed.current_stage == runs.STAGE_PUBLICATION, completed.last_error
    assert completed.motion_version_id == job["phase_version_id"]
    assert provider.submits == provider.collects == 1
    assert provider.requests[0].start_image_bytes == storage[saved.object_path]
    # A pinned derived-start motion must also survive a later publication retry.
    _run(runs.retry_generation_run(user_id=USER, run_id=rid))
    published = _run(runs.process_next_generation_run(worker_id="new-worker"))
    assert published.status == runs.STATUS_PUBLISHED, published.last_error
    assert len(jobs.list_for_run(rid)) == 1
    assert provider.submits == provider.collects == 1
    assert len(mv._MOCK_VERSIONS) == len(mv._MOCK_CANDIDATES) == 1
