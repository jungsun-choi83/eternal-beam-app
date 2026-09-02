"""
VLM 시맨틱 특성 분석 (Phase 2) — **격리된** 비전-언어 모델 인터페이스.

── 위치와 원칙 ─────────────────────────────────────────────────────────────
결정론적 분석(pet_identity_service)이 잴 수 없는 시맨틱 특성 — 귀 모양, 주둥이
색, 무늬 서술 — 만 여기서 얻는다. 원칙:

  * 출력은 **구조화**된다 (JSON schema 강제). 자유 서술이 스키마 밖으로 새지 않는다.
  * 증거가 부족한 항목은 "unknown" 이다 — 프롬프트와 스키마 둘 다 이를 강제한다.
  * 이 출력은 **정본이 아니다.** 원본 이미지가 정본이고, 이 결과는 프로필의
    semantic_traits 네임스페이스에만 들어간다 — 결정론적 필드를 덮지 않는다.
  * 어떤 모델/버전이 만든 값인지 항상 함께 기록된다.

── 왜 기본 꺼짐인가 ────────────────────────────────────────────────────────
배포된 Render 환경에는 anthropic 패키지도 API 키도 없다. 켜려면:
  PET_VLM_IDENTITY_ENABLED=1  +  ANTHROPIC_API_KEY  +  pip install anthropic
꺼져 있거나 실패하면 None 을 돌려주고, 호출자는 semantic_traits 를 unknown 으로
기록한다 — 신원 분석 실패가 파이프라인을 막지 않는다.

거절(stop_reason == "refusal")도 같은 경로다: 분석 불가 → None → unknown.
그래서 server-side fallback 베타는 붙이지 않았다 — 여기서 거절의 올바른 처리는
"다른 모델로 재시도"가 아니라 "증거 없음"이다.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

VLM_ANALYZER_VERSION = "vlm-identity-v1"
VLM_CLASSIFIER_VERSION = "vlm-view-pose-v1"
VLM_CANONICAL_QA_VERSION = "vlm-canonical-qa-v1"

_ENABLED_ENV = "PET_VLM_IDENTITY_ENABLED"
_MODEL_ENV = "PET_VLM_MODEL"
_DEFAULT_MODEL = "claude-opus-5"

#: 한 번의 분석에 보낼 최대 이미지 수 (원본 레퍼런스 앞에서부터).
MAX_IMAGES = 3

UNKNOWN = "unknown"

#: 구조화 출력 스키마. 모든 문자열 필드는 증거가 부족하면 "unknown" 이어야 한다.
SEMANTIC_TRAITS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "species": {"type": "string"},
        "breed_estimate": {"type": "string"},
        "breed_confidence": {"type": "string", "enum": ["high", "medium", "low", "unknown"]},
        "face": {
            "type": "object",
            "properties": {
                "muzzle_color": {"type": "string"},
                "facial_markings": {"type": "string"},
            },
            "required": ["muzzle_color", "facial_markings"],
            "additionalProperties": False,
        },
        "eyes": {
            "type": "object",
            "properties": {
                "color": {"type": "string"},
                "surrounding_markings": {"type": "string"},
            },
            "required": ["color", "surrounding_markings"],
            "additionalProperties": False,
        },
        "ears": {
            "type": "object",
            "properties": {
                "shape": {
                    "type": "string",
                    "enum": ["erect", "semi_erect", "floppy", "rose", "cropped", "unknown"],
                },
                "color_markings": {"type": "string"},
            },
            "required": ["shape", "color_markings"],
            "additionalProperties": False,
        },
        "coat": {
            "type": "object",
            "properties": {
                "dominant_colors": {"type": "array", "items": {"type": "string"}},
                "secondary_colors": {"type": "array", "items": {"type": "string"}},
                "length": {
                    "type": "string",
                    "enum": ["hairless", "short", "medium", "long", "unknown"],
                },
                "texture": {
                    "type": "string",
                    "enum": ["smooth", "wiry", "curly", "double", "silky", "unknown"],
                },
                "marking_distribution": {"type": "string"},
            },
            "required": [
                "dominant_colors",
                "secondary_colors",
                "length",
                "texture",
                "marking_distribution",
            ],
            "additionalProperties": False,
        },
        "body": {
            "type": "object",
            "properties": {
                "chest_markings": {"type": "string"},
                "torso_markings": {"type": "string"},
            },
            "required": ["chest_markings", "torso_markings"],
            "additionalProperties": False,
        },
        "paws": {
            "type": "object",
            "properties": {"colors_markings": {"type": "string"}},
            "required": ["colors_markings"],
            "additionalProperties": False,
        },
        "tail": {
            "type": "object",
            "properties": {
                "appearance": {"type": "string"},
                "tip_marking": {"type": "string"},
            },
            "required": ["appearance", "tip_marking"],
            "additionalProperties": False,
        },
        "unique_features": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "species",
        "breed_estimate",
        "breed_confidence",
        "face",
        "eyes",
        "ears",
        "coat",
        "body",
        "paws",
        "tail",
        "unique_features",
    ],
    "additionalProperties": False,
}

_PROMPT = (
    "You are documenting the visual identity of ONE pet from its owner's reference "
    "photos, for later image-generation fidelity checks. Report ONLY what is clearly "
    "visible in these exact images.\n"
    "Rules:\n"
    "- If a body part is not visible, occluded, blurry, or you are not confident, use "
    'the exact string "unknown" for that field (or an empty list for list fields).\n'
    "- Never guess hidden regions. A photo that hides the tail means tail fields are "
    '"unknown".\n'
    "- Describe colors and markings concretely (e.g. \"white blaze from forehead to "
    'nose", "dark saddle over back").\n'
    "- unique_features: only clearly visible distinctive traits (heterochromia, torn "
    "ear, specific spot patterns). Empty list if none are visible."
)


def is_enabled() -> bool:
    return os.getenv(_ENABLED_ENV, "0").strip().lower() in ("1", "true", "yes")


def model_name() -> str:
    return (os.getenv(_MODEL_ENV) or "").strip() or _DEFAULT_MODEL


def analyze_semantic_traits(
    images: Sequence[tuple[bytes, str]],
) -> Optional[dict[str, Any]]:
    """
    원본 이미지들 → 구조화된 시맨틱 특성. 실패/비활성은 None.

    images: (bytes, mime_type) 목록. 앞에서부터 MAX_IMAGES 장만 쓴다.
    반환: {"traits": <스키마 준수 dict>, "model": ..., "analyzer": ..., "image_count": n}
    """
    if not is_enabled():
        return None
    if not images:
        return None

    try:
        import anthropic
    except ImportError:
        logger.warning("PET_VLM_IDENTITY_ENABLED=1 이지만 anthropic 패키지가 없습니다.")
        return None

    content: list[dict[str, Any]] = []
    used = 0
    for data, mime in images[:MAX_IMAGES]:
        if not data:
            continue
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": (mime or "image/jpeg"),
                    "data": base64.standard_b64encode(data).decode("ascii"),
                },
            }
        )
        used += 1
    if not used:
        return None
    content.append({"type": "text", "text": _PROMPT})

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model_name(),
            max_tokens=4096,
            messages=[{"role": "user", "content": content}],
            output_config={
                "format": {"type": "json_schema", "schema": SEMANTIC_TRAITS_SCHEMA}
            },
        )
    except Exception:
        logger.warning("VLM 시맨틱 분석 호출 실패", exc_info=True)
        return None

    # 거절은 "증거 제공 불가"로 처리한다 — 호출자가 unknown 으로 기록한다.
    if getattr(response, "stop_reason", None) == "refusal":
        logger.warning("VLM 시맨틱 분석이 거절됨 (stop_details=%s)", getattr(response, "stop_details", None))
        return None

    try:
        text = next(b.text for b in response.content if b.type == "text")
        traits = json.loads(text)
    except Exception:
        logger.warning("VLM 응답 파싱 실패", exc_info=True)
        return None

    return {
        "traits": traits,
        "model": getattr(response, "model", model_name()),
        "analyzer": VLM_ANALYZER_VERSION,
        "image_count": used,
    }


# ══════════════════════════════════════════════════════════════════════════
# 레퍼런스 1장 뷰/포즈/가시성 분류 (Phase 3)
# ══════════════════════════════════════════════════════════════════════════

#: pet_reference_images.view_label CHECK 의 상위집합 — 같은 문자열 체계 하나만 쓴다.
VIEW_LABELS = (
    "FRONT",
    "FRONT_LEFT_3Q",
    "FRONT_RIGHT_3Q",
    "LEFT",
    "RIGHT",
    "BACK",
    "TOP",
    "FULL_BODY",
    "FACE_CLOSEUP",
    "UNKNOWN",
)

POSE_LABELS = ("STANDING", "SITTING", "LYING", "SLEEPING", "CLOSEUP", "UNKNOWN")

_YNU = {"type": "string", "enum": ["yes", "no", "unknown"]}
_CONF = {"type": "string", "enum": ["high", "medium", "low"]}

REFERENCE_CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "view_label": {"type": "string", "enum": list(VIEW_LABELS)},
        "view_confidence": _CONF,
        "pose_label": {"type": "string", "enum": list(POSE_LABELS)},
        "pose_confidence": _CONF,
        "visibility": {
            "type": "object",
            "properties": {
                "face_visible": _YNU,
                "full_body_visible": _YNU,
                "left_side_visible": _YNU,
                "right_side_visible": _YNU,
                "paws_visible": _YNU,
                "tail_visible": _YNU,
                "ears_visible": _YNU,
                "distinct_markings_visible": _YNU,
                "heavy_occlusion": _YNU,
                "person_obstruction": _YNU,
            },
            "required": [
                "face_visible",
                "full_body_visible",
                "left_side_visible",
                "right_side_visible",
                "paws_visible",
                "tail_visible",
                "ears_visible",
                "distinct_markings_visible",
                "heavy_occlusion",
                "person_obstruction",
            ],
            "additionalProperties": False,
        },
    },
    "required": ["view_label", "view_confidence", "pose_label", "pose_confidence", "visibility"],
    "additionalProperties": False,
}

_CLASSIFY_PROMPT = (
    "Classify this single pet reference photo for an identity-reference catalog.\n"
    "Rules:\n"
    '- LEFT/RIGHT are from the PET\'s perspective (its left flank visible = "LEFT").\n'
    '- Be conservative: if the camera angle is ambiguous, use view_label "UNKNOWN" '
    "with low confidence rather than guessing a side.\n"
    '- visibility answers are about what is actually visible in THIS photo; use '
    '"unknown" when you cannot tell.\n'
    "- Never infer hidden anatomy: a tail out of frame means tail_visible is "
    '"no", not a guess.'
)


def classify_reference(data: bytes, mime_type: str = "image/jpeg") -> Optional[dict[str, Any]]:
    """
    원본 1장 → {view_label, pose_label, visibility, ...}. 실패/비활성은 None —
    호출자는 결정론적 UNKNOWN 폴백을 쓴다.
    """
    if not is_enabled() or not data:
        return None
    try:
        import anthropic
    except ImportError:
        logger.warning("PET_VLM_IDENTITY_ENABLED=1 이지만 anthropic 패키지가 없습니다.")
        return None

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model_name(),
            max_tokens=2048,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": (mime_type or "image/jpeg"),
                                "data": base64.standard_b64encode(data).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": _CLASSIFY_PROMPT},
                    ],
                }
            ],
            output_config={
                "format": {"type": "json_schema", "schema": REFERENCE_CLASSIFICATION_SCHEMA}
            },
        )
    except Exception:
        logger.warning("VLM 레퍼런스 분류 호출 실패", exc_info=True)
        return None

    if getattr(response, "stop_reason", None) == "refusal":
        return None

    try:
        text = next(b.text for b in response.content if b.type == "text")
        result = json.loads(text)
    except Exception:
        logger.warning("VLM 분류 응답 파싱 실패", exc_info=True)
        return None

    result["source"] = VLM_CLASSIFIER_VERSION
    result["model"] = getattr(response, "model", model_name())
    return result


# ══════════════════════════════════════════════════════════════════════════
# 정본 후보 QA (Phase 4) — 생성된 마스터 이미지가 "같은 펫"인가
# ══════════════════════════════════════════════════════════════════════════

CANONICAL_QA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "same_pet": _YNU,
        "same_pet_confidence": _CONF,
        "anatomy_plausible": _YNU,
        "single_pet": _YNU,
        "human_present": _YNU,
        "accessories_present": _YNU,
        "background_neutral": _YNU,
        "pose_neutral": _YNU,
        "full_body_visible": _YNU,
        "major_occlusion": _YNU,
        "identity_notes": {"type": "string"},
    },
    "required": [
        "same_pet",
        "same_pet_confidence",
        "anatomy_plausible",
        "single_pet",
        "human_present",
        "accessories_present",
        "background_neutral",
        "pose_neutral",
        "full_body_visible",
        "major_occlusion",
        "identity_notes",
    ],
    "additionalProperties": False,
}

_CANONICAL_QA_PROMPT = (
    "The FIRST image is a GENERATED candidate for a canonical reference image of a "
    "pet. The following images are REAL reference photos of the actual pet.\n"
    "Judge the candidate strictly:\n"
    '- same_pet: does the candidate clearly show the SAME individual pet as the real '
    'photos (coat colors, markings, ear shape, facial proportions)? Use "no" for a '
    'similar-looking but different animal, "unknown" if you cannot tell.\n'
    "- anatomy_plausible: correct number of visible limbs, natural joints, no "
    "merged/extra body parts.\n"
    "- The candidate should contain a single pet, no human, neutral plain "
    "background, neutral pose, full body visible, no major occlusion.\n"
    '- Use "unknown" whenever the evidence is insufficient. Be conservative.'
)


def qa_canonical_image(
    candidate: bytes,
    references: Sequence[tuple[bytes, str]],
    candidate_mime: str = "image/png",
) -> Optional[dict[str, Any]]:
    """생성 후보 vs 실제 레퍼런스 — 구조화 QA. 실패/비활성은 None."""
    if not is_enabled() or not candidate:
        return None
    try:
        import anthropic
    except ImportError:
        logger.warning("PET_VLM_IDENTITY_ENABLED=1 이지만 anthropic 패키지가 없습니다.")
        return None

    content: list[dict[str, Any]] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": candidate_mime,
                "data": base64.standard_b64encode(candidate).decode("ascii"),
            },
        }
    ]
    for data, mime in references[:MAX_IMAGES]:
        if data:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": (mime or "image/jpeg"),
                        "data": base64.standard_b64encode(data).decode("ascii"),
                    },
                }
            )
    content.append({"type": "text", "text": _CANONICAL_QA_PROMPT})

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model_name(),
            max_tokens=2048,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": CANONICAL_QA_SCHEMA}},
        )
    except Exception:
        logger.warning("VLM 정본 QA 호출 실패", exc_info=True)
        return None
    if getattr(response, "stop_reason", None) == "refusal":
        return None
    try:
        text = next(b.text for b in response.content if b.type == "text")
        result = json.loads(text)
    except Exception:
        logger.warning("VLM 정본 QA 응답 파싱 실패", exc_info=True)
        return None
    result["source"] = VLM_CANONICAL_QA_VERSION
    result["model"] = getattr(response, "model", model_name())
    return result


# ══════════════════════════════════════════════════════════════════════════
# 액션 키프레임 QA (Phase 5) — 정본 QA + 포즈 확인
# ══════════════════════════════════════════════════════════════════════════

VLM_KEYFRAME_QA_VERSION = "vlm-keyframe-qa-v1"

KEYFRAME_QA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **CANONICAL_QA_SCHEMA["properties"],
        "pose_matches": _YNU,
        "pose_confidence": _CONF,
        "body_orientation_ok": _YNU,
        "required_regions_visible": _YNU,
    },
    "required": CANONICAL_QA_SCHEMA["required"]
    + ["pose_matches", "pose_confidence", "body_orientation_ok", "required_regions_visible"],
    "additionalProperties": False,
}

_KEYFRAME_QA_PROMPT_TEMPLATE = (
    "The FIRST image is a GENERATED action-keyframe candidate of a pet. The "
    "following images are the pet's canonical reference and/or real photos.\n"
    "Requested pose for this keyframe: {required_pose}\n"
    "Required visible regions: {required_visibility}\n"
    "Judge strictly:\n"
    "- same_pet: is the candidate clearly the SAME individual pet (coat, markings, "
    "ear shape, facial proportions)?\n"
    "- pose_matches: does the candidate actually show the requested pose (not the "
    "reference pose, not a different pose)?\n"
    "- body_orientation_ok: is the body orientation natural and appropriate for "
    "the requested pose?\n"
    "- required_regions_visible: are the required regions listed above visible?\n"
    "- anatomy_plausible: correct limb count, natural joints, no merged/extra parts.\n"
    "- The candidate must contain one pet, no human, plain neutral background, no "
    "scene objects.\n"
    '- Use "unknown" whenever evidence is insufficient. Be conservative.'
)


def qa_action_keyframe(
    candidate: bytes,
    references: Sequence[tuple[bytes, str]],
    *,
    required_pose: str,
    required_visibility: Sequence[str] = (),
    candidate_mime: str = "image/png",
) -> Optional[dict[str, Any]]:
    """키프레임 후보 vs 정본/실제 레퍼런스 + 요구 포즈 — 구조화 QA. 실패/비활성 None."""
    if not is_enabled() or not candidate:
        return None
    try:
        import anthropic
    except ImportError:
        logger.warning("PET_VLM_IDENTITY_ENABLED=1 이지만 anthropic 패키지가 없습니다.")
        return None

    content: list[dict[str, Any]] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": candidate_mime,
                "data": base64.standard_b64encode(candidate).decode("ascii"),
            },
        }
    ]
    for data, mime in references[:MAX_IMAGES]:
        if data:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": (mime or "image/jpeg"),
                        "data": base64.standard_b64encode(data).decode("ascii"),
                    },
                }
            )
    content.append(
        {
            "type": "text",
            "text": _KEYFRAME_QA_PROMPT_TEMPLATE.format(
                required_pose=required_pose,
                required_visibility=", ".join(required_visibility) or "(none specified)",
            ),
        }
    )

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model_name(),
            max_tokens=2048,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": KEYFRAME_QA_SCHEMA}},
        )
    except Exception:
        logger.warning("VLM 키프레임 QA 호출 실패", exc_info=True)
        return None
    if getattr(response, "stop_reason", None) == "refusal":
        return None
    try:
        text = next(b.text for b in response.content if b.type == "text")
        result = json.loads(text)
    except Exception:
        logger.warning("VLM 키프레임 QA 응답 파싱 실패", exc_info=True)
        return None
    result["source"] = VLM_KEYFRAME_QA_VERSION
    result["model"] = getattr(response, "model", model_name())
    return result


# ══════════════════════════════════════════════════════════════════════════
# 모션 비디오 QA (Phase 6) — 샘플 프레임 시퀀스에 대한 확인
# ══════════════════════════════════════════════════════════════════════════

VLM_MOTION_QA_VERSION = "vlm-motion-qa-v1"

MOTION_QA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "same_pet_all_frames": _YNU,
        "anatomy_plausible_all_frames": _YNU,
        "requested_motion_occurs": _YNU,
        "unintended_large_motion": _YNU,
        "single_pet": _YNU,
        "duplicated_pet": _YNU,
        "human_present": _YNU,
        "scene_cut": _YNU,
        "major_flicker": _YNU,
        "camera_stable": _YNU,
        "background_neutral": _YNU,
        "ends_in_target_pose": _YNU,
        "notes": {"type": "string"},
    },
    "required": [
        "same_pet_all_frames",
        "anatomy_plausible_all_frames",
        "requested_motion_occurs",
        "unintended_large_motion",
        "single_pet",
        "duplicated_pet",
        "human_present",
        "scene_cut",
        "major_flicker",
        "camera_stable",
        "background_neutral",
        "ends_in_target_pose",
        "notes",
    ],
    "additionalProperties": False,
}

_MOTION_QA_PROMPT_TEMPLATE = (
    "You are judging a GENERATED pet motion video via {n} frames sampled in "
    "chronological order at fractions {fractions} of its duration. The LAST "
    "supplied image (after the sampled frames) is the pet's reference keyframe"
    "{target_note}.\n"
    "Requested motion: {motion_description}\nMotion class: {motion_class}\n"
    "Judge strictly across ALL frames:\n"
    "- same_pet_all_frames: identical individual pet in every frame (coat, "
    "markings, ears, face)?\n"
    "- anatomy_plausible_all_frames: correct limbs, no melting/merging/extra parts "
    "in any frame?\n"
    "- requested_motion_occurs: does the requested motion visibly happen?\n"
    "- unintended_large_motion: any large movement beyond what was requested?\n"
    "- duplicated_pet / scene_cut / major_flicker / camera_stable / "
    "background_neutral: temporal and composition quality.\n"
    "- ends_in_target_pose: only when a target pose is specified{target_note2}; "
    'otherwise "unknown".\n'
    '- Use "unknown" whenever the frames are insufficient to judge. Be conservative.'
)


def qa_motion_video(
    frame_images: Sequence[tuple[bytes, str]],
    *,
    motion_description: str,
    motion_class: str,
    sample_fractions: Sequence[float],
    reference_image: Optional[tuple[bytes, str]] = None,
    target_image: Optional[tuple[bytes, str]] = None,
) -> Optional[dict[str, Any]]:
    """샘플 프레임들 + 레퍼런스 → 구조화 모션 QA. 실패/비활성은 None."""
    if not is_enabled() or not frame_images:
        return None
    try:
        import anthropic
    except ImportError:
        logger.warning("PET_VLM_IDENTITY_ENABLED=1 이지만 anthropic 패키지가 없습니다.")
        return None

    content: list[dict[str, Any]] = []
    for data, mime in frame_images[:6]:
        if data:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": (mime or "image/png"),
                        "data": base64.standard_b64encode(data).decode("ascii"),
                    },
                }
            )
    for extra in (reference_image, target_image):
        if extra and extra[0]:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": (extra[1] or "image/png"),
                        "data": base64.standard_b64encode(extra[0]).decode("ascii"),
                    },
                }
            )
    if not content:
        return None
    target_note = (
        "; the very last image is the TARGET pose keyframe" if target_image else ""
    )
    content.append(
        {
            "type": "text",
            "text": _MOTION_QA_PROMPT_TEMPLATE.format(
                n=min(len(frame_images), 6),
                fractions=list(sample_fractions),
                motion_description=motion_description,
                motion_class=motion_class,
                target_note=target_note,
                target_note2=(" (last image)" if target_image else ""),
            ),
        }
    )

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model_name(),
            max_tokens=2048,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": MOTION_QA_SCHEMA}},
        )
    except Exception:
        logger.warning("VLM 모션 QA 호출 실패", exc_info=True)
        return None
    if getattr(response, "stop_reason", None) == "refusal":
        return None
    try:
        text = next(b.text for b in response.content if b.type == "text")
        result = json.loads(text)
    except Exception:
        logger.warning("VLM 모션 QA 응답 파싱 실패", exc_info=True)
        return None
    result["source"] = VLM_MOTION_QA_VERSION
    result["model"] = getattr(response, "model", model_name())
    return result
