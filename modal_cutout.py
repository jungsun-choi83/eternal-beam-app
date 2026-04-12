"""
Modal GPU - rembg 영상 누끼 (15초 내 처리 목표)
배포: modal deploy modal_cutout.py
호출: 백엔드에서 Function.from_name("eternal-beam-cutout", "process_video_to_rgba_modal").remote(video_bytes=...)
"""

import os
import io
import tempfile
import subprocess
import glob
from pathlib import Path
from typing import Optional, Tuple

import modal

# --- Image: rembg GPU + opencv + ffmpeg ---
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install(
        "rembg[gpu]==2.0.57",
        "opencv-python-headless>=4.8.0",
        "Pillow>=10.0.0",
        "numpy>=1.24.0",
        "requests",
    )
)

app = modal.App("eternal-beam-cutout", image=image)


def _compute_720p_size(w: int, h: int) -> Tuple[int, int]:
    if h >= w:
        max_w, max_h = 720, 1280
    else:
        max_w, max_h = 1280, 720
    scale = min(max_w / w, max_h / h)
    return (max(64, int(w * scale)), max(64, int(h * scale)))


def _replace_background(rgb, target: str):
    import numpy as np

    h, w = rgb.shape[:2]
    fill = np.array([255, 255, 255], dtype=np.uint8) if target == "white" else np.array([0, 0, 0], dtype=np.uint8)
    margin = max(2, min(w, h) // 30)
    corners = [
        rgb[:margin, :margin].reshape(-1, 3),
        rgb[:margin, -margin:].reshape(-1, 3),
        rgb[-margin:, :margin].reshape(-1, 3),
        rgb[-margin:, -margin:].reshape(-1, 3),
    ]
    bg = np.median(np.vstack(corners), axis=0).astype(np.float32)
    diff = np.linalg.norm(rgb.astype(np.float32) - bg, axis=2)
    thresh = 35.0 if target == "white" else 40.0
    mask = diff < thresh
    out = rgb.copy()
    out[mask] = fill
    return out


def _remove_bg(image_bytes: bytes, use_alpha_matting: bool, model_name: str) -> bytes:
    from rembg import remove as rembg_remove
    from rembg.session_factory import new_session
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    session = new_session(model_name, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    kwargs = dict(
        alpha_matting=use_alpha_matting,
        alpha_matting_foreground_threshold=240,
        alpha_matting_background_threshold=10,
        alpha_matting_base_size=1000,
    )
    try:
        out = rembg_remove(img, session=session, alpha_matting_erode_structure_size=10, **kwargs)
    except TypeError:
        out = rembg_remove(img, session=session, alpha_matting_erode_size=10, **kwargs)
    if out is None:
        raise RuntimeError("rembg 반환값 없음")
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def _png_seq_to_mp4(png_dir: str, out_path: str, base: str, fps: float) -> str:
    files = sorted(glob.glob(os.path.join(png_dir, f"{base}_*.png")))
    if not files:
        raise RuntimeError("PNG 없음")
    list_path = os.path.join(tempfile.gettempdir(), "concat.txt")
    with open(list_path, "w") as f:
        for fp in files:
            f.write(f"file '{os.path.abspath(fp)}'\n")
            f.write(f"duration {1/fps}\n")
        f.write(f"file '{os.path.abspath(files[-1])}'\n")
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-c:v", "libx264", "-profile:v", "high",
            "-pix_fmt", "yuva420p", "-movflags", "+faststart",
            out_path,
        ],
        capture_output=True, text=True, timeout=300, check=True,
    )
    return out_path


@app.function(
    gpu=os.getenv("MODAL_GPU", "T4"),
    timeout=120,
    memory=4096,
)
def process_video_to_rgba_modal(
    video_bytes: bytes,
    replace_bg: Optional[str] = None,
    max_frames: int = 30,
    use_alpha_matting: bool = False,
) -> bytes:
    """
    영상 bytes → rembg 누끼 → RGBA MP4 bytes 반환.
    GPU 사용, 작업 완료 시 인스턴스 즉시 종료.
    """
    import cv2
    import numpy as np
    from PIL import Image

    tmp = tempfile.mkdtemp()
    inp_path = os.path.join(tmp, "input.mp4")
    with open(inp_path, "wb") as f:
        f.write(video_bytes)

    cap = cv2.VideoCapture(inp_path)
    if not cap.isOpened():
        raise RuntimeError("영상 열기 실패")
    vid_fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    base = "input"
    out_w, out_h = 1280, 720
    frame_idx = 0
    paths = []

    while True:
        ret, frame = cap.read()
        if not ret or (max_frames and frame_idx >= max_frames):
            break
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        orig_w, orig_h = rgb.shape[1], rgb.shape[0]
        out_w, out_h = _compute_720p_size(orig_w, orig_h)
        proxy_w = max(64, out_w // 2)
        proxy_h = max(64, out_h // 2)

        rgb_out = cv2.resize(rgb, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
        rgb_proxy = cv2.resize(rgb_out, (proxy_w, proxy_h), interpolation=cv2.INTER_LINEAR)
        if replace_bg in ("white", "black"):
            rgb_proxy = _replace_background(rgb_proxy, replace_bg)

        pil_proxy = Image.fromarray(rgb_proxy)
        buf = io.BytesIO()
        pil_proxy.save(buf, format="PNG")
        raw = buf.getvalue()

        png_bytes = _remove_bg(raw, use_alpha_matting, "isnet-general-use")
        rgba_proxy = np.array(Image.open(io.BytesIO(png_bytes)).convert("RGBA"))
        mask_proxy = rgba_proxy[:, :, 3]
        mask_out = cv2.resize(mask_proxy, (out_w, out_h), interpolation=cv2.INTER_LINEAR)

        rgba_out = np.zeros((out_h, out_w, 4), dtype=np.uint8)
        rgba_out[:, :, :3] = rgb_out
        rgba_out[:, :, 3] = mask_out
        bgra = cv2.cvtColor(rgba_out, cv2.COLOR_RGBA2BGRA)
        png_path = os.path.join(tmp, f"{base}_{frame_idx:05d}.png")
        cv2.imwrite(png_path, bgra)
        paths.append(png_path)
        frame_idx += 1

    cap.release()
    if not paths:
        raise RuntimeError("프레임 처리 결과 없음")

    rgba_mp4 = os.path.join(tmp, "output_rgba.mp4")
    _png_seq_to_mp4(tmp, rgba_mp4, base, vid_fps)
    with open(rgba_mp4, "rb") as f:
        result = f.read()
    return result
