"""
Modal 배포: eternal-beam-cutout — process_video_to_rgba_modal (rembg 누끼 → RGBA MP4)

프로젝트 루트에서:
  modal deploy modal_cutout_app.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Dict

import modal

_ROOT = Path(__file__).resolve().parent

app = modal.App("eternal-beam-cutout")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install(
        "rembg[gpu]==2.0.57",
        "opencv-python-headless>=4.8.0",
        "numpy>=1.24.0",
        "Pillow>=10.0.0",
        "ultralytics>=8.1.0",
    )
    .add_local_dir(str(_ROOT / "backend"), remote_path="/root/backend")
)


@app.function(
    image=image,
    gpu="T4",
    timeout=900,
    memory=8192,
)
def process_video_to_rgba_modal(
    video_bytes: bytes,
    replace_bg: str = "white",
    max_frames: int = 120,
    use_alpha_matting: bool = False,
) -> bytes:
    sys.path.insert(0, "/root/backend")
    from services.video_cutout_service import process_video_to_rgba

    tmpdir = tempfile.mkdtemp()
    try:
        inp = os.path.join(tmpdir, "in.mp4")
        with open(inp, "wb") as f:
            f.write(video_bytes)
        rb = replace_bg if replace_bg in ("white", "black") else "white"
        result = process_video_to_rgba(
            inp,
            tmpdir,
            output_format="rgba_video",
            model_name="isnet-general-use",
            use_alpha_matting=use_alpha_matting,
            max_frames=max_frames,
            replace_bg_before_rembg=rb,
            output_resolution=(1280, 720),
        )
        rgba_path = result[0] if result else None
        if rgba_path and os.path.isfile(rgba_path):
            with open(rgba_path, "rb") as f:
                return f.read()
        raise RuntimeError("Modal: RGBA MP4 생성 실패")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@app.function(
    image=image,
    gpu="T4",
    timeout=900,
    memory=8192,
)
def process_dog_only_video_modal(
    video_bytes: bytes,
    replace_bg: str = "white",
    max_frames: int = 120,
    use_alpha_matting: bool = False,
) -> Dict[str, bytes]:
    """
    YOLO 강아지 검출 → 크롭 rembg → 원본 프레임 크기 검정 배경 합성.
    반환: rgb_mp4, alpha_mp4 바이너리 (키: rgb_mp4, alpha_mp4).
    """
    sys.path.insert(0, "/root/backend")
    from services.video_cutout_service import process_dog_only_from_video

    tmpdir = tempfile.mkdtemp()
    try:
        inp = os.path.join(tmpdir, "in.mp4")
        with open(inp, "wb") as f:
            f.write(video_bytes)
        rb = replace_bg if replace_bg in ("white", "black") else "white"
        rgb_path, alpha_path = process_dog_only_from_video(
            inp,
            tmpdir,
            model_name="isnet-general-use",
            use_alpha_matting=use_alpha_matting,
            max_frames=max_frames,
            replace_bg_before_rembg=rb,
            output_resolution=(1280, 720),
            bbox_pad_frac=0.2,
            yolo_model="yolov8n.pt",
        )
        out: Dict[str, bytes] = {}
        if rgb_path and os.path.isfile(rgb_path):
            with open(rgb_path, "rb") as f:
                out["rgb_mp4"] = f.read()
        if alpha_path and os.path.isfile(alpha_path):
            with open(alpha_path, "rb") as f:
                out["alpha_mp4"] = f.read()
        if len(out) == 2:
            return out
        raise RuntimeError("Modal: dog-only RGB/Alpha MP4 생성 실패")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
