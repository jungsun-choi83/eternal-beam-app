"""DOG PLAY_BOW keyframe refinement (2026-10-30): natural, morphology-aware hindquarters.

Customer feedback: the play bow looked natural overall but the hindquarters were
excessively elevated, and the right posture varies between individual dogs. Both
PLAY_BOW keyframe prompts (full and compact) now keep the rear at about normal
standing height, never lifted or arched, and let the bow depth follow the dog's own
anatomy. The DOG PLAY_WITH_ME segment prompts and the VLM acceptance contract were
aligned so nothing upstream or downstream asks for a raised rear. CAT PLAY_CROUCH,
STAND_READY, the other roles and the global keyframe versions are untouched.
"""

from __future__ import annotations

import copy

import pytest

from backend.services import (
    action_keyframe_spec,
    canonical_image_providers,
    motion_spec as ms,
    motion_video_prompts,
    species_motion_variants as sv,
    vlm_identity,
)

from .test_canonical_prompt_v3 import RICH_VISUAL_IDENTITY
from .test_play_with_me import PWM, _contract, judge_play, play_evidence

RAISED_WORDS = ("hindquarters raised", "hindquarters stay raised", "rear raised", "hips raised",
                "hips up", "rear up high", "arch the", "arched spine", "lift the hips",
                "hindquarters clearly raised", "stay raised")
IDENTITY_WORDS = ("face", "coat colors", "markings", "ear", "tail", "body proportions")


def _rich(coat=("brown", "white"), ears="floppy"):
    vi = copy.deepcopy(RICH_VISUAL_IDENTITY)
    vi["coat"]["dominant_colors"] = [{"name": c, "fraction": 0.3} for c in coat]
    vi["semantic_traits"]["traits"]["ears"]["shape"] = ears
    return vi


def _role():
    return action_keyframe_spec.KEYFRAME_ROLES["PLAY_BOW"]


def _prompts(tone="light-gray", vi=None):
    vi = vi if vi is not None else {}
    return (
        action_keyframe_spec.build_keyframe_prompt(_role(), vi, background_tone=tone),
        action_keyframe_spec.build_compact_keyframe_prompt(_role(), vi, background_tone=tone),
    )


# ── 1–2. shoulders/chest lower, hindquarters at standing height, no lift/arch ──


@pytest.mark.parametrize("which", ["full", "compact"])
def test_play_bow_keyframe_prompt_lowers_the_front_and_keeps_the_rear_at_standing_height(which):
    full, compact = _prompts()
    text = full if which == "full" else compact
    low = text.lower()
    assert "play bow" in low
    assert "shoulders and chest lower" in low
    assert "front paws" in low and ("slightly forward" in low or "a bit forward" in low)
    assert "standing height" in low and "supported hind legs" in low
    assert "not lifted" in low and "arched" in low
    for phrase in RAISED_WORDS:
        assert phrase not in low, (which, phrase)
    # still a bow, not a stretch / dip / sit / lie
    assert "dip" in low and "sit" in low and "sprawl" in low
    if which == "full":
        assert "front legs reach into a comfortable bow" in low
        assert "hips not lifted above standing height, spine not arched" in low
        assert "not an exaggerated stretch" in low


# ── 3. morphology-aware depth ──


def test_play_bow_keyframe_prompts_adapt_bow_depth_to_the_individual_dog():
    full, compact = _prompts()
    low = full.lower()
    assert "bow depth follows this dog's own anatomy, leg length, body proportions and natural flexibility" in low
    assert "a shallower bow is correct when the build allows no more" in low
    assert "front body is clearly lower than the rear" in low
    assert "depth suited to this dog" in compact.lower()
    # no breed logic — adaptation is by measured anatomy, never by breed name
    for word in ("breed", "retriever", "dachshund", "bulldog", "poodle"):
        assert word not in low and word not in compact.lower()


# ── 4–5. identity, weight distribution, grounding ──


def test_play_bow_keyframe_prompts_preserve_identity_weight_and_ground_contact():
    full, compact = _prompts(vi=_rich())
    low = full.lower()
    assert "exact face, muzzle, eyes, coat colors, markings, fur texture, body structure and proportions, tail shape" in low
    assert "ears keep this pet's own shape, size, fold and anatomy" in low
    assert "realistic weight distribution and natural ground contact" in low
    assert "all four paws rest on the ground" in low
    assert "the following must be clearly visible: face, full body, ears, front paws" in low
    clow = compact.lower()
    assert "preserve face, coat colors, markings, ear shape, body proportions, paws and tail" in clow
    assert "all four paws grounded" in clow and "ears keep own shape/fold" in clow
    # identity trait lines are appended to both prompts for a rich identity
    assert "Ear shape: floppy." in full and "Coat colors:" in compact


# ── Assembled lengths: Runway compact ≤ 1000 with every trait line intact; GPT full bounded ──


@pytest.mark.parametrize("vi,tone", [
    (_rich(), "light-gray"),
    (_rich(coat=("golden red", "cream", "white"), ears="semi_pricked"), "warm-off-white"),
    (_rich(coat=("black", "tan", "white"), ears="floppy"), "light-gray"),
])
def test_assembled_play_bow_prompts_fit_provider_limits_without_dropping_identity(vi, tone):
    runway = canonical_image_providers.RunwayImageProvider()
    full = action_keyframe_spec.build_keyframe_prompt(_role(), vi, background_tone=tone)
    compact = action_keyframe_spec.build_compact_keyframe_prompt(
        _role(), vi, max_chars=runway.max_prompt_chars, background_tone=tone
    )
    untruncated = action_keyframe_spec.build_compact_keyframe_prompt(
        _role(), vi, max_chars=10**6, background_tone=tone
    )
    assert len(compact) <= runway.max_prompt_chars, len(compact)
    assert compact == untruncated, "a trait line was dropped to fit the compact cap"
    assert "Clearly visible: face, full body, ears, front paws." in compact
    assert "Coat colors:" in compact and "The pet is a dog." in compact and "ears." in compact
    assert len(full) < 3000, len(full)
    assert len(_role().compact_pose) <= 250  # the compact pose budget (shared base ≈ 660 + traits ≈ 130)


# ── Keyframe pose QA reads the same required_pose text ──


def test_keyframe_pose_qa_receives_the_revised_required_pose():
    snapshot = action_keyframe_spec.role_spec_snapshot(_role())
    assert snapshot["required_pose"] == _role().required_pose
    assert "standing height" in snapshot["required_pose"] and "raised" not in snapshot["required_pose"]
    assert snapshot["anchor_keyframe_role"] == "STAND_READY"
    assert snapshot["anchor_framing_clause"].startswith("Keep the same camera")


# ── Scope: CAT PLAY_CROUCH, STAND_READY, other roles, global versions unchanged ──


def test_scope_cat_stand_ready_other_roles_and_global_versions_are_untouched():
    crouch = action_keyframe_spec.KEYFRAME_ROLES["PLAY_CROUCH"]
    assert "hindquarters are gathered low and compact" in crouch.required_pose
    assert "ears forward" in crouch.required_pose and "standing height" not in crouch.required_pose
    assert "standing height" not in (crouch.compact_pose or "")
    stand = action_keyframe_spec.KEYFRAME_ROLES["STAND_READY"]
    assert "play bow" not in stand.required_pose.lower() and stand.anchor_keyframe_role is None
    for r in action_keyframe_spec.KEYFRAME_ROLE_ORDER:
        if r in ("PLAY_BOW", "PLAY_BOW_NEAR"):  # PLAY_BOW_NEAR inherits the same rear rule (COME_PLAY)
            continue
        assert "standing height" not in action_keyframe_spec.KEYFRAME_ROLES[r].required_pose
    assert action_keyframe_spec.KEYFRAME_SPEC_VERSION == "keyframe-spec-v4"
    assert action_keyframe_spec.KEYFRAME_PROMPT_VERSION == "keyframe-prompt-v2"
    assert action_keyframe_spec.KEYFRAME_COMPACT_PROMPT_VERSION == "keyframe-prompt-compact-v2"
    assert ms.MOTION_SPEC_VERSION == "motion-spec-v17"
    # CAT motion text carries no hindquarter-height language either
    cat = motion_video_prompts.build_play_with_me_segment_prompts(_contract(sv.PLAY_CROUCH))
    assert all("standing height" not in t for t in cat.values())
    assert "standing height" not in sv.PLAY_CROUCH.vlm_guidance


# ── DOG PLAY_WITH_ME segments and VLM criteria aligned with the revised pose ──


def test_dog_segment_prompts_no_longer_ask_for_raised_hindquarters():
    dog = motion_video_prompts.build_play_with_me_segment_prompts(_contract(sv.PLAY_BOW))
    a, b = dog["A"].lower(), dog["B"].lower()
    for phrase in RAISED_WORDS:
        assert phrase not in a and phrase not in b, phrase
    assert "the hindquarters near standing height on supported hind legs" in a
    assert "the bow as deep as this dog's build allows" in a
    assert "the rear stays near standing height, not lifted or arched, and never sinks into a sit or lie-down" in a
    # genuine motion-correctness constraints are intact
    assert "the whole front body lowers, not just the head" in a
    assert "shoulders and chest lower" in a and "front paws reposition slightly forward" in a
    assert "during the rise the rear never sinks into a sit or lie-down" in b
    assert "hindquarters relax to a normal stance" in b
    for v in (sv.PLAY_BOW.description, sv.PLAY_BOW.into_pose, sv.PLAY_BOW.hold, sv.PLAY_BOW.return_to_stand):
        assert "raised" not in v.lower()


def test_dog_vlm_criteria_judge_rear_as_up_and_supported_not_lifted_or_arched():
    g = sv.PLAY_BOW.vlm_guidance
    assert "the hindquarters stay up at about their normal standing height on standing hind legs rather than sinking, sitting or lying (rear_posture_correct)" in g
    assert "hips lifted higher than standing or an arched spine are NOT required and NOT a reason to answer no" in g
    assert "accept a shallower bow from a short-legged or stocky dog as long as the front body is clearly lower than the rear" in g
    assert "hindquarters stay raised" not in g
    acc = sv.PLAY_BOW.acceptance
    assert "hindquarters at about normal standing height on supported hind legs (not lifted higher or arched)" in acc["expected_intermediate_state"]
    assert "bow depth natural to this dog's build" in acc["expected_intermediate_state"]
    assert "stay near their normal standing height (not lifted or arched)" in acc["description"]
    assert any("a shallower or deeper bow according to this dog's own leg length" in c for c in acc["allowed_secondary_motion"])
    # the sit / lie-down block is kept (genuine check), the exaggerated bow is advisory only
    assert any(c.startswith("The hindquarters clearly sink to a sit or lie-down") for c in acc["blocking_criteria"])
    assert not any("lifted higher" in c for c in acc["blocking_criteria"])
    assert any("lifted higher than standing or the spine arched" in c for c in acc["advisory_criteria"])
    for c in acc["blocking_criteria"]:
        assert "raised" not in c.lower()
    # the guidance reaches the dog contract's VLM prompt; the cat's is untouched
    dog_acc = ms.acceptance_for_variant(ms.MOTIONS[PWM], sv.PLAY_BOW)
    assert "standing height" in vlm_identity._play_with_me_guidance(dog_acc)
    assert "standing height" not in vlm_identity._play_with_me_guidance(
        ms.acceptance_for_variant(ms.MOTIONS[PWM], sv.PLAY_CROUCH)
    )


def test_motion_correctness_checks_are_not_weakened():
    # The field set and required checks are unchanged; sit/lie-down and anatomy still fail hard.
    assert len(sv.PLAY_WITH_ME_QA_FIELD_NAMES) == 14 and len(sv.PLAY_WITH_ME_REQUIRED_CHECK_NAMES) == 19
    assert judge_play(play_evidence())["decision"] == "PASS"
    for defect in ({"rear_posture_correct": "no"}, {"posture_not_lying_or_sitting": "no"},
                   {"front_body_lowered": "no"}, {"joint_anatomy_plausible": "no"}):
        assert judge_play(play_evidence(**defect))["decision"] == "FAIL", defect


def test_versions_moved_only_for_the_dog_motion_text_and_evaluator():
    assert motion_video_prompts.PLAY_WITH_ME_PROMPT_VERSION == "motion-video-prompt-v3-play-with-me-v7"
    assert vlm_identity.VLM_PLAY_WITH_ME_QA_VERSION == "vlm-motion-qa-v3-play-with-me-v5"
    assert sv.PLAY_WITH_ME_POLICY_REVISION == "play-with-me-v3"
