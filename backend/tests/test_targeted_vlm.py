from __future__ import annotations

from typing import Any

import pytest

from backend.services import vlm_escalation, vlm_identity


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setenv("PET_VLM_IDENTITY_ENABLED", "1")
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv(vlm_identity.VLM_QA_CACHE_ENV, "on")
    vlm_identity.clear_semantic_cache()
    yield
    vlm_identity.clear_semantic_cache()


def _answer_for(schema: dict[str, Any]) -> dict[str, Any]:
    answer: dict[str, Any] = {}
    for key in schema["properties"]:
        if key.endswith("confidence"):
            answer[key] = "high"
        elif key in {
            "human_present",
            "duplicated_pet",
            "scene_cut",
            "major_flicker",
            "unintended_large_motion",
        }:
            answer[key] = "no"
        elif key in {"notes", "identity_notes"}:
            answer[key] = "targeted evidence"
        else:
            answer[key] = "yes"
    return answer


@pytest.fixture
def calls(monkeypatch):
    recorded: list[dict[str, Any]] = []

    def fake(content, schema, *, label):
        recorded.append(
            {
                "label": label,
                "properties": set(schema["properties"]),
                "prompt": content[-1]["text"],
            }
        )
        return {**_answer_for(schema), "model": "targeted-test"}

    monkeypatch.setattr(vlm_identity, "_structured_qa_call", fake)
    return recorded


def test_identity_conflict_calls_identity_vlm_only(calls):
    result = vlm_identity.qa_canonical_image(
        b"candidate",
        [(b"reference", "image/jpeg")],
        tasks=[vlm_escalation.IDENTITY_VLM],
        unresolved_questions=["distinctive_markings_identity"],
    )
    assert result["called_tasks"] == [vlm_escalation.IDENTITY_VLM]
    assert len(calls) == 1
    assert "IDENTITY_VLM" in calls[0]["label"]
    assert "distinctive_markings_consistent" in calls[0]["properties"]
    assert "anatomy_plausible" not in calls[0]["properties"]
    assert "face_head_consistent" not in calls[0]["properties"]


def test_anatomy_uncertainty_calls_anatomy_vlm_only(calls):
    result = vlm_identity.qa_canonical_image(
        b"candidate",
        [(b"reference", "image/jpeg")],
        tasks=[vlm_escalation.ANATOMY_VLM],
        unresolved_questions=["anatomy"],
    )
    assert result["called_tasks"] == [vlm_escalation.ANATOMY_VLM]
    assert len(calls) == 1
    assert calls[0]["properties"] == {
        "anatomy_plausible", "single_pet", "human_present", "major_occlusion"
    }
    assert "same_pet" not in calls[0]["properties"]


def test_keyframe_pose_uncertainty_calls_pose_vlm_only(calls):
    result = vlm_identity.qa_action_keyframe(
        b"candidate",
        [(b"canonical", "image/png")],
        required_pose="sitting",
        required_visibility=("face", "paws"),
        tasks=[vlm_escalation.POSE_VLM],
        unresolved_questions=["required_pose"],
    )
    assert result["called_tasks"] == [vlm_escalation.POSE_VLM]
    assert len(calls) == 1
    assert calls[0]["properties"] == {
        "pose_matches", "pose_confidence", "body_orientation_ok", "required_regions_visible"
    }
    assert "same_pet" not in calls[0]["properties"]


def test_motion_uncertainty_calls_motion_vlm_only(calls):
    result = vlm_identity.qa_motion_video(
        [(b"f0", "image/jpeg"), (b"f1", "image/jpeg")],
        motion_description="walk left",
        motion_class="LOCOMOTION",
        sample_fractions=(0.0, 1.0),
        tasks=[vlm_escalation.MOTION_VLM],
        unresolved_questions=["motion_correctness"],
    )
    assert result["called_tasks"] == [vlm_escalation.MOTION_VLM]
    assert len(calls) == 1
    assert {
        "requested_motion_occurs", "locomotion_form_correct", "direction_travel_correct"
    } <= calls[0]["properties"]
    assert "same_pet_all_frames" not in calls[0]["properties"]
    assert "anatomy_plausible_all_frames" not in calls[0]["properties"]


def test_targeted_cache_is_independent_and_reused(calls):
    kwargs = {
        "tasks": [vlm_escalation.POSE_VLM],
        "unresolved_questions": ["required_pose"],
        "required_pose": "lying",
    }
    first = vlm_identity.qa_action_keyframe(b"c", [(b"r", "image/png")], **kwargs)
    second = vlm_identity.qa_action_keyframe(b"c", [(b"r", "image/png")], **kwargs)
    assert first["called_tasks"] == second["called_tasks"]
    assert first["pose_matches"] == second["pose_matches"]
    assert len(calls) == 1
    assert first["source"] == vlm_identity.VLM_TARGETED_QA_VERSION
    assert vlm_identity.KIND_TARGETED_QA in next(iter(vlm_identity._MOCK_DURABLE_CACHE.values()))["kind"]
    evidence = first["targeted_vlm_evidence"][vlm_escalation.POSE_VLM]
    cached = second["targeted_vlm_evidence"][vlm_escalation.POSE_VLM]
    assert evidence["cache_receipt"]["status"] == "computed"
    assert cached["cache_receipt"]["status"] == "cache_hit"


def test_targeted_cache_changed_image_hash_recomputes(calls):
    kwargs = {
        "tasks": [vlm_escalation.POSE_VLM],
        "unresolved_questions": ["required_pose"],
        "required_pose": "lying",
    }
    vlm_identity.qa_action_keyframe(b"candidate-a", [(b"r", "image/png")], **kwargs)
    result = vlm_identity.qa_action_keyframe(
        b"candidate-b", [(b"r", "image/png")], **kwargs
    )
    assert len(calls) == 2
    assert result["targeted_vlm_evidence"][vlm_escalation.POSE_VLM][
        "cache_receipt"
    ]["status"] == "computed"


def test_targeted_cache_changed_qa_version_recomputes(calls, monkeypatch):
    kwargs = {
        "tasks": [vlm_escalation.POSE_VLM],
        "unresolved_questions": ["required_pose"],
        "required_pose": "lying",
    }
    vlm_identity.qa_action_keyframe(b"candidate", [(b"r", "image/png")], **kwargs)
    monkeypatch.setattr(vlm_identity, "VLM_TARGETED_QA_VERSION", "vlm-targeted-qa-v999-test")
    result = vlm_identity.qa_action_keyframe(
        b"candidate", [(b"r", "image/png")], **kwargs
    )
    assert len(calls) == 2
    assert result["targeted_vlm_evidence"][vlm_escalation.POSE_VLM][
        "cache_receipt"
    ]["qa_version"].startswith("vlm-targeted-qa-v999-test")


def test_targeted_cache_changed_model_recomputes(calls, monkeypatch):
    kwargs = {
        "tasks": [vlm_escalation.POSE_VLM],
        "unresolved_questions": ["required_pose"],
        "required_pose": "lying",
    }
    vlm_identity.qa_action_keyframe(b"candidate", [(b"r", "image/png")], **kwargs)
    monkeypatch.setenv("PET_VLM_MODEL", "different-test-model")
    result = vlm_identity.qa_action_keyframe(
        b"candidate", [(b"r", "image/png")], **kwargs
    )
    assert len(calls) == 2
    assert result["targeted_vlm_evidence"][vlm_escalation.POSE_VLM][
        "cache_receipt"
    ]["model"] == "different-test-model"


def test_targeted_cache_changed_prompt_parameter_recomputes(calls):
    base = {
        "tasks": [vlm_escalation.POSE_VLM],
        "unresolved_questions": ["required_pose"],
    }
    vlm_identity.qa_action_keyframe(
        b"candidate", [(b"r", "image/png")], required_pose="lying", **base
    )
    result = vlm_identity.qa_action_keyframe(
        b"candidate", [(b"r", "image/png")], required_pose="sitting", **base
    )
    assert len(calls) == 2
    assert result["targeted_vlm_evidence"][vlm_escalation.POSE_VLM][
        "cache_receipt"
    ]["status"] == "computed"


def test_targeted_cache_changed_profile_lineage_recomputes(calls):
    base = {
        "tasks": [vlm_escalation.POSE_VLM],
        "unresolved_questions": ["required_pose"],
        "required_pose": "lying",
    }
    vlm_identity.qa_action_keyframe(
        b"candidate",
        [(b"r", "image/png")],
        evidence_context={"identity_profile_version": 1, "reference_set_version": 2},
        **base,
    )
    result = vlm_identity.qa_action_keyframe(
        b"candidate",
        [(b"r", "image/png")],
        evidence_context={"identity_profile_version": 2, "reference_set_version": 2},
        **base,
    )
    assert len(calls) == 2
    receipt = result["targeted_vlm_evidence"][vlm_escalation.POSE_VLM][
        "cache_receipt"
    ]
    assert receipt["status"] == "computed"
