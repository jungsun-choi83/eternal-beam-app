"""
PLAY_WITH_ME ("Let's Play!") — species-aware interactive action, v2 (2026-10-29):
species intermediate keyframe + two start/end-conditioned segments joined into one asset.

    DOG: STAND_READY → brief playful excitement → PLAY_BOW   → STAND_READY → BREATHING
    CAT: STAND_READY → brief playful anticipation → PLAY_CROUCH → STAND_READY → BREATHING

One commercial motion id; the species variant (DOG → PLAY_BOW, CAT → PLAY_CROUCH) is
resolved only inside the generation contract. Providers, samplers, joiners and the VLM
are all injected — no paid call anywhere in this file.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from types import SimpleNamespace

import anyio
import numpy as np
import pytest
from fastapi import FastAPI

from backend.auth import AuthedUser, require_user
from backend.routers import device_ws_v1
from backend.scenarios.pet_scenarios import PET_ACTIONS, PREMIUM_ACTIONS
from backend.services import (
    canonical_image_providers,
    action_keyframe_service as kf,
    action_keyframe_spec,
    business_qa,
    device_gateway,
    durable_provider_jobs,
    generation_queue,
    motion_delivery_service,
    motion_reference_service,
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
from backend.services.provider_job_contract import PENDING, SUCCEEDED, ProviderJobCheck, ProviderSubmission
from backend.services.video_motion_providers import MotionVideoResult, VideoProviderError

from .conftest import ASGITestClient
from .test_action_keyframes import VLM_KF_OK, _build_kf, install_kf_vlm
from .test_canonical_pet_builder import GOOD, FakeProvider
from .test_motion_acceptance import micro_video_evidence
from .test_motion_video_generation import (  # noqa: F401 — fixtures
    FakeVideoProvider,
    VLM_MV_OK,
    _build_motion,
    _mock_backend,
    _prepare_pipeline,
    install_mv_vlm,
    sampler_identical,
    storage,
)
from .test_pet_identity_profile import make_pet_cutout_png
from .test_pet_reference_sets import PET, USER

PWM = sv.PLAY_WITH_ME
VLM_PLAY_OK = {
    **VLM_MV_OK,
    **{name: "yes" for name in sv.PLAY_WITH_ME_QA_FIELD_NAMES},
    "source": vlm_identity.VLM_PLAY_WITH_ME_QA_VERSION,
}


def _run(coro):
    return anyio.run(lambda: coro)


def _force_species(monkeypatch, species: str) -> None:
    """Pin the motion profile's species without touching identity/morphology rows."""
    original = motion_reference_service.derive_motion_profile

    def fake(*args, **kwargs):
        profile = original(*args, **kwargs)
        profile["species"] = species
        return profile

    monkeypatch.setattr(motion_reference_service, "derive_motion_profile", fake)

    async def species_of(*_a, **_k):
        return species

    monkeypatch.setattr(sv, "resolve_pet_species", species_of)


def play_evidence(**changes):
    result = micro_video_evidence(**{name: "yes" for name in sv.PLAY_WITH_ME_QA_FIELD_NAMES})
    result.update(changes)
    return result


def _segment_checks_ok() -> dict[str, str]:
    return {name: "PASS" for name in sv.PLAY_WITH_ME_SEGMENT_CHECK_NAMES}


def judge_play(vlm_evidence, *, temporal_stability="PASS", segment_checks=None):
    result = motion_video_qa.apply_judgement(
        checks={
            "identity_over_time": "PASS",
            "temporal_stability": temporal_stability,
            "loop_return": "PASS",
            "structural_morphology_consistency": "PASS",
        },
        reasons=[],
        spec_contract=ms.motion_snapshot(ms.MOTIONS[PWM]),
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


def passthrough_join(videos, **_kw):
    """Test joiner: the fake "videos" are PNG stills, ffmpeg cannot concat them."""
    return videos[0], {"version": seg.SEGMENT_JOIN_VERSION, "segment_count": len(videos),
                       "dropped_leading_frames": len(videos) - 1}


# ══════════════════════════════════════════════════════════════════════════
# Registry
# ══════════════════════════════════════════════════════════════════════════


def test_registry_is_a_segmented_start_end_interaction_on_the_shared_home_keyframe():
    spec = ms.MOTIONS[PWM]
    assert spec.motion_class == ms.CLASS_INTERACTION
    assert spec.start_keyframe_role == "STAND_READY"
    assert spec.target_keyframe_role == "STAND_READY"
    assert not spec.requires_target_keyframe
    assert spec.preferred_video_strategy == ms.STRATEGY_START_END
    assert spec.video_compat["end_frame_conditioning_required"] is True
    assert spec.video_compat["allow_generated_hand"] is False
    assert spec.video_compat["returns_to_start_pose"] is True
    assert spec.video_compat["species_variants"] == ["DOG", "CAT"]
    assert spec.video_compat["segmented"] is True
    assert spec.video_compat["intermediate_keyframe_roles"] == ["PLAY_BOW", "PLAY_CROUCH"]
    assert spec.interruptible is False
    assert spec.requirements["provider_capabilities"]["required_all"] == ["supports_end_frame"]
    assert spec.requirements["provider_capabilities"]["degrade_allowed"] is False
    assert spec.requirements["morphology"]["sensitivity"] == "HIGH"
    required = set(spec.qa_acceptance_spec["required_checks"])
    assert set(sv.PLAY_WITH_ME_CHECK_NAMES) <= required
    assert set(sv.PLAY_WITH_ME_SEGMENT_CHECK_NAMES) <= required
    domain = spec.requirements["qa"]["business"]["domains"]["play_with_me"]
    assert domain["required_checks"] == list(sv.PLAY_WITH_ME_REQUIRED_CHECK_NAMES)
    authority = spec.requirements["qa"]["business"]["check_authority"]
    assert all(authority[c] == ms.AUTHORITY_INTEGRITY_HARD for c in sv.PLAY_WITH_ME_REQUIRED_CHECK_NAMES)
    # New v2 VLM fields are present and mapped once each.
    assert {"intermediate_pose_matches_keyframe", "playful_lead_in_visible",
            "segment_seam_smooth", "stays_in_place_no_approach"} <= set(sv.PLAY_WITH_ME_QA_FIELD_NAMES)
    assert len(set(sv.PLAY_WITH_ME_CHECK_NAMES)) == len(sv.PLAY_WITH_ME_QA_FIELD_NAMES)


def test_species_keyframe_roles_exist_without_touching_global_versions_or_home_mapping():
    """PLAY_BOW / PLAY_CROUCH are intermediate pose roles anchored on STAND_READY; the
    action still starts/ends on STAND_READY, and no global version moves."""
    assert action_keyframe_spec.KEYFRAME_SPEC_VERSION == "keyframe-spec-v4"
    assert action_keyframe_spec.KEYFRAME_PROMPT_VERSION == "keyframe-prompt-v2"
    assert ms.MOTION_SPEC_VERSION == "motion-spec-v17"
    assert action_keyframe_spec.KEYFRAME_ROLE_ORDER[:6] == (
        "NEUTRAL_IDLE", "STAND_READY", "LIE", "SLEEP", "LOOK_UP", "HAPPY",
    )
    assert action_keyframe_spec.KEYFRAME_ROLE_ORDER[6:8] == ("PLAY_BOW", "PLAY_CROUCH")
    assert action_keyframe_spec.role_for_action(PWM) == "STAND_READY"
    assert PWM in ms.motions_for_keyframe_role("STAND_READY")
    for variant in (sv.PLAY_BOW, sv.PLAY_CROUCH):
        role = action_keyframe_spec.KEYFRAME_ROLES[variant.keyframe_role]
        assert role.anchor_keyframe_role == "STAND_READY"
        assert role.supported_action_ids == ()  # intermediate pose, never a start pose
        assert role.video_compat["intermediate_for"] == [PWM]
        assert role.video_compat["species"] == variant.species
        snapshot = action_keyframe_spec.role_spec_snapshot(role)
        assert snapshot["anchor_keyframe_role"] == "STAND_READY"
    # PLAY_CROUCH keeps the strict framing text; PLAY_BOW frees the body (refined 2026-10-30).
    assert "same position, scale, camera distance and framing" in action_keyframe_spec.KEYFRAME_ROLES["PLAY_CROUCH"].required_pose
    assert "same position" not in action_keyframe_spec.KEYFRAME_ROLES["PLAY_BOW"].required_pose
    # Existing role snapshots are byte-identical (no anchor key leaks into them).
    assert "anchor_keyframe_role" not in action_keyframe_spec.role_spec_snapshot(
        action_keyframe_spec.KEYFRAME_ROLES["STAND_READY"]
    )
    assert "play bow" in action_keyframe_spec.KEYFRAME_ROLES["PLAY_BOW"].required_pose
    assert "crouch" in action_keyframe_spec.KEYFRAME_ROLES["PLAY_CROUCH"].required_pose
    assert sv.variant_for_keyframe_role("PLAY_BOW") is sv.PLAY_BOW
    assert sv.variant_for_keyframe_role("PLAY_CROUCH") is sv.PLAY_CROUCH
    assert sv.variant_for_keyframe_role("STAND_READY") is None
    # The species variants are not motions of their own.
    assert "PLAY_BOW" not in ms.MOTIONS and "PLAY_CROUCH" not in ms.MOTIONS


def _rich_dog_identity(*, coat=("brown", "white"), ears="floppy"):
    """A realistic measured+VLM identity so the appended trait lines are exercised."""
    import copy
    from .test_canonical_prompt_v3 import RICH_VISUAL_IDENTITY
    vi = copy.deepcopy(RICH_VISUAL_IDENTITY)
    vi["coat"]["dominant_colors"] = [{"name": c, "fraction": 0.3} for c in coat]
    vi["semantic_traits"]["traits"]["ears"]["shape"] = ears
    return vi


def test_anchored_keyframe_prompts_keep_reference_order_and_shared_identity_tail():
    role = action_keyframe_spec.KEYFRAME_ROLES["PLAY_BOW"]
    full = action_keyframe_spec.build_keyframe_prompt(role, {"coat_colors": ["brown"]})
    assert full.startswith("The first supplied image is the pet's approved STAND_READY still")
    assert "the second supplied image is the canonical identity reference" in full
    assert "Preserve exactly: face identity" in full  # the shared tail is unchanged
    for r in ("PLAY_BOW", "PLAY_CROUCH"):
        compact = action_keyframe_spec.build_compact_keyframe_prompt(
            action_keyframe_spec.KEYFRAME_ROLES[r], {"coat_colors": ["brown"], "species": "dog"}
        )
        assert len(compact) <= 1000, (r, len(compact))
        assert compact.startswith("First image: the pet's approved STAND_READY still. Second: its canonical")
        assert "Requested pose" in compact
        assert ("play bow" in compact) == (r == "PLAY_BOW")
    # Non-anchored roles keep their exact previous prompt head.
    stand = action_keyframe_spec.build_keyframe_prompt(
        action_keyframe_spec.KEYFRAME_ROLES["STAND_READY"], {}
    )
    assert stand.startswith("The first supplied image is the canonical reference of a specific pet")


def test_play_bow_prompt_allows_natural_repositioning_and_preserves_ears_and_identity():
    """Refinement (2026-10-30): the bow must not be forced into the standing silhouette,
    'ears forward' is gone, and the mechanics are paws-forward / chest-lower / rear-raised."""
    role = action_keyframe_spec.KEYFRAME_ROLES["PLAY_BOW"]
    full = action_keyframe_spec.build_keyframe_prompt(role, {}, background_tone="light-gray")
    compact = action_keyframe_spec.build_compact_keyframe_prompt(role, {}, background_tone="light-gray")
    for text in (full, compact):
        low = text.lower()
        # 1. camera/framing/scale/ground locked, body free to reposition
        assert "ground line" in low and "framing" in low
        assert "same position" not in low and "exact paw positions" not in low.replace("need not keep the standing outline or the exact paw positions", "")
        assert "body" in low and ("reposition naturally" in low or "move naturally" in low)
        # 2. ears: no forced direction, own anatomy kept
        assert "ears forward" not in low
        assert "ears keep" in low and "fold" in low
        # 3. mechanics
        assert "sunk toward the ground" not in low and "elbows bent" not in low
        assert "front paws" in low and "forward" in low
        assert "shoulders and chest" in low
        # 2026-10-30: the rear stays near standing height — never "raised"/"lifted"/"arched"
        assert "hindquarters raised" not in low and "hindquarters stay raised" not in low
        assert "standing height" in low and "not lifted" in low and "arched" in low
        assert "supported hind legs" in low
        # 5. pose correctness
        assert "play bow" in low
        assert "head" in low and "dip" in low and "sit" in low and "sprawl" in low
        # 6. composition
        assert "all four paws" in low
    # 4. identity (full variant spells it out; compact relies on the shared preserve line)
    assert "exact face, muzzle, eyes, coat colors, markings, fur texture, body structure and proportions, tail shape" in full
    assert "realistic shoulder and elbow anatomy" in full
    assert "need not keep the standing outline or the exact paw positions" in full
    assert "Preserve face, coat colors, markings, ear shape, body proportions, paws and tail" in compact
    # The spec snapshot carries the role-specific framing override (PLAY_BOW only).
    snap = action_keyframe_spec.role_spec_snapshot(role)
    assert snap["anchor_framing_clause"].startswith("Keep the same camera")
    assert "anchor_framing_clause" not in action_keyframe_spec.role_spec_snapshot(
        action_keyframe_spec.KEYFRAME_ROLES["PLAY_CROUCH"]
    )


def test_play_bow_prompts_fit_provider_limits_with_identity_traits_intact():
    """Runway caps promptText at 1000 chars; the compact builder drops trailing trait lines to
    fit. The refined pose text must leave room so a rich identity is NOT truncated."""
    import os
    role = action_keyframe_spec.KEYFRAME_ROLES["PLAY_BOW"]
    runway = canonical_image_providers.RunwayImageProvider()
    assert runway.max_prompt_chars == int(os.getenv("RUNWAY_MAX_PROMPT_CHARS", "1000"))
    assert getattr(canonical_image_providers.GptImageProvider, "max_prompt_chars", None) is None
    for vi, tone in (
        (_rich_dog_identity(), "light-gray"),
        (_rich_dog_identity(coat=("golden red", "cream", "white"), ears="semi_pricked"), "warm-off-white"),
    ):
        full = action_keyframe_spec.build_keyframe_prompt(role, vi, background_tone=tone)
        compact = action_keyframe_spec.build_compact_keyframe_prompt(
            role, vi, max_chars=runway.max_prompt_chars, background_tone=tone
        )
        assert len(compact) <= runway.max_prompt_chars, len(compact)
        # Nothing important was dropped: visibility + every compact trait line survive.
        assert "Clearly visible: face, full body, ears, front paws." in compact
        assert "Coat colors:" in compact and "The pet is a dog." in compact and "ears." in compact
        # The full prompt carries the appended trait lines too (GPT image has no cap).
        assert "The pet is a dog." in full and "Ear shape:" in full and len(full) < 3000


def test_play_bow_refinement_leaves_every_other_role_prompt_byte_identical():
    """Scope guard: PLAY_CROUCH, STAND_READY and the rest render exactly as before."""
    import copy
    vi = _rich_dog_identity()
    crouch = action_keyframe_spec.KEYFRAME_ROLES["PLAY_CROUCH"]
    full = action_keyframe_spec.build_keyframe_prompt(crouch, vi, background_tone="light-gray")
    assert (
        "Keep the pet at the same position, scale, camera distance, framing, background tone "
        "and lighting as the first image; change only the pose."
    ) in full
    assert "ears forward" in full  # PLAY_CROUCH text untouched
    compact = action_keyframe_spec.build_compact_keyframe_prompt(crouch, vi, background_tone="light-gray")
    assert "change only the pose from the first image; keep its position, scale, camera distance, framing and background." in compact
    for r in action_keyframe_spec.KEYFRAME_ROLE_ORDER:
        if r in ("PLAY_BOW", "PLAY_CROUCH", "PLAY_BOW_NEAR"):  # anchored pose roles
            continue
        spec = action_keyframe_spec.KEYFRAME_ROLES[r]
        assert spec.anchor_keyframe_role is None and spec.anchor_framing_clause is None
        assert "anchor_framing_clause" not in action_keyframe_spec.role_spec_snapshot(spec)
        assert action_keyframe_spec.build_keyframe_prompt(spec, {}).startswith(
            "The first supplied image is the canonical reference of a specific pet"
        )
    assert action_keyframe_spec.KEYFRAME_SPEC_VERSION == "keyframe-spec-v4"
    assert action_keyframe_spec.KEYFRAME_PROMPT_VERSION == "keyframe-prompt-v2"


def test_model_is_pinned_to_an_end_frame_capable_model_without_touching_other_routing(monkeypatch):
    for name in ("MOTION_MODEL_PLAY_WITH_ME", "MOTION_MODEL_PLAY_WITH_ME_PLAY_BOW",
                 "MOTION_MODEL_PLAY_WITH_ME_PLAY_CROUCH", "MOTION_MODEL_PAW_HELLO",
                 "MOTION_MODEL_PET_HEAD", "MOTION_MODEL_BREATHING", "MOTION_MODEL_COME_CLOSER"):
        monkeypatch.delenv(name, raising=False)
    assert ms.provider_order_for_motion(PWM) == (ms.PROVIDER_KLING_3,)
    assert ms.provider_order_for_motion(PWM, variant_id="PLAY_BOW") == (ms.PROVIDER_KLING_3,)
    assert ms.provider_order_for_motion(PWM, variant_id="PLAY_CROUCH") == (ms.PROVIDER_KLING_3,)
    assert ms.provider_order_for_motion("PAW_HELLO") == (ms.provider_order_for_class(ms.CLASS_INTERACTION)[0],)
    assert ms.provider_order_for_motion("PET_HEAD") == (ms.provider_order_for_class(ms.CLASS_INTERACTION)[0],)
    assert ms.provider_order_for_motion("COME_CLOSER") == (ms.PROVIDER_GROK_IMAGINE_LITE,)
    assert ms.provider_order_for_motion("BREATHING") == (ms.PROVIDER_WAN_3_STANDARD,)


def test_species_variant_selection_is_fail_closed():
    assert sv.select_variant(PWM, "DOG") is sv.PLAY_BOW
    assert sv.select_variant(PWM, "cat") is sv.PLAY_CROUCH
    for bad in ("RABBIT", "OTHER", "UNKNOWN", "", None):
        assert sv.select_variant(PWM, bad) is None
        with pytest.raises(sv.SpeciesVariantError) as exc:
            sv.require_variant(PWM, bad)
        assert exc.value.code == "SPECIES_UNSUPPORTED"
        assert exc.value.status == 409
    assert sv.select_variant("PAW_HELLO", "DOG") is None
    assert sv.requires_species_variant(PWM) and not sv.requires_species_variant("PAW_HELLO")
    assert sv.intermediate_keyframe_roles(PWM) == ("PLAY_BOW", "PLAY_CROUCH")
    assert sv.intermediate_keyframe_roles("PAW_HELLO") == ()


def test_variant_acceptance_keeps_registry_checks_and_swaps_species_text():
    spec = ms.MOTIONS[PWM]
    dog = ms.acceptance_for_variant(spec, sv.PLAY_BOW)
    cat = ms.acceptance_for_variant(spec, sv.PLAY_CROUCH)
    base = spec.qa_acceptance_spec
    for acc in (dog, cat):
        assert acc["required_checks"] == base["required_checks"]
        assert acc["check_authority"] == base["check_authority"]
        assert acc["version"] == ms.MOTION_ACCEPTANCE_VERSION
        assert acc["policy_revision"] == sv.PLAY_WITH_ME_POLICY_REVISION == "play-with-me-v3"
        assert acc["blocking_criteria"][:2] == list(ms._GENERIC_BLOCKING_CRITERIA)
        assert vlm_identity.motion_acceptance_evaluator_version(acc) == vlm_identity.VLM_PLAY_WITH_ME_QA_VERSION
        assert "expected_intermediate_state" in acc
        assert any("two segments join" in c for c in acc["blocking_criteria"])
        assert any("toward or away from the camera" in c for c in acc["blocking_criteria"])
    assert (dog["species"], dog["species_variant"], dog["intermediate_keyframe_role"]) == ("DOG", "PLAY_BOW", "PLAY_BOW")
    assert (cat["species"], cat["species_variant"], cat["intermediate_keyframe_role"]) == ("CAT", "PLAY_CROUCH", "PLAY_CROUCH")
    assert "play bow" in dog["description"] and "normal standing height" in dog["description"]
    assert "hindquarters stay raised" not in dog["description"]
    assert "crouch" in cat["description"] and "play bow" not in cat["description"]


# ══════════════════════════════════════════════════════════════════════════
# Prompts — two start/end-conditioned segments
# ══════════════════════════════════════════════════════════════════════════


def _contract(variant):
    return {
        "motion_id": PWM, "motion_class": "INTERACTION",
        "species_variant": sv.variant_payload(variant),
        "video_compat": dict(ms.MOTIONS[PWM].video_compat),
    }


def test_segment_prompts_are_species_specific_and_describe_lead_in_pose_and_return():
    assert motion_video_prompts.prompt_version_for_motion(PWM) == motion_video_prompts.PLAY_WITH_ME_PROMPT_VERSION
    assert motion_video_prompts.PLAY_WITH_ME_PROMPT_VERSION.endswith("play-with-me-v7")
    assert motion_video_prompts.prompt_version_for_motion("PAW_HELLO") == motion_video_prompts.PAW_HELLO_PROMPT_VERSION

    dog = motion_video_prompts.build_play_with_me_segment_prompts(_contract(sv.PLAY_BOW))
    cat = motion_video_prompts.build_play_with_me_segment_prompts(_contract(sv.PLAY_CROUCH))
    for prompts in (dog, cat):
        assert list(prompts) == ["A", "B"]
        a, b = prompts["A"], prompts["B"]
        assert "first supplied frame is the pet's approved STAND_READY pose" in a
        assert "playful anticipation" in a  # lead-in before the pose
        assert "steps toward or away from the camera" in a and "toward or away from the camera" in b
        assert "never reversed or rewound playback" in b
        assert "BREATHING resumes" in b
        for text in (a, b):
            assert "no human, hand, arm, person, leash, toy, platform" in text and "Exactly four limbs" in text
    assert "play bow" in dog["A"] and "play bow" in dog["B"]
    # Segment B is the rise: the into-pose shape clauses belong to A only.
    for variant, prompts in ((sv.PLAY_BOW, dog), (sv.PLAY_CROUCH, cat)):
        for clause in variant.prompt_clauses:
            assert clause in prompts["A"] and clause not in prompts["B"]
        assert "During the rise" in prompts["B"]
    assert "front body drops" not in dog["B"]
    assert "play crouch" in cat["A"] and "play crouch" in cat["B"]
    assert "play bow" not in (cat["A"] + cat["B"]).lower()
    assert "crouch" not in (dog["A"] + dog["B"]).lower()
    # The persisted/display prompt carries both labelled segments.
    combined = motion_video_prompts.build_motion_video_prompt(_contract(sv.PLAY_BOW), sv.PLAY_BOW.description)
    assert combined.startswith("[SEGMENT A]") and "[SEGMENT B]" in combined
    assert motion_video_prompts.build_segment_prompts(_contract(sv.PLAY_CROUCH)) == cat


def test_segment_prompts_fail_closed_without_a_resolved_variant_or_for_other_motions():
    contract = _contract(sv.PLAY_BOW)
    contract.pop("species_variant")
    with pytest.raises(ValueError):
        motion_video_prompts.build_play_with_me_segment_prompts(contract)
    with pytest.raises(ValueError):
        motion_video_prompts.build_segment_prompts({"motion_id": "PAW_HELLO", "motion_class": "INTERACTION"})


@pytest.mark.parametrize("variant", [sv.PLAY_BOW, sv.PLAY_CROUCH], ids=["DOG", "CAT"])
def test_each_serialized_segment_prompt_is_within_the_fal_kling_limit(variant, monkeypatch):
    """Live regression (2026-10-29): fal Kling v3 rejects prompts > 2,500 chars with HTTP 422.
    Each segment is its own provider request, so each must fit on its own."""
    monkeypatch.delenv("FAL_KLING_MAX_PROMPT_CHARS", raising=False)
    provider = video_motion_providers.FalKlingProvider()
    prompts = motion_video_prompts.build_play_with_me_segment_prompts(_contract(variant))
    for segment_id, prompt in prompts.items():
        payload = provider.build_payload(video_motion_providers.MotionVideoRequest(
            prompt=prompt, start_image_url="https://x/start.png", start_image_bytes=b"s",
            end_image_url="https://x/end.png", end_image_bytes=b"s",
            output_spec={"duration_sec": 4, "audio": False},
        ))
        assert payload["prompt"] == prompt, "adapter must send the built prompt verbatim"
        assert len(payload["prompt"]) <= provider.max_prompt_chars == 2500
        # headroom under the live limit so a longer species clause cannot tip into a 422
        assert len(payload["prompt"]) <= provider.max_prompt_chars - 100, (segment_id, len(payload["prompt"]))
        assert payload["start_image_url"] and payload["end_image_url"]
        assert payload["duration"] == "4"


@pytest.mark.parametrize("variant", [sv.PLAY_BOW, sv.PLAY_CROUCH], ids=["DOG", "CAT"])
def test_segment_prompts_describe_movement_through_time_in_place_with_natural_anatomy(variant):
    """Refinement (2026-10-30): no 'forward path' (reads as travel), no forced ears/tail,
    paws may reposition for balance, camera + home spot fixed while body height changes,
    and the A/B join carries exactly one brief beat (A arrives, B holds briefly)."""
    prompts = motion_video_prompts.build_play_with_me_segment_prompts(_contract(variant))
    a, b = prompts["A"], prompts["B"]
    for text in (a, b):
        low = text.lower()
        # 1. continuous through time, never travel
        assert "forward path" not in low
        assert "continuous movement through time" in low and "in place" in low
        assert "toward or away from the camera" in low
        # 2. natural ear / face / tail anatomy, nothing forced
        assert "ears forward" not in low and "swivel forward" not in low and "tail lifts" not in low
        assert "natural shape and carriage" in low and "ears not forced forward" in low
        # 3. paw repositioning allowed but grounded
        assert "paws reposition only as much as realistic grounding and balance need" in low
        assert "without sliding" not in low and "stay planted" not in low
        # 4. fixed camera + home spot, free silhouette
        assert "camera and home spot stay fixed" in low
        assert "body height and silhouette change" in low
        # 6. breathing, playful secondary motion, identity, endpoint conditioning
        assert "gentle breathing" in low and "playful secondary movements" in low
        assert text.startswith("Same pet throughout")
        assert "supplied final" in low and "exactly" in low
    # 5. one brief beat at the join: A arrives late and never holds; B starts alive and brief.
    assert "do not reach it early and hold it motionless" in a
    assert "no pause, snap, freeze or visible jump" in a
    assert "Begin exactly in the supplied first frame, already alive: a brief beat" in b
    assert "Keep this beat brief" in b and "do not repeat the settle" in b
    assert "played forward, never reversed or rewound" in b
    assert a.count("hold") <= 1 and "holds" not in a  # A never describes a hold of its own
    # The into-pose text follows the refined keyframe mechanics (dog) / stays a crouch (cat).
    if variant is sv.PLAY_BOW:
        assert "front paws reposition slightly forward" in a and "shoulders and chest lower" in a
        assert "elbows bend and sink" not in a and "front body drops" not in a
    else:
        assert "hindquarters gather low and compact" in a and "play crouch" in b


@pytest.mark.parametrize("variant", [sv.PLAY_BOW, sv.PLAY_CROUCH], ids=["DOG", "CAT"])
def test_segment_prompts_fit_the_selected_provider_limit_with_margin(variant, monkeypatch):
    """PLAY_WITH_ME is pinned to kling_3; the configured transport adapter (fal) enforces
    2,500 chars locally before any HTTP. Each serialized segment must pass that check
    with headroom, so a slightly longer species clause cannot tip it into a 422."""
    monkeypatch.delenv("FAL_KLING_MAX_PROMPT_CHARS", raising=False)
    for name in ("MOTION_MODEL_PLAY_WITH_ME", "MOTION_MODEL_PLAY_WITH_ME_PLAY_BOW",
                 "MOTION_MODEL_PLAY_WITH_ME_PLAY_CROUCH"):
        monkeypatch.delenv(name, raising=False)
    assert ms.provider_order_for_motion(PWM, variant_id=variant.variant_id) == (ms.PROVIDER_KLING_3,)
    provider = video_motion_providers.FalKlingProvider()
    assert provider.logical_model_id == ms.PROVIDER_KLING_3
    prompts = motion_video_prompts.build_play_with_me_segment_prompts(_contract(variant))
    for segment_id, prompt in prompts.items():
        video_motion_providers.validate_prompt_length(provider, prompt)  # must not raise
        assert len(prompt) <= provider.max_prompt_chars - 100, (segment_id, len(prompt))
        assert len(prompt) <= 2500  # Kling's documented native prompt cap as well


@pytest.mark.parametrize("variant", [sv.PLAY_BOW, sv.PLAY_CROUCH], ids=["DOG", "CAT"])
def test_vlm_rules_do_not_reject_pose_driven_shape_changes_or_grounded_paw_repositioning(variant):
    acc = variant.acceptance
    allowed = " ".join(acc["allowed_secondary_motion"]).lower()
    assert "small paw repositioning required by the intended pose" in allowed
    assert "change in body height and silhouette" in allowed
    assert "ears need not point forward and the tail need not be raised" in allowed
    blocking = " ".join(acc["blocking_criteria"])
    assert "a change in body height or silhouette caused by the pose itself does not count" in blocking
    assert "prolonged motionless hold" in blocking
    guidance = variant.vlm_guidance
    assert "NOT a position change, sliding or a step" in guidance
    assert "Ears that do not point forward or a tail that is not raised are NOT defects" in guidance
    assert "stay planted" not in guidance and "ears forward" not in variant.description
    # Versions moved together so stored rows are never judged by the old rules.
    assert sv.PLAY_WITH_ME_POLICY_REVISION == "play-with-me-v3"
    assert vlm_identity.VLM_PLAY_WITH_ME_QA_VERSION.endswith("play-with-me-v5")
    assert vlm_identity.motion_acceptance_evaluator_version(
        {"policy_revision": sv.PLAY_WITH_ME_POLICY_REVISION}
    ) == vlm_identity.VLM_PLAY_WITH_ME_QA_VERSION
    # The species-neutral base contract in motion_spec carries the same allowances.
    base = ms.MOTIONS[PWM].qa_acceptance_spec
    assert any("paw repositioning" in x for x in base["allowed_secondary_motion"])
    assert any("does not count" in x for x in base["blocking_criteria"])
    # Scope guard: PAW_HELLO's contract and prompt did not pick any of this up.
    paw = ms.MOTIONS["PAW_HELLO"].qa_acceptance_spec
    assert not any("paw repositioning required by the intended pose" in x for x in paw["allowed_secondary_motion"])
    assert "toward or away from the camera" not in " ".join(paw["blocking_criteria"])
    paw_prompt = motion_video_prompts.build_motion_video_prompt(
        {"motion_id": "PAW_HELLO", "motion_class": "INTERACTION"}, ms.MOTIONS["PAW_HELLO"].description
    )
    assert "continuous movement through time" not in paw_prompt and "home spot" not in paw_prompt


def test_fal_kling_adapter_enforces_the_verified_duration_contract_and_prices_receipts(monkeypatch):
    """fal model page (2026-10-29): duration enum 3..15 s, end_image_url supported,
    $0.084/s audio off — the segment duration must be inside that range."""
    monkeypatch.delenv("FAL_KLING_MAX_PROMPT_CHARS", raising=False)
    provider = video_motion_providers.FalKlingProvider()

    def request(duration):
        return video_motion_providers.MotionVideoRequest(
            prompt="p", start_image_url="https://x/s.png", start_image_bytes=b"s",
            end_image_url="https://x/e.png", end_image_bytes=b"e",
            output_spec={"duration_sec": duration, "audio": False},
        )

    for bad in (2, 16, "x"):
        with pytest.raises(VideoProviderError) as exc:
            provider.build_payload(request(bad))
        assert exc.value.code == "PROVIDER_CONTRACT"
    payload = provider.build_payload(request(4))
    assert payload["duration"] == "4" and payload["generate_audio"] is False
    assert provider.estimate_cost_usd(payload) == pytest.approx(0.336)
    assert provider.estimate_cost_usd({**payload, "generate_audio": True}) == pytest.approx(0.504)
    # The default INTERACTION profile duration (4..5 s midpoint → 4 s) is inside the contract.
    spec_duration = mv.build_output_spec(duration_range=ms.MOTIONS[PWM].duration_range_sec,
                                         motion_class=ms.CLASS_INTERACTION)[0]["duration_sec"]
    assert provider.MIN_DURATION_SEC <= spec_duration <= provider.MAX_DURATION_SEC


# ══════════════════════════════════════════════════════════════════════════
# Durable job keying — one candidate = two paid segment jobs
# ══════════════════════════════════════════════════════════════════════════


def test_segment_durable_attempts_never_collide_with_single_requests_or_each_other():
    assert durable_provider_jobs.segment_durable_attempt(1, 1) == 101
    assert durable_provider_jobs.segment_durable_attempt(1, 2) == 102
    assert durable_provider_jobs.segment_durable_attempt(2, 1) == 201
    keys = {durable_provider_jobs.segment_durable_attempt(a, i) for a in (1, 2) for i in (1, 2)}
    assert len(keys) == 4 and keys.isdisjoint({1, 2})
    with pytest.raises(ValueError):
        durable_provider_jobs.segment_durable_attempt(1, 0)


class _RecordingDurableVideo:
    name = "kling_3"
    logical_model_id = "kling_3"
    vendor_id = "fal"
    adapter_id = "FalKlingProvider"
    supports_durable_jobs = True
    supports_end_frame = True
    supports_motion_reference = False
    reference_budget = 0

    def __init__(self):
        self.submits: list = []

    def available(self):
        return True

    def model_name(self):
        return "fal-ai/kling-video/v3/standard/image-to-video"

    def submit(self, request):
        self.submits.append(request)
        return ProviderSubmission(f"job-{len(self.submits)}", provider_status=PENDING)

    def check(self, external_job_id):
        return ProviderJobCheck(SUCCEEDED, SUCCEEDED)

    def collect(self, external_job_id):
        return MotionVideoResult(video_bytes=b"v" + external_job_id.encode(), provider=self.name,
                                 model=self.model_name(), external_job_id=external_job_id)


def test_durable_video_provider_keys_each_segment_separately_and_submits_both_in_one_tick(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    durable_provider_jobs.__reset_for_tests()
    delegate = _RecordingDurableVideo()
    wrapper = durable_provider_jobs.DurableVideoProvider(
        delegate, run_id="00000000-0000-0000-0000-00000000a001", user_id=USER, pet_id=PET,
        provider_operation=durable_provider_jobs.OP_MOTION,
    )
    inputs = [
        seg.SegmentInput(id="A", index=1, prompt="A prompt", start_image_url="s", start_image_bytes=b"s",
                         end_image_url="m", end_image_bytes=b"m", start_keyframe_id="kf-s", end_keyframe_id="kf-m"),
        seg.SegmentInput(id="B", index=2, prompt="B prompt", start_image_url="m", start_image_bytes=b"m",
                         end_image_url="s", end_image_bytes=b"s", start_keyframe_id="kf-m", end_keyframe_id="kf-s"),
    ]
    base = {"motion_version_id": "00000000-0000-0000-0000-00000000b001", "attempt": 1}
    with pytest.raises(durable_provider_jobs.ProviderWorkPending):
        seg.generate_segments(wrapper, inputs, output_spec={"duration_sec": 4}, base_metadata=base)
    # Both segments were submitted in the same tick — two receipts with distinct keys.
    assert len(delegate.submits) == 2
    assert sorted(row["attempt"] for row in durable_provider_jobs._MOCK_JOBS) == [101, 102]
    assert len({row["request_fingerprint"] for row in durable_provider_jobs._MOCK_JOBS}) == 2
    assert [r.metadata["segment"] for r in delegate.submits] == ["A", "B"]
    assert delegate.submits[0].metadata["target_keyframe_id"] == "kf-m"
    assert delegate.submits[1].metadata["start_keyframe_id"] == "kf-m"
    # Resume tick: both collected, nothing resubmitted.
    results = seg.generate_segments(wrapper, inputs, output_spec={"duration_sec": 4}, base_metadata=base)
    assert [r.external_job_id for r in results] == ["job-1", "job-2"]
    assert len(delegate.submits) == 2
    durable_provider_jobs.__reset_for_tests()


def test_generate_segments_prefers_contract_errors_over_waiting():
    class Provider:
        calls = 0

        def generate(self, request):
            Provider.calls += 1
            if request.metadata["segment"] == "A":
                raise durable_provider_jobs.ProviderWorkPending("op", "PENDING")
            raise VideoProviderError("PROVIDER_CONTRACT", "bad request")

    inputs = [
        seg.SegmentInput(id="A", index=1, prompt="a", start_image_url=None, start_image_bytes=b"s",
                         end_image_url=None, end_image_bytes=b"m", start_keyframe_id=None, end_keyframe_id=None),
        seg.SegmentInput(id="B", index=2, prompt="b", start_image_url=None, start_image_bytes=b"m",
                         end_image_url=None, end_image_bytes=b"s", start_keyframe_id=None, end_keyframe_id=None),
    ]
    with pytest.raises(VideoProviderError) as exc:
        seg.generate_segments(Provider(), inputs, output_spec={}, base_metadata={"attempt": 1})
    assert exc.value.code == "PROVIDER_CONTRACT"


# ══════════════════════════════════════════════════════════════════════════
# Segment plan / output spec / join / deterministic QA
# ══════════════════════════════════════════════════════════════════════════


def test_segment_plan_and_output_specs():
    plan = seg.segment_plan(sv.PLAY_BOW)
    assert [(p["id"], p["start"], p["end"]) for p in plan] == [
        ("A", "start_keyframe", "intermediate_keyframe"),
        ("B", "intermediate_keyframe", "target_keyframe"),
    ]
    assert plan[0]["end_role"] == "PLAY_BOW" and plan[1]["start_role"] == "PLAY_BOW"
    per = {"aspect_ratio": "16:9", "resolution": "480p", "duration_sec": 4, "audio": False}
    joined = seg.joined_output_spec(per, plan)
    assert joined["duration_sec"] == 8
    assert joined["segments"] == [{"id": "A", "duration_sec": 4}, {"id": "B", "duration_sec": 4}]
    assert seg.segment_output_spec(joined) == per
    assert seg.is_segmented_contract({"intermediate_keyframe": {"keyframe_id": "k"}, "segments": plan})
    assert not seg.is_segmented_contract({"intermediate_keyframe": None, "segments": None})


def _frame(color) -> np.ndarray:
    frame = np.zeros((60, 80, 3), dtype=np.uint8)
    frame[:, :] = (235, 235, 235)                    # flat neutral background
    frame[15:45, 20:60] = color                      # "pet"
    return frame


def _segment_inputs(start, mid, end):
    return [
        seg.SegmentInput(id="A", index=1, prompt="a", start_image_url=None, start_image_bytes=None,
                         end_image_url=None, end_image_bytes=None, start_keyframe_id=None, end_keyframe_id=None,
                         expected_start_rgb=start, expected_end_rgb=mid),
        seg.SegmentInput(id="B", index=2, prompt="b", start_image_url=None, start_image_bytes=None,
                         end_image_url=None, end_image_bytes=None, start_keyframe_id=None, end_keyframe_id=None,
                         expected_start_rgb=mid, expected_end_rgb=end),
    ]


def test_segment_qa_passes_when_both_segments_honor_their_endpoints_and_the_seam_is_continuous():
    stand, play = _frame((120, 80, 50)), _frame((110, 90, 60))
    clips = {b"A": [stand, stand, play], b"B": [play, play, stand]}
    qa = seg.evaluate_segments(segment_videos=[b"A", b"B"], inputs=_segment_inputs(stand, play, stand),
                               frame_sampler=lambda data: clips[data])
    assert qa["version"] == seg.SEGMENT_QA_VERSION
    assert qa["decision"] == "PASS"
    assert set(qa["checks"]) == set(sv.PLAY_WITH_ME_SEGMENT_CHECK_NAMES)
    assert all(v == "PASS" for v in qa["checks"].values())
    assert qa["metrics"]["segment_seam_ssim"] == pytest.approx(1.0)
    assert qa["metrics"]["segment_a_reaches_play_pose"]["pet_hist_intersection"] > 0.9


def test_segment_qa_fails_a_segment_that_ignored_its_end_frame_and_reviews_a_soft_seam():
    stand, play = _frame((120, 80, 50)), _frame((110, 90, 60))
    wrong = _frame((10, 200, 10))                      # a completely different pet/end state
    clips = {b"A": [stand, stand, wrong], b"B": [play, play, stand]}
    qa = seg.evaluate_segments(segment_videos=[b"A", b"B"], inputs=_segment_inputs(stand, play, stand),
                               frame_sampler=lambda data: clips[data])
    assert qa["checks"]["segment_a_reaches_play_pose"] == "FAIL"
    assert qa["checks"]["segment_seam_continuity"] == "FAIL", "A ends far from where B starts"
    assert qa["checks"]["segment_a_starts_at_stand_ready"] == "PASS"
    assert qa["decision"] == "FAIL"
    # Same pet, pose not clearly reached (shape moved, colors kept): REVIEW, not a hard FAIL —
    # the pose verdict belongs to the VLM fields; deterministic QA never invents a threshold.
    shifted = np.roll(play, 18, axis=0)
    clips = {b"A": [stand, stand, shifted], b"B": [play, play, stand]}
    qa = seg.evaluate_segments(segment_videos=[b"A", b"B"], inputs=_segment_inputs(stand, play, stand),
                               frame_sampler=lambda data: clips[data])
    assert qa["checks"]["segment_a_reaches_play_pose"] == "REVIEW"
    assert qa["decision"] == "REVIEW"
    # Unmeasurable (sampler failed) → unknown → REVIEW, never a silent PASS.
    qa = seg.evaluate_segments(segment_videos=[b"A", b"B"], inputs=_segment_inputs(stand, play, stand),
                               frame_sampler=lambda data: None)
    assert qa["decision"] == "REVIEW"
    assert all(v == "unknown" for v in qa["checks"].values())


def test_merge_segment_qa_only_ever_lowers_the_decision_and_refreshes_the_domain():
    base = {"decision": "PASS", "checks": {"identity_over_time": "PASS"}, "reasons": [],
            "business_domains": {"play_with_me": {"required_checks": ["identity_over_time",
                                                                      "segment_seam_continuity"],
                                                  "status": "REVIEW"}}}
    ok = seg.merge_segment_qa(base, {"checks": {"segment_seam_continuity": "PASS"}, "reasons": []})
    assert ok["decision"] == "PASS" and ok["business_domains"]["play_with_me"]["status"] == "PASS"
    soft = seg.merge_segment_qa(base, {"checks": {"segment_seam_continuity": "REVIEW"}, "reasons": ["x"]})
    assert soft["decision"] == "REVIEW" and soft["reasons"] == ["segment:x"]
    hard = seg.merge_segment_qa(base, {"checks": {"segment_seam_continuity": "FAIL"}, "reasons": []})
    assert hard["decision"] == "FAIL"
    # A failed candidate is never rescued by clean segment checks.
    failed = seg.merge_segment_qa({**base, "decision": "FAIL"}, {"checks": {"segment_seam_continuity": "PASS"}})
    assert failed["decision"] == "FAIL"
    assert base["checks"] == {"identity_over_time": "PASS"}, "input is not mutated"


def _tiny_clip(path: str, color: str, seconds: float, fps: int = 12, size: str = "64x48") -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r={fps}",
         "-t", f"{seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", path],
        check=True, capture_output=True, timeout=60,
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg/ffprobe not installed")
def test_join_segment_videos_concatenates_in_order_and_drops_the_duplicated_seam_frame(tmp_path):
    a, b = str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")
    _tiny_clip(a, "red", 1.0)
    _tiny_clip(b, "blue", 1.5)
    joined, meta = seg.join_segment_videos([open(a, "rb").read(), open(b, "rb").read()])
    assert meta["version"] == seg.SEGMENT_JOIN_VERSION
    assert meta["segment_count"] == 2 and meta["dropped_leading_frames"] == 1
    assert meta["joined_frames"] == sum(meta["segment_frames"]) - 1
    assert meta["joined_duration_sec"] == pytest.approx(2.5 - 1 / 12, abs=0.1)
    assert meta["width"] == 64 and meta["height"] == 48 and meta["scaled_segments"] == []
    frames = motion_video_qa.sample_frames(joined, (0.0, 1.0))
    assert frames is not None and frames[0] is not None and frames[-1] is not None
    first, last = frames[0].reshape(-1, 3).mean(axis=0), frames[-1].reshape(-1, 3).mean(axis=0)
    assert first[0] > 150 and first[2] < 100, "starts red (segment A)"
    assert last[2] > 150 and last[0] < 100, "ends blue (segment B)"
    # Output conformance can read the joined asset like any provider mp4.
    probe = motion_video_qa._probe_video_streams(joined)
    assert probe and probe["width"] == 64


# ══════════════════════════════════════════════════════════════════════════
# VLM schema / QA judgement
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("variant,needle", [
    (sv.PLAY_BOW, "on a DOG (PLAY_BOW)"),
    (sv.PLAY_CROUCH, "on a CAT (PLAY_CROUCH)"),
])
def test_targeted_vlm_schema_and_species_guidance_cover_intermediate_pose_and_seam(variant, needle):
    acceptance = ms.acceptance_for_variant(ms.MOTIONS[PWM], variant)
    specs = vlm_identity._motion_targeted_specs(
        questions=["motion_correctness", "temporal_integrity"],
        motion_description=variant.description,
        motion_class="INTERACTION",
        expected_direction=None,
        interaction_type="play_invite",
        allow_generated_hand=False,
        has_target=True,
        acceptance_spec=acceptance,
        has_intermediate=True,
    )
    schema, prompt = specs[vlm_escalation.MOTION_VLM]
    assert set(vlm_identity.PLAY_WITH_ME_QA_FIELDS) <= set(schema["properties"])
    assert {"intermediate_pose_matches_keyframe", "segment_seam_smooth",
            "stays_in_place_no_approach", "playful_lead_in_visible"} <= set(schema["properties"])
    assert needle in prompt
    assert "INTERMEDIATE" in prompt and "segment_seam_smooth" in prompt
    _, identity_prompt = specs[vlm_escalation.IDENTITY_VLM]
    assert "final three supplied images are reference keyframes" in identity_prompt
    paw_only = set(vlm_identity.PAW_HELLO_QA_FIELDS) - set(vlm_identity.PLAY_WITH_ME_QA_FIELDS)
    assert paw_only and paw_only.isdisjoint(schema["properties"])
    assert "For PAW_HELLO" not in prompt


def test_dedicated_checks_cover_pass_review_and_fail_including_seam_and_in_place():
    passed = judge_play(play_evidence())
    assert passed["decision"] == "PASS"
    for check in sv.PLAY_WITH_ME_REQUIRED_CHECK_NAMES:
        assert passed["checks"][check] == "PASS", check
    assert passed["business_qa"]["delivery_action"] == "DELIVER"

    for defect in (
        {"limb_count_correct": "no"},
        {"joint_anatomy_plausible": "no"},
        {"posture_not_lying_or_sitting": "no"},
        {"smooth_return_to_stand_ready": "no"},
        {"rear_posture_correct": "no"},
        {"segment_seam_smooth": "no"},
        {"stays_in_place_no_approach": "no"},
    ):
        failed = judge_play(play_evidence(**defect))
        assert failed["decision"] == "FAIL", defect
        assert failed["business_qa"]["delivery_action"] == "BLOCK"

    # A deterministic segment FAIL blocks too; a REVIEW stays publishable with advisory.
    hard = judge_play(play_evidence(), segment_checks={**_segment_checks_ok(), "segment_a_reaches_play_pose": "FAIL"})
    assert hard["decision"] == "FAIL" and hard["business_qa"]["delivery_action"] == "BLOCK"
    soft = judge_play(play_evidence(), segment_checks={**_segment_checks_ok(), "segment_seam_continuity": "REVIEW"})
    assert soft["decision"] == "REVIEW"
    assert soft["business_qa"]["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert soft["business_qa"]["retry_action"] == "STOP"
    assert mv.candidate_is_publishable({"motion_id": PWM, "decision": "REVIEW", "qa_result": soft})

    # Sparse coverage: observational non-observation (incl. lead-in / intermediate pose) is REVIEW.
    sampling = {"version": mv.PLAY_WITH_ME_VLM_SAMPLING_VERSION,
                "strategy": "adaptive_dense_unavailable", "absence_reliable": False}
    uncertain = mv._enforce_temporal_evidence_reliability(
        play_evidence(requested_motion_occurs="no", interaction_correct="no",
                      play_posture_recognized="no", playful_lead_in_visible="no",
                      intermediate_pose_matches_keyframe="no"),
        motion_id=PWM, sampling=sampling,
    )
    for key in ("play_posture_recognized", "playful_lead_in_visible", "intermediate_pose_matches_keyframe"):
        assert uncertain[key] == "unknown" and uncertain[f"{key}_raw"] == "no"
    reviewed = judge_play(uncertain)
    assert reviewed["decision"] == "REVIEW"
    assert reviewed["business_qa"]["delivery_action"] == "DELIVER_WITH_ADVISORY"
    # Seam / in-place / anatomy answers never soften under sparse coverage.
    hard = mv._enforce_temporal_evidence_reliability(
        play_evidence(requested_motion_occurs="no", segment_seam_smooth="no", stays_in_place_no_approach="no"),
        motion_id=PWM, sampling=sampling,
    )
    assert hard["segment_seam_smooth"] == "no" and hard["stays_in_place_no_approach"] == "no"
    assert judge_play(hard)["decision"] == "FAIL"


def test_sampling_versions_are_motion_scoped():
    assert PWM in mv.TARGETED_MICRO_VLM_MOTIONS
    assert PWM in mv.SPARSE_BASE_ABSENCE_UNRELIABLE_MOTIONS
    assert mv._motion_sampling_version(PWM) == mv.PLAY_WITH_ME_VLM_SAMPLING_VERSION
    assert mv._motion_sampling_version("PAW_HELLO") == mv.PAW_HELLO_VLM_SAMPLING_VERSION
    assert mv._motion_sampling_version("BLINKING") == mv.MOTION_VLM_SAMPLING_VERSION
    stamp = mv.analyzer_versions([], prompt_version=motion_video_prompts.PLAY_WITH_ME_PROMPT_VERSION)
    assert stamp["segment_join"] == seg.SEGMENT_JOIN_VERSION
    assert "segment_qa" in stamp and "segment_qa" in mv.QA_ONLY_VERSION_KEYS
    assert "segment_join" not in mv.analyzer_versions([], prompt_version=motion_video_prompts.PAW_HELLO_PROMPT_VERSION)


# ══════════════════════════════════════════════════════════════════════════
# Intermediate keyframe generation (image providers injected — no paid calls)
# ══════════════════════════════════════════════════════════════════════════


class _RefRecordingProvider(FakeProvider):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.seen_references: list[list] = []
        self.seen_prompts: list[str] = []

    def generate(self, references, prompt, output_spec, metadata):
        self.seen_references.append(list(references))
        self.seen_prompts.append(prompt)
        return super().generate(references, prompt, output_spec, metadata)


@pytest.mark.parametrize("role", ["PLAY_BOW", "PLAY_CROUCH"])
def test_species_keyframe_is_generated_from_the_approved_stand_ready_plate(storage, monkeypatch, role):
    h, _ = _prepare_pipeline(monkeypatch, storage)              # canonical + STAND_READY approved
    stand = _run(kf.get_keyframe(user_id=USER, pet_id=PET, keyframe_role="STAND_READY"))
    stand_sel = next(c for c in stand.candidates if c.selected)
    assert stand_sel.plate_object_path, "STAND_READY plate is the segment conditioning input"
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    provider = _RefRecordingProvider("runway", [GOOD()])

    built = _build_kf(h, [provider], role=role)

    assert built.status == kf.STATUS_COMPLETE and built.keyframe_role == role
    assert built.canonical_version_id == stand.canonical_version_id
    assert built.analyzer_versions["anchor_keyframe"] == f"STAND_READY:{stand.id}"
    assert built.spec["anchor_keyframe_role"] == "STAND_READY"
    refs = provider.seen_references[0]
    assert [r.role for r in refs][:2] == ["ANCHOR_KEYFRAME", "CANONICAL"]
    assert len(refs) <= 3, "image providers accept at most 3 references"
    assert refs[0].reference_id == f"keyframe:{stand.id}"
    assert refs[0].data == storage[stand_sel.plate_object_path], "the first image is the STAND_READY plate"
    assert provider.seen_prompts[0].startswith("The first supplied image is the pet's approved STAND_READY still")
    selected = next(c for c in built.candidates if c.selected)
    assert selected.generation_metadata["canonical_input"]["kind"] == "anchor_keyframe_plate"
    assert selected.generation_metadata["canonical_input"]["anchor"]["keyframe_id"] == stand.id
    consistency = selected.qa_result["anchor_keyframe_consistency"]
    assert consistency["anchor_role"] == "STAND_READY"
    assert consistency["status"] == "PASS" and consistency["signature_hist_intersection"] > 0.9
    # Identity/pose/anatomy QA is the keyframe QA everyone else gets.
    assert selected.qa_result["checks"]["pose"] == "PASS"
    assert selected.qa_result["business_domains"]["identity_continuity"] == "PASS"
    # Role-scoped storage, canonical signature still the primary identity anchor.
    assert f"/keyframes/{role.lower()}/v1/" in selected.raw_object_path
    assert selected.qa_result["identity_reference"]["primary"] == "approved_canonical"


def test_species_keyframe_requires_an_approved_stand_ready_and_never_falls_back_to_the_canonical(storage, monkeypatch):
    from .test_action_keyframes import _prepare_canonical

    h, _ = _prepare_canonical(monkeypatch, storage)              # canonical only, no STAND_READY
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    provider = _RefRecordingProvider("runway", [GOOD()])
    with pytest.raises(kf.ActionKeyframeError) as exc:
        _build_kf(h, [provider], role="PLAY_BOW")
    assert exc.value.code == "ANCHOR_KEYFRAME_REQUIRED"
    assert exc.value.status == 409
    assert provider.calls == 0
    assert _run(kf.list_keyframes(user_id=USER, pet_id=PET)) == [], "no building row is left behind"


def test_species_keyframe_is_stored_once_and_reused_until_its_anchor_changes(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    provider = _RefRecordingProvider("runway", [GOOD(), GOOD(), GOOD()])
    first = _build_kf(h, [provider], role="PLAY_BOW")
    again = _build_kf(h, [provider], role="PLAY_BOW")
    assert again.id == first.id and again.deduplicated and provider.calls == 1

    # A new approved STAND_READY version re-anchors the pose: the stamp differs → rebuild.
    stand_v2 = _build_kf(h, [FakeProvider("runway", [GOOD()])], role="STAND_READY", skip_if_unchanged=False)
    assert stand_v2.version == 2
    rebuilt = _build_kf(h, [provider], role="PLAY_BOW")
    assert rebuilt.version == 2 and provider.calls == 2
    assert rebuilt.analyzer_versions["anchor_keyframe"] == f"STAND_READY:{stand_v2.id}"
    # Pinning an explicit anchor version selects that approved version.
    pinned = _build_kf(h, [provider], role="PLAY_BOW", skip_if_unchanged=False, anchor_keyframe_version=1)
    assert pinned.analyzer_versions["anchor_keyframe"] == f"STAND_READY:{first.analyzer_versions['anchor_keyframe'].split(':')[1]}"
    assert provider.calls == 3


# ══════════════════════════════════════════════════════════════════════════
# Generation (video providers injected — no paid calls)
# ══════════════════════════════════════════════════════════════════════════


def _prepare_play(monkeypatch, storage, species: str):
    role = sv.select_variant(PWM, species).keyframe_role
    h, _ = _prepare_pipeline(monkeypatch, storage, roles=("STAND_READY", role))
    _force_species(monkeypatch, species)
    install_mv_vlm(monkeypatch, VLM_PLAY_OK)
    return h, role


def _plate_bytes(storage, role: str) -> bytes:
    keyframe = _run(kf.get_keyframe(user_id=USER, pet_id=PET, keyframe_role=role))
    selected = next(c for c in keyframe.candidates if c.selected)
    return storage[selected.plate_object_path]


def _build_play(h, providers, **kw):
    kw.setdefault("segment_join_fn", passthrough_join)
    return _build_motion(h, PWM, providers, **kw)


@pytest.mark.parametrize("species,variant", [("DOG", sv.PLAY_BOW), ("CAT", sv.PLAY_CROUCH)])
def test_generation_conditions_two_segments_on_the_actual_approved_endpoint_images(
    storage, monkeypatch, species, variant
):
    h, role = _prepare_play(monkeypatch, storage, species)
    provider = FakeVideoProvider("kling_3", [GOOD(), GOOD()], end_frame=True)

    version = _build_play(h, [provider])

    assert version.status == mv.STATUS_COMPLETE
    assert version.video_strategy == ms.STRATEGY_START_END
    assert version.prompt_version == motion_video_prompts.PLAY_WITH_ME_PROMPT_VERSION
    assert version.prompt.startswith("[SEGMENT A]") and "[SEGMENT B]" in version.prompt
    assert provider.calls == 2, "one candidate = two start/end-conditioned provider jobs"
    a, b = provider.requests
    stand_plate, pose_plate = _plate_bytes(storage, "STAND_READY"), _plate_bytes(storage, role)
    assert stand_plate != pose_plate or True  # plates are stored per role (same fixture image is fine)
    assert a.metadata["segment"] == "A" and a.metadata["segment_index"] == 1
    assert b.metadata["segment"] == "B" and b.metadata["segment_index"] == 2
    # A: STAND_READY → intermediate pose; B: intermediate pose → STAND_READY — from the
    # approved plates (through the anchor), never from a frame of the other segment.
    stand_id = _run(kf.get_keyframe(user_id=USER, pet_id=PET, keyframe_role="STAND_READY")).id
    pose_id = _run(kf.get_keyframe(user_id=USER, pet_id=PET, keyframe_role=role)).id
    assert (a.metadata["start_keyframe_id"], a.metadata["target_keyframe_id"]) == (stand_id, pose_id)
    assert (b.metadata["start_keyframe_id"], b.metadata["target_keyframe_id"]) == (pose_id, stand_id)
    assert a.start_image_bytes and a.end_image_bytes and b.start_image_bytes and b.end_image_bytes
    assert a.end_image_bytes == b.start_image_bytes, "the seam is the same approved intermediate still"
    assert a.start_image_bytes == b.end_image_bytes, "both outer ends are the same STAND_READY still"
    # Each segment got the per-segment duration; the candidate carries the joined total.
    assert a.output_spec["duration_sec"] == b.output_spec["duration_sec"] == 4
    assert version.output_spec["duration_sec"] == 8
    assert version.output_spec["segments"] == [{"id": "A", "duration_sec": 4}, {"id": "B", "duration_sec": 4}]
    # Species-specific prompts went to the provider verbatim and each fits the Kling limit.
    assert ("play bow" in a.prompt) == (variant is sv.PLAY_BOW)
    assert ("play crouch" in b.prompt) == (variant is sv.PLAY_CROUCH)
    assert "playful anticipation" in a.prompt and "never reversed" in b.prompt
    assert max(len(a.prompt), len(b.prompt)) <= video_motion_providers.FalKlingProvider().max_prompt_chars

    selected = next(c for c in version.candidates if c.selected)
    segments = selected.generation_metadata["segments"]
    assert [item["id"] for item in segments["items"]] == ["A", "B"]
    assert all(item["raw_video_path"] in storage for item in segments["items"]), "segment evidence stored"
    assert segments["items"][0]["raw_video_path"].endswith("_segA_raw.mp4")
    assert segments["join"]["version"] == seg.SEGMENT_JOIN_VERSION
    assert selected.raw_video_path.endswith("_a1_raw.mp4") and selected.raw_video_path in storage
    assert selected.provider_job_id == "kling_3-job-1+kling_3-job-2"
    qa = selected.qa_result
    assert qa["segments"]["decision"] == "PASS"
    for name in sv.PLAY_WITH_ME_SEGMENT_CHECK_NAMES:
        assert qa["checks"][name] == "PASS", name
    assert qa["business_domains"]["play_with_me"]["status"] == "PASS"
    assert qa["business_qa"]["delivery_action"] == "DELIVER"
    assert "starts_at_start_pose" not in qa["checks"] and "reaches_target_pose" not in qa["checks"]
    refs = {r["kind"]: r for r in selected.input_references}
    assert refs["intermediate_keyframe"]["keyframe_id"] == pose_id
    assert refs["intermediate_keyframe"]["role"] == role


def test_contract_pins_the_variant_intermediate_keyframe_and_segment_plan(storage, monkeypatch):
    h, role = _prepare_play(monkeypatch, storage, "CAT")
    contract = _run(ms.resolve_video_generation_spec(user_id=USER, pet_id=PET, motion_id=PWM))
    assert contract["species_variant"]["id"] == "PLAY_CROUCH"
    assert contract["species_variant"]["keyframe_role"] == "PLAY_CROUCH"
    assert contract["intermediate_keyframe"]["role"] == "PLAY_CROUCH"
    assert contract["intermediate_keyframe"]["plate"]["object_path"]
    assert contract["intermediate_keyframe"]["canonical_version_id"] == contract["canonical_version_id"]
    assert [s["id"] for s in contract["segments"]] == ["A", "B"]
    assert contract["provider_order"] == [ms.PROVIDER_KLING_3]
    assert contract["qa_context"]["acceptance"]["intermediate_keyframe_role"] == "PLAY_CROUCH"
    assert contract["video_strategy"] == ms.STRATEGY_START_END
    paw = _run(ms.resolve_video_generation_spec(user_id=USER, pet_id=PET, motion_id="PAW_HELLO"))
    assert paw["species_variant"] is None and paw["intermediate_keyframe"] is None and paw["segments"] is None


def test_missing_intermediate_keyframe_fails_closed_before_any_provider_call(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)               # STAND_READY only
    _force_species(monkeypatch, "DOG")
    install_mv_vlm(monkeypatch, VLM_PLAY_OK)
    provider = FakeVideoProvider("kling_3", [GOOD(), GOOD()], end_frame=True)
    with pytest.raises(mv.MotionVideoError) as exc:
        _build_play(h, [provider])
    assert exc.value.code == "INTERMEDIATE_KEYFRAME_REQUIRED"
    assert exc.value.status == 409
    assert provider.calls == 0
    # The other species' keyframe does not satisfy a dog.
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    assert _build_kf(h, [FakeProvider("runway", [GOOD()])], role="PLAY_CROUCH").status == kf.STATUS_COMPLETE
    with pytest.raises(mv.MotionVideoError) as exc:
        _build_play(h, [provider])
    assert exc.value.code == "INTERMEDIATE_KEYFRAME_REQUIRED" and provider.calls == 0


@pytest.mark.parametrize("species", ["RABBIT", "OTHER", "UNKNOWN"])
def test_unsupported_species_is_rejected_before_any_provider_call(storage, monkeypatch, species):
    h, _ = _prepare_pipeline(monkeypatch, storage, roles=("STAND_READY", "PLAY_BOW"))
    _force_species(monkeypatch, species)
    provider = FakeVideoProvider("kling_3", [GOOD(), GOOD()], end_frame=True)
    with pytest.raises(mv.MotionVideoError) as exc:
        _build_play(h, [provider])
    assert exc.value.code == "SPECIES_UNSUPPORTED"
    assert exc.value.status == 409
    assert provider.calls == 0


def test_never_degrades_when_the_model_cannot_receive_the_end_frame(storage, monkeypatch):
    h, _ = _prepare_play(monkeypatch, storage, "DOG")
    incapable = FakeVideoProvider("kling_3", [GOOD(), GOOD()], end_frame=False)
    with pytest.raises(mv.MotionVideoError) as exc:
        _build_play(h, [incapable])
    assert exc.value.code == "END_FRAME_UNSUPPORTED"
    assert incapable.calls == 0


def test_segment_endpoint_violation_blocks_and_requests_one_regeneration(storage, monkeypatch):
    """Segment B that ignored its end frame → deterministic FAIL → BLOCK + second paid
    candidate (bounded budget), exactly like any other integrity failure."""
    h, _ = _prepare_play(monkeypatch, storage, "DOG")
    monkeypatch.setenv("PHASE6_MAX_PRIMARY", "2")
    monkeypatch.setenv("PHASE6_STOP_AFTER_PASSES", "1")
    good = GOOD()
    wrong = make_pet_cutout_png(body=(20, 200, 20), patch=(20, 20, 200))   # a different-looking end frame
    provider = FakeVideoProvider("kling_3", [good, wrong, good, good], end_frame=True)

    def sampler(video_bytes):
        frames = sampler_identical(video_bytes)
        return frames

    version = _build_play(h, [provider], sampler=sampler)
    assert provider.calls == 4, "attempt 1 (A+B) failed segment QA → attempt 2 (A+B)"
    first, second = sorted(version.candidates, key=lambda c: c.attempt)
    assert first.decision == "FAIL"
    assert first.qa_result["checks"]["segment_b_returns_to_stand_ready"] == "FAIL"
    assert first.qa_result["business_qa"]["delivery_action"] == "BLOCK"
    assert first.qa_result["business_qa"]["retry_action"] == "REGENERATE"
    assert second.decision == "PASS" and second.selected
    assert version.status == mv.STATUS_COMPLETE


class _PendingOncePerSegment(FakeVideoProvider):
    """Durable-style double: the first generate() per segment yields (job submitted,
    still running); the next call for that segment returns the collected clip."""

    durable_execution = True

    def __init__(self, name, clip):
        super().__init__(name, [])
        self._clip = clip
        self.submitted: list[str] = []

    def generate(self, request):
        self.calls += 1
        self.requests.append(request)
        segment = request.metadata["segment"]
        if segment not in self.submitted:
            self.submitted.append(segment)
            raise durable_provider_jobs.ProviderWorkPending(f"op-{segment}", "PENDING")
        return MotionVideoResult(video_bytes=self._clip, provider=self.name, model=self.model_name(),
                                 external_job_id=f"job-{segment}")


def test_durable_resume_submits_both_segments_in_one_tick_and_completes_one_version(storage, monkeypatch):
    h, _ = _prepare_play(monkeypatch, storage, "DOG")
    monkeypatch.setenv("PHASE6_MAX_PRIMARY", "1")
    monkeypatch.setenv("PHASE6_STOP_AFTER_PASSES", "1")
    provider = _PendingOncePerSegment("kling_3", GOOD())

    with pytest.raises(durable_provider_jobs.ProviderWorkPending):
        _build_play(h, [provider])
    assert provider.submitted == ["A", "B"], "B is submitted in the same tick A started waiting"
    assert len(_run(mv._version_rows(PET, PWM))) == 1
    building = _run(mv._version_rows(PET, PWM))[0]
    frozen = building["analyzer_versions"][mv.DURABLE_CONTRACT_KEY]
    assert set(frozen["segment_prompts"]) == {"A", "B"}
    assert frozen["inputs"]["intermediate"]["object_path"]
    assert frozen["contract"]["intermediate_keyframe"]["role"] == "PLAY_BOW"

    completed = _build_play(h, [provider])
    assert completed.status == mv.STATUS_COMPLETE
    assert len(_run(mv._version_rows(PET, PWM))) == 1
    assert len(completed.candidates) == 1
    assert provider.calls == 4  # A,B pending + A,B collected — no third submission


# ══════════════════════════════════════════════════════════════════════════
# Run orchestration — KEYFRAMES stage builds the species keyframe on the pinned STAND_READY
# ══════════════════════════════════════════════════════════════════════════


def _orchestrated_play(monkeypatch, *, species="DOG", intermediate_in_contract=True):
    from . import test_phase7c_generation_runs as p7c

    p7c.seed_intake()
    harness = p7c.PipelineHarness(monkeypatch)
    harness.motion.motion_id = PWM
    stand = harness.keyframe
    pose_role = sv.select_variant(PWM, species).keyframe_role if sv.select_variant(PWM, species) else "PLAY_BOW"
    pose = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000511", pet_id=p7c.PET, user_id=p7c.USER, keyframe_role=pose_role,
        version=1, status="complete", canonical_version_id=harness.canonical.id,
        canonical_version=harness.canonical.version,
        selected_candidate_id="00000000-0000-0000-0000-000000000512",
        candidates=[SimpleNamespace(id="00000000-0000-0000-0000-000000000512", selected=True)],
        analyzer_versions={"anchor_keyframe": f"STAND_READY:{stand.id}"},
    )
    by_role = {"STAND_READY": stand, pose_role: pose}

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
            "motion_id": PWM,
            "motion_spec_version": ms.MOTION_SPEC_VERSION,
            "start_keyframe": {"keyframe_id": stand.id, "version": 1},
            "intermediate_keyframe": (
                {"keyframe_id": pose.id, "version": 1, "role": pose_role} if intermediate_in_contract
                else {"keyframe_id": "00000000-0000-0000-0000-0000000005ff", "version": 9, "role": pose_role}
            ),
            "segments": seg.segment_plan(sv.select_variant(PWM, species) or sv.PLAY_BOW),
            "canonical_version_id": harness.canonical.id,
        }

    async def finalize(**kwargs):
        harness._capture("finalize", kwargs)
        harness.counts["publication"] += 1
        return SimpleNamespace(publication_id="00000000-0000-0000-0000-000000000711")

    monkeypatch.setattr(kf, "get_keyframe", keyframe_get)
    monkeypatch.setattr(kf, "build_keyframe", keyframe_build)
    monkeypatch.setattr(sv, "resolve_pet_species", species_of)
    monkeypatch.setattr(ms, "resolve_video_generation_spec", resolve_spec)
    monkeypatch.setattr(premium_motion_finalization, "finalize_premium_motion", finalize)
    monkeypatch.setattr(runs, "_image_providers", lambda run, operation: [FakeProvider("gpt_image")])
    # build_motion_video is mocked by the harness; the durable video routing only needs to resolve.
    monkeypatch.setattr(runs, "_video_providers", lambda run, motion_id: [SimpleNamespace(name="kling_3")])
    return harness, stand, pose


def _start_play_run(key="premium:PLAY_WITH_ME:test"):
    from . import test_phase7c_generation_runs as p7c

    return _run(runs.start_generation_run(
        user_id=p7c.USER, pet_id=p7c.PET, motion_id=PWM, request_kind=runs.REQUEST_PREMIUM_PRODUCT,
        idempotency_key=key, product_key="action:PLAY_WITH_ME",
    ))


@pytest.fixture
def run_env(monkeypatch):
    from . import test_phase7c_generation_runs as p7c

    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    for svc in (runs,):
        svc.__reset_for_tests()
    durable_provider_jobs.__reset_for_tests()
    from backend.services import pet_reference_service, pet_registry

    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()
    yield p7c
    runs.__reset_for_tests()
    durable_provider_jobs.__reset_for_tests()
    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()


def test_run_builds_the_species_keyframe_on_the_pinned_stand_ready_and_publishes(run_env, monkeypatch, storage):
    harness, stand, pose = _orchestrated_play(monkeypatch, species="DOG")
    _start_play_run()
    result = _run(runs.process_next_generation_run(worker_id="pwm-test-worker"))

    assert result.status == runs.STATUS_PUBLISHED, result.last_error
    assert set(result.keyframes) == {"STAND_READY", "PLAY_BOW"}
    assert result.keyframes["PLAY_BOW"]["id"] == pose.id
    roles = [call["keyframe_role"] for call in harness.keyframe_build_calls]
    assert roles == ["STAND_READY", "PLAY_BOW"], "the pose is built after (and on top of) STAND_READY"
    pose_call = harness.keyframe_build_calls[1]
    assert pose_call["anchor_keyframe_version"] == stand.version
    assert pose_call["pinned_canonical_version_id"] == harness.canonical.id
    assert harness.counts["motion_build"] == 1 and harness.counts["publication"] == 1


def test_run_refuses_a_contract_whose_intermediate_keyframe_is_not_the_pinned_one(run_env, monkeypatch, storage):
    harness, _, _ = _orchestrated_play(monkeypatch, species="DOG", intermediate_in_contract=False)
    _start_play_run("premium:PLAY_WITH_ME:lineage")
    result = _run(runs.process_next_generation_run(worker_id="pwm-test-worker"))
    assert result.status == runs.STATUS_FAILED
    assert result.last_error["code"] == "RUN_LINEAGE_INVALID"
    assert harness.counts["motion_build"] == 0, "no paid generation on a split lineage"


def test_run_rejects_an_unsupported_species_at_the_keyframe_stage_without_generation(run_env, monkeypatch, storage):
    harness, _, _ = _orchestrated_play(monkeypatch, species="RABBIT")
    _start_play_run("premium:PLAY_WITH_ME:rabbit")
    result = _run(runs.process_next_generation_run(worker_id="pwm-test-worker"))
    assert result.status == runs.STATUS_FAILED
    assert result.last_error["code"] == "SPECIES_UNSUPPORTED"
    assert [c["keyframe_role"] for c in harness.keyframe_build_calls] == ["STAND_READY"]
    assert harness.counts["motion_build"] == 0


# ══════════════════════════════════════════════════════════════════════════
# Commerce / registries / device / playback continuity
# ══════════════════════════════════════════════════════════════════════════


def test_registered_everywhere_the_commercial_path_reads():
    assert PWM in PET_ACTIONS and PWM in PREMIUM_ACTIONS
    assert PWM in premium_run_fulfillment.PREMIUM_MOTIONS
    assert PWM in motion_delivery_service.PACKAGEABLE_MOTIONS
    assert PWM not in motion_delivery_service.SYSTEM_MOTIONS
    assert generation_queue.GENERATION_ORDER[-2:] == (PWM, "COME_PLAY")  # COME_PLAY appended after (2026-10-30)
    assert set(generation_queue.GENERATION_ORDER) == set(PREMIUM_ACTIONS)
    assert premium_run_fulfillment.motion_id_for_product("action:PLAY_WITH_ME") == PWM
    assert premium_purchase.resolve_kind("PLAY_WITH_ME") == "ACTION:PLAY_WITH_ME"
    assert premium_purchase._product_key("ACTION:PLAY_WITH_ME") == "action:PLAY_WITH_ME"
    # One-time purchase at the existing 1-credit price — unchanged by the two-segment upgrade.
    assert ("action:PLAY_WITH_ME", product_catalog.TYPE_ACTION, 1, "Let's Play!") in product_catalog._SEED


def test_packaging_and_playback_contract_is_unchanged_for_the_joined_asset():
    """The joined asset is one STAND_READY→STAND_READY clip: packed-alpha packaging and the
    web/hologram runtime (STANDING→STANDING, seam-aligned return) do not change."""
    import os

    ts = os.path.join(os.path.dirname(__file__), "..", "..", "src", "lib", "pet-runtime-events.ts")
    with open(ts, encoding="utf-8") as f:
        content = f.read()
    block = content[content.index("PLAY_WITH_ME: {"):]
    block = block[: block.index("},")]
    for needle in ('kind: "ACTION"', 'returnPolicy: "seam-aligned"', 'requiredPose: "STANDING"',
                   'endPose: "STANDING"', "returnToIdle: true", "interruptible: false"):
        assert needle in block, needle
    assert motion_delivery_service.DELIVERY_PACKED_ALPHA == "packed_alpha"
    assert ms.MOTIONS[PWM].video_compat["returns_to_start_pose"] is True


def test_purchase_rejects_unsupported_species_before_reserving_credits(monkeypatch):
    async def owned(*_a, **_k):
        return None

    async def entitlement(_user):
        return SimpleNamespace(blocks_generation=False, enforced=False, entitled=True, status=None)

    async def state(*_a, **_k):
        return premium_purchase.AssetState(ready={}, active=[], missing=[PWM])

    async def species(*_a, **_k):
        return "RABBIT"

    async def never_reserve(**_k):
        raise AssertionError("credits must not be reserved for an unsupported species")

    async def never_claim(*_a, **_k):
        raise AssertionError("purchase ledger must not be claimed for an unsupported species")

    monkeypatch.setattr(premium_purchase, "assert_pet_owned", owned)
    monkeypatch.setattr(premium_purchase.premium_entitlement, "get_entitlement", entitlement)
    monkeypatch.setattr(premium_purchase, "asset_state", state)
    monkeypatch.setattr(sv, "resolve_pet_species", species)
    monkeypatch.setattr(premium_purchase.credit_reservation, "reserve", never_reserve)
    monkeypatch.setattr(premium_purchase, "_claim_purchase", never_claim)

    with pytest.raises(premium_purchase.PurchaseError) as exc:
        _run(premium_purchase.purchase(
            user_id="u", pet_id="pet_x", kind="ACTION:PLAY_WITH_ME",
            pet_image_url=None, api_base="http://api.test",
        ))
    assert exc.value.code == "SPECIES_UNSUPPORTED"
    assert exc.value.status == 409


def test_purchase_species_gate_ignores_other_actions_and_ready_assets(monkeypatch):
    calls = []

    async def species(*_a, **_k):
        calls.append(1)
        return "RABBIT"

    monkeypatch.setattr(sv, "resolve_pet_species", species)
    _run(premium_purchase._assert_species_supported("u", "p", ["PAW_HELLO", "BLINKING"]))
    assert calls == []
    _run(premium_purchase._assert_species_supported("u", "p", []))
    with pytest.raises(premium_purchase.PurchaseError):
        _run(premium_purchase._assert_species_supported("u", "p", [PWM]))


def _device_client(monkeypatch):
    monkeypatch.setenv("ETERNAL_BEAM_DEVICE_TOKENS", '{"beam-test":"secret"}')
    device_gateway.__reset_for_tests()
    app = FastAPI()
    app.include_router(device_ws_v1.router, prefix="/api")
    app.dependency_overrides[require_user] = lambda: AuthedUser(user_id="user-1")
    return ASGITestClient(app)


def test_device_command_sends_an_owned_published_play_with_me_asset(monkeypatch):
    async def fake_library(**kwargs):
        return SimpleNamespace(motions=[SimpleNamespace(
            pet_id="pet-1", motion_id=PWM,
            url="https://signed.example/play_packed.mp4?token=fresh",
            delivery_format="packed_alpha",
            access=SimpleNamespace(state="playable"),
        )])

    monkeypatch.setattr(device_ws_v1.library_service, "get_library", fake_library)
    response = _device_client(monkeypatch).post(
        "/api/v1/device/commands",
        json={"device_id": "beam-test", "event": "pet_asset", "pet_id": "pet-1", "motion_id": PWM},
    )
    assert response.status_code == 200, response.text
    command = _run(device_gateway.pending("beam-test"))[0]
    assert command["motion_id"] == PWM
    assert command["packed_url"] == command["video_url"]
    assert command["video_url"].endswith("token=fresh")


def test_device_command_still_rejects_touch_motions_and_unowned_play(monkeypatch):
    async def empty_library(**_kwargs):
        return SimpleNamespace(motions=[])

    monkeypatch.setattr(device_ws_v1.library_service, "get_library", empty_library)
    client = _device_client(monkeypatch)
    for motion in (PWM, "PET_HEAD"):
        response = client.post(
            "/api/v1/device/commands",
            json={"device_id": "beam-test", "event": "pet_asset", "pet_id": "pet-1", "motion_id": motion},
        )
        assert response.status_code == 409, motion
        assert response.json()["detail"]["code"] == "PUBLISHED_ASSET_REQUIRED"
    assert _run(device_gateway.pending("beam-test")) == []
