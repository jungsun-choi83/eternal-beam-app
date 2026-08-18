"""
idle 검증의 배경색 정합 테스트.

버그: `_load_rgb_thumbnail` 이 RGBA 누끼를 `.convert("RGB")` 로 열어 알파를 버렸다.
투명 영역의 RGB 는 보통 (0,0,0) 이라 레퍼런스 배경이 통째로 검정이 된다. 반면
생성 영상의 첫 프레임은 keyframe 배경을 갖고 있고, 어두운 강아지는 흰 배경으로
간다(resolve_keyframe_bg_rgb). 결과적으로 "검정 배경 레퍼런스 vs 흰 배경 프레임"
을 비교하게 되어 SSIM 이 0 근처로 무너졌다(실측 -0.034 < 임계 0.72).

→ 어두운 강아지는 항상 검증 실패 → generate.py 가 유료 Luma 재생성을 한 번 더 태움.

수정: 알파가 있으면 keyframe 과 같은 배경 위에 합성한 뒤 비교한다.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from backend.services.idle_validation_service import (
    DEFAULT_SSIM_THRESHOLD,
    compare_reference_to_frame,
)
from backend.services.luma_keyframe import (
    BG_BLACK,
    BG_WHITE,
    flatten_rgba_to_jpeg_bytes,
    resolve_keyframe_bg_rgb,
)


@pytest.fixture(autouse=True)
def _no_forced_keyframe_bg(monkeypatch: pytest.MonkeyPatch):
    """LUMA_KEYFRAME_BG 가 로컬 .env 에서 새어 들어오면 자동 판정이 무력화된다."""
    monkeypatch.delenv("LUMA_KEYFRAME_BG", raising=False)


def _subject_rgba(rgb: tuple[int, int, int], w: int = 200, h: int = 300) -> bytes:
    """가운데 70% 를 채운 불투명 피사체 + 나머지는 완전 투명인 RGBA PNG."""
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    y0, y1 = int(h * 0.15), int(h * 0.85)
    x0, x1 = int(w * 0.15), int(w * 0.85)
    arr[y0:y1, x0:x1, 0] = rgb[0]
    arr[y0:y1, x0:x1, 1] = rgb[1]
    arr[y0:y1, x0:x1, 2] = rgb[2]
    arr[y0:y1, x0:x1, 3] = 255
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


DARK_DOG = _subject_rgba((30, 25, 20))    # 평균 휘도 낮음 → keyframe 흰 배경
LIGHT_DOG = _subject_rgba((220, 200, 180))  # 평균 휘도 높음 → keyframe 검정 배경


def _keyframe_like_pipeline(reference: bytes) -> bytes:
    """generate.py / luma_idle_pipeline.py 와 동일하게 만든 keyframe JPEG."""
    return flatten_rgba_to_jpeg_bytes(reference, bg_rgb=resolve_keyframe_bg_rgb(reference))


# ── 배경 판정이 기대대로인지 먼저 못박는다 ────────────────────────────────────


def test_dark_subject_resolves_to_white_keyframe_background():
    assert resolve_keyframe_bg_rgb(DARK_DOG) == BG_WHITE


def test_light_subject_resolves_to_black_keyframe_background():
    assert resolve_keyframe_bg_rgb(LIGHT_DOG) == BG_BLACK


# ── 핵심 회귀: 어두운 강아지가 더 이상 자동 실패하지 않는다 ──────────────────


def test_dark_dog_passes_against_its_white_background_frame():
    """이 케이스가 수정 전에는 SSIM -0.034 로 항상 실패했다."""
    score = compare_reference_to_frame(DARK_DOG, _keyframe_like_pipeline(DARK_DOG))
    assert score is not None
    assert score >= DEFAULT_SSIM_THRESHOLD, f"어두운 강아지가 여전히 실패한다: {score}"


def test_light_dog_still_passes_against_its_black_background_frame():
    """밝은 강아지는 원래도 통과했다 — 회귀가 없는지 확인."""
    score = compare_reference_to_frame(LIGHT_DOG, _keyframe_like_pipeline(LIGHT_DOG))
    assert score is not None
    assert score >= DEFAULT_SSIM_THRESHOLD


def test_background_mismatch_is_what_used_to_break_it():
    """
    배경을 일부러 어긋나게 주면 옛날 증상이 재현된다 —
    즉 통과의 원인이 '임계값을 느슨하게 해서'가 아니라 '배경을 맞춰서'임을 보인다.
    """
    matched = compare_reference_to_frame(DARK_DOG, _keyframe_like_pipeline(DARK_DOG))
    mismatched = compare_reference_to_frame(
        DARK_DOG, _keyframe_like_pipeline(DARK_DOG), background_rgb=BG_BLACK
    )
    assert mismatched < DEFAULT_SSIM_THRESHOLD < matched


# ── 느슨해지지 않았는지: 진짜 다른 피사체는 여전히 떨어져야 한다 ─────────────


def test_different_subject_still_fails():
    frame = _keyframe_like_pipeline(LIGHT_DOG)
    score = compare_reference_to_frame(DARK_DOG, frame, background_rgb=BG_BLACK)
    assert score is not None
    assert score < DEFAULT_SSIM_THRESHOLD


def test_empty_alpha_reference_does_not_pass():
    """완전 투명 누끼(알파 0)는 배경만 남으므로 실제 피사체와 일치하면 안 된다."""
    blank = _subject_rgba((0, 0, 0))
    blank_arr = np.zeros((300, 200, 4), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(blank_arr, mode="RGBA").save(buf, format="PNG")
    score = compare_reference_to_frame(buf.getvalue(), _keyframe_like_pipeline(DARK_DOG))
    assert score is None or score < DEFAULT_SSIM_THRESHOLD
    del blank


# ── 알파 없는 입력은 동작이 바뀌면 안 된다 (프레임 vs 프레임 비교 경로) ──────


def test_non_alpha_inputs_are_unchanged():
    frame = _keyframe_like_pipeline(LIGHT_DOG)
    assert compare_reference_to_frame(frame, frame) == pytest.approx(1.0, abs=1e-6)


def test_explicit_background_override_is_honoured():
    """호출자가 배경을 알고 있으면 자동 판정 대신 그 값을 쓴다."""
    on_white = compare_reference_to_frame(
        DARK_DOG, flatten_rgba_to_jpeg_bytes(DARK_DOG, bg_rgb=BG_WHITE), background_rgb=BG_WHITE
    )
    on_black = compare_reference_to_frame(
        DARK_DOG, flatten_rgba_to_jpeg_bytes(DARK_DOG, bg_rgb=BG_BLACK), background_rgb=BG_BLACK
    )
    assert on_white >= DEFAULT_SSIM_THRESHOLD
    assert on_black >= DEFAULT_SSIM_THRESHOLD
