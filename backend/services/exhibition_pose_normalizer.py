"""
AI 포즈 정규화 — NEEDS_AI_NORMALIZATION 판정일 때만 쓰는 전시 전용 생성 호출.

같은 펫 신원(코트 색상·무늬·귀·얼굴형·몸통 비례)은 보존하면서, 전시용 고정
포즈(전신, 서있거나 깔끔히 앉은 자세, 정면/약한 3/4 측면, 머리 또렷이 보임,
깨끗한 실루엣, 무지 중립 배경)로 다시 생성한다.

일반 파이프라인(정본·루마·키프레임·모션)과는 완전히 분리된 모듈이다 — 이 파일은
그 어떤 일반 생성 모듈도 import 하지 않고, exhibition_prep_service.py 가 이미
만든 manifest 만 읽는다. 실제 이미지 생성 호출(generate_fn)은 주입식이다 — 이
파일 자체는 HTTP/외부 API 를 직접 걸지 않는다(그 역할은 exhibition_handoff_client.py
하나로 고정돼 있다 — test_exhibition_prep_run.py 의 격리 테스트 참고). 실제
생성기를 연결하려면 호출자가 generate_fn 을 주입한다; 기본값은 mock(원본을 그대로
돌려줌) 또는 off(설정 안 됨 에러)뿐이다.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

MODE_MOCK = "mock"
MODE_OFF = "off"

NORMALIZATION_PROMPT_TEMPLATE = (
    "Full-body studio portrait of the same {subject_class}, standing or neatly seated, "
    "facing forward or at a slight three-quarter angle, head clearly visible and upright, "
    "clean sharp silhouette with no cropped limbs, plain neutral solid-color background, "
    "even soft lighting, no props, no people, no text. "
    "Preserve the exact same individual: identical coat colors and markings, ear shape, "
    "face shape, and body proportions as the reference photo. Do not invent a different "
    "pose, breed, or markings — this is a pose/orientation correction, not a redesign."
)


def build_normalization_prompt(manifest: dict[str, Any]) -> str:
    """manifest 의 subject_class 만 반영한 고정 템플릿 (포즈/배경은 항상 동일)."""
    subject_class = str(manifest.get("subject_class") or "pet").strip() or "pet"
    return NORMALIZATION_PROMPT_TEMPLATE.format(subject_class=subject_class)


class PoseNormalizationError(Exception):
    """생성 실패/미설정 — process_run 은 이걸 잡아 원본 이미지로 안전하게 되돌아간다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _mode_from_env() -> str:
    mode = (os.getenv("EXHIBITION_POSE_NORMALIZATION_MODE") or "").strip().lower()
    return mode if mode in (MODE_MOCK, MODE_OFF) else MODE_OFF


def default_normalize(reference_png: bytes, manifest: dict[str, Any]) -> bytes:
    """기본 생성기 — 실제 생성 API 연결 전까지의 자리. 운영에선 process_run(normalize_fn=...)
    으로 진짜 생성기를 주입한다; 이 함수는 EXHIBITION_POSE_NORMALIZATION_MODE=mock 일 때만
    (원본을 그대로 돌려줌, 오프라인 개발/테스트용) 동작하고, 그 외엔 설정 안 됨으로 실패한다.
    """
    prompt = build_normalization_prompt(manifest)
    mode = _mode_from_env()
    if mode == MODE_MOCK:
        logger.info("exhibition pose normalization [mock] prompt=%r", prompt[:120])
        return reference_png
    raise PoseNormalizationError(
        "NORMALIZATION_NOT_CONFIGURED",
        "No pose-normalization generator configured — set EXHIBITION_POSE_NORMALIZATION_MODE=mock "
        "for a no-op placeholder, or pass process_run(normalize_fn=...) with a real generator.",
    )
