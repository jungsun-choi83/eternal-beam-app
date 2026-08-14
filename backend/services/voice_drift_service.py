"""
VOICE 전용 드리프트 측정 — **진단 우선(diagnostic-first)**.

왜 SSIM 이 아닌가
-----------------
IDLE 게이트는 first↔true-last SSIM 을 쓴다. IDLE 은 "거의 정지"가 목표라 그게 맞다.
VOICE 는 **이벤트 클립**이라 의도된 움직임이 있고, 그만큼 SSIM 이 정상적으로 낮아진다.
실측(3클립)에서 SSIM 은 0.4733~0.6761 이었는데, 그 낮음이 "고개를 돌렸다"(정상)에서
온 건지 "개가 걸어가며 작아졌다"(불량)에서 온 건지 SSIM 만으로는 구분되지 않는다.

그래서 VOICE 는 **기하학적 드리프트**를 본다: 피사체가 처음과 같은 크기로, 같은
자리에 남아 있는가. 고개/귀 움직임은 이 지표들을 거의 건드리지 않는다.

모드
----
diagnostic (기본)  : 계산·기록만. 절대 재생성을 유발하지 않는다.
gate (VOICE_DRIFT_GATE_ENABLED=1) : passed=False 를 낼 수 있다. 기본 꺼짐.

임계값은 **잠정(provisional) 후보**다. n=3 에서 뽑은 값이라 보정된 값이 아니다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── 잠정 후보 한계값 — 보정되지 않았다 (n=3) ────────────────────────────────
PROVISIONAL_MAX_AREA_CHANGE_PCT = float(os.getenv("VOICE_MAX_AREA_CHANGE_PCT", "15.0"))
PROVISIONAL_MAX_BBOX_H_CHANGE_PCT = float(os.getenv("VOICE_MAX_BBOX_H_CHANGE_PCT", "15.0"))
PROVISIONAL_MAX_CENTROID_DISP_PCT = float(os.getenv("VOICE_MAX_CENTROID_DISP_PCT", "6.0"))

#: 배경 대비 이만큼 밝으면 피사체로 본다 (검정 플레이트 기준).
SUBJECT_LUMA_DELTA = 28.0

#: 몸통 밴드 = bbox 세로의 이 구간만 사용. 위(꼬리·귀)와 아래(발)를 잘라내
#: 꼬리가 흔들리는 것이 "몸 전체가 이동했다"로 오독되는 걸 줄인다.
TORSO_BAND = (0.25, 0.75)


def gate_enabled() -> bool:
    """기본 off. 켜기 전에 반드시 실측으로 보정할 것."""
    return os.getenv("VOICE_DRIFT_GATE_ENABLED", "0").strip().lower() in ("1", "true", "yes")


@dataclass
class VoiceDriftMetrics:
    bbox_h_change_pct: Optional[float] = None
    area_change_pct: Optional[float] = None
    centroid_disp_pct: Optional[float] = None       # mean centroid / 프레임 폭
    torso_centroid_disp_pct: Optional[float] = None  # 몸통 밴드 median / 프레임 폭
    # ── 피사체 상대 이동량 (프레임이 아니라 **개 크기** 기준) ────────────────
    # 프레임 폭 정규화는 "개가 프레임에서 얼마나 크게 잡혔는가"에 좌우된다.
    # 작게 잡힌 개는 같은 걸음이라도 %W 가 작게 나오고, 크게 잡힌 개는 꼬리만
    # 흔들려도 커 보인다. 몸 크기로 나누면 "제 몸 길이의 몇 %를 이동했는가"가 되어
    # 프레임 내 피사체 크기와 무관해진다.
    torso_dx_over_bbox_w: Optional[float] = None
    torso_dy_over_bbox_h: Optional[float] = None
    torso_disp_over_diag: Optional[float] = None
    duration_sec: Optional[float] = None
    frame_width: Optional[int] = None
    #: gate 모드에서만 의미가 있다. diagnostic 모드에서는 항상 True.
    passed: bool = True
    gate_enabled: bool = False
    violations: Optional[list[str]] = None
    message: str = "diagnostic_only"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _subject_mask(png_bytes: bytes):
    """
    프레임 PNG → (마스크, 폭).

    System B 는 항상 검정 플레이트지만, 밝은 배경 클립(System A 의 흰 플레이트,
    과거 자료)에서 마스크가 통째로 비어 조용히 "측정 불가"가 되는 걸 막으려고
    코너 휘도로 방향을 정한다 — idle_validation/키어와 같은 관례.
    """
    import io

    import numpy as np
    from PIL import Image

    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    a = np.asarray(im, dtype=np.float64)
    lum = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    h, w = lum.shape
    s = max(2, min(w, h) // 20)
    corners = [lum[:s, :s], lum[:s, -s:], lum[-s:, :s], lum[-s:, -s:]]
    bg = float(sum(c.mean() for c in corners) / 4.0)
    if bg >= 128.0:  # 밝은 배경 → 피사체가 더 어둡다
        return lum < (bg - SUBJECT_LUMA_DELTA), w
    return lum > (bg + SUBJECT_LUMA_DELTA), w


def _shape_stats(png_bytes: bytes) -> Optional[dict[str, float]]:
    import numpy as np

    mask, width = _subject_mask(png_bytes)
    if not mask.any():
        return None
    ys, xs = np.nonzero(mask)
    y0, y1 = int(ys.min()), int(ys.max())

    # 몸통 밴드 median 중심 — 꼬리/발 변형에 훨씬 둔감하다.
    band_lo = y0 + (y1 - y0) * TORSO_BAND[0]
    band_hi = y0 + (y1 - y0) * TORSO_BAND[1]
    in_band = (ys >= band_lo) & (ys <= band_hi)
    if in_band.any():
        tcx, tcy = float(np.median(xs[in_band])), float(np.median(ys[in_band]))
    else:
        tcx, tcy = float(np.median(xs)), float(np.median(ys))

    x0, x1 = int(xs.min()), int(xs.max())
    return {
        "bbox_h": float(y1 - y0),
        "bbox_w": float(x1 - x0),
        "area": float(mask.sum()),
        "cx": float(xs.mean()),
        "cy": float(ys.mean()),
        "tcx": tcx,
        "tcy": tcy,
        "width": float(width),
    }


def measure_voice_drift(video_bytes: bytes) -> VoiceDriftMetrics:
    """
    VOICE 클립의 기하학적 드리프트를 잰다.

    diagnostic 모드(기본)에서는 passed 가 항상 True 다 — 이 함수만으로는
    유료 재생성이 절대 늘어나지 않는다.
    """
    from .idle_validation_service import (
        _extract_frame_png,
        _probe_duration_sec,
        true_last_frame_sample_time,
    )

    m = VoiceDriftMetrics(gate_enabled=gate_enabled())

    duration = _probe_duration_sec(video_bytes)
    if duration is None:
        m.message = "duration_unavailable"
        return m
    m.duration_sec = round(duration, 2)

    first = _extract_frame_png(video_bytes, ss="0.15")
    last = _extract_frame_png(video_bytes, ss=f"{true_last_frame_sample_time(duration):.3f}")
    if not first or not last:
        m.message = "frame_extract_failed"
        return m

    s0, s1 = _shape_stats(first), _shape_stats(last)
    if not s0 or not s1 or s0["bbox_h"] <= 0 or s0["area"] <= 0:
        m.message = "subject_not_found"
        return m

    m.frame_width = int(s0["width"])
    w = s0["width"] or 1.0
    m.bbox_h_change_pct = round(100.0 * (s1["bbox_h"] - s0["bbox_h"]) / s0["bbox_h"], 2)
    m.area_change_pct = round(100.0 * (s1["area"] - s0["area"]) / s0["area"], 2)
    m.centroid_disp_pct = round(
        100.0 * ((s1["cx"] - s0["cx"]) ** 2 + (s1["cy"] - s0["cy"]) ** 2) ** 0.5 / w, 2
    )
    m.torso_centroid_disp_pct = round(
        100.0 * ((s1["tcx"] - s0["tcx"]) ** 2 + (s1["tcy"] - s0["tcy"]) ** 2) ** 0.5 / w, 2
    )

    # 피사체 상대 이동 — **처음** 프레임의 몸 크기로 나눈다(끝 프레임으로 나누면
    # 개가 작아질 때 분모도 작아져 이동량이 부풀려진다).
    dx = s1["tcx"] - s0["tcx"]
    dy = s1["tcy"] - s0["tcy"]
    bw = s0.get("bbox_w") or 0.0
    bh = s0.get("bbox_h") or 0.0
    if bw > 0:
        m.torso_dx_over_bbox_w = round(100.0 * abs(dx) / bw, 2)
    if bh > 0:
        m.torso_dy_over_bbox_h = round(100.0 * abs(dy) / bh, 2)
    if bw > 0 and bh > 0:
        diag = (bw**2 + bh**2) ** 0.5
        m.torso_disp_over_diag = round(100.0 * (dx**2 + dy**2) ** 0.5 / diag, 2)

    violations: list[str] = []
    if abs(m.area_change_pct) > PROVISIONAL_MAX_AREA_CHANGE_PCT:
        violations.append(f"area({m.area_change_pct:+.1f}%)")
    if abs(m.bbox_h_change_pct) > PROVISIONAL_MAX_BBOX_H_CHANGE_PCT:
        violations.append(f"bbox_h({m.bbox_h_change_pct:+.1f}%)")
    if m.torso_centroid_disp_pct > PROVISIONAL_MAX_CENTROID_DISP_PCT:
        violations.append(f"torso_centroid({m.torso_centroid_disp_pct:.1f}%W)")
    m.violations = violations

    if m.gate_enabled:
        m.passed = not violations
        m.message = "; ".join(violations) if violations else "ok"
    else:
        m.passed = True  # diagnostic — 절대 막지 않는다
        m.message = (
            f"diagnostic_only(would_flag: {'; '.join(violations)})" if violations
            else "diagnostic_only(ok)"
        )

    logger.info(
        "voice_drift: dur=%.2fs area=%+.1f%% bbox_h=%+.1f%% centroid=%.1f%%W "
        "torso_centroid=%.1f%%W gate=%s -> %s",
        m.duration_sec, m.area_change_pct, m.bbox_h_change_pct,
        m.centroid_disp_pct, m.torso_centroid_disp_pct,
        "on" if m.gate_enabled else "off(diagnostic)", m.message,
    )
    return m
