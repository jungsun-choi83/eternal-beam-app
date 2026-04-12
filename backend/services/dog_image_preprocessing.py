"""
Single-image pipeline: YOLO dog bbox -> padded crop -> rembg -> full-size RGBA PNG.
Used for Luma I2V input (hands/background reduced vs full-frame rembg alone).
"""

from __future__ import annotations

import io
from typing import Optional, Tuple

import numpy as np
from PIL import Image

from .cutout_service import remove_background
from .luma_service import is_black_tan_dog
from .video_cutout_service import replace_background_for_rembg

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


def build_dog_only_nobg_png_bytes(
    image_bytes: bytes,
    bbox_pad_frac: float = 0.2,
    yolo_model: str = "yolov8n.pt",
    rembg_model: str = "isnet-general-use",
    use_alpha_matting: bool = True,
) -> bytes:
    """
    Returns PNG bytes (RGBA): dog-only cutout aligned to original image dimensions.
    If no dog is detected, falls back to full-frame rembg (best-effort).
    """
    try:
        from ultralytics import YOLO
    except ImportError as e:
        raise RuntimeError("ultralytics is required: pip install ultralytics") from e

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    rgb = np.array(img)

    replace_bg = "white" if is_black_tan_dog(image_bytes) else "black"

    yolo = YOLO(yolo_model)
    results = yolo.predict(
        source=rgb,
        classes=[_COCO_DOG_CLASS_ID],
        verbose=False,
        conf=0.25,
    )
    dog_xyxy = _largest_dog_xyxy(results[0]) if results else None

    def _full_frame_rembg() -> bytes:
        work = rgb.copy()
        if replace_bg in ("white", "black"):
            work = replace_background_for_rembg(work, replace_bg)
        pil = Image.fromarray(work)
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        return remove_background(
            buf.getvalue(),
            use_alpha_matting=use_alpha_matting,
            model_name=rembg_model,
        )

    if dog_xyxy is None:
        return _full_frame_rembg()

    x1, y1, x2, y2 = dog_xyxy
    nx1, ny1, nx2, ny2 = _expand_bbox_xyxy(
        x1, y1, x2, y2, w, h, bbox_pad_frac
    )
    if nx2 <= nx1 or ny2 <= ny1:
        return _full_frame_rembg()

    crop = rgb[ny1:ny2, nx1:nx2].copy()
    if replace_bg in ("white", "black"):
        crop = replace_background_for_rembg(crop, replace_bg)
    pil_crop = Image.fromarray(crop)
    buf = io.BytesIO()
    pil_crop.save(buf, format="PNG")
    png_bytes = remove_background(
        buf.getvalue(),
        use_alpha_matting=use_alpha_matting,
        model_name=rembg_model,
    )
    rgba_crop = np.array(Image.open(io.BytesIO(png_bytes)).convert("RGBA"))
    ch, cw = rgba_crop.shape[:2]

    full_rgba = np.zeros((h, w, 4), dtype=np.uint8)
    ph = min(ch, h - ny1, ny2 - ny1)
    pw = min(cw, w - nx1, nx2 - nx1)
    if ph <= 0 or pw <= 0:
        return _full_frame_rembg()
    full_rgba[ny1 : ny1 + ph, nx1 : nx1 + pw, :] = rgba_crop[:ph, :pw, :]

    out = io.BytesIO()
    Image.fromarray(full_rgba).save(out, format="PNG")
    return out.getvalue()
