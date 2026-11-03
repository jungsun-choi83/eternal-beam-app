"""
AI 포즈 정규화 — 모든 전시 업로드에 한 번씩 실행하는 전시 전용 생성 호출.

같은 펫 신원(코트 색상·무늬·털·귀·얼굴형·몸통 비례)은 보존하면서, 전시용 고정
포즈(전신, 자연스럽게 서 있음, 몸은 카메라에서 30–45°, 머리는 카메라를 봄,
고정된 프레이밍·지면선, 무지 중립 배경)로 다시 생성한다.

일반 파이프라인(정본·루마·키프레임·모션)과는 완전히 분리된 모듈이다 — 정본 빌더·
키프레임·QA·크레딧은 import 하지 않고, exhibition_pose_gate 가 만든 판정 레코드
(subject_class 만 읽는다)만 받는다. 유일한 공유 지점은 프로바이더 어댑터
canonical_image_providers.GptImageProvider (OpenAI /v1/images/edits, 원본 1장을
레퍼런스로 동기 호출) — HTTP 는 그 어댑터 안에서만 일어난다 (test_exhibition_prep_run.py
의 격리 테스트가 이 import 하나만 허용한다).

YOLO + CLIP 포즈 게이트 결과는 입력 분석/감사 기록으로만 쓰고, 생성 여부를
결정하지 않는다. 생성본에 대한 신원·포즈·VLM QA는 없다. 성공한 생성본은
기존 CUTOUT → MAPS → PACKAGE 경로로 바로 넘어간다.

  EXHIBITION_POSE_NORMALIZATION_MODE
    gpt_image  GptImageProvider 로 1회 생성 (OPENAI_API_KEY / OPENAI_IMAGE_MODEL /
               OPENAI_API_BASE / OPENAI_IMAGE_TIMEOUT_SEC — 정본 생성과 같은 설정)
    mock       원본을 그대로 돌려줌 (오프라인 개발/테스트)
    off        (기본) 설정 안 됨 → NORMALIZATION_NOT_CONFIGURED (실행 FAILED)

process_run 은 PoseNormalizationError 를 FAILED 로 기록하고 CUTOUT 을 시작하지 않는다.
원본을 대신 패키징하지 않아 모든 READY 패키지가 표준화 생성본을 거치게 한다.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

MODE_GPT_IMAGE = "gpt_image"
MODE_MOCK = "mock"
MODE_OFF = "off"
MODES = (MODE_GPT_IMAGE, MODE_MOCK, MODE_OFF)

#: 생성 출력 — GPT Image edits 의 정사각 기본값. 전시 패키지는 어차피 피사체 크롭이라 비율은 무관하다.
OUTPUT_SPEC: dict[str, Any] = {"size": "1024x1024", "ratio": "1024:1024", "purpose": "exhibition_pose_normalization"}

NORMALIZATION_PROMPT_TEMPLATE = (
    "Create a full-body studio portrait of the exact same {subject_class} in a standardized "
    "STAND_READY pose. STAND_READY means a natural, static standing pose with balanced weight "
    "and the body held at a clear three-quarter orientation, approximately 30–45 degrees from "
    "the camera. For dogs and cats, all four paws must be visible and firmly grounded on the same "
    "level ground plane, with the legs naturally extended, the head upright, and the eyes looking "
    "toward the camera. For any other pet, preserve its species-appropriate natural standing stance "
    "and anatomy while keeping the head upright and directed toward the camera. "
    "Keep the entire pet visible from ear tips to paws and tail, with no cropped body parts. "
    "Use a consistent eye-level camera, centered framing, consistent subject scale, feet aligned "
    "on one horizontal ground line, a plain neutral light-gray solid-color background, even soft "
    "studio lighting, a natural contact shadow only, no props, no people, and no text. "
    "Preserve the exact individual identity from the reference photo: original coat colors, exact "
    "marking shapes and positions, fur length and texture, ear shape, face shape, muzzle, eyes, tail, "
    "body build, body proportions, and species-specific anatomy. Do not idealize the breed, alter "
    "anatomy, add markings, or redesign the pet. Do not depict the pet sitting, lying down, walking, "
    "running, jumping, or in an exaggerated action pose. Do not lift any paw for dogs or cats."
)


def build_normalization_prompt(context: dict[str, Any]) -> str:
    """context(게이트 레코드)의 subject_class 만 반영한 고정 템플릿 (포즈/배경은 항상 동일)."""
    subject_class = str(context.get("subject_class") or "pet").strip() or "pet"
    return NORMALIZATION_PROMPT_TEMPLATE.format(subject_class=subject_class)


class PoseNormalizationError(Exception):
    """생성 실패/미설정 — process_run 은 실행을 FAILED 로 마친다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def mode_from_env() -> str:
    mode = (os.getenv("EXHIBITION_POSE_NORMALIZATION_MODE") or "").strip().lower()
    return mode if mode in MODES else MODE_OFF


_mode_from_env = mode_from_env  # 하위 호환 (이전 이름)


def _gpt_provider() -> Any:
    """GptImageProvider 인스턴스 — 테스트는 이 함수를 monkeypatch 해 가짜 프로바이더를 넣는다."""
    from .canonical_image_providers import GptImageProvider

    return GptImageProvider()


def generator_info() -> dict[str, Any]:
    """manifest 기록용 — 어떤 생성기 설정으로 돌았는가 (호출 전에도 안전하게 읽을 수 있다)."""
    mode = mode_from_env()
    info: dict[str, Any] = {"mode": mode}
    if mode == MODE_GPT_IMAGE:
        try:
            provider = _gpt_provider()
            info.update(provider=provider.name, model=provider.model_name(), output_spec=dict(OUTPUT_SPEC))
        except Exception as e:  # noqa: BLE001 — 기록용, 실패해도 실행을 막지 않는다
            info["provider_error"] = type(e).__name__
    return info


def _normalize_with_gpt_image(reference_png: bytes, prompt: str, context: dict[str, Any]) -> bytes:
    from .canonical_image_providers import CanonicalProviderError, CanonicalReference

    provider = _gpt_provider()
    if not provider.available():
        raise PoseNormalizationError(
            "NORMALIZATION_NOT_CONFIGURED",
            "EXHIBITION_POSE_NORMALIZATION_MODE=gpt_image but OPENAI_API_KEY is not set.",
        )
    references = [
        CanonicalReference(
            reference_id="exhibition:source", role="PRIMARY_SOURCE", data=reference_png, mime_type="image/png"
        )
    ]
    metadata = {
        "purpose": "exhibition_pose_normalization",
        "subject_class": str(context.get("subject_class") or ""),
        "gate_decision": str(context.get("decision") or ""),
        "gate_top_label": str(context.get("top_label") or ""),
    }
    try:
        result = provider.generate(references, prompt, dict(OUTPUT_SPEC), metadata)
    except CanonicalProviderError as e:
        raise PoseNormalizationError(e.code, e.message) from e
    except Exception as e:  # noqa: BLE001 — 어댑터 밖의 예외도 일관된 도메인 오류로 바꾼다
        raise PoseNormalizationError("NORMALIZATION_GENERATOR_ERROR", f"{type(e).__name__}: {e}") from e
    image = getattr(result, "image_bytes", None)
    if not image:
        raise PoseNormalizationError("NORMALIZATION_EMPTY_RESULT", "Generator returned no image bytes.")
    logger.info(
        "exhibition pose normalization [gpt_image] provider=%s model=%s external_id=%s bytes=%d",
        getattr(result, "provider", "?"), getattr(result, "model", "?"),
        getattr(result, "external_job_id", None), len(image),
    )
    return image


def default_normalize(reference_png: bytes, context: dict[str, Any]) -> bytes:
    """기본 생성기 — EXHIBITION_POSE_NORMALIZATION_MODE 에 따라 1회 생성한다.

    gpt_image: GptImageProvider(OpenAI images/edits), 원본 PNG 1장을 레퍼런스로.
    mock:      원본을 그대로 돌려줌.   off(기본): NORMALIZATION_NOT_CONFIGURED.
    실패는 전부 PoseNormalizationError — 호출자는 패키징 전에 실행을 중단한다.
    """
    prompt = build_normalization_prompt(context)
    mode = mode_from_env()
    if mode == MODE_GPT_IMAGE:
        return _normalize_with_gpt_image(reference_png, prompt, context)
    if mode == MODE_MOCK:
        logger.info("exhibition pose normalization [mock] prompt=%r", prompt[:120])
        return reference_png
    raise PoseNormalizationError(
        "NORMALIZATION_NOT_CONFIGURED",
        "No pose-normalization generator configured — set EXHIBITION_POSE_NORMALIZATION_MODE=gpt_image "
        "(OpenAI) or mock (no-op placeholder), or pass process_run(normalize_fn=...).",
    )
