"""PLAY_WITH_ME DOG timing refinement (2026-10-30): playful bounce before the bow.

Live result: the play bow itself was good, but segment A went almost straight from
STAND_READY into the bow and the pose was held too long. Segment A now carries one
short, lively, in-place bounce (all four paws landing back on the same spot) before
the dog lowers into the approved PLAY_BOW keyframe; segment B holds the bow briefly
and rises naturally to STAND_READY. These tests pin:

  * the DOG segment text (bounce → land → lower; brief hold → natural rise)
  * no travel / run / approach language anywhere in the DOG prompts
  * the CAT (PLAY_CROUCH) text, acceptance and guidance untouched
  * QA: the single in-place bounce is allowed and is not locomotion, while a bounce
    that travels, a run, or repeated hopping stays blocked; no new required check
  * fal Kling v3 limits: prompt ≤ 2,500 chars with headroom, 4 s segments in 3..15 s
  * version stamps moved only where behaviour changed (prompt, VLM evaluator)
"""

from __future__ import annotations

import re

import pytest

from backend.services import (
    motion_spec as ms,
    motion_video_prompts,
    motion_video_segments as seg,
    motion_video_service as mv,
    species_motion_variants as sv,
    video_motion_providers,
    vlm_identity,
)

from .test_play_with_me import PWM, _contract, judge_play, play_evidence

TRAVEL_WORDS = ("run toward", "runs toward", "lunge toward", "approach the camera",
                "closer to the camera", "forward travel", "move forward", "moves forward",
                "walk toward", "walks toward")


def _dog():
    return motion_video_prompts.build_play_with_me_segment_prompts(_contract(sv.PLAY_BOW))


def _cat():
    return motion_video_prompts.build_play_with_me_segment_prompts(_contract(sv.PLAY_CROUCH))


# ── Segment A: anticipation → one in-place bounce → land → lower into the bow ──


def test_dog_segment_a_bounces_once_in_place_lands_then_lowers_into_the_bow():
    a = _dog()["A"]
    low = a.lower()
    # one short, lively bounce — described as a small in-place hop that lands on the same spot
    assert "one short, lively bounce in place" in low
    assert "small hop landing all four paws on the same spot" in low
    assert "one small vertical hop in place" in low
    # the bounce precedes the lowering and the dog lands first
    bounce_at = low.index("bounce in place")
    land_at = low.index("once landed")
    lower_at = low.index("shoulders and chest lower")
    bow_frame_at = low.index("supplied final play bow frame")
    assert bounce_at < land_at < lower_at < bow_frame_at
    assert "land, then lower" in low
    # the approved keyframes stay the two endpoints
    assert "first supplied frame is the pet's approved stand_ready pose" in low
    assert "last supplied frame is the same pet's approved play bow pose" in low
    assert "settles exactly into the supplied final play-bow frame" in low
    # still a single bounce, never a sequence
    assert "bounces" not in low and "hops" not in low and "repeated" not in low


def test_dog_prompts_never_describe_travel_or_a_run_toward_the_viewer():
    for segment_id, text in _dog().items():
        low = text.lower()
        for phrase in TRAVEL_WORDS:
            assert phrase not in low, (segment_id, phrase)
        # the fixed camera / home spot contract is intact and the hop is explicitly in place
        assert "camera and home spot stay fixed" in low
        assert "never travels, runs, walks or steps toward or away from the camera" in low
        assert "fixed camera, crop, framing, perspective and focus" in low
    a = _dog()["A"].lower()
    assert "never a run, lunge or step toward the camera" in a


# ── Segment B: brief hold → natural rise to STAND_READY ──


def test_dog_segment_b_holds_the_bow_briefly_then_rises_naturally():
    b = _dog()["B"]
    low = b.lower()
    assert "begin exactly in the supplied first frame, already alive: a brief beat in the inviting play bow" in low
    assert "held playfully low and ready" in low
    assert "keep this beat brief" in low and "do not repeat the settle" in low
    assert "the dog rises naturally" in low
    hold_at = low.index("a brief beat in the inviting play bow")
    rise_at = low.index("the dog rises naturally")
    end_at = low.index("supplied final stand_ready frame")
    assert hold_at < rise_at < end_at
    assert "played forward, never reversed or rewound" in low
    # no bounce in the rise — the bounce belongs to the lead-in only
    assert "bounce" not in low and "hop" not in low
    assert "during the rise the rear never sinks into a sit or lie-down" in low


# ── Identity / anatomy / grounding / breathing / camera preserved in both segments ──


def test_dog_segments_keep_identity_anatomy_grounding_breathing_and_fixed_camera():
    for text in _dog().values():
        assert text.startswith("Same pet throughout")
        low = text.lower()
        assert "exactly four limbs" in low and "never duplicate, merge, detach or invent a limb" in low
        assert "paws reposition only as much as realistic grounding and balance need" in low
        assert "they never skate or float" in low
        assert "gentle breathing" in low
        assert "ears not forced forward, tail not forced up" in low
        assert "no human, hand, arm, person, leash, toy, platform" in low
        assert "silent clip" in low
    # the variant still binds to the same approved keyframe roles
    assert sv.PLAY_BOW.keyframe_role == "PLAY_BOW"
    assert ms.MOTIONS[PWM].start_keyframe_role == ms.MOTIONS[PWM].target_keyframe_role == "STAND_READY"


# ── Scope: CAT untouched ──


def test_cat_prompts_acceptance_and_guidance_carry_no_bounce():
    cat = _cat()
    for text in cat.values():
        low = text.lower()
        assert "bounce" not in low and "hop" not in low and "once landed" not in low
    acc = sv.PLAY_CROUCH.acceptance
    blob = " ".join(
        [acc["description"], *acc["blocking_criteria"], *acc["advisory_criteria"],
         *acc["allowed_secondary_motion"], sv.PLAY_CROUCH.vlm_guidance,
         sv.PLAY_CROUCH.lead_in, sv.PLAY_CROUCH.into_pose, sv.PLAY_CROUCH.hold,
         sv.PLAY_CROUCH.return_to_stand, sv.PLAY_CROUCH.description]
    ).lower()
    assert "bounce" not in blob and " hop" not in blob
    # the cat lists are exactly the shared lists — nothing dog-specific leaked in
    assert acc["allowed_secondary_motion"] == list(sv._SHARED_ALLOWED_SECONDARY)
    assert acc["advisory_criteria"] == list(sv._SHARED_ADVISORY)
    assert acc["blocking_criteria"][2:] == list(sv._SHARED_BLOCKING_TAIL)
    # the shared constants themselves did not grow a dog clause
    assert all("bounce" not in c.lower() for c in sv._SHARED_BLOCKING_TAIL)
    assert all("bounce" not in c.lower() for c in sv._SHARED_ALLOWED_SECONDARY)


# ── QA contract: the single in-place bounce is allowed, locomotion is not ──


def test_dog_acceptance_allows_one_in_place_bounce_but_blocks_travel_and_repeated_hopping():
    acc = sv.PLAY_BOW.acceptance
    allowed = " ".join(acc["allowed_secondary_motion"]).lower()
    assert "one short, lively in-place bounce during the lead-in" in allowed
    assert "landing softly back on the same spot" in allowed
    assert "not a run, lunge or approach" in allowed
    # the existing carve-outs survive
    assert "small paw repositioning required by the intended pose" in allowed
    assert "change in body height and silhouette" in allowed

    blocking = acc["blocking_criteria"]
    joined = " ".join(blocking)
    assert "a full-body step" not in joined  # a hop must not be read as a 'full-body step'
    assert any("The lead-in bounce travels" in c for c in blocking)
    assert any("sustained hopping sequence" in c for c in blocking)
    assert any("sustained repeated jumping that never becomes a bow" in c for c in blocking)
    home = next(c for c in blocking if c.startswith("The pet runs or steps toward or away from the camera"))
    assert "a change in body height or silhouette caused by the pose itself does not count" in home
    assert "Neither does the single brief in-place bounce of the lead-in that lands on the same spot" in home
    assert any("prolonged motionless hold" in c for c in blocking)
    assert "in-place bounce" in acc["description"] and "not locomotion or an approach" in acc["description"]
    # brief event policy: sparse sampling of the bounce is REVIEW, not FAIL
    assert acc["temporal_evidence_policy"]["brief_event"] is True
    assert any("lead-in bounce is small" in c for c in acc["advisory_criteria"])


def test_dog_vlm_guidance_names_the_hop_as_expected_and_not_locomotion():
    g = sv.PLAY_BOW.vlm_guidance
    assert "one short, lively in-place bounce" in g and "that single hop is expected and correct" in g
    assert "the single brief in-place hop of the lead-in (landing on the same spot) are NOT a position change, sliding or a step, nor locomotion" in g
    assert "A hop that lands clearly nearer to or farther from the camera, a run, or repeated hopping that replaces the bow IS a defect" in g
    # the guidance reaches the VLM prompt for the dog contract only
    dog_acc = ms.acceptance_for_variant(ms.MOTIONS[PWM], sv.PLAY_BOW)
    cat_acc = ms.acceptance_for_variant(ms.MOTIONS[PWM], sv.PLAY_CROUCH)
    assert "in-place bounce" in vlm_identity._play_with_me_guidance(dog_acc)
    assert "bounce" not in vlm_identity._play_with_me_guidance(cat_acc)


def test_qa_fields_and_required_checks_are_unchanged_and_in_place_defects_still_fail():
    # No new VLM field or deterministic check was added for the bounce.
    assert len(sv.PLAY_WITH_ME_QA_FIELD_NAMES) == 14
    assert len(sv.PLAY_WITH_ME_REQUIRED_CHECK_NAMES) == 19
    assert "playful_lead_in_visible" in sv.PLAY_WITH_ME_OBSERVATIONAL_FIELDS
    assert "stays_in_place_no_approach" not in sv.PLAY_WITH_ME_OBSERVATIONAL_FIELDS
    assert judge_play(play_evidence())["decision"] == "PASS"
    # A VLM that sees travel still blocks; a merely unconfirmed lead-in only reviews.
    assert judge_play(play_evidence(stays_in_place_no_approach="no"))["decision"] == "FAIL"
    reviewed = judge_play(play_evidence(playful_lead_in_visible="unknown"))
    assert reviewed["decision"] == "REVIEW"
    assert reviewed["business_qa"]["delivery_action"] == "DELIVER_WITH_ADVISORY"


# ── Provider and duration budget ──


@pytest.mark.parametrize("variant", [sv.PLAY_BOW, sv.PLAY_CROUCH], ids=["DOG", "CAT"])
def test_segment_prompts_fit_fal_kling_with_headroom_after_the_bounce(variant, monkeypatch):
    monkeypatch.delenv("FAL_KLING_MAX_PROMPT_CHARS", raising=False)
    provider = video_motion_providers.FalKlingProvider()
    for segment_id, prompt in motion_video_prompts.build_play_with_me_segment_prompts(_contract(variant)).items():
        video_motion_providers.validate_prompt_length(provider, prompt)
        assert len(prompt) <= provider.max_prompt_chars - 100, (segment_id, len(prompt))


def test_selected_provider_and_segment_durations_accommodate_bounce_bow_and_rise(monkeypatch):
    for name in ("MOTION_MODEL_PLAY_WITH_ME", "MOTION_MODEL_PLAY_WITH_ME_PLAY_BOW",
                 "PHASE6_GENERATION_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    assert ms.provider_order_for_motion(PWM, variant_id="PLAY_BOW") == (ms.PROVIDER_KLING_3,)
    provider = video_motion_providers.FalKlingProvider()
    assert provider.supports_end_frame  # both segments stay start/end-conditioned
    spec = ms.MOTIONS[PWM]
    assert spec.duration_range_sec == (4.0, 5.0)  # unchanged — no extra paid seconds
    per_segment = mv.build_output_spec(duration_range=spec.duration_range_sec,
                                       motion_class=ms.CLASS_INTERACTION)[0]
    plan = seg.segment_plan(sv.PLAY_BOW)
    joined = seg.joined_output_spec(per_segment, plan)
    # Each 4 s segment is inside Kling's 3..15 s contract and long enough for
    # anticipation (~1 s) + one bounce and landing (~1 s) + lowering (~2 s), and for
    # a brief hold (~1 s) + natural rise (~3 s). Joined asset stays 8 s.
    assert per_segment["duration_sec"] == 4
    assert provider.MIN_DURATION_SEC <= per_segment["duration_sec"] <= provider.MAX_DURATION_SEC
    assert joined["duration_sec"] == 8 and [s["duration_sec"] for s in joined["segments"]] == [4, 4]
    payload = provider.build_payload(video_motion_providers.MotionVideoRequest(
        prompt=_dog()["A"], start_image_url="https://x/stand.png", start_image_bytes=b"s",
        end_image_url="https://x/bow.png", end_image_bytes=b"e",
        output_spec=seg.segment_output_spec(joined),
    ))
    assert payload["duration"] == "4" and payload["end_image_url"] == "https://x/bow.png"
    assert provider.estimate_cost_usd(payload) == pytest.approx(0.336)  # cost per segment unchanged


# ── Version stamps ──


def test_version_stamps_move_only_where_behaviour_changed():
    assert motion_video_prompts.PLAY_WITH_ME_PROMPT_VERSION == "motion-video-prompt-v3-play-with-me-v7"
    assert motion_video_prompts.prompt_version_for_motion(PWM) == motion_video_prompts.PLAY_WITH_ME_PROMPT_VERSION
    assert vlm_identity.VLM_PLAY_WITH_ME_QA_VERSION == "vlm-motion-qa-v3-play-with-me-v5"
    # policy revision (contract shape / field set) and the global spec versions stay put
    assert sv.PLAY_WITH_ME_POLICY_REVISION == "play-with-me-v3"
    assert ms.MOTION_SPEC_VERSION == "motion-spec-v17"
    for other in ("PAW_HELLO", "PET_HEAD", "COME_CLOSER", "BLINKING", "BREATHING"):
        assert "play-with-me" not in motion_video_prompts.prompt_version_for_motion(other)
    for variant in (sv.PLAY_BOW, sv.PLAY_CROUCH):
        acc = ms.acceptance_for_variant(ms.MOTIONS[PWM], variant)
        assert vlm_identity.motion_acceptance_evaluator_version(acc) == vlm_identity.VLM_PLAY_WITH_ME_QA_VERSION
