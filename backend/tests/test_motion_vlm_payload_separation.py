"""MOTION_VLM payload separation + frame/fraction alignment (2026-10-22).

Regression guard for the COME_CLOSER MOTION_VLM bug: the appended STAND_READY
reference keyframe was handed to MOTION_VLM as if it were a chronological video
frame, so the VLM read the distant standing reference as the final generated
frame and falsely reported "returns to the distant starting pose".

These tests assert the fix end to end, at the payload level:
  * MOTION_VLM receives ONLY generated video frames (no reference bytes).
  * The appended START reference can never be the last MOTION_VLM image, and the
    MOTION_VLM prompt states no reference image is included.
  * Identity/anatomy tasks still receive the reference (and target) images.
  * A dropped (failed-decode) sampled frame drops its fraction with it, so later
    frames keep their true labels and the 1.0 frame stays labelled 1.0.
"""

from __future__ import annotations

import base64
import io
from typing import Any

import numpy as np
import pytest

from backend.services import motion_video_service, vlm_escalation, vlm_identity


# ── qa_motion_video payload tests ───────────────────────────────────────────

VIDEO_FRACTIONS = (0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0)
VIDEO_FRAMES = [(f"vf{i}".encode(), "image/jpeg") for i in range(len(VIDEO_FRACTIONS))]
START_REF = (b"START_STAND_READY_REFERENCE", "image/png")
TARGET_REF = (b"TARGET_POSE_KEYFRAME", "image/png")


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setenv("PET_VLM_IDENTITY_ENABLED", "1")
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    # Cache off: every call reaches _structured_qa_call so we can inspect it.
    monkeypatch.setenv(vlm_identity.VLM_QA_CACHE_ENV, "off")
    vlm_identity.clear_semantic_cache()
    yield
    vlm_identity.clear_semantic_cache()


def _decode_images(content: list[dict[str, Any]]) -> list[bytes]:
    return [
        base64.standard_b64decode(block["source"]["data"])
        for block in content
        if block.get("type") == "image"
    ]


@pytest.fixture
def calls(monkeypatch):
    """Record the exact image bytes + prompt sent to each structured QA call."""
    recorded: list[dict[str, Any]] = []

    def fake(content, schema, *, label):
        recorded.append(
            {
                "label": label,
                "images": _decode_images(content),
                "prompt": content[-1]["text"],
                "properties": set(schema["properties"]),
            }
        )
        # Minimal well-formed answer so the merge path runs.
        return {key: "unknown" for key in schema["properties"]} | {"model": "test"}

    monkeypatch.setattr(vlm_identity, "_structured_qa_call", fake)
    return recorded


def _come_closer_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs = dict(
        motion_description="The pet runs toward the owner",
        motion_class="LOCOMOTION",
        sample_fractions=VIDEO_FRACTIONS,
        reference_image=START_REF,
        target_image=None,  # COME_CLOSER has no target keyframe
        expected_direction="TOWARD_CAMERA",
    )
    kwargs.update(overrides)
    return kwargs


def test_come_closer_motion_vlm_sees_only_video_frames(calls):
    vlm_identity.qa_motion_video(
        list(VIDEO_FRAMES),
        tasks=[vlm_escalation.MOTION_VLM],
        unresolved_questions=["motion_correctness"],
        **_come_closer_kwargs(),
    )
    assert len(calls) == 1
    motion = calls[0]
    assert "MOTION_VLM" in motion["label"]
    # Only the 9 generated video frames, in chronological order.
    assert motion["images"] == [data for data, _ in VIDEO_FRAMES]
    # The STAND_READY reference is NOT in the MOTION_VLM payload at all.
    assert START_REF[0] not in motion["images"]


def test_appended_reference_cannot_be_read_as_final_frame(calls):
    vlm_identity.qa_motion_video(
        list(VIDEO_FRAMES),
        tasks=[vlm_escalation.MOTION_VLM],
        unresolved_questions=["motion_correctness", "temporal_integrity"],
        **_come_closer_kwargs(),
    )
    motion = calls[0]
    # The LAST image MOTION_VLM sees is the true 1.0 video frame, never the ref.
    assert motion["images"][-1] == VIDEO_FRAMES[-1][0] == b"vf8"
    assert motion["images"][-1] != START_REF[0]
    # And the prompt tells the model there is no reference among the images.
    assert "no reference" in motion["prompt"].lower()
    assert "final generated frame" in motion["prompt"].lower()


def test_identity_anatomy_still_receives_reference(calls):
    vlm_identity.qa_motion_video(
        list(VIDEO_FRAMES),
        tasks=[vlm_escalation.IDENTITY_ANATOMY_VLM],
        unresolved_questions=["full_stage_review"],
        **_come_closer_kwargs(),
    )
    assert len(calls) == 1
    ia = calls[0]
    assert "IDENTITY_ANATOMY_VLM" in ia["label"]
    # Video frames followed by the reference keyframe.
    assert ia["images"] == [data for data, _ in VIDEO_FRAMES] + [START_REF[0]]
    assert ia["images"][-1] == START_REF[0]
    # The reference is labelled as a reference, not a video frame.
    assert "reference keyframe" in ia["prompt"].lower()
    assert "not a video frame" in ia["prompt"].lower()


def test_mixed_tasks_split_payloads_in_one_call(calls):
    vlm_identity.qa_motion_video(
        list(VIDEO_FRAMES),
        tasks=[vlm_escalation.MOTION_VLM, vlm_escalation.IDENTITY_ANATOMY_VLM],
        unresolved_questions=["full_stage_review"],
        **_come_closer_kwargs(),
    )
    by_task = {}
    for rec in calls:
        task = "MOTION" if "MOTION_VLM" in rec["label"] else "IDENTITY_ANATOMY"
        by_task[task] = rec
    # Same underlying call, two payloads: motion video-only, identity +reference.
    assert START_REF[0] not in by_task["MOTION"]["images"]
    assert len(by_task["MOTION"]["images"]) == len(VIDEO_FRAMES)
    assert by_task["IDENTITY_ANATOMY"]["images"][-1] == START_REF[0]
    assert len(by_task["IDENTITY_ANATOMY"]["images"]) == len(VIDEO_FRAMES) + 1


def test_target_image_only_for_identity_not_motion(calls):
    # START_END motions (e.g. RETURN_HOME) also carry a TARGET keyframe.
    vlm_identity.qa_motion_video(
        list(VIDEO_FRAMES),
        tasks=[vlm_escalation.MOTION_VLM, vlm_escalation.IDENTITY_ANATOMY_VLM],
        unresolved_questions=["full_stage_review"],
        **_come_closer_kwargs(target_image=TARGET_REF),
    )
    motion = next(r for r in calls if "MOTION_VLM" in r["label"])
    ia = next(r for r in calls if "IDENTITY_ANATOMY_VLM" in r["label"])
    # MOTION_VLM: video only, neither reference nor target.
    assert motion["images"] == [data for data, _ in VIDEO_FRAMES]
    assert START_REF[0] not in motion["images"]
    assert TARGET_REF[0] not in motion["images"]
    # IDENTITY_ANATOMY: video + START + TARGET, in that trailing order.
    assert ia["images"] == [data for data, _ in VIDEO_FRAMES] + [START_REF[0], TARGET_REF[0]]
    assert "target pose keyframe" in ia["prompt"].lower()


# ── frame/fraction alignment tests ──────────────────────────────────────────

def _solid(color: int) -> np.ndarray:
    arr = np.zeros((8, 8, 3), dtype=np.uint8)
    arr[:, :, 0] = color
    return arr


def _encode_like_service(arr: np.ndarray) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGB").save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def test_dropped_frame_does_not_shift_fraction_labels():
    fractions = (0.0, 0.25, 0.5, 0.75, 1.0)
    frames = [_solid(10), None, _solid(120), _solid(200), _solid(250)]  # 0.25 fails
    images, kept = motion_video_service._frames_to_jpeg_pairs(fractions, frames)

    # The failed 0.25 frame dropped WITH its fraction — later labels unchanged.
    assert kept == (0.0, 0.5, 0.75, 1.0)
    assert len(images) == 4
    # The 0.5 frame kept its 0.5 label (byte-identical to a standalone encode),
    # i.e. it was NOT shifted down into the vacated 0.25 slot.
    assert images[1][0] == _encode_like_service(_solid(120))


def test_true_last_frame_stays_labelled_one_point_zero():
    fractions = (0.0, 0.25, 0.5, 0.75, 1.0)
    frames = [_solid(10), None, _solid(120), None, _solid(250)]  # two mid drops
    images, kept = motion_video_service._frames_to_jpeg_pairs(fractions, frames)

    assert kept[-1] == 1.0
    # The 1.0-labelled image is the actual final frame, not a shifted neighbour.
    assert images[-1][0] == _encode_like_service(_solid(250))
    assert kept == (0.0, 0.5, 1.0)


def test_alignment_survives_into_vlm_fraction_labels(calls):
    # End to end through the combined template (which renders the fraction list):
    # drop a middle frame, confirm the prompt's fraction labels match the
    # surviving frames — the vacated 0.375 is gone, not shifted onto a later
    # frame, and the final frame stays labelled 1.0.
    frames = [_solid(i * 25) for i in range(len(VIDEO_FRACTIONS))]
    frames[3] = None  # 0.375 fails to decode
    images, kept = motion_video_service._frames_to_jpeg_pairs(VIDEO_FRACTIONS, frames)
    assert kept == (0.0, 0.125, 0.25, 0.5, 0.625, 0.75, 0.875, 1.0)

    vlm_identity.qa_motion_video(
        images,
        **_come_closer_kwargs(sample_fractions=kept),  # no tasks → combined template
    )
    combined = calls[0]
    # 8 surviving video frames + the trailing START reference.
    assert len(combined["images"]) == 9
    assert combined["images"][:8] == [data for data, _ in images]
    assert combined["images"][-1] == START_REF[0]
    # Fraction labels follow the surviving frames: 1.0 present, 0.375 gone.
    assert "1.0]" in combined["prompt"]
    assert "0.375" not in combined["prompt"]
