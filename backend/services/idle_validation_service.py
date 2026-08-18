"""
Post-generation idle video validation — compare first frame vs reference cutout.

Uses numpy SSIM on grayscale thumbnails (opencv/PIL only — no extra ML deps).
On failure: caller may auto-retry or flag for human review.
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# First-frame vs reference SSIM below this → validation failed (wrong pet / heavy drift).
DEFAULT_SSIM_THRESHOLD = float(os.getenv("IDLE_VALIDATION_SSIM_THRESHOLD", "0.72"))

# 첫 프레임 vs **진짜 마지막 프레임** SSIM — 이 값이 낮으면 루프 이음매가 튄다.
#
# 임계값 0.65 근거 (Wan 10클립 보정 세트, 같은 개·같은 메인 IDLE 프롬프트):
#   눈으로 판정한 BAD 최댓값        = 0.5884
#   눈으로 판정한 BORDERLINE 최솟값 = 0.7171
# 두 집단이 겹치지 않아 그 사이 어디를 잘라도 완전히 분리된다 → 중간값 0.65 채택.
# 예전 0.80 은 legacy(ss=2.85) 지표용 값이었고, true-last 에 그대로 쓰면 10개 중
# 7개가 떨어져 유료 재생성이 급증한다.
DEFAULT_LOOP_SSIM_THRESHOLD = float(os.getenv("IDLE_VALIDATION_LOOP_SSIM_THRESHOLD", "0.65"))


# 진짜 마지막 프레임을 뽑을 때 끝에서 얼마나 앞을 샘플링할지(초).
# 정확히 duration 을 주면 ffmpeg 가 프레임을 못 잡는 경우가 있어 살짝 앞을 본다.
TRUE_LAST_FRAME_OFFSET_SEC = 0.10

# 예전 고정 샘플 지점. 게이트에서는 빠졌지만 과거 로그와 비교하려고 계속 계산한다.
LEGACY_LAST_FRAME_SS = "2.85"


@dataclass
class IdleValidationResult:
    passed: bool
    ssim_first_vs_reference: Optional[float]
    ssim_first_vs_last: Optional[float]
    preset_violation: bool
    needs_human_review: bool
    message: str
    # ssim_first_vs_true_last = duration-0.10s 에서 뽑은 **실제** 마지막 프레임 비교.
    # 이제 루프 게이트는 이 값으로 판정한다(ssim_first_vs_last 아님).
    ssim_first_vs_true_last: Optional[float] = None
    video_duration_sec: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _extract_frame_png(video_bytes: bytes, *, ss: str = "0.15") -> Optional[bytes]:
    """ffmpeg로 영상 프레임 1장을 PNG bytes로 추출. 실패하면 None."""
    with tempfile.TemporaryDirectory(prefix="eb_idle_val_") as td:
        inp = Path(td) / "in.mp4"
        out = Path(td) / "frame.png"
        inp.write_bytes(video_bytes)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", str(inp),
                    "-ss", ss, "-frames:v", "1",
                    str(out),
                ],
                capture_output=True,
                timeout=30,
                check=True,
            )
        except Exception:
            return None
        if not out.is_file():
            return None
        return out.read_bytes()


def _probe_duration_sec(video_bytes: bytes) -> Optional[float]:
    """ffprobe 로 실제 재생 길이(초)를 구한다. 실패하면 None."""
    with tempfile.TemporaryDirectory(prefix="eb_idle_dur_") as td:
        inp = Path(td) / "in.mp4"
        inp.write_bytes(video_bytes)
        try:
            proc = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=nw=1:nk=1",
                    str(inp),
                ],
                capture_output=True,
                timeout=30,
                check=True,
            )
        except Exception:
            return None
    try:
        dur = float((proc.stdout or b"").decode().strip())
    except (ValueError, UnicodeDecodeError):
        return None
    return dur if dur > 0 else None


def true_last_frame_sample_time(duration_sec: float) -> float:
    """실제 마지막 프레임 샘플 지점 = max(duration - 0.10, 0)."""
    return max(duration_sec - TRUE_LAST_FRAME_OFFSET_SEC, 0.0)


def _has_alpha(im) -> bool:
    return im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)


def _load_rgb_thumbnail(
    image_bytes: bytes,
    size: int = 256,
    background_rgb: Optional[tuple] = None,
):
    """
    비교용 RGB 썸네일.

    RGBA 누끼는 `.convert("RGB")` 로 알파를 그냥 버리면 안 된다 — 투명 영역의 RGB가
    보통 (0,0,0)이라 배경이 통째로 검정이 되어 버린다. 생성된 영상의 첫 프레임은
    keyframe 배경(어두운 강아지 → 흰색)을 그대로 갖고 있으므로, 검정 배경 레퍼런스와
    흰 배경 프레임을 비교하게 되어 SSIM이 0 근처로 무너진다(실측 -0.034 < 임계 0.72).
    그래서 어두운 강아지는 항상 검증에 실패하고 유료 재생성을 한 번 더 태웠다.

    이제 알파가 있으면 keyframe 과 **같은 배경색** 위에 합성한 뒤 비교한다.
    배경색은 generate.py / luma_idle_pipeline.py 가 keyframe 을 만들 때 쓰는
    resolve_keyframe_bg_rgb() 와 동일한 함수로 정한다.
    알파가 없는 입력(프레임 vs 프레임 비교 등)은 예전과 완전히 동일하게 동작한다.
    """
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return None

    try:
        im = Image.open(io.BytesIO(image_bytes))
        if _has_alpha(im):
            if background_rgb is None:
                from .luma_keyframe import resolve_keyframe_bg_rgb

                background_rgb = resolve_keyframe_bg_rgb(image_bytes)
            im = im.convert("RGBA")
            # 축소 전에 합성해야 경계 픽셀이 올바른 배경색과 섞인다.
            canvas = Image.new("RGBA", im.size, (*background_rgb, 255))
            im = Image.alpha_composite(canvas, im)
        im = im.convert("RGB")
        im.thumbnail((size, size), Image.Resampling.LANCZOS)
        if im.mode != "RGB":
            im = im.convert("RGB")
        return np.array(im, dtype=np.float64)
    except Exception:
        return None


def _ssim_rgb(a, b) -> float:
    """Grayscale SSIM on two RGB numpy arrays (same shape)."""
    import numpy as np

    if a.shape != b.shape:
        return 0.0
    gray_a = 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]
    gray_b = 0.299 * b[:, :, 0] + 0.587 * b[:, :, 1] + 0.114 * b[:, :, 2]
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    mu_a = gray_a.mean()
    mu_b = gray_b.mean()
    sigma_a = gray_a.var()
    sigma_b = gray_b.var()
    sigma_ab = ((gray_a - mu_a) * (gray_b - mu_b)).mean()
    num = (2 * mu_a * mu_b + c1) * (2 * sigma_ab + c2)
    den = (mu_a ** 2 + mu_b ** 2 + c1) * (sigma_a + sigma_b + c2)
    if den == 0:
        return 0.0
    return float(num / den)


def compare_reference_to_frame(
    reference_bytes: bytes,
    frame_png: bytes,
    background_rgb: Optional[tuple] = None,
) -> Optional[float]:
    """
    background_rgb: RGBA 레퍼런스를 합성할 배경색. None 이면 레퍼런스 바이트에서
    resolve_keyframe_bg_rgb() 로 자동 판정한다(호출자가 keyframe 과 다른 이미지로
    배경을 정했다면 그 값을 직접 넘기는 편이 정확하다).
    """
    ref = _load_rgb_thumbnail(reference_bytes, background_rgb=background_rgb)
    frame = _load_rgb_thumbnail(frame_png, background_rgb=background_rgb)
    if ref is None or frame is None:
        return None
    # Match sizes for SSIM.
    if ref.shape != frame.shape:
        try:
            from PIL import Image
            import numpy as np

            target = (min(ref.shape[1], frame.shape[1]), min(ref.shape[0], frame.shape[0]))
            ref_im = Image.fromarray(ref.astype("uint8")).resize(target, Image.Resampling.LANCZOS)
            frame_im = Image.fromarray(frame.astype("uint8")).resize(target, Image.Resampling.LANCZOS)
            ref = np.array(ref_im, dtype=np.float64)
            frame = np.array(frame_im, dtype=np.float64)
        except Exception:
            return None
    return _ssim_rgb(ref, frame)


def validate_idle_video(
    video_bytes: bytes,
    reference_image_bytes: bytes,
    *,
    template_key: str = "IDLE_BREATH",
    ssim_threshold: float = DEFAULT_SSIM_THRESHOLD,
    loop_ssim_threshold: float = DEFAULT_LOOP_SSIM_THRESHOLD,
    background_rgb: Optional[tuple] = None,
) -> IdleValidationResult:
    """
    Compare generated idle mp4 first frame (and loop consistency) against reference cutout.

    판정 기준 2가지:
      1) ssim_first_vs_reference — 첫 프레임 vs 레퍼런스 누끼 (변경 없음).
      2) ssim_first_vs_true_last — 첫 프레임 vs **진짜 마지막 프레임**
         (duration-0.10s). 루프 이음매 게이트는 이 값으로만 판정한다.
         ssim_first_vs_last(ss=2.85 고정)는 과거 기록 비교용으로 계속 계산해
         진단에 담지만 합격/재생성 판정에는 쓰지 않는다.
    모든 template_key 에 루프 검사를 적용한다 — 예전 IDLE_LOOK_AROUND 면제는 제거됐다.

    background_rgb: RGBA 레퍼런스를 합성할 keyframe 배경색. None 이면 레퍼런스에서
    자동 판정 — 호출자가 keyframe 배경을 레퍼런스와 같은 이미지로 정했다면(현재
    generate.py 의 skip_preprocessing=true 경로, luma_idle_pipeline.py 전부)
    자동 판정 결과가 keyframe 과 정확히 일치한다.
    """
    first_png = _extract_frame_png(video_bytes, ss="0.15")
    # ss=2.85 는 historical 지표 전용(게이트 아님). 아래 true-last 가 실제 게이트다.
    last_png = _extract_frame_png(video_bytes, ss=LEGACY_LAST_FRAME_SS)

    if not first_png:
        return IdleValidationResult(
            passed=True,
            ssim_first_vs_reference=None,
            ssim_first_vs_last=None,
            preset_violation=False,
            needs_human_review=False,
            message="frame_extract_skipped",
        )

    ssim_ref = compare_reference_to_frame(
        reference_image_bytes, first_png, background_rgb=background_rgb
    )
    ssim_loop: Optional[float] = None
    preset_violation = False

    if last_png:
        # 기록/과거 비교 전용(historical). **합격 판정에는 더 이상 쓰지 않는다.**
        # ss=2.85 고정이라 클립 길이에 따라 55~71% 지점을 찍는다. 10클립 보정에서
        # 눈 판정과의 상관이 +0.418 에 그쳤고, BAD 와 정상 구간이 겹쳐(0.6589~0.8433)
        # 어떤 임계값으로도 분리가 되지 않았다.
        ssim_loop = compare_reference_to_frame(first_png, last_png)

    # ── 루프 게이트: 진짜 마지막 프레임 (duration - 0.10s) ────────────────────
    # 눈 판정과의 상관 +0.818, BAD(≤0.5884) 와 정상(≥0.7171) 이 깨끗하게 갈린다.
    duration_sec = _probe_duration_sec(video_bytes)
    ssim_true_last: Optional[float] = None
    sample_at: Optional[float] = None
    if duration_sec is not None:
        sample_at = true_last_frame_sample_time(duration_sec)
        true_last_png = _extract_frame_png(video_bytes, ss=f"{sample_at:.3f}")
        if true_last_png:
            ssim_true_last = compare_reference_to_frame(first_png, true_last_png)

    # IDLE_LOOK_AROUND 예외 제거 — "끝에서 원래 각도로 정확히 돌아온다"고 약속하는
    # 프리셋일수록 실제 종료 상태를 검사해야 한다(예전에는 유일하게 면제였다).
    if ssim_true_last is not None and ssim_true_last < loop_ssim_threshold:
        preset_violation = True

    logger.info(
        "idle_validation loop: template=%s duration=%s | GATE true_last@%s -> %s "
        "(threshold %.2f) | historical ss=2.85 -> %s",
        template_key,
        f"{duration_sec:.2f}s" if duration_sec is not None else "n/a",
        f"{sample_at:.2f}s" if sample_at is not None else "n/a",
        f"{ssim_true_last:.4f}" if ssim_true_last is not None else "n/a",
        loop_ssim_threshold,
        f"{ssim_loop:.4f}" if ssim_loop is not None else "n/a",
    )

    passed = True
    needs_review = False
    messages: list[str] = []

    if ssim_ref is not None:
        if ssim_ref < ssim_threshold:
            passed = False
            needs_review = True
            messages.append(f"low_ssim_vs_reference({ssim_ref:.3f}<{ssim_threshold})")
    else:
        messages.append("ssim_unavailable")

    if preset_violation:
        passed = False
        needs_review = True
        messages.append(f"preset_pose_drift({ssim_true_last:.3f}<{loop_ssim_threshold})")
    elif ssim_true_last is None:
        # ffprobe/프레임 추출 실패 — 루프 검사를 할 수 없다. 여기서 실패로 돌리면
        # 측정 사고가 곧바로 유료 재생성이 되므로 fail-open 으로 두고 기록만 한다.
        messages.append("loop_ssim_unavailable")

    if not messages:
        messages.append("ok")

    return IdleValidationResult(
        passed=passed,
        ssim_first_vs_reference=ssim_ref,
        ssim_first_vs_last=ssim_loop,
        preset_violation=preset_violation,
        needs_human_review=needs_review,
        message="; ".join(messages),
        ssim_first_vs_true_last=ssim_true_last,
        video_duration_sec=duration_sec,
    )
