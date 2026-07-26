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

# First vs last frame SSIM for non-LOOK_AROUND presets — large drop suggests pose change.
DEFAULT_LOOP_SSIM_THRESHOLD = float(os.getenv("IDLE_VALIDATION_LOOP_SSIM_THRESHOLD", "0.80"))


@dataclass
class IdleValidationResult:
    passed: bool
    ssim_first_vs_reference: Optional[float]
    ssim_first_vs_last: Optional[float]
    preset_violation: bool
    needs_human_review: bool
    message: str

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


def _load_rgb_thumbnail(image_bytes: bytes, size: int = 256):
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return None

    try:
        im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
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


def compare_reference_to_frame(reference_bytes: bytes, frame_png: bytes) -> Optional[float]:
    ref = _load_rgb_thumbnail(reference_bytes)
    frame = _load_rgb_thumbnail(frame_png)
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
) -> IdleValidationResult:
    """
    Compare generated idle mp4 first frame (and loop consistency) against reference cutout.
    """
    first_png = _extract_frame_png(video_bytes, ss="0.15")
    last_png = _extract_frame_png(video_bytes, ss="2.85")

    if not first_png:
        return IdleValidationResult(
            passed=True,
            ssim_first_vs_reference=None,
            ssim_first_vs_last=None,
            preset_violation=False,
            needs_human_review=False,
            message="frame_extract_skipped",
        )

    ssim_ref = compare_reference_to_frame(reference_image_bytes, first_png)
    ssim_loop: Optional[float] = None
    preset_violation = False

    if last_png:
        ssim_loop = compare_reference_to_frame(first_png, last_png)
        # Non head-rotation presets: first/last should stay aligned (loop seamless).
        if template_key != "IDLE_LOOK_AROUND" and ssim_loop is not None:
            if ssim_loop < loop_ssim_threshold:
                preset_violation = True

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
        messages.append(f"preset_pose_drift({ssim_loop:.3f})")

    if not messages:
        messages.append("ok")

    return IdleValidationResult(
        passed=passed,
        ssim_first_vs_reference=ssim_ref,
        ssim_first_vs_last=ssim_loop,
        preset_violation=preset_violation,
        needs_human_review=needs_review,
        message="; ".join(messages),
    )
