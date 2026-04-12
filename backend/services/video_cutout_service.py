"""
Luma 등 영상 → RGBA PNG 시퀀스 / RGBA 영상 파이프라인

목표: 광고 촬영 수준 홀로그램 - RGB와 Alpha 완전 분리
- Proxy Masking: 360p에서 rembg 계산 → 마스크만 720p로 업스케일 → 720p 원본에 적용
- 최종 출력: 720p 고정 (속도 + 화질)
"""

import os
import io
import tempfile
import subprocess
from pathlib import Path
from typing import Callable, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from .cutout_service import remove_background

# 최종 출력 720p (비율 유지), rembg proxy 360p
def _compute_720p_size(w: int, h: int) -> Tuple[int, int]:
    """비율 유지하며 720p 내 맞춤. 세로형→720x1280, 가로형→1280x720"""
    if h >= w:  # portrait (세로)
        max_w, max_h = 720, 1280
    else:
        max_w, max_h = 1280, 720
    scale = min(max_w / w, max_h / h)
    return (max(64, int(w * scale)), max(64, int(h * scale)))


def replace_background_for_rembg(
    rgb: np.ndarray,
    target: str,  # "white" | "black"
) -> np.ndarray:
    """
    rembg 전 전처리: 배경을 흰/검정으로 교체 → 블랙탄/밝은 강아지 누끼 품질 향상.
    가장자리 픽셀만 샘플링해 배경 추정 (코너에 강아지 있을 수 있으므로 보수적).
    """
    h, w = rgb.shape[:2]
    fill = np.array([255, 255, 255], dtype=np.uint8) if target == "white" else np.array([0, 0, 0], dtype=np.uint8)
    margin = max(2, min(w, h) // 30)  # 더 좁은 가장자리만 샘플
    corners = [
        rgb[:margin, :margin].reshape(-1, 3),
        rgb[:margin, -margin:].reshape(-1, 3),
        rgb[-margin:, :margin].reshape(-1, 3),
        rgb[-margin:, -margin:].reshape(-1, 3),
    ]
    bg = np.median(np.vstack(corners), axis=0).astype(np.float32)
    diff = np.linalg.norm(rgb.astype(np.float32) - bg, axis=2)
    thresh = 35.0 if target == "white" else 40.0  # 더 보수적: 확실한 배경만 교체
    mask = diff < thresh
    out = rgb.copy()
    out[mask] = fill
    return out


def process_video_to_rgba(
    input_video_path: str,
    output_dir: str,
    output_format: str = "png_sequence",  # "png_sequence" | "rgba_video"
    model_name: str = "isnet-general-use",
    use_alpha_matting: bool = True,
    fps: Optional[float] = None,
    max_frames: Optional[int] = None,
    progress_callback=None,
    replace_bg_before_rembg: Optional[str] = None,  # "white" (블랙탄) | "black" (밝은강아지)
    output_resolution: Optional[Tuple[int, int]] = (1280, 720),  # 720p 비율유지. None=원본
) -> tuple[str, ...]:
    """
    Runway 영상 → RGBA PNG 시퀀스 또는 RGBA 영상

    Returns:
        (output_path_or_dir, ...) - PNG 시퀀스면 디렉토리 경로, RGBA 영상이면 파일 경로
    """
    cap = cv2.VideoCapture(input_video_path)
    if not cap.isOpened():
        raise RuntimeError(f"영상 열기 실패: {input_video_path}")

    vid_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    fps = fps or vid_fps
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0

    os.makedirs(output_dir, exist_ok=True)
    base = Path(input_video_path).stem

    frame_idx = 0
    paths = []

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if max_frames is not None and frame_idx >= max_frames:
            break

        # BGR → RGB
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        orig_h, orig_w = rgb.shape[:2]

        if output_resolution:
            out_w, out_h = _compute_720p_size(orig_w, orig_h)
            proxy_w = max(64, out_w // 2)
            proxy_h = max(64, out_h // 2)
            # Proxy Masking: 360p급에서 rembg → 마스크 720p 업스케일 → 출력
            rgb_out = cv2.resize(rgb, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
            rgb_proxy = cv2.resize(rgb_out, (proxy_w, proxy_h), interpolation=cv2.INTER_LINEAR)

            if replace_bg_before_rembg in ("white", "black"):
                rgb_proxy = replace_background_for_rembg(rgb_proxy, replace_bg_before_rembg)

            pil_proxy = Image.fromarray(rgb_proxy)
            buf = io.BytesIO()
            pil_proxy.save(buf, format="PNG")
            raw = buf.getvalue()

            try:
                png_bytes = remove_background(
                    raw,
                    use_alpha_matting=use_alpha_matting,
                    model_name=model_name,
                )
            except Exception as e:
                raise RuntimeError(f"프레임 {frame_idx} 배경 제거 실패: {e}") from e

            rgba_proxy = np.array(Image.open(io.BytesIO(png_bytes)).convert("RGBA"))
            mask_proxy = rgba_proxy[:, :, 3]
            mask_out = cv2.resize(mask_proxy, (out_w, out_h), interpolation=cv2.INTER_LINEAR)

            rgba_out = np.zeros((out_h, out_w, 4), dtype=np.uint8)
            rgba_out[:, :, :3] = rgb_out
            rgba_out[:, :, 3] = mask_out
            bgra = cv2.cvtColor(rgba_out, cv2.COLOR_RGBA2BGRA)
        else:
            # 원본 해상도 (--no-proxy)
            rgb_work = rgb.copy()
            if replace_bg_before_rembg in ("white", "black"):
                rgb_work = replace_background_for_rembg(rgb_work, replace_bg_before_rembg)
            pil_img = Image.fromarray(rgb_work)
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            raw = buf.getvalue()
            try:
                png_bytes = remove_background(
                    raw,
                    use_alpha_matting=use_alpha_matting,
                    model_name=model_name,
                )
            except Exception as e:
                raise RuntimeError(f"프레임 {frame_idx} 배경 제거 실패: {e}") from e
            rgba = np.array(Image.open(io.BytesIO(png_bytes)).convert("RGBA"))
            bgra = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)

        png_path = os.path.join(output_dir, f"{base}_{frame_idx:05d}.png")
        cv2.imwrite(png_path, bgra)
        paths.append(png_path)

        frame_idx += 1
        if progress_callback and frame_idx % 5 == 0:
            progress_callback(frame_idx, total or frame_idx)

    cap.release()

    if output_format == "rgba_video" and paths:
        out_path = os.path.join(output_dir, f"{base}_rgba.mp4")
        png_sequence_to_rgba_video(output_dir, out_path, f"{base}_*.png", fps or vid_fps)
        return (out_path,)
    return tuple(paths) if paths else (output_dir,)


def video_to_rgba_png_sequence(
    input_path: str,
    output_dir: str,
    model_name: str = "isnet-general-use",
    **kwargs,
) -> list[str]:
    """Luma 등 영상 → RGBA PNG 시퀀스 (간편 래퍼)"""
    result = process_video_to_rgba(
        input_path,
        output_dir,
        output_format="png_sequence",
        model_name=model_name,
        **kwargs,
    )
    return [p for p in result if p.endswith(".png")]


def png_sequence_to_rgba_video(
    png_dir: str,
    output_path: str,
    pattern: str = "*.png",
    fps: float = 30,
) -> str:
    """RGBA PNG 시퀀스 → RGBA MP4 (FFmpeg)"""
    import glob

    files = sorted(glob.glob(os.path.join(png_dir, pattern)))
    if not files:
        raise RuntimeError(f"PNG 파일 없음: {png_dir}/{pattern}")

    # libx264 + yuva420p: 가로·세로 모두 짝수 픽셀 필요. concat PNG는 표현식 scale가 먹지 않는 경우가 있어
    # 첫 장에서 짝수 w/h를 계산해 고정 scale 적용 (예: 720x955 → 720x954)
    try:
        with Image.open(files[0]) as _im:
            w0, h0 = _im.size
    except Exception as e:
        raise RuntimeError(f"PNG 크기 읽기 실패: {files[0]}") from e
    w_even = max(2, (w0 // 2) * 2)
    h_even = max(2, (h0 // 2) * 2)

    # ffmpeg concat
    list_path = os.path.join(tempfile.gettempdir(), "concat_list.txt")
    with open(list_path, "w") as f:
        for fp in files:
            f.write(f"file '{os.path.abspath(fp)}'\n")
            f.write(f"duration {1/fps}\n")
        f.write(f"file '{os.path.abspath(files[-1])}'\n")

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-vf", f"scale={w_even}:{h_even}",
        "-c:v", "libx264", "-profile:v", "high",
        "-pix_fmt", "yuva420p",
        "-movflags", "+faststart",
        output_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"FFmpeg 실패: {r.stderr[-1000:]}")
    return output_path


# COCO 80클래스 중 dog (YOLO/ultralytics 공통 인덱스)
_COCO_DOG_CLASS_ID = 16


def _expand_bbox_xyxy(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    frame_w: int,
    frame_h: int,
    pad_frac: float,
) -> Tuple[int, int, int, int]:
    """bbox 중심 유지, 가로·세로 각각 pad_frac만큼 확대 후 프레임에 클램프."""
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    bw = x2 - x1
    bh = y2 - y1
    nw = bw * (1.0 + pad_frac)
    nh = bh * (1.0 + pad_frac)
    nx1 = int(round(cx - nw / 2))
    ny1 = int(round(cy - nh / 2))
    nx2 = int(round(cx + nw / 2))
    ny2 = int(round(cy + nh / 2))
    nx1 = max(0, nx1)
    ny1 = max(0, ny1)
    nx2 = min(frame_w, nx2)
    ny2 = min(frame_h, ny2)
    if nx2 <= nx1 or ny2 <= ny1:
        return 0, 0, 0, 0
    return nx1, ny1, nx2, ny2


def _largest_dog_xyxy(result) -> Optional[Tuple[float, float, float, float]]:
    """Ultralytics 결과에서 COCO dog만, 면적 최대 1개."""
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return None
    best = None
    best_area = 0.0
    for i in range(len(boxes)):
        cls = int(boxes.cls[i])
        if cls != _COCO_DOG_CLASS_ID:
            continue
        xyxy = boxes.xyxy[i].cpu().numpy()
        x1, y1, x2, y2 = float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])
        area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        if area > best_area:
            best_area = area
            best = (x1, y1, x2, y2)
    return best


def _blend_rgb_on_black(rgba: np.ndarray) -> np.ndarray:
    """RGBA → 검정 배경 위에 올린 BGR (8-bit)."""
    r, g, b, a = (
        rgba[:, :, 0].astype(np.float32),
        rgba[:, :, 1].astype(np.float32),
        rgba[:, :, 2].astype(np.float32),
        rgba[:, :, 3].astype(np.float32) / 255.0,
    )
    out = np.zeros_like(rgba[:, :, :3], dtype=np.uint8)
    out[:, :, 2] = (r * a).astype(np.uint8)
    out[:, :, 1] = (g * a).astype(np.uint8)
    out[:, :, 0] = (b * a).astype(np.uint8)
    return out


# NOTE: Used by Modal `process_dog_only_video_modal` (GPU batch / tests) only.
# The main product API is POST /generate-pet-video: photo -> YOLO+rembg -> Luma x2
# (see dog_image_preprocessing + routers/generate.py). Do not wire this into the pet-photo flow.
def process_dog_only_from_video(
    input_video_path: str,
    output_dir: str,
    model_name: str = "isnet-general-use",
    use_alpha_matting: bool = True,
    fps: Optional[float] = None,
    max_frames: Optional[int] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    replace_bg_before_rembg: Optional[str] = None,
    output_resolution: Optional[Tuple[int, int]] = (1280, 720),
    bbox_pad_frac: float = 0.2,
    yolo_model: str = "yolov8n.pt",
) -> Tuple[str, str]:
    """
    YOLO로 프레임마다 강아지(COCO 16)만 검출 → 패딩 크롭 → rembg → 원본 해상도 검정 배경에 합성.
    강아지 없음: 해당 프레임은 검정 + 알파 0.
    여러 마리: 가장 큰 bbox 하나만 사용.

    Returns:
        (rgb_mp4_path, alpha_mp4_path) — 알파는 그레이스케일을 BGR 3채널로 인코딩한 영상.
    """
    try:
        from ultralytics import YOLO
    except ImportError as e:
        raise RuntimeError("ultralytics가 필요합니다: pip install ultralytics") from e

    cap = cv2.VideoCapture(input_video_path)
    if not cap.isOpened():
        raise RuntimeError(f"영상 열기 실패: {input_video_path}")

    vid_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    fps = fps or vid_fps
    orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0

    os.makedirs(output_dir, exist_ok=True)
    base = Path(input_video_path).stem

    if output_resolution:
        out_w, out_h = _compute_720p_size(orig_w, orig_h)
    else:
        out_w, out_h = orig_w, orig_h

    out_w = max(2, (out_w // 2) * 2)
    out_h = max(2, (out_h // 2) * 2)

    rgb_path = os.path.join(output_dir, f"{base}_dog_rgb.mp4")
    alpha_path = os.path.join(output_dir, f"{base}_dog_alpha.mp4")

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer_rgb = cv2.VideoWriter(rgb_path, fourcc, fps, (out_w, out_h))
    writer_alpha = cv2.VideoWriter(alpha_path, fourcc, fps, (out_w, out_h))
    if not writer_rgb.isOpened() or not writer_alpha.isOpened():
        cap.release()
        raise RuntimeError("VideoWriter 열기 실패 (코덱/경로 확인)")

    yolo = YOLO(yolo_model)
    frame_idx = 0

    try:
        while True:
            ret, frame_bgr = cap.read()
            if not ret:
                break
            if max_frames is not None and frame_idx >= max_frames:
                break

            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            rgb = cv2.resize(rgb, (out_w, out_h), interpolation=cv2.INTER_LINEAR)

            full_rgba = np.zeros((out_h, out_w, 4), dtype=np.uint8)

            results = yolo.predict(
                source=rgb,
                classes=[_COCO_DOG_CLASS_ID],
                verbose=False,
                conf=0.25,
            )
            dog_xyxy = _largest_dog_xyxy(results[0]) if results else None

            if dog_xyxy is not None:
                x1, y1, x2, y2 = dog_xyxy
                nx1, ny1, nx2, ny2 = _expand_bbox_xyxy(
                    x1, y1, x2, y2, out_w, out_h, bbox_pad_frac
                )
                if nx2 > nx1 and ny2 > ny1:
                    crop = rgb[ny1:ny2, nx1:nx2].copy()
                    if replace_bg_before_rembg in ("white", "black"):
                        crop = replace_background_for_rembg(crop, replace_bg_before_rembg)
                    pil_crop = Image.fromarray(crop)
                    buf = io.BytesIO()
                    pil_crop.save(buf, format="PNG")
                    try:
                        png_bytes = remove_background(
                            buf.getvalue(),
                            use_alpha_matting=use_alpha_matting,
                            model_name=model_name,
                        )
                    except Exception as e:
                        raise RuntimeError(
                            f"프레임 {frame_idx} 배경 제거 실패: {e}"
                        ) from e
                    rgba_crop = np.array(Image.open(io.BytesIO(png_bytes)).convert("RGBA"))
                    ch, cw = rgba_crop.shape[:2]
                    ph = min(ch, out_h - ny1, ny2 - ny1)
                    pw = min(cw, out_w - nx1, nx2 - nx1)
                    if ph > 0 and pw > 0:
                        full_rgba[ny1 : ny1 + ph, nx1 : nx1 + pw, :] = rgba_crop[:ph, :pw, :]

            bgr_rgb = _blend_rgb_on_black(full_rgba)
            alpha = full_rgba[:, :, 3]
            alpha_bgr = cv2.merge([alpha, alpha, alpha])

            writer_rgb.write(bgr_rgb)
            writer_alpha.write(alpha_bgr)

            frame_idx += 1
            if progress_callback and frame_idx % 5 == 0:
                progress_callback(frame_idx, total or frame_idx)
    finally:
        writer_rgb.release()
        writer_alpha.release()
        cap.release()

    if frame_idx == 0:
        raise RuntimeError("처리된 프레임이 없습니다.")

    return rgb_path, alpha_path
