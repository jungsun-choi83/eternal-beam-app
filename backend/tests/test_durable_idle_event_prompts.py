"""Complete durable provider prompts for the four commercial idle events."""

from __future__ import annotations

import hashlib

import pytest

from backend.services import motion_spec as ms
from backend.services import motion_video_prompts as prompts
from backend.services.video_motion_providers import (
    FalMinimaxH3MaxTurboProvider,
    MotionVideoRequest,
)


IDLES = ("BLINKING", "EAR_TWITCHING", "HEAD_TILTING", "TAIL_WAGGING")

DISTINCT_REQUIREMENTS = {
    "BLINKING": (
        "complete, natural bilateral blink",
        "eyelids close fully together",
        "reopen fully",
        "do not substitute a wink, squint",
    ),
    "EAR_TWITCHING": (
        "small, quick ear twitch",
        "ear tip or ear body flicks",
        "preserve the source ear shape",
        "do not straighten, perk up",
    ),
    "HEAD_TILTING": (
        "sideways roll of the head",
        "10 to 20 degrees",
        "muzzle and gaze keep pointing in the same direction",
        "tail, paws and lower body remain calm",
    ),
    "TAIL_WAGGING": (
        "gentle, natural tail wag",
        "side to side from its base two or three times",
        "preserve the source tail's length",
        "rather than a hip swing",
    ),
}


def _contract(motion_id: str) -> dict:
    spec = ms.MOTIONS[motion_id]
    return {
        "motion_id": motion_id,
        "motion_class": spec.motion_class,
        "start_keyframe": {"role": spec.start_keyframe_role},
        "video_compat": spec.video_compat,
        "conditioning_fallbacks": [],
        "loop_same_start_end": ms.is_loop_same_start_end(spec),
    }


def _prompt(motion_id: str) -> str:
    spec = ms.MOTIONS[motion_id]
    return prompts.build_motion_video_prompt(_contract(motion_id), spec.description)


@pytest.mark.parametrize("motion_id", IDLES)
def test_complete_idle_prompt_has_four_non_conflicting_layers(motion_id):
    prompt = _prompt(motion_id)
    layers = prompt.split("\n")

    assert len(layers) == 4
    assert layers[0] == ms.MOTIONS[motion_id].description + "."
    for phrase in DISTINCT_REQUIREMENTS[motion_id]:
        assert phrase in layers[0].lower()

    # Shared motion layer: named part can move, unrelated body motion cannot take over.
    assert "dominant and clearly readable intentional movement" in layers[1]
    assert "Explicitly allow the named body part" in layers[1]
    assert "local silhouette may change naturally" in layers[1]
    assert "Do not add another intentional gesture" in layers[1]
    assert "Do not walk, step, slide" in layers[1]

    # Return layer: forward-time natural recovery into the BREATHING home state.
    assert "returns continuously and naturally" in layers[2]
    for forbidden_return in ("reverse", "snap", "dissolve", "teleport", "morph"):
        assert forbidden_return in layers[2]
    assert "used by BREATHING" in layers[2]

    # Identity layer preserves anatomy and stable natural shading without freezing motion.
    assert "preserve the face, eyes, fur colors, markings" in layers[3]
    assert "Preserve source occlusions" in layers[3]
    assert "stable natural self-shading" in layers[3]
    assert "Keep the camera, crop, framing, perspective and focus fixed" in layers[3]
    assert "shadowless" not in layers[3]

    assert "호기심 어린" not in prompt
    assert "움찔" not in prompt
    assert "흔들기" not in prompt
    assert "깜빡임" not in prompt


def test_four_complete_prompts_are_distinct_and_stably_versioned():
    assembled = {motion_id: _prompt(motion_id) for motion_id in IDLES}
    assert len(set(assembled.values())) == len(IDLES)
    assert {
        prompts.prompt_version_for_motion(motion_id) for motion_id in IDLES
    } == {prompts.DURABLE_IDLE_EVENT_PROMPT_VERSION}

    # A complete-prompt fingerprint catches changes in any of the four layers.
    assert {
        motion_id: hashlib.sha256(prompt.encode()).hexdigest()
        for motion_id, prompt in assembled.items()
    } == {
        "BLINKING": "040901fbc932afb84b5c174759e8f21962792fb9c68abb26bf8f2d8269231af0",
        "EAR_TWITCHING": "e8b50d61b21de20d5b83a3486d3ead1644f1a380b799d64955f21d1c799938ff",
        "HEAD_TILTING": "bad6c4827c813ea168124e9ff60085906b4140668df9c01a1347fea4ed0adee3",
        "TAIL_WAGGING": "827febd037decac28622ebe0b6ff3192b69c4ff8edb62b3d4fdac9f39783b2b0",
    }


@pytest.mark.parametrize(
    "motion_id", ("BREATHING", "LOOK_UP", "LIE_IDLE", "SLEEP_BREATH")
)
def test_unrelated_micro_prompts_keep_the_existing_generic_composition(motion_id):
    spec = ms.MOTIONS[motion_id]
    contract = _contract(motion_id)
    assert prompts.build_motion_video_prompt(
        contract, spec.description
    ) == prompts._micro(contract, spec.description)
    assert prompts.prompt_version_for_motion(
        motion_id
    ) == prompts.MOTION_VIDEO_PROMPT_VERSION


def test_continuity_locomotion_prompts_keep_existing_composition_and_global_version():
    for motion_id in ("COME_CLOSER", "RETURN_HOME"):
        spec = ms.MOTIONS[motion_id]
        contract = _contract(motion_id)
        assert prompts.build_motion_video_prompt(
            contract, spec.description
        ) == prompts._locomotion(contract, spec.description)
        assert prompts.prompt_version_for_motion(
            motion_id
        ) == prompts.MOTION_VIDEO_PROMPT_VERSION


@pytest.mark.parametrize("motion_id", IDLES)
def test_minimax_payload_receives_the_complete_prompt_unchanged(monkeypatch, motion_id):
    monkeypatch.delenv("FAL_MINIMAX_H3_PROMPT_EXPANSION", raising=False)
    final_prompt = _prompt(motion_id)
    request = MotionVideoRequest(
        prompt=final_prompt,
        start_image_url="https://cdn.test/stand-ready.png",
        start_image_bytes=b"stand-ready",
        output_spec={
            "aspect_ratio": "16:9",
            "resolution": "480p",
            "duration_sec": 4,
            "audio": False,
            "camera_fixed": True,
        },
    )
    payload = FalMinimaxH3MaxTurboProvider().build_payload(request)
    assert payload["prompt"] == final_prompt
    assert payload["prompt_expansion_mode"] == "disabled"


def test_durable_idle_prompt_fails_closed_if_pose_contract_stops_being_stand_ready():
    contract = _contract("HEAD_TILTING")
    contract["start_keyframe"] = {"role": "LIE"}
    with pytest.raises(ValueError, match="requires STAND_READY"):
        prompts.build_motion_video_prompt(
            contract, ms.MOTIONS["HEAD_TILTING"].description
        )
