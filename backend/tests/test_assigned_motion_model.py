"""Assigned-model integration tests: real registry/adapters, fake paid generation."""

from dataclasses import replace

import pytest

from backend.services import durable_provider_jobs as durable
from backend.services import motion_spec as ms
from backend.services import motion_reference_service as references
from backend.services import motion_video_service as mv
from backend.services import video_motion_providers as vp

from .test_canonical_pet_builder import GOOD
from .test_motion_video_generation import (
    _mock_backend,  # noqa: F401
    _prepare_pipeline,
    _run,
    conformance_ok,
    sampler_identical,
    storage,  # noqa: F401
)
from .test_pet_reference_sets import PET, USER


def _assigned_adapter(monkeypatch, motion_id, model, *, error=False):
    monkeypatch.setenv(f"MOTION_MODEL_{motion_id}", model)
    monkeypatch.setenv("FAL_KEY", "test-only")
    monkeypatch.setenv(f"MOTION_VENDOR_{model.upper()}", "fal")
    monkeypatch.setenv("PHASE6_MAX_PRIMARY", "1")
    adapter = vp.get_provider(model)
    requests = []
    payloads = []

    def generate(request):
        requests.append(request)
        payloads.append(adapter.build_payload(request))
        if error:
            raise vp.VideoProviderError("PROVIDER_FAILED", "test provider error")
        return vp.MotionVideoResult(video_bytes=GOOD(), provider=model, model=adapter.model_name())

    monkeypatch.setattr(adapter, "generate", generate)
    original_lookup = vp.get_provider
    lookups = []

    def lookup(name):
        lookups.append(name)
        assert name == model, f"Unexpected cross-model lookup: {name}"
        return original_lookup(name)

    monkeypatch.setattr(vp, "get_provider", lookup)
    monkeypatch.setattr(
        vp, "reference_capable_providers",
        lambda: pytest.fail("Must not search for a replacement reference model"),
    )
    return adapter, requests, payloads, lookups


def _build(h, motion_id, **kwargs):
    return _run(mv.build_motion_video(
        user_id=USER, pet_id=PET, motion_id=motion_id,
        fetch_bytes=h.kf_fetch,
        sign_url_fn=lambda ref: f"https://signed.test/{ref.object_path}",
        frame_sampler=sampler_identical, conformance_fn=conformance_ok, **kwargs,
    ))


@pytest.mark.parametrize("motion_id", ["BREATHING", "COME_CLOSER"])
def test_assigned_grok_generates_start_only_without_substitution(monkeypatch, storage, motion_id):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    _, requests, payloads, lookups = _assigned_adapter(monkeypatch, motion_id, "grok_imagine_lite")
    contract = _run(ms.resolve_video_generation_spec(user_id=USER, pet_id=PET, motion_id=motion_id))
    assert contract["start_keyframe"]["role"] == "STAND_READY"
    # A persisted legacy contract must not re-enable its old fallback list.
    contract["provider_order"] += ["seedance", "wan_3_standard", "kling_3"]
    version = _build(h, motion_id, precomputed_contract=contract)
    assert lookups == ["grok_imagine_lite"]
    assert len(requests) == 1
    assert requests[0].start_image_bytes
    assert requests[0].end_image_bytes is None
    assert requests[0].end_image_url is None
    assert requests[0].motion_reference_url is None
    assert payloads[0]["image_url"] == requests[0].start_image_url
    assert version.video_strategy == ms.STRATEGY_I2V
    assert {c.provider for c in version.candidates} == {"grok_imagine_lite"}
    assert requests[0].output_spec == mv.build_output_spec(
        duration_range=contract["duration_range_sec"], motion_class=contract["motion_class"]
    )[0]
    if motion_id == "BREATHING":
        assert "return to the exact starting pose" in requests[0].prompt
        assert "breathing" in requests[0].prompt
        assert version.status == mv.STATUS_COMPLETE
        assert "loop_return" in version.candidates[0].qa_result["checks"]
    else:
        assert "original home distance" not in requests[0].prompt
        assert contract["target_keyframe"] is None


@pytest.mark.parametrize("model", ["grok_imagine_lite", "seedance"])
def test_return_home_uses_assigned_model_and_keeps_qa_target(monkeypatch, storage, model):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    adapter, requests, payloads, lookups = _assigned_adapter(monkeypatch, "RETURN_HOME", model)
    path = f"{USER}/{PET[4:]}/derived/close_position_run1.png"
    storage[path] = GOOD()
    contract = _run(ms.resolve_video_generation_spec(
        user_id=USER, pet_id=PET, motion_id="RETURN_HOME",
        start_image_override={"bucket": "user-assets", "object_path": path},
    ))
    original_strategy = contract["video_strategy"]
    version = _build(h, "RETURN_HOME", precomputed_contract=contract)
    assert lookups == [model]
    assert len(requests) == 1
    req = requests[0]
    assert req.start_image_bytes == storage[path]
    assert {c.provider for c in version.candidates} == {model}
    assert contract["video_strategy"] == original_strategy  # No mutation of cached contract.
    assert contract["target_keyframe"]["role"] == "STAND_READY"
    checks = version.candidates[0].qa_result["checks"]
    assert checks["reaches_target_pose"] == "PASS"
    target_ref = next(r for r in version.candidates[0].input_references if r["kind"] == "target_keyframe")
    if adapter.supports_end_frame:
        assert req.end_image_bytes is not None and req.end_image_url
        assert payloads[0]["end_image_url"] == req.end_image_url
        assert version.video_strategy == ms.STRATEGY_START_END
        assert target_ref["conditioning_sent"] is True
    else:
        assert req.end_image_bytes is None and req.end_image_url is None
        assert "end_image_url" not in payloads[0]
        assert version.video_strategy == ms.STRATEGY_I2V
        assert "original home distance" in req.prompt
        assert "home framing" in req.prompt
        assert "balanced standing pose" in req.prompt
        assert "second frame" not in req.prompt
        assert target_ref["conditioning_sent"] is False
        assert "sent_input" not in target_ref


def test_endpoint_capable_breathing_receives_same_start_and_end(monkeypatch, storage):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    _, requests, payloads, lookups = _assigned_adapter(monkeypatch, "BREATHING", "seedance")
    version = _build(h, "BREATHING")
    assert lookups == ["seedance"]
    assert version.video_strategy == ms.STRATEGY_START_END
    assert requests[0].start_image_bytes == requests[0].end_image_bytes
    assert payloads[0]["image_url"] == payloads[0]["end_image_url"]


def test_grok_error_does_not_submit_another_model(monkeypatch, storage):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    _, requests, _, lookups = _assigned_adapter(monkeypatch, "BREATHING", "grok_imagine_lite", error=True)
    version = _build(h, "BREATHING")
    assert lookups == ["grok_imagine_lite"]
    assert len(requests) == 1
    assert version.status == mv.STATUS_FAILED
    assert {c.provider for c in version.candidates} == {"grok_imagine_lite"}
    assert all("PROVIDER_FAILED" in c.error for c in version.candidates)


def test_durable_wrapper_preserves_grok_and_unsupported_endpoint(monkeypatch, storage):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    adapter, requests, _, _ = _assigned_adapter(monkeypatch, "BREATHING", "grok_imagine_lite")
    wrapped = durable.durable_video_providers([adapter], run_id="run", user_id=USER, pet_id=PET)
    assert len(wrapped) == 1 and wrapped[0].delegate is adapter
    # Only replace durable I/O; the actual wrapper capability/identity is retained.
    monkeypatch.setattr(wrapped[0], "generate", adapter.generate)
    version = _build(h, "BREATHING", providers=wrapped)
    assert version.video_strategy == ms.STRATEGY_I2V
    assert requests[0].end_image_bytes is None
    assert version.candidates[0].provider == "grok_imagine_lite"


@pytest.mark.parametrize("reference_available", [False, True])
def test_required_reference_uses_prompt_on_assigned_grok(monkeypatch, storage, reference_available):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    _, requests, _, lookups = _assigned_adapter(monkeypatch, "RUN", "grok_imagine_lite")
    monkeypatch.setitem(ms.MOTIONS, "RUN", replace(
        ms.MOTIONS["RUN"], motion_reference_policy=ms.REF_REQUIRED,
    ))

    async def resolve_reference(**kwargs):
        if not reference_available:
            return None
        return {
            "reference_key": "run-reference", "version": 1, "duration_sec": 4,
            "asset": {"bucket": "b", "object_path": "reference.mp4"},
        }

    monkeypatch.setattr(references, "resolve_motion_reference", resolve_reference)
    version = _build(h, "RUN")
    assert lookups == ["grok_imagine_lite"]
    assert len(requests) == 1
    assert requests[0].motion_reference_url is None
    assert "continuous limb timing" in requests[0].prompt
    assert version.video_strategy == ms.STRATEGY_I2V
    assert version.candidates[0].generation_metadata["motion_reference"]["consumed"] is False


def test_return_home_provider_error_does_not_invalidate_come_closer(monkeypatch, storage):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    _assigned_adapter(monkeypatch, "COME_CLOSER", "grok_imagine_lite")
    closer = _build(h, "COME_CLOSER")
    assert closer.status == mv.STATUS_COMPLETE
    before = _run(mv._version_rows(PET, "COME_CLOSER"))

    _assigned_adapter(monkeypatch, "RETURN_HOME", "grok_imagine_lite", error=True)
    path = f"{USER}/{PET[4:]}/derived/close_position_failed_return.png"
    storage[path] = GOOD()
    contract = _run(ms.resolve_video_generation_spec(
        user_id=USER, pet_id=PET, motion_id="RETURN_HOME",
        start_image_override={"bucket": "user-assets", "object_path": path},
    ))
    home = _build(h, "RETURN_HOME", precomputed_contract=contract)
    assert home.status == mv.STATUS_FAILED
    assert _run(mv._version_rows(PET, "COME_CLOSER")) == before


@pytest.mark.parametrize("motion_id", list(ms.MOTIONS))
def test_every_motion_honors_explicit_model_assignment(monkeypatch, motion_id):
    monkeypatch.setenv(f"MOTION_MODEL_{motion_id}", "grok_imagine_lite")
    assert ms.provider_order_for_motion(motion_id) == ("grok_imagine_lite",)
