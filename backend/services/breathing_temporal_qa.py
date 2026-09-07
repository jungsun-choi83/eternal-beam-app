"""
BREATHING 전용 시간축 QA (Phase 6 보강) — 희소 스틸로는 판정 불가능한 것을 잰다.

── 왜 필요한가 (라이브 v1/v2 실측) ──────────────────────────────────────────
두 실제 후보 모두 단 하나의 체크(vlm_motion=unknown)로 REVIEW 에 갇혔다.
0.5초 간격 스틸 9장으로는 1~2px 의 흉곽 운동을 압축 노이즈와 구분할 수 없고,
VLM 은 보수적으로 답하라고 지시받는다 — unknown 이 구조적 정상 상태였다.
동시에 VLM 소견은 두 클립 모두에서 **전신 줌/프레이밍 드리프트**(가짜 호흡)를
기록했지만 그것을 잴 결정론 계기가 없었다.

이 모듈이 그 계기다:

    조밀 샘플링 → 인접쌍 부화소 전역 정합(이동+스케일)
      → 전역 성분 = 펄스/드리프트 검출기 (스케일 진동 = 전신 펄스)
      → 정합 후 잔차 = 호흡 신호 (흉곽 밴드 국소성 + 주기성-ish)

── 판정 계약 ────────────────────────────────────────────────────────────────
  breathing_detected  흉곽 밴드에 배경 대비 유의한 주기적-ish 잔차 운동,
                      전역 펄스/드리프트/머리 요동 없음
  global_pulse        전역 스케일 진동 또는 프레이밍 드리프트가 임계 초과
  unlocalized_motion  운동이 흉곽이 아니라 머리 쪽에 몰려 있다 (head bobbing)
  no_motion           흉곽 잔차가 배경 노이즈 바닥과 구분되지 않는다
  inconclusive        측정은 됐지만 어느 쪽도 확신할 수 없다
  unmeasurable        디코딩/마스크 실패 — 아무 주장도 하지 않는다

이 증거의 **사용 규칙은 motion_video_qa 가 쥔다**: VLM "no" 는 여전히 FAIL 이고,
시간축 증거는 unknown 만 해소하며, 상반된 VLM 증거를 절대 뒤집지 않는다.

~2 주기(4~5초 클립)에서 엄격한 스펙트럼 피크는 요구하지 않는다 — 자기상관의
양의 재발(periodic-ish)만 요구한다. 모든 임계는 env 로 조정 가능하다.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

# v2 (2026-09-06, 16클립 보정): 스케일은 **보조 증거**다 — 유일한 게이트가 아니다.
#   * 상한 1% → 2% (실측: 자연스러운 흉곽 국소 클립 1.16% 가 REVIEW 에 갇혔고,
#     진짜 전신 펄스는 전부 ≥3.3% 이거나 드리프트 >2% 로 따로 잡힌다)
#   * 새 신호: scale 시계열의 진동(osc, 추세 제거 후 p2p) vs 추세(trend) —
#     단조 수축/침하(trend 지배)는 호흡이 아니라 프레이밍 드리프트다
#   * 가시성 바닥 osc ≥ 0.3% — 동결·준정지 클립(0.18%)이 "스케일이 작다"는
#     이유만으로 breathing_detected 를 받지 못한다 (실측 분리: 0.18% vs 0.40%+)
#   * 리듬은 주기성 **또는** 시간 변조 — 완벽한 주기를 요구하지 않는다
#     (자연 클립 K: periodic 0.24, modulation 0.47)
#   * 중대역(구 1% 초과)은 머리/흉곽 국소성 상한을 1.0 으로 조인다 — 균일
#     전신 펄스가 중대역으로 통과할 잔여 위험의 완화 (관측 양성 전부 ≤0.83)
BREATHING_TEMPORAL_QA_VERSION = "breathing-temporal-qa-v2"

#: 분석 해상도(가로). 호흡 신호는 저주파 대비 변화라 다운스케일에 강하고,
#: 정합·NCC 비용은 해상도 제곱에 비례한다.
_ANALYSIS_WIDTH = 224
#: 조밀 샘플 프레임 수 (클립 전체에 균등). 5초 × 24 = ~4.8fps — 2~3초 주기의
#: 호흡을 주기당 10+ 샘플로 본다.
_DENSE_FRAMES = 24

VERDICT_BREATHING = "breathing_detected"
VERDICT_GLOBAL_PULSE = "global_pulse"
VERDICT_UNLOCALIZED = "unlocalized_motion"
VERDICT_NO_MOTION = "no_motion"
VERDICT_INCONCLUSIVE = "inconclusive"
VERDICT_UNMEASURABLE = "unmeasurable"


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


# ══════════════════════════════════════════════════════════════════════════
# 펫 마스크 — 시작 키프레임(중립 회색 배경 계약)에서 유도
# ══════════════════════════════════════════════════════════════════════════


def pet_mask_from_neutral_gray(keyframe_rgb: np.ndarray) -> Optional[np.ndarray]:
    """
    NEUTRAL_IDLE 키프레임 → 펫 마스크(bool). 테두리 중앙값 배경색 대비 거리
    키잉 — motion_delivery_service 의 bgmodel 과 같은 계약(평탄한 중립 회색)에
    기댄다. 마스크가 비정상(거의 전부/거의 없음)이면 None.
    """
    if keyframe_rgb is None or keyframe_rgb.ndim != 3:
        return None
    f = keyframe_rgb.astype(np.float32)
    edges = np.concatenate([f[0, :], f[-1, :], f[:, 0], f[:, -1]])
    bg = np.median(edges, axis=0)
    dist = np.abs(f - bg[None, None, :]).max(axis=2)
    mask = dist > _f("BREATHING_QA_MASK_KEY", 24.0)
    frac = float(mask.mean())
    if frac < 0.02 or frac > 0.90:
        return None
    return mask


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """불리언 마스크 팽창 (PIL MaxFilter — scipy 무의존)."""
    from PIL import Image, ImageFilter

    size = 2 * radius + 1
    im = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(size))
    return np.asarray(im, dtype=np.uint8) > 127


def _torso_bands(mask: np.ndarray) -> Optional[dict[str, np.ndarray]]:
    """
    펫 bbox 세로 비율로 머리/흉곽 밴드를 나눈다 (관측 프레이밍: 정면 앉음).

    밴드는 **팽창된** 마스크로 만든다 — 호흡의 실체는 휴지 실루엣 *바깥*으로의
    윤곽 팽창이라, 키프레임(휴지 자세) 마스크 그대로면 정작 호흡 픽셀이
    배경으로 계산된다. 배경 영역은 더 크게 판 여백 밖으로 잡는다.
    """
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return None
    top, bottom = int(ys.min()), int(ys.max())
    h = max(1, bottom - top)
    near = _dilate(mask, max(2, int(round(0.02 * h))))   # 윤곽 팽창 포함
    far = _dilate(mask, max(6, int(round(0.06 * h))))    # 배경 판정 여백
    head_lo, head_hi = top, top + int(0.30 * h)
    torso_lo, torso_hi = top + int(0.32 * h), top + int(0.75 * h)
    head = np.zeros_like(mask)
    head[head_lo:head_hi] = True
    head &= near
    torso = np.zeros_like(mask)
    torso[torso_lo:torso_hi] = True
    torso &= near
    background = ~far
    if torso.sum() < 50 or background.sum() < 200:
        return None
    return {"torso": torso, "head": head, "background": background, "pet_height_px": np.array([h])}


# ══════════════════════════════════════════════════════════════════════════
# 조밀 디코딩
# ══════════════════════════════════════════════════════════════════════════


def decode_dense_frames(video_bytes: bytes, count: int = _DENSE_FRAMES) -> Optional[tuple[list[np.ndarray], float]]:
    """영상 → 균등 간격 RGB 프레임 count 장 + 실측 fps(샘플 기준). 실패 시 None."""
    from PIL import Image

    from .motion_video_qa import _probe_duration

    if not video_bytes:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="eb_breath_qa_") as td:
            path = os.path.join(td, "input.mp4")
            with open(path, "wb") as f:
                f.write(video_bytes)
            duration = _probe_duration(path)
            if not duration or duration <= 0.5:
                return None
            fps = count / duration
            pattern = os.path.join(td, "d_%03d.png")
            r = subprocess.run(
                ["ffmpeg", "-y", "-v", "quiet", "-i", path,
                 "-vf", f"fps={fps:.4f}", "-frames:v", str(count), pattern],
                capture_output=True, timeout=120,
            )
            if r.returncode != 0:
                return None
            frames: list[np.ndarray] = []
            for name in sorted(os.listdir(td)):
                if not name.startswith("d_"):
                    continue
                with Image.open(os.path.join(td, name)) as im:
                    frames.append(np.asarray(im.convert("RGB"), dtype=np.uint8))
            if len(frames) < 12:
                return None
            return frames, fps
    except Exception:
        logger.warning("BREATHING 조밀 샘플링 실패", exc_info=True)
        return None


# ══════════════════════════════════════════════════════════════════════════
# 부화소 전역 정합 — 이동(위상 상관) + 스케일(NCC 스윕)
# ══════════════════════════════════════════════════════════════════════════


def _to_gray_small(rgb: np.ndarray, width: int = _ANALYSIS_WIDTH) -> np.ndarray:
    from PIL import Image

    im = Image.fromarray(rgb, "RGB").convert("L")
    h = max(1, round(im.height * width / im.width))
    return np.asarray(im.resize((width, h), Image.BILINEAR), dtype=np.float32)


def _phase_shift(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """b 가 a 대비 얼마나 이동했는가 (dx, dy) — 위상 상관 + 포물선 부화소."""
    h, w = a.shape
    win = np.outer(np.hanning(h), np.hanning(w))
    fa = np.fft.rfft2(a * win)
    fb = np.fft.rfft2(b * win)
    cross = fa * np.conj(fb)
    denom = np.abs(cross)
    denom[denom == 0] = 1.0
    corr = np.fft.irfft2(cross / denom, s=(h, w))
    peak = np.unravel_index(int(np.argmax(corr)), corr.shape)

    def _sub(idx: int, axis_len: int, line: np.ndarray) -> float:
        prev_v = line[(idx - 1) % axis_len]
        cur = line[idx]
        nxt = line[(idx + 1) % axis_len]
        denom2 = (prev_v - 2 * cur + nxt)
        frac = 0.0 if abs(denom2) < 1e-9 else 0.5 * (prev_v - nxt) / denom2
        pos = idx + max(-0.5, min(0.5, frac))
        return pos if pos <= axis_len / 2 else pos - axis_len

    dy = _sub(peak[0], h, corr[:, peak[1]])
    dx = _sub(peak[1], w, corr[peak[0], :])
    # 위상 상관의 부호: corr 피크는 "a 를 얼마나 옮기면 b 가 되는가"의 역방향.
    return -float(dx), -float(dy)


def _foreground_height(gray: np.ndarray, bg_level: float, key: float) -> Optional[float]:
    """
    전경(펫) 세로 크기 — 행별 전경 픽셀 수 프로파일의 2%~98% 누적 구간.

    NCC 스케일 스윕 대신 기하로 잰다: 잡음 텍스처에서 NCC 는 더 스무딩된
    쪽(확대)을 체계적으로 선호해 피크가 1.0 에서 밀린다(실측 ~0.2% 편향).
    전경 높이는 그런 편향이 없고, 전신 스케일 펄스는 높이를 바꾸지만 흉곽의
    **측면** 팽창(진짜 호흡)은 높이를 바꾸지 않는다 — 원하는 구분 그 자체다.
    """
    fg = np.abs(gray - bg_level) > key
    rows = fg.sum(axis=1).astype(np.float64)
    total = rows.sum()
    if total < 100:
        return None
    cum = np.cumsum(rows) / total
    top_idx = int(np.searchsorted(cum, 0.02))
    bot_idx = int(np.searchsorted(cum, 0.98))
    if bot_idx <= top_idx:
        return None
    # 경계 행 안에서의 선형 보간으로 부화소화 — 임계 행 지터(±1px)를 줄인다.
    def _interp(idx: int, target: float) -> float:
        prev_c = cum[idx - 1] if idx > 0 else 0.0
        step = cum[idx] - prev_c
        return idx + ((target - prev_c) / step if step > 1e-9 else 0.0)

    return float(_interp(bot_idx, 0.98) - _interp(top_idx, 0.02))


def _align(frame: np.ndarray, dx: float, dy: float, s: float, center: tuple[float, float]) -> np.ndarray:
    """frame 에서 (이동 dx,dy + 중심 기준 스케일 s)를 제거한 정합 프레임."""
    from PIL import Image

    h, w = frame.shape
    cx, cy = center
    a = s  # inverse of 1/s scaling back
    matrix = (a, 0.0, dx + cx - a * cx, 0.0, a, dy + cy - a * cy)
    im = Image.fromarray(np.clip(frame, 0, 255).astype(np.uint8))
    return np.asarray(im.transform((w, h), Image.AFFINE, matrix, resample=Image.BILINEAR), dtype=np.float32)


# ══════════════════════════════════════════════════════════════════════════
# 분석 본체 (프레임 주입 가능 — 테스트는 ffmpeg 없이 돈다)
# ══════════════════════════════════════════════════════════════════════════


def analyze_frames(
    frames: list[np.ndarray],
    fps: float,
    keyframe_rgb: np.ndarray,
) -> dict[str, Any]:
    thresholds = {
        # 상한/바닥의 근거는 버전 주석의 16클립 보정표다.
        "scale_pulse_max": _f("BREATHING_QA_SCALE_PULSE_MAX", 0.020),
        "scale_strict": _f("BREATHING_QA_SCALE_STRICT", 0.010),
        "drift_max_frac": _f("BREATHING_QA_DRIFT_MAX_FRAC", 0.020),
        "sag_trend_max": _f("BREATHING_QA_SAG_TREND_MAX", 0.010),
        "visible_osc_min": _f("BREATHING_QA_VISIBLE_OSC_MIN", 0.003),
        "torso_snr_min": _f("BREATHING_QA_TORSO_SNR_MIN", 1.6),
        "periodic_min": _f("BREATHING_QA_PERIODIC_MIN", 0.25),
        "modulation_strong": _f("BREATHING_QA_MODULATION_STRONG", 0.45),
        "head_ratio_max": _f("BREATHING_QA_HEAD_RATIO_MAX", 1.6),
        # 중대역 국소성 상한 1.2 — 실측 양성 전부 ≤0.83, 전신 펄스 ≥1.81 의
        # 사이값. 어깨 동반 호흡(상단 윤곽이 조금 움직인다)은 자르지 않되
        # 균일 펄스의 머리 우세는 자른다.
        "head_ratio_max_midband": _f("BREATHING_QA_HEAD_RATIO_MAX_MIDBAND", 1.2),
    }
    base: dict[str, Any] = {
        "version": BREATHING_TEMPORAL_QA_VERSION,
        "thresholds": thresholds,
        "frame_count": len(frames),
        "fps": round(float(fps), 3),
    }
    if len(frames) < 12 or fps <= 0:
        return {**base, "verdict": VERDICT_UNMEASURABLE, "reason": "too_few_frames"}

    mask_full = pet_mask_from_neutral_gray(keyframe_rgb)
    if mask_full is None:
        return {**base, "verdict": VERDICT_UNMEASURABLE, "reason": "pet_mask_unavailable"}

    grays = [_to_gray_small(f) for f in frames]
    gh, gw = grays[0].shape
    if any(g.shape != (gh, gw) for g in grays):
        return {**base, "verdict": VERDICT_UNMEASURABLE, "reason": "frame_size_mismatch"}

    # 마스크를 분석 해상도로.
    from PIL import Image

    mask_img = Image.fromarray((mask_full * 255).astype(np.uint8))
    mask = (
        np.asarray(mask_img.resize((gw, gh), Image.BILINEAR), dtype=np.float32) > 127
    )
    bands = _torso_bands(mask)
    if bands is None:
        return {**base, "verdict": VERDICT_UNMEASURABLE, "reason": "torso_band_unavailable"}
    pet_h = float(bands["pet_height_px"][0])
    ys, xs = np.nonzero(mask)
    center = (float(xs.mean()), float(ys.mean()))

    # ── 전역 성분: 매 프레임을 **프레임 0 기준으로** 직접 추정한다 ────────
    # 인접쌍 추정을 누적하면 정지 클립에서도 추정 잡음이 랜덤워크로 쌓여
    # 가짜 스케일 드리프트가 생긴다 — 고정 기준 추정은 잡음이 누적되지 않는다.
    # ── 전역 성분 ─────────────────────────────────────────────────────────
    # 이동: 매 프레임을 프레임 0 기준으로 위상 상관 (잡음이 누적되지 않는다).
    # 스케일: 전경 높이의 기하 측정 — 전신 펄스는 높이를 바꾸고, 흉곽의 측면
    # 팽창(진짜 호흡)은 바꾸지 않는다.
    ref = grays[0]
    edges = np.concatenate([ref[0, :], ref[-1, :], ref[:, 0], ref[:, -1]])
    bg_level = float(np.median(edges))
    key = _f("BREATHING_QA_MASK_KEY", 24.0)

    frame_dx: list[float] = [0.0]
    frame_dy: list[float] = [0.0]
    heights: list[Optional[float]] = [_foreground_height(ref, bg_level, key)]
    for g in grays[1:]:
        dx, dy = _phase_shift(ref, g)
        frame_dx.append(dx)
        frame_dy.append(dy)
        heights.append(_foreground_height(g, bg_level, key))

    valid_h = [h for h in heights if h]
    if len(valid_h) < len(heights) - 2 or not heights[0]:
        return {**base, "verdict": VERDICT_UNMEASURABLE, "reason": "foreground_height_unavailable"}
    h0 = float(heights[0])
    scale_series = np.asarray([float(h or h0) / h0 for h in heights], dtype=np.float64)
    scale_range = float(scale_series.max() - scale_series.min())
    # v2 — 진동 vs 추세 분해: 선형 추세를 제거한 잔차의 p2p 가 "숨의 진폭"이고,
    # 추세 자체는 침하/수축(프레이밍 드리프트의 일종)이다. 단조 침하 클립은
    # scale_range 만으로는 진짜 호흡과 구분되지 않는다 (실측 v6: -2.2% 침하).
    t_idx = np.arange(scale_series.size)
    trend_fit = np.polyval(np.polyfit(t_idx, scale_series, 1), t_idx)
    scale_trend = float(abs(trend_fit[-1] - trend_fit[0]))
    residual = scale_series - trend_fit
    scale_oscillation = float(residual.max() - residual.min())
    drift_px = float(np.sqrt(np.asarray(frame_dx) ** 2 + np.asarray(frame_dy) ** 2).max())
    drift_frac = drift_px / max(1.0, pet_h)

    # ── 정합 후 잔차 — 인접쌍 이동 정합 + **양쪽 모두 워프** ───────────────
    # 한쪽만 리샘플링하면 그 스무딩이 텍스처 영역에 가짜 잔차를 만든다. 기준
    # 프레임도 항등 워프를 통과시켜 처리 경로를 맞춘다. 스케일은 정합하지
    # 않는다 — 전신 펄스는 위의 기하 검출기가 그 전에 거부한다.
    torso_e: list[float] = []
    head_e: list[float] = []
    bg_e: list[float] = []
    torso_mean: list[float] = []
    for a, b in zip(grays, grays[1:]):
        dx, dy = _phase_shift(a, b)
        a_id = _align(a, 0.0, 0.0, 1.0, center)
        aligned_b = _align(b, dx, dy, 1.0, center)
        diff = np.abs(aligned_b - a_id)
        # 정합 워프의 테두리 아티팩트 배제 — 4px 안쪽만 잰다.
        diff[:4, :] = 0
        diff[-4:, :] = 0
        diff[:, :4] = 0
        diff[:, -4:] = 0
        torso_e.append(float(diff[bands["torso"]].mean()))
        head_e.append(float(diff[bands["head"]].mean()) if bands["head"].sum() > 20 else 0.0)
        bg_e.append(float(diff[bands["background"]].mean()))
        torso_mean.append(float(a_id[bands["torso"]].mean()))
    torso_mean.append(float(_align(grays[-1], 0.0, 0.0, 1.0, center)[bands["torso"]].mean()))

    torso_signal = float(np.mean(torso_e))
    noise_floor = float(np.mean(bg_e))
    snr = torso_signal / max(1e-6, noise_floor)
    head_ratio = float(np.mean(head_e)) / max(1e-6, torso_signal)
    # 시간 변조: 흉곽 잔차 에너지가 시간에 따라 출렁이는가. 텍스처 있는 펫은
    # 정지 상태에서도 배경보다 잔차 바닥이 높다(압축/센서 노이즈 × 질감) —
    # 절대 SNR 만으로는 "노이즈 바닥"과 "일정한 미세 운동"을 못 가른다.
    # 운동은 위상에 따라 에너지가 변조되고(들숨 구간 ≫ 휴지 구간), 노이즈는
    # 일정하다.
    torso_arr = np.asarray(torso_e)
    torso_modulation = float(torso_arr.std() / max(1e-6, torso_arr.mean()))

    # ── 주기성-ish: 흉곽 밴드 평균 밝기 시계열의 자기상관 재발 ───────────
    series = np.asarray(torso_mean, dtype=np.float64)
    series = series - series.mean()
    t = np.arange(series.size)
    if series.size > 3:
        series = series - np.polyval(np.polyfit(t, series, 1), t)  # 선형 추세 제거
    periodic_score = 0.0
    period_sec: Optional[float] = None
    denom = float((series * series).sum())
    if denom > 1e-9:
        lag_lo = max(2, int(round(0.8 * fps)))
        lag_hi = min(series.size - 2, int(round(4.0 * fps)))
        for lag in range(lag_lo, max(lag_lo + 1, lag_hi + 1)):
            v = float((series[:-lag] * series[lag:]).sum()) / denom
            if v > periodic_score:
                periodic_score = v
                period_sec = lag / fps

    metrics = {
        "scale_range": round(scale_range, 5),
        "scale_oscillation": round(scale_oscillation, 5),
        "scale_trend": round(scale_trend, 5),
        "translation_drift_px": round(drift_px, 3),
        "translation_drift_frac_of_pet": round(drift_frac, 5),
        "torso_motion_energy": round(torso_signal, 4),
        "background_noise_floor": round(noise_floor, 4),
        "torso_snr": round(snr, 3),
        "head_to_torso_ratio": round(head_ratio, 3),
        "torso_energy_modulation": round(torso_modulation, 4),
        "periodic_score": round(periodic_score, 4),
        "estimated_period_sec": (round(period_sec, 2) if period_sec else None),
        "pet_height_px": pet_h,
    }

    # ── 판정 (v2) — 스케일은 보조 증거, 여러 신호의 합의로 판정한다 ───────
    # 거부(전역 성분): 상한 초과 스케일 / 프레이밍 드리프트 / 단조 침하.
    # 거부(신호 없음): 배경 수준 잔차, 또는 가시성 바닥 미달(동결 클립).
    # 거부(비국소): 머리 우세 — 중대역(구 1% 초과)은 상한을 1.0 으로 조인다.
    # 탐지: 가시적 진폭 + 리듬(주기성 **또는** 강한 시간 변조 — 완벽한 주기
    # 를 요구하지 않는다. 자연 호흡은 불규칙하다).
    midband = scale_range > thresholds["scale_strict"]
    head_cap = (
        thresholds["head_ratio_max_midband"] if midband else thresholds["head_ratio_max"]
    )
    rhythm_ok = (
        periodic_score >= thresholds["periodic_min"]
        or torso_modulation >= thresholds["modulation_strong"]
    )
    if scale_range > thresholds["scale_pulse_max"]:
        verdict = VERDICT_GLOBAL_PULSE
        reason = f"scale_range {metrics['scale_range']} > {thresholds['scale_pulse_max']}"
    elif drift_frac > thresholds["drift_max_frac"]:
        verdict = VERDICT_GLOBAL_PULSE
        reason = f"drift {metrics['translation_drift_frac_of_pet']} > {thresholds['drift_max_frac']}"
    elif scale_trend > thresholds["sag_trend_max"] and scale_trend > scale_oscillation:
        # 추세가 진동을 지배한다 = 들숨/날숨의 오르내림이 아니라 한 방향
        # 수축/팽창(침하·설정 이동)이다.
        verdict = VERDICT_GLOBAL_PULSE
        reason = f"monotonic_scale_sag trend {metrics['scale_trend']} > osc {metrics['scale_oscillation']}"
    elif snr < thresholds["torso_snr_min"]:
        verdict = VERDICT_NO_MOTION
        reason = f"torso_snr {metrics['torso_snr']} < {thresholds['torso_snr_min']}"
    elif scale_oscillation < thresholds["visible_osc_min"]:
        # 잔차가 배경보다 높아도 윤곽 진폭이 가시성 바닥 미달이면 "동결 + 미세
        # 틱"이다 — 낮은 스케일이 보상이 되지 않는다 (v2 의 핵심 교정).
        verdict = VERDICT_NO_MOTION
        reason = f"amplitude_below_visible_floor osc {metrics['scale_oscillation']} < {thresholds['visible_osc_min']}"
    elif head_ratio > head_cap:
        verdict = VERDICT_UNLOCALIZED
        reason = f"head_to_torso {metrics['head_to_torso_ratio']} > {head_cap}"
    elif rhythm_ok:
        verdict = VERDICT_BREATHING
        reason = None
    else:
        verdict = VERDICT_INCONCLUSIVE
        reason = (
            f"periodic_score {metrics['periodic_score']} < {thresholds['periodic_min']} "
            f"and modulation {metrics['torso_energy_modulation']} < {thresholds['modulation_strong']}"
        )

    return {**base, "verdict": verdict, "reason": reason, "metrics": metrics}


def analyze(video_bytes: bytes, keyframe_rgb: Optional[np.ndarray]) -> dict[str, Any]:
    """조밀 디코딩 + 분석. 어떤 실패도 unmeasurable — 절대 던지지 않는다."""
    base = {"version": BREATHING_TEMPORAL_QA_VERSION}
    if keyframe_rgb is None:
        return {**base, "verdict": VERDICT_UNMEASURABLE, "reason": "no_start_keyframe"}
    try:
        decoded = decode_dense_frames(video_bytes)
        if not decoded:
            return {**base, "verdict": VERDICT_UNMEASURABLE, "reason": "decode_failed"}
        frames, fps = decoded
        return analyze_frames(frames, fps, keyframe_rgb)
    except Exception:
        logger.warning("BREATHING 시간축 분석 실패", exc_info=True)
        return {**base, "verdict": VERDICT_UNMEASURABLE, "reason": "analysis_error"}


#: VLM 에 추가로 보낼 근접쌍 분율 (Δ≈0.05 ≈ 5초 클립에서 0.25초) — 기존 9장
#: 캡(12) 안에서 3쌍을 만든다: (0.25,0.30) (0.50,0.55) (0.75,0.80).
VLM_EVIDENCE_FRACTIONS: tuple[float, ...] = (0.30, 0.55, 0.80)
