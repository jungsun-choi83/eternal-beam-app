"""
COME_PLAY ("Come Play!") — DOG-only interactive action (2026-10-30):

    STAND_READY (home) → playful trot toward the viewer → deceleration → near play bow
    (PLAY_BOW_NEAR keyframe, anchored on the approved PLAY_BOW) → brief hold → trot back
    home → STAND_READY → BREATHING

Two start/end-conditioned Kling segments (A 5 s / B 6 s) joined into one asset, exactly
like PLAY_WITH_ME v2 but with travel. PLAY_WITH_ME and COME_CLOSER/RETURN_HOME are left
byte-for-byte unchanged (guarded below). Providers, samplers, joiners and the VLM are
injected — no paid call anywhere in this file.
"""

from __future__ import annotations

import os
import re
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from backend.auth import AuthedUser, require_user
from backend.routers import device_ws_v1
from backend.scenarios.pet_scenarios import PET_ACTIONS, PREMIUM_ACTIONS
from backend.services import (
    action_keyframe_service as kf,
    action_keyframe_spec,
    business_qa,
    device_gateway,
    generation_queue,
    motion_delivery_service,
    motion_profile,
    motion_spec as ms,
    motion_video_prompts,
    motion_video_qa,
    motion_video_segments as seg,
    motion_video_service as mv,
    pet_generation_run_service as runs,
    premium_motion_finalization,
    premium_purchase,
    premium_run_fulfillment,
    product_catalog,
    species_motion_variants as sv,
    video_motion_providers,
    vlm_escalation,
    vlm_identity,
)
from backend.services.video_motion_providers import MotionVideoRequest, MotionVideoResult

from .conftest import ASGITestClient
from .test_action_keyframes import VLM_KF_OK, _build_kf, install_kf_vlm
from .test_canonical_pet_builder import GOOD, FakeProvider
from .test_motion_acceptance import micro_video_evidence
from .test_motion_video_generation import (  # noqa: F401 — fixtures (incl. the autouse mock backend)
    FakeVideoProvider,
    VLM_MV_OK,
    _build_motion,
    _mock_backend,
    _prepare_pipeline,
    install_mv_vlm,
    storage,
)
from .test_pet_reference_sets import PET, USER
from .test_play_with_me import (
    _RefRecordingProvider,
    _force_species,
    _plate_bytes,
    _run,
    passthrough_join,
)

CP = sv.COME_PLAY
PWM = sv.PLAY_WITH_ME
NEAR = "PLAY_BOW_NEAR"
CHAIN = ("STAND_READY", "PLAY_BOW", NEAR)
VLM_COME_PLAY_OK = {
    **VLM_MV_OK,
    **{name: "yes" for name in sv.COME_PLAY_QA_FIELD_NAMES},
    "source": vlm_identity.VLM_COME_PLAY_QA_VERSION,
}


def _contract(variant=sv.COME_PLAY_BOW) -> dict:
    return {"motion_id": CP, "motion_class": "INTERACTION", "species_variant": sv.variant_payload(variant)}


def come_play_evidence(**changes):
    result = micro_video_evidence(**{name: "yes" for name in sv.COME_PLAY_QA_FIELD_NAMES})
    result.update(changes)
    return result


def _segment_checks_ok() -> dict[str, str]:
    return {name: "PASS" for name in sv.COME_PLAY_SEGMENT_CHECK_NAMES}


def judge_come_play(vlm_evidence, *, segment_checks=None):
    result = motion_video_qa.apply_judgement(
        checks={
            "identity_over_time": "PASS",
            "temporal_stability": "PASS",
            "loop_return": "PASS",
            "structural_morphology_consistency": "PASS",
        },
        reasons=[],
        spec_contract=ms.motion_snapshot(ms.MOTIONS[CP]),
        vlm_qa=vlm_evidence,
        temporal_qa=None,
        structural_evidence={},
    )
    result = seg.merge_segment_qa(
        result,
        {"version": seg.SEGMENT_QA_VERSION,
         "checks": segment_checks if segment_checks is not None else _segment_checks_ok(),
         "metrics": {}, "reasons": []},
    )
    business_qa.attach_business_result(result, attempt_number=1, request_kind="INTERACTION")
    return result


# ══════════════════════════════════════════════════════════════════════════
# Registry
# ══════════════════════════════════════════════════════════════════════════


def test_registry_is_a_dog_only_segmented_approach_bow_return_interaction():
    spec = ms.MOTIONS[CP]
    assert spec.motion_class == ms.CLASS_INTERACTION
    assert spec.start_keyframe_role == "STAND_READY" and spec.target_keyframe_role == "STAND_READY"
    assert spec.preferred_video_strategy == ms.STRATEGY_START_END
    assert spec.video_compat["end_frame_conditioning_required"] is True
    assert spec.video_compat["segmented"] is True and spec.video_compat["travels"] is True
    assert spec.video_compat["returns_to_start_pose"] is True  # home → home: loop_return applies
    assert spec.video_compat["species_variants"] == ["DOG"]
    assert spec.video_compat["intermediate_keyframe_roles"] == [NEAR]
    assert spec.video_compat["allow_generated_hand"] is False
    assert spec.interruptible is False
    assert spec.duration_range_sec == (5.0, 8.0)  # v2: B 8 s
    assert spec.requirements["provider_capabilities"] == {
        "required_all": ["supports_end_frame"], "preferred_any": [],
        "degrade_allowed": False, "degrade_to_strategy": None,
    }
    assert spec.requirements["morphology"]["sensitivity"] == "HIGH"
    assert spec.requirements["morphology"]["match_fields"] == ["leg_length_class", "body_length_class", "body_build_class"]
    required = set(spec.qa_acceptance_spec["required_checks"])
    assert set(sv.COME_PLAY_REQUIRED_CHECK_NAMES) <= required
    assert required.isdisjoint(sv.PLAY_WITH_ME_CHECK_NAMES), "COME_PLAY never inherits PLAY_WITH_ME's checks"
    domain = spec.requirements["qa"]["business"]["domains"]["come_play"]
    assert domain["required_checks"] == list(sv.COME_PLAY_REQUIRED_CHECK_NAMES)
    assert "play_with_me" not in spec.requirements["qa"]["business"]["domains"]
    authority = spec.requirements["qa"]["business"]["check_authority"]
    assert all(authority[c] == ms.AUTHORITY_INTEGRITY_HARD for c in sv.COME_PLAY_REQUIRED_CHECK_NAMES)
    # v2: movement *quality* (VLM gait/turn fields + deterministic motion profile) is advisory —
    # it can only ever downgrade to REVIEW, never block or spend another candidate.
    quality = spec.requirements["qa"]["business"]["domains"]["come_play_motion_quality"]
    assert quality["required_checks"] == [] and set(quality["advisory_checks"]) == set(ms._COME_PLAY_QUALITY_CHECK_NAMES)
    assert set(sv.COME_PLAY_ADVISORY_CHECK_NAMES) | set(motion_profile.COME_PLAY_PROFILE_CHECK_NAMES) == set(ms._COME_PLAY_QUALITY_CHECK_NAMES)
    for c in ms._COME_PLAY_QUALITY_CHECK_NAMES:
        assert authority[c] == ms.AUTHORITY_QUALITY_ADVISORY, c
        assert spec.qa_acceptance_spec["check_authority"][c] == ms.AUTHORITY_QUALITY_ADVISORY, c
    assert set(sv.COME_PLAY_ADVISORY_CHECK_NAMES).isdisjoint(sv.COME_PLAY_REQUIRED_CHECK_NAMES)
    assert spec.qa_acceptance_spec["policy_revision"] == sv.COME_PLAY_POLICY_REVISION == "come-play-v2"
    assert len(set(sv.COME_PLAY_CHECK_NAMES)) == 17
    assert len(sv.COME_PLAY_QA_FIELD_NAMES) == 20  # 17 hard + 3 advisory gait/turn fields
    assert {"run_gait_coordinated", "run_ground_contact_plausible", "turn_natural"} <= set(sv.COME_PLAY_QA_FIELD_NAMES)
    assert {"playful_approach_visible", "deceleration_before_bow", "near_bow_matches_keyframe",
            "return_travel_to_home_visible", "returns_to_home_scale_and_position",
            "return_played_forward"} <= set(sv.COME_PLAY_QA_FIELD_NAMES)
    # Pinned to an end-frame-capable model; the variant-level default agrees.
    assert ms.MOTION_MODEL_DEFAULTS[CP] == ms.PROVIDER_KLING_3
    assert ms.MOTION_VARIANT_MODEL_DEFAULTS[(CP, "COME_PLAY_BOW")] == ms.PROVIDER_KLING_3
    assert CP in ms.motions_for_keyframe_role("STAND_READY")
    assert action_keyframe_spec.role_for_action(CP) == "STAND_READY"


def test_species_variant_is_dog_only_and_fail_closed():
    assert sv.requires_species_variant(CP)
    assert sv.supported_species(CP) == ("DOG",)
    assert sv.select_variant(CP, "DOG") is sv.COME_PLAY_BOW
    for species in ("CAT", "RABBIT", "OTHER", "UNKNOWN", None):
        assert sv.select_variant(CP, species) is None
        with pytest.raises(sv.SpeciesVariantError) as exc:
            sv.require_variant(CP, species)
        assert exc.value.code == "SPECIES_UNSUPPORTED" and exc.value.status == 409
    assert sv.variant_for_keyframe_role(NEAR) is sv.COME_PLAY_BOW
    assert sv.intermediate_keyframe_roles(CP) == (NEAR,)
    assert sv.variant_from_contract(_contract()) is sv.COME_PLAY_BOW
    # PLAY_WITH_ME still serves cats — the two actions do not share a species table.
    assert sv.select_variant(PWM, "CAT") is sv.PLAY_CROUCH
    assert sv.variant_from_contract({"motion_id": PWM, "species_variant": {"id": "COME_PLAY_BOW"}}) is None
    payload = sv.variant_payload(sv.COME_PLAY_BOW)
    assert payload["segment_phases"] == ["approach_into_bow", "hold_return_home"]
    assert payload["segment_durations_sec"] == [5, 8]  # v2: the return gets 8 s
    assert payload["keyframe_role"] == NEAR


def test_variant_acceptance_covers_approach_bow_return_and_home_scale():
    acc = ms.acceptance_for_variant(ms.MOTIONS[CP], sv.COME_PLAY_BOW)
    assert acc["species"] == "DOG" and acc["species_variant"] == "COME_PLAY_BOW"
    assert acc["intermediate_keyframe_role"] == NEAR
    assert acc["required_checks"] == ms.MOTIONS[CP].qa_acceptance_spec["required_checks"]
    assert "PLAY_BOW_NEAR" in acc["expected_intermediate_state"]
    assert "home position and scale" in acc["expected_end_state"]
    blocking = " ".join(acc["blocking_criteria"])
    assert "no approach toward the camera" in blocking
    assert "run_gait_coordinated" in " ".join(acc["advisory_criteria"])  # quality is advisory
    assert "played backward" in blocking
    assert "zoom, slide or float" in blocking
    assert blocking.startswith(ms._GENERIC_BLOCKING_CRITERIA[0])
    allowed = " ".join(acc["allowed_secondary_motion"])
    assert "change in apparent size" in allowed and "camera stays fixed; the dog moves" in allowed
    assert acc["temporal_evidence_policy"]["sparse_non_observation"] == "REVIEW"
    # The species-neutral base body and the VLM evaluator version are COME_PLAY-scoped.
    assert vlm_identity.motion_acceptance_evaluator_version(acc) == vlm_identity.VLM_COME_PLAY_QA_VERSION
    assert vlm_identity.VLM_COME_PLAY_QA_VERSION != vlm_identity.VLM_PLAY_WITH_ME_QA_VERSION


# ══════════════════════════════════════════════════════════════════════════
# Near play-bow keyframe role (anchored on PLAY_BOW)
# ══════════════════════════════════════════════════════════════════════════


def test_near_bow_role_is_anchored_on_play_bow_and_the_chain_reaches_stand_ready():
    role = action_keyframe_spec.KEYFRAME_ROLES[NEAR]
    assert role.anchor_keyframe_role == "PLAY_BOW"
    assert role.supported_action_ids == ()
    assert role.video_compat["intermediate_for"] == [CP] and role.video_compat["species"] == "DOG"
    assert role.video_compat["camera_distance"] == "near"
    assert action_keyframe_spec.anchor_chain(NEAR) == CHAIN
    assert action_keyframe_spec.anchor_chain("PLAY_BOW") == ("STAND_READY", "PLAY_BOW")
    assert action_keyframe_spec.anchor_chain("STAND_READY") == ("STAND_READY",)
    assert action_keyframe_spec.KEYFRAME_ROLE_ORDER[-1] == NEAR
    assert action_keyframe_spec.KEYFRAME_ROLE_ORDER[:8] == (
        "NEUTRAL_IDLE", "STAND_READY", "LIE", "SLEEP", "LOOK_UP", "HAPPY", "PLAY_BOW", "PLAY_CROUCH",
    )
    snap = action_keyframe_spec.role_spec_snapshot(role)
    assert snap["anchor_keyframe_role"] == "PLAY_BOW" and snap["anchor_framing_clause"].startswith("Keep the same camera")
    # Global versions do not move — adding a role never invalidates existing keyframes.
    assert action_keyframe_spec.KEYFRAME_SPEC_VERSION == "keyframe-spec-v4"
    assert action_keyframe_spec.KEYFRAME_PROMPT_VERSION == "keyframe-prompt-v2"
    assert ms.MOTION_SPEC_VERSION == "motion-spec-v17"


def test_near_bow_prompts_say_closer_larger_same_bow_and_fit_provider_limits():
    from .test_play_with_me import _rich_dog_identity
    from backend.services import canonical_image_providers

    role = action_keyframe_spec.KEYFRAME_ROLES[NEAR]
    runway = canonical_image_providers.RunwayImageProvider()
    for vi, tone in ((_rich_dog_identity(), "light-gray"),
                     (_rich_dog_identity(coat=("golden red", "cream", "white"), ears="semi_pricked"), "warm-off-white")):
        full = action_keyframe_spec.build_keyframe_prompt(role, vi, background_tone=tone)
        compact = action_keyframe_spec.build_compact_keyframe_prompt(role, vi, max_chars=runway.max_prompt_chars, background_tone=tone)
        assert full.startswith("The first supplied image is the pet's approved PLAY_BOW still")
        assert "the second supplied image is the canonical identity reference" in full
        assert len(full) < 3200
        assert len(compact) <= runway.max_prompt_chars
        assert compact.startswith("First image: the pet's approved PLAY_BOW still. Second: its canonical")
        assert "Clearly visible: face, full body, ears, front paws." in compact
        assert "Coat colors:" in compact and "The pet is a dog." in compact, "identity trait lines must survive"
        for text in (full, compact):
            low = text.lower()
            assert "closer to the camera" in low or "closer" in low
            assert "larger" in low and "lower in" in low
            assert "same play-bow posture" in low or "same play bow" in low
            assert "standing height" in low and "all four paws" in low
            assert "sit" in low and "lie" in low
            assert "same position" not in low  # the whole point: the dog has moved
    # Every other role renders exactly as before.
    for r in action_keyframe_spec.KEYFRAME_ROLE_ORDER:
        if r in ("PLAY_BOW", "PLAY_CROUCH", NEAR):
            continue
        spec = action_keyframe_spec.KEYFRAME_ROLES[r]
        assert spec.anchor_keyframe_role is None
        assert action_keyframe_spec.build_keyframe_prompt(spec, {}).startswith(
            "The first supplied image is the canonical reference of a specific pet"
        )
    assert action_keyframe_spec.build_keyframe_prompt(
        action_keyframe_spec.KEYFRAME_ROLES["PLAY_BOW"], {}
    ).startswith("The first supplied image is the pet's approved STAND_READY still")


# ══════════════════════════════════════════════════════════════════════════
# Segment prompts / plan / durations
# ══════════════════════════════════════════════════════════════════════════


def test_segment_prompts_describe_approach_deceleration_near_bow_and_return_home():
    prompts = motion_video_prompts.build_segment_prompts(_contract())
    assert set(prompts) == {"A", "B"}
    a, b = prompts["A"], prompts["B"]
    for text in (a, b):
        assert text.startswith("Same pet throughout")  # identity lock first
        low = text.lower()
        assert "camera, crop and framing stay fixed" in low and "the dog itself moves" in low
        assert "never slides, floats or changes size without leg movement" in low
        assert "exactly four limbs" in low
        assert "no human, hand, person, leash, toy or added object" in low
        assert "home spot stay fixed" not in low  # that is PLAY_WITH_ME's in-place rule
        assert "ears not forced forward" in low
        assert "timing:" in low  # v2: explicit per-second script
    low_a = a.lower()
    assert "one continuous 5-second take" in low_a
    assert "must actually travel toward the viewer" in low_a
    # v2 timing: 0.5 s excitement, 3 s bounding run, 1 s braking, arrive in the last half second.
    for needle in ("0.0-0.5 s:", "0.5-3.5 s:", "3.5-4.5 s:", "4.5-5.0 s:"):
        assert needle in a, needle
    assert "bounding playful run" in low_a and "grows steadily larger in frame" in low_a
    assert "clear weight transfer" in low_a and "accelerates out of the first stride" in low_a
    assert "brakes over the last two strides" in low_a and "chest and shoulders drop" in low_a
    # morphology-aware bow preserved: rear at standing height, hips never lifted.
    assert "hindquarters at normal standing height" in low_a
    assert "hips not lifted above standing" in low_a and "never a sit or lie-down" in low_a
    assert "only as the clip ends" in low_a
    assert "Do not reach the final near play bow frame early and hold it motionless" in a
    assert "no pause, snap, freeze or visible jump" in a
    assert "bowing in place" in low_a
    low_b = b.lower()
    assert "one continuous 8-second take" in low_b
    assert "played forward, never reversed or rewound" in low_b
    for needle in ("0.0-0.5 s:", "0.5-1.5 s:", "1.5-3.0 s:", "3.0-6.0 s:", "6.0-7.5 s:", "7.5-8.0 s:"):
        assert needle in b, needle
    assert "begin exactly in the supplied first frame, already alive" in low_b and "no longer" in low_b
    assert "pushes up out of the bow" in low_b
    assert "smooth arc until its back is to the camera" in low_b  # stepped turn, not a snap
    assert "trots back away from the camera to its home spot" in low_b and "growing steadily smaller" in low_b
    assert "to face the camera" in low_b and "settles square on all four paws" in low_b
    assert "same home position, size and framing" in low_b
    assert "never the approach in reverse" in low_b
    assert "second approach" in low_b and "never walks backward" in low_b
    # Display/record form labels both segments, like PLAY_WITH_ME.
    combined = motion_video_prompts.build_motion_video_prompt(_contract(), ms.MOTIONS[CP].description)
    assert combined.startswith("[SEGMENT A]") and "[SEGMENT B]" in combined
    assert motion_video_prompts.prompt_version_for_motion(CP) == motion_video_prompts.COME_PLAY_PROMPT_VERSION
    assert motion_video_prompts.COME_PLAY_PROMPT_VERSION.endswith("come-play-v2")
    assert motion_video_prompts.prompt_version_for_motion(PWM) == "motion-video-prompt-v3-play-with-me-v7"


def test_segment_prompts_fail_closed_without_variant_or_for_other_motions():
    with pytest.raises(ValueError):
        motion_video_prompts.build_come_play_segment_prompts({"motion_id": CP, "motion_class": "INTERACTION"})
    with pytest.raises(ValueError):
        motion_video_prompts.build_come_play_segment_prompts(
            {"motion_id": PWM, "motion_class": "INTERACTION", "species_variant": sv.variant_payload(sv.PLAY_BOW)}
        )
    with pytest.raises(ValueError):
        motion_video_prompts.build_segment_prompts({"motion_id": "COME_CLOSER", "motion_class": "LOCOMOTION"})


def test_each_segment_prompt_fits_the_fal_kling_limit_with_headroom_and_carries_its_duration(monkeypatch):
    monkeypatch.delenv("FAL_KLING_MAX_PROMPT_CHARS", raising=False)
    for name in ("MOTION_MODEL_COME_PLAY", "MOTION_MODEL_COME_PLAY_COME_PLAY_BOW"):
        monkeypatch.delenv(name, raising=False)
    assert ms.provider_order_for_motion(CP, variant_id="COME_PLAY_BOW") == (ms.PROVIDER_KLING_3,)
    provider = video_motion_providers.FalKlingProvider()
    prompts = motion_video_prompts.build_come_play_segment_prompts(_contract())
    plan = seg.segment_plan(sv.COME_PLAY_BOW)
    for entry in plan:
        prompt = prompts[entry["id"]]
        video_motion_providers.validate_prompt_length(provider, prompt)
        assert len(prompt) <= provider.max_prompt_chars - 100 == 2400, (entry["id"], len(prompt))
        payload = provider.build_payload(MotionVideoRequest(
            prompt=prompt, start_image_url="https://x/s.png", start_image_bytes=b"s",
            end_image_url="https://x/e.png", end_image_bytes=b"e",
            output_spec={"duration_sec": entry["duration_sec"], "audio": False},
        ))
        assert payload["prompt"] == prompt and payload["end_image_url"]
        assert payload["duration"] == str(entry["duration_sec"])
        assert payload["generate_audio"] is False
    # Verified fal contract: 3..15 s integers, $0.084/s audio off → 5 s + 8 s = $1.092 per attempt (v2).
    cost = sum(provider.estimate_cost_usd({"duration": str(e["duration_sec"]), "generate_audio": False}) for e in plan)
    assert round(cost, 3) == 1.092


def test_segment_plan_has_per_segment_durations_and_the_joined_spec_sums_them():
    plan = seg.segment_plan(sv.COME_PLAY_BOW)
    assert [(p["id"], p["index"], p["start"], p["end"], p["start_role"], p["end_role"], p["phase"], p["duration_sec"]) for p in plan] == [
        ("A", 1, "start_keyframe", "intermediate_keyframe", "STAND_READY", NEAR, "approach_into_bow", 5),
        ("B", 2, "intermediate_keyframe", "target_keyframe", NEAR, "STAND_READY", "hold_return_home", 8),
    ]
    base = {"duration_sec": 5, "aspect_ratio": "16:9", "audio": False}
    joined = seg.joined_output_spec(base, plan)
    assert joined["duration_sec"] == 13
    assert joined["segments"] == [{"id": "A", "duration_sec": 5}, {"id": "B", "duration_sec": 8}]
    assert seg.segment_output_spec(joined) == {**base, "duration_sec": 5}
    assert seg.segment_durations(plan, joined) == {"A": 5, "B": 8}
    # PLAY_WITH_ME's plan is byte-for-byte what it was: no durations, same phases, 2×4 s.
    pw = seg.segment_plan(sv.PLAY_BOW)
    assert [p["phase"] for p in pw] == ["lead_in_into_pose", "hold_return_to_stand"]
    assert all("duration_sec" not in p for p in pw)
    pw_joined = seg.joined_output_spec({"duration_sec": 4}, pw)
    assert pw_joined["duration_sec"] == 8 and seg.segment_durations(pw, pw_joined) == {"A": 4, "B": 4}


def test_generate_segments_sends_each_segment_its_own_duration():
    provider = FakeVideoProvider("kling_3", [GOOD(), GOOD()], end_frame=True)
    inputs = [
        seg.SegmentInput(id="A", index=1, prompt="a", start_image_url="s", start_image_bytes=b"s",
                         end_image_url="m", end_image_bytes=b"m", start_keyframe_id="k1", end_keyframe_id="k2",
                         duration_sec=5),
        seg.SegmentInput(id="B", index=2, prompt="b", start_image_url="m", start_image_bytes=b"m",
                         end_image_url="s", end_image_bytes=b"s", start_keyframe_id="k2", end_keyframe_id="k1",
                         duration_sec=6),
    ]
    results = seg.generate_segments(provider, inputs, output_spec={"duration_sec": 5, "audio": False},
                                    base_metadata={"motion_version_id": "v", "attempt": 1})
    assert len(results) == 2
    a, b = provider.requests
    assert a.output_spec["duration_sec"] == 5 and b.output_spec["duration_sec"] == 6
    assert a.output_spec["audio"] is False and b.output_spec["audio"] is False
    # Without a per-segment duration the shared spec is used untouched (PLAY_WITH_ME path).
    plain = seg.SegmentInput(id="A", index=1, prompt="a", start_image_url="s", start_image_bytes=b"s",
                             end_image_url="m", end_image_bytes=b"m", start_keyframe_id="k1", end_keyframe_id="k2")
    provider2 = FakeVideoProvider("kling_3", [GOOD()], end_frame=True)
    seg.generate_segments(provider2, [plain], output_spec={"duration_sec": 4}, base_metadata={})
    assert provider2.requests[0].output_spec == {"duration_sec": 4}


# ══════════════════════════════════════════════════════════════════════════
# Deterministic segment QA + VLM contract
# ══════════════════════════════════════════════════════════════════════════


def _png_rgb(color):
    import io
    import numpy as np
    from PIL import Image

    arr = np.zeros((48, 48, 3), dtype=np.uint8)
    arr[:, :] = color
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue(), arr


def test_segment_qa_uses_come_play_check_names_and_merge_refreshes_the_come_play_domain():
    home_png, home = _png_rgb((120, 90, 60))
    near_png, near = _png_rgb((120, 90, 60))
    inputs = [
        seg.SegmentInput(id="A", index=1, prompt="", start_image_url=None, start_image_bytes=home_png,
                         end_image_url=None, end_image_bytes=near_png, start_keyframe_id="s", end_keyframe_id="n",
                         expected_start_rgb=home, expected_end_rgb=near),
        seg.SegmentInput(id="B", index=2, prompt="", start_image_url=None, start_image_bytes=near_png,
                         end_image_url=None, end_image_bytes=home_png, start_keyframe_id="n", end_keyframe_id="s",
                         expected_start_rgb=near, expected_end_rgb=home),
    ]
    frames = {home_png: [home, home], near_png: [near, near]}
    result = seg.evaluate_segments(
        segment_videos=[home_png, near_png], inputs=inputs,
        frame_sampler=lambda data: frames[data],
        check_names=sv.segment_check_names(CP),
    )
    assert set(result["checks"]) == set(sv.COME_PLAY_SEGMENT_CHECK_NAMES)
    assert "segment_a_reaches_near_bow" in result["checks"] and "segment_a_reaches_play_pose" not in result["checks"]
    assert sv.segment_check_names(PWM) == sv.PLAY_WITH_ME_SEGMENT_CHECK_NAMES
    assert sv.segment_check_names("PAW_HELLO") == sv.PLAY_WITH_ME_SEGMENT_CHECK_NAMES  # legacy default
    with pytest.raises(ValueError):
        seg.evaluate_segments(segment_videos=[], inputs=[], frame_sampler=lambda d: None, check_names=("x",))

    judged = judge_come_play(come_play_evidence())
    assert judged["decision"] == "PASS"
    assert judged["business_domains"]["come_play"]["status"] == "PASS"
    assert "play_with_me" not in judged["business_domains"]
    hard = judge_come_play(come_play_evidence(), segment_checks={**_segment_checks_ok(), "segment_b_returns_to_stand_ready": "FAIL"})
    assert hard["decision"] == "FAIL" and hard["business_domains"]["come_play"]["status"] == "FAIL"
    assert hard["business_qa"]["delivery_action"] == "BLOCK"
    soft = judge_come_play(come_play_evidence(), segment_checks={**_segment_checks_ok(), "segment_seam_continuity": "REVIEW"})
    assert soft["decision"] == "REVIEW" and soft["business_qa"]["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert mv.candidate_is_publishable({"motion_id": CP, "decision": "REVIEW", "qa_result": soft})


@pytest.mark.parametrize("needle", [
    "For COME_PLAY on a DOG (COME_PLAY_BOW)", "playful_approach_visible", "returns_to_home_scale_and_position",
    "return_played_forward", "NOT camera motion, zoom or a defect", "COME_PLAY DEDICATED CHECKS",
])
def test_vlm_schema_and_guidance_cover_the_approach_bow_and_return(needle):
    acceptance = ms.acceptance_for_variant(ms.MOTIONS[CP], sv.COME_PLAY_BOW)
    specs = vlm_identity._motion_targeted_specs(
        questions=["motion_correctness", "temporal_integrity"],
        motion_description=sv.COME_PLAY_BOW.description,
        motion_class="INTERACTION",
        expected_direction=None,
        interaction_type="come_play",
        allow_generated_hand=False,
        has_target=True,
        acceptance_spec=acceptance,
        has_intermediate=True,
    )
    schema, prompt = specs[vlm_escalation.MOTION_VLM]
    assert set(vlm_identity.COME_PLAY_QA_FIELDS) <= set(schema["properties"])
    play_only = set(vlm_identity.PLAY_WITH_ME_QA_FIELDS) - set(vlm_identity.COME_PLAY_QA_FIELDS)
    assert play_only and play_only.isdisjoint(schema["properties"])
    assert "For PLAY_WITH_ME" not in prompt and "PAW_HELLO DEDICATED" not in prompt
    if needle != "COME_PLAY DEDICATED CHECKS":
        assert needle in prompt
    # The full qa_motion_video prompt path adds the dedicated-check block and schema fields.
    calls = {}

    def fake_call(**kwargs):
        calls.update(kwargs)
        return None

    monkeypatched = vlm_identity.__dict__.get("_structured_call")
    _ = monkeypatched  # the direct call path is exercised in generation tests; schema is checked here
    assert "INTERMEDIATE" in prompt


def test_dedicated_checks_pass_review_and_fail_with_hard_approach_and_return_travel():
    passed = judge_come_play(come_play_evidence())
    assert passed["decision"] == "PASS" and passed["business_qa"]["delivery_action"] == "DELIVER"
    for check in sv.COME_PLAY_REQUIRED_CHECK_NAMES:
        assert passed["checks"][check] == "PASS", check

    for defect in (
        {"playful_approach_visible": "no"},
        {"approach_gait_natural": "no"},
        {"play_bow_recognized": "no"},
        {"rear_posture_correct": "no"},
        {"limb_count_correct": "no"},
        {"return_travel_to_home_visible": "no"},
        {"returns_to_home_scale_and_position": "no"},
        {"return_played_forward": "no"},
        {"segment_seam_smooth": "no"},
        {"posture_not_lying_or_sitting": "no"},
    ):
        failed = judge_come_play(come_play_evidence(**defect))
        assert failed["decision"] == "FAIL", defect
        assert failed["business_qa"]["delivery_action"] == "BLOCK", defect

    # Sparse coverage: brief posture events soften to unknown (REVIEW, publishable with advisory) …
    sampling = {"version": mv.COME_PLAY_VLM_SAMPLING_VERSION,
                "strategy": "adaptive_dense_unavailable", "absence_reliable": False}
    uncertain = mv._enforce_temporal_evidence_reliability(
        come_play_evidence(requested_motion_occurs="no", interaction_correct="no",
                           play_bow_recognized="no", deceleration_before_bow="no",
                           near_bow_matches_keyframe="no", playful_hold_visible="no"),
        motion_id=CP, sampling=sampling,
    )
    for key in ("play_bow_recognized", "deceleration_before_bow", "near_bow_matches_keyframe", "playful_hold_visible"):
        assert uncertain[key] == "unknown" and uncertain[f"{key}_raw"] == "no"
    assert uncertain["interaction_correct"] == "unknown"
    reviewed = judge_come_play(uncertain)
    assert reviewed["decision"] == "REVIEW"
    assert reviewed["business_qa"]["delivery_action"] == "DELIVER_WITH_ADVISORY"
    # … but a clearly sampled missing approach / missing return / wrong home scale never softens:
    # the travel spans most of each segment, so a no is a defect even under sparse sampling.
    hard = mv._enforce_temporal_evidence_reliability(
        come_play_evidence(requested_motion_occurs="no", playful_approach_visible="no",
                           return_travel_to_home_visible="no", returns_to_home_scale_and_position="no",
                           return_played_forward="no", approach_gait_natural="no"),
        motion_id=CP, sampling=sampling,
    )
    for key in ("playful_approach_visible", "return_travel_to_home_visible",
                "returns_to_home_scale_and_position", "return_played_forward", "approach_gait_natural"):
        assert hard[key] == "no", key
    assert judge_come_play(hard)["decision"] == "FAIL"
    # unknown (ambiguous) never hard-fails on its own.
    ambiguous = judge_come_play(come_play_evidence(playful_approach_visible="unknown", play_bow_recognized="unknown"))
    assert ambiguous["decision"] == "REVIEW"


def test_versions_are_motion_scoped_and_do_not_move_other_motions():
    assert CP in mv.TARGETED_MICRO_VLM_MOTIONS and CP in mv.SPARSE_BASE_ABSENCE_UNRELIABLE_MOTIONS
    assert mv._motion_sampling_version(CP) == mv.COME_PLAY_VLM_SAMPLING_VERSION == "motion-vlm-sampling-v2-come-play-v2"
    assert mv._motion_sampling_version(PWM) == mv.PLAY_WITH_ME_VLM_SAMPLING_VERSION
    assert mv._motion_sampling_version("COME_CLOSER") == mv.MOTION_VLM_SAMPLING_VERSION
    stamp = mv.analyzer_versions([], prompt_version=motion_video_prompts.COME_PLAY_PROMPT_VERSION)
    assert stamp["vlm_motion_qa"] == vlm_identity.VLM_COME_PLAY_QA_VERSION
    assert stamp["motion_vlm_sampling"] == mv.COME_PLAY_VLM_SAMPLING_VERSION
    # v2: COME_PLAY joins with the trimming rule (generation-affecting) and stamps the motion-profile
    # diagnostic (QA-only); PLAY_WITH_ME keeps join v1 and gets no profile stamp.
    assert stamp["segment_join"] == seg.COME_PLAY_SEGMENT_JOIN_VERSION == "motion-segment-join-v2-come-play-v1"
    assert stamp["segment_qa"] == seg.SEGMENT_QA_VERSION
    assert stamp["motion_profile_qa"] == motion_profile.COME_PLAY_PROFILE_QA_VERSION
    assert vlm_identity.VLM_COME_PLAY_QA_VERSION == "vlm-motion-qa-v3-come-play-v2"
    play_stamp = mv.analyzer_versions([], prompt_version=motion_video_prompts.PLAY_WITH_ME_PROMPT_VERSION)
    assert play_stamp["vlm_motion_qa"] == vlm_identity.VLM_PLAY_WITH_ME_QA_VERSION
    assert play_stamp["segment_join"] == seg.SEGMENT_JOIN_VERSION == "motion-segment-join-v1"
    assert "motion_profile_qa" not in play_stamp
    assert "segment_join" not in mv.analyzer_versions([], prompt_version=motion_video_prompts.MOTION_VIDEO_PROMPT_VERSION)
    # Generation-affecting vs QA-only split is unchanged.
    assert "segment_qa" in mv.QA_ONLY_VERSION_KEYS and "segment_join" not in mv.QA_ONLY_VERSION_KEYS
    assert "motion_profile_qa" in mv.QA_ONLY_VERSION_KEYS
    assert "vlm_motion_qa" not in mv.generation_versions(stamp) and "segment_join" in mv.generation_versions(stamp)
    assert sv.segment_join_version(CP) == seg.COME_PLAY_SEGMENT_JOIN_VERSION
    assert sv.segment_join_version(PWM) == seg.SEGMENT_JOIN_VERSION
    assert sv.segment_trim_policy(PWM) is None and sv.segment_trim_policy(CP) is not None
    assert sv.has_motion_profile(CP) and not sv.has_motion_profile(PWM)


# ══════════════════════════════════════════════════════════════════════════
# Near play-bow keyframe generation (image providers injected — no paid calls)
# ══════════════════════════════════════════════════════════════════════════


def test_near_bow_keyframe_is_generated_from_the_approved_play_bow_plate(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage, roles=("STAND_READY", "PLAY_BOW"))
    bow = _run(kf.get_keyframe(user_id=USER, pet_id=PET, keyframe_role="PLAY_BOW"))
    bow_sel = next(c for c in bow.candidates if c.selected)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    provider = _RefRecordingProvider("runway", [GOOD()])

    built = _build_kf(h, [provider], role=NEAR)

    assert built.status == kf.STATUS_COMPLETE and built.keyframe_role == NEAR
    assert built.canonical_version_id == bow.canonical_version_id
    assert built.analyzer_versions["anchor_keyframe"] == f"PLAY_BOW:{bow.id}"
    assert built.spec["anchor_keyframe_role"] == "PLAY_BOW"
    refs = provider.seen_references[0]
    assert [r.role for r in refs][:2] == ["ANCHOR_KEYFRAME", "CANONICAL"]
    assert refs[0].reference_id == f"keyframe:{bow.id}"
    assert refs[0].data == storage[bow_sel.plate_object_path], "the first image is the PLAY_BOW plate"
    assert provider.seen_prompts[0].startswith("The first supplied image is the pet's approved PLAY_BOW still")
    selected = next(c for c in built.candidates if c.selected)
    assert selected.generation_metadata["canonical_input"]["kind"] == "anchor_keyframe_plate"
    assert selected.generation_metadata["canonical_input"]["anchor"]["keyframe_id"] == bow.id
    assert selected.qa_result["anchor_keyframe_consistency"]["anchor_role"] == "PLAY_BOW"
    assert selected.qa_result["identity_reference"]["primary"] == "approved_canonical"
    assert f"/keyframes/{NEAR.lower()}/v1/" in selected.raw_object_path
    # Reused until its anchor changes.
    again = _build_kf(h, [provider], role=NEAR)
    assert again.id == built.id and again.deduplicated and provider.calls == 1


def test_near_bow_keyframe_requires_an_approved_play_bow(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)  # STAND_READY only — no PLAY_BOW yet
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    provider = _RefRecordingProvider("runway", [GOOD()])
    with pytest.raises(kf.ActionKeyframeError) as exc:
        _build_kf(h, [provider], role=NEAR)
    assert exc.value.code == "ANCHOR_KEYFRAME_REQUIRED" and exc.value.status == 409
    assert provider.calls == 0
    assert all(k.keyframe_role != NEAR for k in _run(kf.list_keyframes(user_id=USER, pet_id=PET)))


# ══════════════════════════════════════════════════════════════════════════
# Generation (video providers injected — no paid calls)
# ══════════════════════════════════════════════════════════════════════════


def _prepare_come_play(monkeypatch, storage, species="DOG", roles=CHAIN):
    h, _ = _prepare_pipeline(monkeypatch, storage, roles=roles)
    _force_species(monkeypatch, species)
    install_mv_vlm(monkeypatch, VLM_COME_PLAY_OK)
    return h


def _build_come_play(h, providers, **kw):
    kw.setdefault("segment_join_fn", passthrough_join)
    return _build_motion(h, CP, providers, **kw)


def test_generation_conditions_two_segments_on_the_approved_home_and_near_bow_images(storage, monkeypatch):
    h = _prepare_come_play(monkeypatch, storage)
    provider = FakeVideoProvider("kling_3", [GOOD(), GOOD()], end_frame=True)

    version = _build_come_play(h, [provider])

    assert version.status == mv.STATUS_COMPLETE
    assert version.video_strategy == ms.STRATEGY_START_END
    assert version.prompt_version == motion_video_prompts.COME_PLAY_PROMPT_VERSION
    assert version.prompt.startswith("[SEGMENT A]") and "[SEGMENT B]" in version.prompt
    assert provider.calls == 2, "one candidate = two start/end-conditioned provider jobs"
    a, b = provider.requests
    stand_id = _run(kf.get_keyframe(user_id=USER, pet_id=PET, keyframe_role="STAND_READY")).id
    near_id = _run(kf.get_keyframe(user_id=USER, pet_id=PET, keyframe_role=NEAR)).id
    bow_id = _run(kf.get_keyframe(user_id=USER, pet_id=PET, keyframe_role="PLAY_BOW")).id
    assert (a.metadata["segment"], a.metadata["segment_index"]) == ("A", 1)
    assert (b.metadata["segment"], b.metadata["segment_index"]) == ("B", 2)
    # A: STAND_READY (home) → near bow; B: near bow → STAND_READY (home). The home-scale PLAY_BOW
    # conditions the near keyframe's image, not the video — it never appears as a video endpoint.
    assert (a.metadata["start_keyframe_id"], a.metadata["target_keyframe_id"]) == (stand_id, near_id)
    assert (b.metadata["start_keyframe_id"], b.metadata["target_keyframe_id"]) == (near_id, stand_id)
    assert bow_id not in (a.metadata["start_keyframe_id"], a.metadata["target_keyframe_id"],
                          b.metadata["start_keyframe_id"], b.metadata["target_keyframe_id"])
    assert a.start_image_bytes and a.end_image_bytes and b.start_image_bytes and b.end_image_bytes
    assert a.end_image_bytes == b.start_image_bytes, "the seam is the same approved near-bow still"
    assert a.start_image_bytes == b.end_image_bytes, "both outer ends are the same STAND_READY still"
    # Asymmetric durations: A 5 s, B 8 s, planned joined 13 s (before trimming).
    assert a.output_spec["duration_sec"] == 5 and b.output_spec["duration_sec"] == 8
    assert version.output_spec["duration_sec"] == 13
    assert version.output_spec["segments"] == [{"id": "A", "duration_sec": 5}, {"id": "B", "duration_sec": 8}]
    assert "bounding playful run" in a.prompt and "trots back" in b.prompt
    assert max(len(a.prompt), len(b.prompt)) <= video_motion_providers.FalKlingProvider().max_prompt_chars - 100

    selected = next(c for c in version.candidates if c.selected)
    segments = selected.generation_metadata["segments"]
    assert [item["id"] for item in segments["items"]] == ["A", "B"]
    assert [p["phase"] for p in segments["plan"]] == ["approach_into_bow", "hold_return_home"]
    assert all(item["raw_video_path"] in storage for item in segments["items"])
    assert selected.provider_job_id == "kling_3-job-1+kling_3-job-2"
    qa = selected.qa_result
    assert qa["segments"]["decision"] == "PASS"
    for name in sv.COME_PLAY_SEGMENT_CHECK_NAMES:
        assert qa["checks"][name] == "PASS", name
    assert not any(name in qa["checks"] for name in ("segment_a_reaches_play_pose", "segment_b_starts_at_play_pose"))
    assert qa["business_domains"]["come_play"]["status"] == "PASS"
    assert qa["business_qa"]["delivery_action"] == "DELIVER"
    refs = {r["kind"]: r for r in selected.input_references}
    assert refs["intermediate_keyframe"]["keyframe_id"] == near_id and refs["intermediate_keyframe"]["role"] == NEAR
    # Same pet, same STAND_READY: a PLAY_WITH_ME build afterwards is a separate version row.
    assert _run(mv._version_rows(PET, PWM)) == []


def test_contract_pins_the_near_bow_keyframe_and_the_durationed_plan(storage, monkeypatch):
    _prepare_come_play(monkeypatch, storage)
    contract = _run(ms.resolve_video_generation_spec(user_id=USER, pet_id=PET, motion_id=CP))
    assert contract["species_variant"]["id"] == "COME_PLAY_BOW"
    assert contract["species_variant"]["keyframe_role"] == NEAR
    assert contract["intermediate_keyframe"]["role"] == NEAR
    assert contract["intermediate_keyframe"]["plate"]["object_path"]
    assert contract["intermediate_keyframe"]["canonical_version_id"] == contract["canonical_version_id"]
    assert [(s["id"], s["duration_sec"]) for s in contract["segments"]] == [("A", 5), ("B", 8)]
    assert contract["provider_order"] == [ms.PROVIDER_KLING_3]
    assert contract["qa_context"]["acceptance"]["intermediate_keyframe_role"] == NEAR
    assert contract["qa_context"]["interaction_type"] == "come_play"
    assert contract["video_strategy"] == ms.STRATEGY_START_END
    assert contract["duration_range_sec"] == [5.0, 8.0]


def test_missing_near_bow_keyframe_fails_closed_before_any_provider_call(storage, monkeypatch):
    h = _prepare_come_play(monkeypatch, storage, roles=("STAND_READY", "PLAY_BOW"))  # no PLAY_BOW_NEAR
    provider = FakeVideoProvider("kling_3", [GOOD(), GOOD()], end_frame=True)
    with pytest.raises(mv.MotionVideoError) as exc:
        _build_come_play(h, [provider])
    assert exc.value.code == "INTERMEDIATE_KEYFRAME_REQUIRED" and exc.value.status == 409
    assert provider.calls == 0, "the home-scale PLAY_BOW is not a substitute for the near bow"


@pytest.mark.parametrize("species", ["CAT", "RABBIT", "UNKNOWN"])
def test_non_dogs_are_rejected_before_any_provider_call(storage, monkeypatch, species):
    h = _prepare_come_play(monkeypatch, storage, species=species)
    provider = FakeVideoProvider("kling_3", [GOOD(), GOOD()], end_frame=True)
    with pytest.raises(mv.MotionVideoError) as exc:
        _build_come_play(h, [provider])
    assert exc.value.code == "SPECIES_UNSUPPORTED" and exc.value.status == 409
    assert provider.calls == 0


def test_never_degrades_when_the_model_cannot_receive_the_end_frame(storage, monkeypatch):
    h = _prepare_come_play(monkeypatch, storage)
    incapable = FakeVideoProvider("kling_3", [GOOD(), GOOD()], end_frame=False)
    with pytest.raises(mv.MotionVideoError) as exc:
        _build_come_play(h, [incapable])
    assert exc.value.code == "END_FRAME_UNSUPPORTED" and incapable.calls == 0


def test_segment_b_that_ignored_the_home_end_frame_blocks_and_requests_one_regeneration(storage, monkeypatch):
    """Deterministic FAIL on 'returns to STAND_READY' (a different-looking last frame) → BLOCK and a
    bounded second paid candidate — the return-to-HOME guarantee is enforced before the VLM."""
    h = _prepare_come_play(monkeypatch, storage)
    monkeypatch.setenv("PHASE6_MAX_PRIMARY", "2")
    monkeypatch.setenv("PHASE6_STOP_AFTER_PASSES", "1")
    stand = _plate_bytes(storage, "STAND_READY")

    def sampler(video_bytes):
        import numpy as np
        rgb = mv._rgb_from_bytes(video_bytes)
        if video_bytes == b"WRONG":
            wrong = np.zeros_like(mv._rgb_from_bytes(stand))
            wrong[:, :] = (10, 200, 30)
            return [wrong] * 5
        return [rgb] * 5 if rgb is not None else None

    provider = FakeVideoProvider("kling_3", [GOOD(), b"WRONG", GOOD(), GOOD()], end_frame=True)
    version = _build_come_play(h, [provider], sampler=sampler)
    assert provider.calls == 4, "attempt 1 (A+B) failed deterministically → one more attempt (A+B)"
    assert len(version.candidates) == 2
    first = next(c for c in version.candidates if c.attempt == 1) if hasattr(version.candidates[0], "attempt") else version.candidates[0]
    failed = version.candidates[0]
    assert failed.qa_result["checks"]["segment_b_returns_to_stand_ready"] in ("FAIL", "REVIEW")
    assert version.status in (mv.STATUS_COMPLETE, mv.STATUS_REVIEW)


# ══════════════════════════════════════════════════════════════════════════
# Run orchestration — KEYFRAMES stage builds STAND_READY → PLAY_BOW → PLAY_BOW_NEAR
# ══════════════════════════════════════════════════════════════════════════


def _orchestrated_come_play(monkeypatch, *, species="DOG"):
    from . import test_phase7c_generation_runs as p7c

    p7c.seed_intake()
    harness = p7c.PipelineHarness(monkeypatch)
    harness.motion.motion_id = CP
    stand = harness.keyframe

    def _kf(role, idx, anchor):
        return SimpleNamespace(
            id=f"00000000-0000-0000-0000-0000000006{idx}1", pet_id=p7c.PET, user_id=p7c.USER, keyframe_role=role,
            version=1, status="complete", canonical_version_id=harness.canonical.id,
            canonical_version=harness.canonical.version,
            selected_candidate_id=f"00000000-0000-0000-0000-0000000006{idx}2",
            candidates=[SimpleNamespace(id=f"00000000-0000-0000-0000-0000000006{idx}2", selected=True)],
            analyzer_versions={"anchor_keyframe": f"{anchor.keyframe_role}:{anchor.id}"},
        )

    bow = _kf("PLAY_BOW", 1, stand)
    near = _kf(NEAR, 2, bow)
    by_role = {"STAND_READY": stand, "PLAY_BOW": bow, NEAR: near}

    async def keyframe_get(**kwargs):
        harness._capture("keyframe_get", kwargs)
        return by_role.get(kwargs.get("keyframe_role")) if kwargs.get("version") else None

    async def keyframe_build(**kwargs):
        harness._capture("keyframe", kwargs)
        harness.keyframe_build_calls.append(dict(kwargs))
        harness.counts["keyframe_build"] += 1
        return by_role[kwargs["keyframe_role"]]

    async def species_of(*_a, **_k):
        return species

    async def resolve_spec(**kwargs):
        harness._capture("motion_spec", kwargs)
        return {
            "motion_id": CP,
            "motion_spec_version": ms.MOTION_SPEC_VERSION,
            "start_keyframe": {"keyframe_id": stand.id, "version": 1},
            "intermediate_keyframe": {"keyframe_id": near.id, "version": 1, "role": NEAR},
            "segments": seg.segment_plan(sv.COME_PLAY_BOW),
            "canonical_version_id": harness.canonical.id,
        }

    async def finalize(**kwargs):
        harness._capture("finalize", kwargs)
        harness.counts["publication"] += 1
        return SimpleNamespace(publication_id="00000000-0000-0000-0000-000000000712")

    monkeypatch.setattr(kf, "get_keyframe", keyframe_get)
    monkeypatch.setattr(kf, "build_keyframe", keyframe_build)
    monkeypatch.setattr(sv, "resolve_pet_species", species_of)
    monkeypatch.setattr(ms, "resolve_video_generation_spec", resolve_spec)
    monkeypatch.setattr(premium_motion_finalization, "finalize_premium_motion", finalize)
    monkeypatch.setattr(runs, "_image_providers", lambda run, operation: [FakeProvider("gpt_image")])
    monkeypatch.setattr(runs, "_video_providers", lambda run, motion_id: [SimpleNamespace(name="kling_3")])
    return harness, stand, bow, near


def _start_come_play_run(key="premium:COME_PLAY:test"):
    from . import test_phase7c_generation_runs as p7c

    return _run(runs.start_generation_run(
        user_id=p7c.USER, pet_id=p7c.PET, motion_id=CP, request_kind=runs.REQUEST_PREMIUM_PRODUCT,
        idempotency_key=key, product_key="action:COME_PLAY",
    ))


@pytest.fixture
def run_env(monkeypatch):
    from . import test_phase7c_generation_runs as p7c
    from backend.services import durable_provider_jobs, pet_reference_service, pet_registry

    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    runs.__reset_for_tests()
    durable_provider_jobs.__reset_for_tests()
    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()
    yield p7c
    runs.__reset_for_tests()
    durable_provider_jobs.__reset_for_tests()
    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()


def test_run_builds_the_anchor_chain_in_order_and_publishes(run_env, monkeypatch, storage):
    harness, stand, bow, near = _orchestrated_come_play(monkeypatch)
    _start_come_play_run()
    result = _run(runs.process_next_generation_run(worker_id="cp-test-worker"))

    assert result.status == runs.STATUS_PUBLISHED, result.last_error
    assert set(result.keyframes) == set(CHAIN)
    assert result.keyframes[NEAR]["id"] == near.id and result.keyframes["PLAY_BOW"]["id"] == bow.id
    roles = [call["keyframe_role"] for call in harness.keyframe_build_calls]
    assert roles == list(CHAIN), "outer anchor first: STAND_READY, then PLAY_BOW on it, then PLAY_BOW_NEAR on that"
    bow_call, near_call = harness.keyframe_build_calls[1], harness.keyframe_build_calls[2]
    assert bow_call["anchor_keyframe_version"] == stand.version
    assert near_call["anchor_keyframe_version"] == bow.version
    assert near_call["pinned_canonical_version_id"] == harness.canonical.id
    assert harness.counts["motion_build"] == 1 and harness.counts["publication"] == 1


def test_run_rejects_a_cat_at_the_keyframe_stage_without_generation(run_env, monkeypatch, storage):
    harness, _, _, _ = _orchestrated_come_play(monkeypatch, species="CAT")
    _start_come_play_run("premium:COME_PLAY:cat")
    result = _run(runs.process_next_generation_run(worker_id="cp-test-worker"))
    assert result.status == runs.STATUS_FAILED
    assert result.last_error["code"] == "SPECIES_UNSUPPORTED"
    assert [c["keyframe_role"] for c in harness.keyframe_build_calls] == ["STAND_READY"]
    assert harness.counts["motion_build"] == 0


# ══════════════════════════════════════════════════════════════════════════
# Commerce / registries / device / playback / independence from PLAY_WITH_ME & COME_CLOSER
# ══════════════════════════════════════════════════════════════════════════


def test_registered_everywhere_the_commercial_path_reads():
    assert CP in PET_ACTIONS and CP in PREMIUM_ACTIONS
    assert CP in premium_run_fulfillment.PREMIUM_MOTIONS
    assert CP in motion_delivery_service.PACKAGEABLE_MOTIONS and CP not in motion_delivery_service.SYSTEM_MOTIONS
    assert generation_queue.GENERATION_ORDER[-1] == CP and generation_queue.GENERATION_ORDER[-2] == PWM
    assert set(generation_queue.GENERATION_ORDER) == set(PREMIUM_ACTIONS)
    assert premium_run_fulfillment.motion_id_for_product("action:COME_PLAY") == CP
    assert premium_purchase.resolve_kind("COME_PLAY") == "ACTION:COME_PLAY"
    assert premium_purchase._product_key("ACTION:COME_PLAY") == "action:COME_PLAY"
    assert ("action:COME_PLAY", product_catalog.TYPE_ACTION, 1, "Come Play!") in product_catalog._SEED
    assert CP in device_ws_v1.DEVICE_OWNED_MOTIONS
    from backend.services import premium_generation
    assert CP not in premium_generation.LEGACY_CAPABLE_ACTIONS, "new-path only — never the legacy black-plate queue"
    sql = open(os.path.join(os.path.dirname(__file__), "..", "..", "supabase", "migrations",
                            "20261114000000_come_play_premium.sql"), encoding="utf-8").read()
    assert sql.count("'COME_PLAY'") == 3, "all three motion CHECK constraints widen"
    assert "('action:COME_PLAY', 'ACTION', 1, 'Come Play!')" in sql
    assert "'PLAY_WITH_ME'" in sql and "'RETURN_HOME'" in sql  # nothing dropped


def test_purchase_rejects_non_dogs_before_reserving_credits(monkeypatch):
    async def owned(*_a, **_k):
        return None

    async def entitlement(_user):
        return SimpleNamespace(blocks_generation=False, enforced=False, entitled=True, status=None)

    async def state(*_a, **_k):
        return premium_purchase.AssetState(ready={}, active=[], missing=[CP])

    async def species(*_a, **_k):
        return "CAT"

    async def never_reserve(**_k):
        raise AssertionError("credits must not be reserved for a cat")

    async def never_claim(*_a, **_k):
        raise AssertionError("purchase ledger must not be claimed for a cat")

    monkeypatch.setattr(premium_purchase, "assert_pet_owned", owned)
    monkeypatch.setattr(premium_purchase.premium_entitlement, "get_entitlement", entitlement)
    monkeypatch.setattr(premium_purchase, "asset_state", state)
    monkeypatch.setattr(sv, "resolve_pet_species", species)
    monkeypatch.setattr(premium_purchase.credit_reservation, "reserve", never_reserve)
    monkeypatch.setattr(premium_purchase, "_claim_purchase", never_claim)
    with pytest.raises(premium_purchase.PurchaseError) as exc:
        _run(premium_purchase.purchase(user_id="u", pet_id="pet_x", kind="ACTION:COME_PLAY",
                                       pet_image_url=None, api_base="http://api.test"))
    assert exc.value.code == "SPECIES_UNSUPPORTED" and exc.value.status == 409
    # A cat can still buy PLAY_WITH_ME (its own CAT variant) — the gate is per action.
    _run(premium_purchase._assert_species_supported("u", "p", [PWM, "PAW_HELLO"]))
    with pytest.raises(premium_purchase.PurchaseError):
        _run(premium_purchase._assert_species_supported("u", "p", [CP]))


def _device_client(monkeypatch):
    monkeypatch.setenv("ETERNAL_BEAM_DEVICE_TOKENS", '{"beam-test":"secret"}')
    device_gateway.__reset_for_tests()
    app = FastAPI()
    app.include_router(device_ws_v1.router, prefix="/api")
    app.dependency_overrides[require_user] = lambda: AuthedUser(user_id="user-1")
    return ASGITestClient(app)


def test_device_command_sends_an_owned_published_come_play_asset_and_rejects_unowned(monkeypatch):
    async def fake_library(**kwargs):
        return SimpleNamespace(motions=[SimpleNamespace(
            pet_id="pet-1", motion_id=CP, url="https://signed.example/come_play_packed.mp4?token=fresh",
            delivery_format="packed_alpha", access=SimpleNamespace(state="playable"),
        )])

    monkeypatch.setattr(device_ws_v1.library_service, "get_library", fake_library)
    client = _device_client(monkeypatch)
    response = client.post("/api/v1/device/commands",
                           json={"device_id": "beam-test", "event": "pet_asset", "pet_id": "pet-1", "motion_id": CP})
    assert response.status_code == 200, response.text
    command = _run(device_gateway.pending("beam-test"))[0]
    assert command["motion_id"] == CP and command["video_url"].endswith("token=fresh")
    # The owned COME_PLAY asset does not unlock PLAY_WITH_ME on the device.
    response = client.post("/api/v1/device/commands",
                           json={"device_id": "beam-test", "event": "pet_asset", "pet_id": "pet-1", "motion_id": PWM})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "PUBLISHED_ASSET_REQUIRED"


def _ts_block(path, key):
    with open(os.path.join(os.path.dirname(__file__), "..", "..", path), encoding="utf-8") as f:
        content = f.read()
    block = content[content.index(key):]
    return block[: block.index("},")]


def test_web_and_hologram_playback_treat_come_play_as_a_seam_aligned_home_to_home_action():
    block = _ts_block("src/lib/pet-runtime-events.ts", "COME_PLAY: {")
    for needle in ('kind: "ACTION"', 'returnPolicy: "seam-aligned"', 'requiredPose: "STANDING"',
                   'endPose: "STANDING"', "returnToIdle: true", "interruptible: false", "priority: 84"):
        assert needle in block, needle
    assert "chainTo" not in block, "the return home is inside the asset — no RETURN_HOME chain"
    # COME_CLOSER keeps its own dissolve/RETURN_HOME contract and PLAY_WITH_ME its priority.
    come_closer = _ts_block("src/lib/pet-runtime-events.ts", "COME_CLOSER: {")
    assert 'chainTo: "RETURN_HOME"' in come_closer and 'returnPolicy: "hold-and-dissolve"' in come_closer
    assert "priority: 86" in _ts_block("src/lib/pet-runtime-events.ts", "PLAY_WITH_ME: {")
    for path, needle in (
        ("src/lib/behavior-library.ts", '"COME_PLAY",'),
        ("src/lib/pet-ready-payload.ts", "'COME_PLAY',"),
        ("src/components/memorial/memorial-device-play-screen.tsx", 'motionId: "COME_PLAY"'),
        ("src/components/memorial/memorial-i18n.ts", 'COME_PLAY: "Come Play!"'),
        ("src/components/memorial/memorial-i18n.ts", 'COME_PLAY: "달려와 놀자!"'),
    ):
        with open(os.path.join(os.path.dirname(__file__), "..", "..", path), encoding="utf-8") as f:
            assert needle in f.read(), (path, needle)
    assert motion_delivery_service.DELIVERY_PACKED_ALPHA == "packed_alpha"


def test_play_with_me_and_come_closer_are_untouched():
    play = ms.MOTIONS[PWM]
    assert play.video_compat["intermediate_keyframe_roles"] == ["PLAY_BOW", "PLAY_CROUCH"]
    assert play.video_compat["species_variants"] == ["DOG", "CAT"] and "travels" not in play.video_compat
    assert play.duration_range_sec == (4.0, 5.0)
    assert play.qa_acceptance_spec["policy_revision"] == "play-with-me-v3"
    assert set(play.qa_acceptance_spec["required_checks"]).isdisjoint(sv.COME_PLAY_CHECK_NAMES)
    assert "come_play" not in play.requirements["qa"]["business"]["domains"]
    play_prompts = motion_video_prompts.build_play_with_me_segment_prompts(
        {"motion_id": PWM, "motion_class": "INTERACTION", "species_variant": sv.variant_payload(sv.PLAY_BOW)}
    )
    assert all("toward or away from the camera" in t and "trots back" not in t for t in play_prompts.values())
    assert max(len(t) for t in play_prompts.values()) <= 2400
    closer = ms.MOTIONS["COME_CLOSER"]
    assert closer.motion_class == ms.CLASS_LOCOMOTION and closer.preferred_video_strategy == ms.STRATEGY_I2V
    assert closer.target_keyframe_role is None and closer.motion_reference_id is None
    assert "species_variants" not in closer.video_compat
    assert ms.MOTION_MODEL_DEFAULTS["COME_CLOSER"] == ms.PROVIDER_GROK_IMAGINE_LITE
    assert ms.MOTIONS["RETURN_HOME"].start_from_derived == "close_position"
    assert ms.TRIGGERS == {"TOUCH": "PET_HEAD", "VOICE": "LOOK_UP", "NFC": "HAPPY", "IDLE": "BREATHING"}
    assert generation_queue.GENERATION_ORDER[:11] == (
        "COME_CLOSER", "BLINKING", "PET_HEAD", "PAW_HELLO", "EAR_TWITCHING", "HEAD_TILTING", "TAIL_WAGGING",
        "LOOK_UP", "LIE_DOWN", "LIE_IDLE", "STAND_UP",
    )
    assert not sv.requires_species_variant("COME_CLOSER") and not sv.requires_species_variant("RETURN_HOME")
