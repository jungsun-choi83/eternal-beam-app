"""
실시간 프리뷰 합성 — Scale/Position 적용
레이어 분리 (배경 어둡고 흐릿, 피사체 밝고 선명)
"""

import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from .compose_video_service import _get_theme_video_path, _get_duration_sec, run_ffmpeg


def generate_preview(
    theme_id: str,
    cutout_png_path: str,
    output_path: str,
    scale: float = 1.0,
    position_x: int = 0,
    position_y: int = 0,
    duration_sec: float = 5.0,
    max_height: int = 720,
) -> str:
    """
    Scale/Position 적용한 프리뷰 합성
    - 배경: 어둡고 흐릿 (brightness=0.5, gblur)
    - 피사체: scale 적용, position_x/y 오프셋
    - position: 0=중앙, -100=왼쪽/위, +100=오른쪽/아래
    """
    theme_path = _get_theme_video_path(theme_id)
    if not theme_path:
        raise FileNotFoundError(f"테마 영상을 찾을 수 없습니다: {theme_id}")

    dur = min(duration_sec, _get_duration_sec(theme_path))
    w = 1280 if max_height >= 1080 else 854
    h = 720 if max_height >= 1080 else 480
    w = (w // 2) * 2
    h = (h // 2) * 2

    scale = max(0.5, min(2.0, scale))
    # position: 0=중앙, -100~100 → offset (100 = +50% of width)
    # main_w, overlay_w (FFmpeg overlay filter variables)
    overlay_x_expr = f"(main_w-overlay_w)/2+main_w*{position_x}/200"
    overlay_y_expr = f"(main_h-overlay_h)/2+main_h*{position_y}/200"

    subject_tmp = os.path.join(tempfile.gettempdir(), f"preview_subj_{int(time.time())}.mp4")
    try:
        # 피사체: scale 적용
        scale_w = int((w // 2) * scale) * 2
        scale_h = int((h // 2) * scale) * 2
        scale_w = max(64, min(w * 2, scale_w))
        scale_h = max(64, min(h * 2, scale_h))

        run_ffmpeg([
            "-loop", "1", "-t", str(dur), "-i", cutout_png_path,
            "-vf", f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=decrease,pad={scale_w}:{scale_h}:(ow-iw)/2:(oh-ih)/2",
            "-pix_fmt", "yuva420p",
            "-c:v", "libx264", "-preset", "ultrafast",
            subject_tmp,
        ])

        # 배경: 어둡고 흐릿 + 피사체 오버레이
        true_black = "curves=all='0/0 0.04/0 1/1'"
        run_ffmpeg([
            "-i", theme_path,
            "-i", subject_tmp,
            "-filter_complex",
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
            f"eq=brightness=0.5:contrast=1.1,boxblur=2:2,{true_black}[bg];"
            f"[1:v]scale={scale_w}:{scale_h}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=x={overlay_x_expr}:y={overlay_y_expr}:format=auto",
            "-t", str(dur),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-color_range", "pc", "-movflags", "+faststart",
            output_path,
        ])
    finally:
        if os.path.exists(subject_tmp):
            try:
                os.unlink(subject_tmp)
            except Exception:
                pass

    return output_path
