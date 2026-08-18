"""
Single-image pipeline: YOLO dog bbox -> padded crop -> rembg -> full-size RGBA PNG.
Used for Luma I2V input (hands/background reduced vs full-frame rembg alone).
"""

from __future__ import annotations

import io
import logging
import os
from typing import Optional, Tuple

import numpy as np
from PIL import Image

from .cutout_errors import SubjectNotDetectedError
from .cutout_service import remove_background
from .luma_service import is_black_tan_dog
from .video_cutout_service import replace_background_for_rembg
from .yolo_input import load_yolo, to_yolo_source

logger = logging.getLogger(__name__)

_COCO_DOG_CLASS_ID = 16


def _allow_full_frame_fallback() -> bool:
    """
    개 미검출 시 "전체 프레임 rembg"로 계속 진행할지 여부. **기본 off**.

    예전 기본 동작은 전체 프레임 rembg 폴백이었는데, 이건 사람·배경까지 그대로
    남긴 누끼를 만들어 유료 Luma 생성까지 흘려보냈다. 개발/디버그 목적이 아니면
    켜지 말 것 (프로덕션에서는 SubjectNotDetectedError → HTTP 422).
    """
    return os.getenv("PET_PREPROCESS_ALLOW_FULLFRAME_FALLBACK", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


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

    Raises:
        SubjectNotDetectedError: 개를 못 찾았을 때. (예전에는 조용히 전체 프레임
            rembg 로 폴백했지만, 그 결과가 사람·배경을 그대로 담은 채 유료 Luma
            생성까지 흘러가서 제거했다. 개발용으로만
            PET_PREPROCESS_ALLOW_FULLFRAME_FALLBACK=1 로 되살릴 수 있다.)
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    rgb = np.array(img)

    replace_bg = "white" if is_black_tan_dog(image_bytes) else "black"

    yolo = load_yolo(yolo_model)
    # to_yolo_source(): ndarray 를 그대로 넘기면 ultralytics 가 BGR 로 해석해
    # R/B 가 뒤바뀐 채 추론된다 (yolo_input.py 참고).
    results = yolo.predict(
        source=to_yolo_source(img),
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

    def _no_subject(reason: str) -> bytes:
        if _allow_full_frame_fallback():
            logger.warning(
                "build_dog_only_nobg_png_bytes: %s — DEV full-frame rembg fallback "
                "(PET_PREPROCESS_ALLOW_FULLFRAME_FALLBACK=1). "
                "This keeps humans/background in the cutout.",
                reason,
            )
            return _full_frame_rembg()
        logger.warning("build_dog_only_nobg_png_bytes: %s (size=%dx%d)", reason, w, h)
        raise SubjectNotDetectedError(
            "No supported pet was detected in the image.",
            diagnostics={
                "detector": "yolo",
                "detector_model": yolo_model,
                "subject_detected": False,
                "subject_class": None,
                "detection_confidence": None,
                "raw_bbox": None,
                "input_width": w,
                "input_height": h,
                "processing_width": w,
                "processing_height": h,
                "pipeline": "dog_only_rembg",
                "reason": reason,
            },
        )

    if dog_xyxy is None:
        return _no_subject("no dog detected")

    x1, y1, x2, y2 = dog_xyxy
    # tight bbox → 크롭용 확장 박스. (rembg 경로라 SAM2 프롬프트와는 무관하지만,
    # 여기서도 "확장 박스는 크롭에만 쓴다"는 규칙은 동일하다.)
    nx1, ny1, nx2, ny2 = _expand_bbox_xyxy(
        x1, y1, x2, y2, w, h, bbox_pad_frac
    )
    if nx2 <= nx1 or ny2 <= ny1:
        return _no_subject("detected bbox collapsed after padding")

    # [IMAGE-TRACE] rembg pet_only 경로의 크롭 지점 (전체 프레임 -> 개 bbox+패딩).
    logger.info(
        "[IMAGE-TRACE] dog_only crop: frame %dx%d -> crop %dx%d at (%d,%d)",
        w,
        h,
        nx2 - nx1,
        ny2 - ny1,
        nx1,
        ny1,
    )
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
        return _no_subject("crop paste region collapsed")
    full_rgba[ny1 : ny1 + ph, nx1 : nx1 + pw, :] = rgba_crop[:ph, :pw, :]

    out = io.BytesIO()
    Image.fromarray(full_rgba).save(out, format="PNG")
    return out.getvalue()
