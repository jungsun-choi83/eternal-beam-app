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
