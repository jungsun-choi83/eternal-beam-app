"""BREATHING 시간축 QA — 합성 클립으로 판정 계약을 고정한다 (ffmpeg 불필요)."""

from __future__ import annotations

import numpy as np
import pytest

from backend.services import breathing_temporal_qa as bt
from backend.services import motion_video_qa as qa

H, W = 398, 224  # 분석 해상도 그대로 생성 — 리사이즈 무영향
FPS = 4.8  # 24프레임 / 5초
N = 24

rng = np.random.default_rng(11)
_PET_TEX = rng.normal(0, 22, (H, W)).astype(np.float32)  # 정합/스케일 추정용 텍스처


def _frame(chest_dx: float = 0.0, scale: float = 1.0, shift: float = 0.0) -> np.ndarray:
    """중립 회색 배경 + 텍스처 있는 펫 사각형. chest_dx = 흉곽 밴드 가장자리 확장(px)."""
    from PIL import Image

    img = np.full((H, W), 200.0, dtype=np.float32)
    # 펫: 중앙 사각형 (앉은 개 근사) rows 90..330, cols 70..154
    top, bottom, left, right = 90, 330, 70, 154
    pet = np.zeros((H, W), dtype=bool)
    pet[top:bottom, left:right] = True
    img[pet] = 120.0 + _PET_TEX[pet]
    # 흉곽 밴드(펫 bbox 세로 32~75% ≈ rows 167..270)의 좌우 윤곽을 확장/수축.
    if abs(chest_dx) > 1e-6:
        band_lo, band_hi = 167, 270
        w_delta = int(round(abs(chest_dx)))
        if w_delta > 0 and chest_dx > 0:
            img[band_lo:band_hi, left - w_delta:left] = 120.0
            img[band_lo:band_hi, right:right + w_delta] = 120.0
    out = np.clip(img, 0, 255)
    if scale != 1.0 or shift != 0.0:
        im = Image.fromarray(out.astype(np.uint8))
        a = 1.0 / scale
        cx, cy = W / 2, H / 2
        matrix = (a, 0.0, cx - a * cx - shift, 0.0, a, cy - a * cy)
        out = np.asarray(
            im.transform((W, H), Image.AFFINE, matrix, resample=Image.BILINEAR),
            dtype=np.float32,
        )
        # 워프 밖 영역은 배경색으로.
        out[out == 0] = 200.0
    rgb = np.repeat(out[:, :, None], 3, axis=2)
    return np.clip(rgb + rng.normal(0, 0.6, rgb.shape), 0, 255).astype(np.uint8)


KEYFRAME = _frame()


def _clip(kind: str) -> list[np.ndarray]:
    frames = []
    for i in range(N):
        t = i / FPS
        phase = 2 * np.pi * t / 2.5  # 2.5초 주기 — 스펙의 2~3초/호흡
        if kind == "breathing":
            frames.append(_frame(chest_dx=2.0 * max(0.0, np.sin(phase))))
        elif kind == "static":
            frames.append(_frame())
        elif kind == "pulse":
            frames.append(_frame(scale=1.0 + 0.012 * np.sin(phase)))
        elif kind == "drift":
            frames.append(_frame(shift=0.35 * i))
        else:
            raise AssertionError(kind)
    return frames


# ══════════════════════════════════════════════════════════════════════════
# 분석 판정
# ══════════════════════════════════════════════════════════════════════════


def test_localized_periodic_breathing_is_detected():
    r = bt.analyze_frames(_clip("breathing"), FPS, KEYFRAME)
    assert r["verdict"] == bt.VERDICT_BREATHING, r
    m = r["metrics"]
    assert m["torso_snr"] >= 1.6
    assert m["periodic_score"] >= 0.25
    assert m["scale_range"] <= 0.010
    # 주기 추정이 실제 주기(2.5s) 근처다.
    assert 1.5 <= (m["estimated_period_sec"] or 0) <= 3.5


def test_static_clip_is_no_motion():
    r = bt.analyze_frames(_clip("static"), FPS, KEYFRAME)
    assert r["verdict"] == bt.VERDICT_NO_MOTION, r


def test_whole_body_scale_pulse_is_rejected():
    r = bt.analyze_frames(_clip("pulse"), FPS, KEYFRAME)
    assert r["verdict"] == bt.VERDICT_GLOBAL_PULSE, r
    assert r["metrics"]["scale_range"] > 0.010


def test_framing_drift_is_rejected():
    r = bt.analyze_frames(_clip("drift"), FPS, KEYFRAME)
    assert r["verdict"] == bt.VERDICT_GLOBAL_PULSE, r
    assert r["metrics"]["translation_drift_frac_of_pet"] > 0.020


def test_unmeasurable_paths_claim_nothing():
    assert bt.analyze_frames([], FPS, KEYFRAME)["verdict"] == bt.VERDICT_UNMEASURABLE
    flat = np.full((H, W, 3), 200, dtype=np.uint8)  # 펫 없음 → 마스크 실패
    r = bt.analyze_frames(_clip("static"), FPS, flat)
    assert r["verdict"] == bt.VERDICT_UNMEASURABLE
    assert bt.analyze(b"", KEYFRAME)["verdict"] == bt.VERDICT_UNMEASURABLE


# ══════════════════════════════════════════════════════════════════════════
# 판정 정책 (motion-video-qa-v3) — 시간축 증거의 사용 규칙
# ══════════════════════════════════════════════════════════════════════════

_CONTRACT = {"motion_id": "BREATHING", "motion_class": "MICRO",
             "video_compat": {"returns_to_start_pose": True}}


def _vlm(motion: str) -> dict:
    return {
        "same_pet_all_frames": "yes", "anatomy_plausible_all_frames": "yes",
        "requested_motion_occurs": motion, "unintended_large_motion": "no",
        "duplicated_pet": "no", "scene_cut": "no", "human_present": "no",
        "major_flicker": "no", "camera_stable": "yes", "background_neutral": "yes",
        "single_pet": "yes", "ends_in_target_pose": "unknown",
    }


def _evaluate(vlm_motion: str, temporal: dict | None) -> dict:
    img = KEYFRAME
    return qa.evaluate_motion_video(
        frames=[img, img, img],
        spec_contract=_CONTRACT,
        start_keyframe_rgb=img,
        target_keyframe_rgb=None,
        vlm_qa=_vlm(vlm_motion),
        temporal_qa=temporal,
    )


def test_temporal_evidence_resolves_vlm_unknown_to_pass():
    out = _evaluate("unknown", {"verdict": bt.VERDICT_BREATHING})
    assert out["checks"]["vlm_motion"] == "PASS"
    assert out["checks"]["temporal_breathing"] == "PASS"
    assert out["decision"] == "PASS"
    assert "vlm_motion_resolved_by_temporal_evidence" in out["reasons"]
    assert out["temporal"]["verdict"] == bt.VERDICT_BREATHING


def test_vlm_no_still_fails_despite_temporal_evidence():
    out = _evaluate("no", {"verdict": bt.VERDICT_BREATHING})
    assert out["checks"]["vlm_motion"] == "FAIL"
    assert out["decision"] == "FAIL"


def test_global_pulse_blocks_promotion_even_when_vlm_says_yes():
    out = _evaluate("yes", {"verdict": bt.VERDICT_GLOBAL_PULSE, "reason": "scale_range"})
    assert out["checks"]["temporal_breathing"] == "REVIEW"
    assert out["decision"] == "REVIEW"
    assert any("temporal_global_pulse" in r for r in out["reasons"])


def test_inconclusive_temporal_never_blocks_a_vlm_confirmed_pass():
    out = _evaluate("yes", {"verdict": bt.VERDICT_INCONCLUSIVE})
    assert "temporal_breathing" not in out["checks"]
    assert out["decision"] == "PASS"


def test_no_temporal_evidence_keeps_v2_behavior():
    out = _evaluate("unknown", None)
    assert out["checks"]["vlm_motion"] == "unknown"
    assert out["decision"] == "REVIEW"
    assert out["temporal"] is None
