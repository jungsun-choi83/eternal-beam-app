"""
SAM2 + ViTMatte 기반 알파 매팅 서비스 — rembg 대체 파이프라인.

  1) YOLOv8로 피사체 bbox 검출 (ultralytics — backend에 이미 있는 의존성 재사용)
  2) bbox를 박스 프롬프트로 SAM2(facebook/sam2.1-hiera-*, Apache-2.0)를 돌려
     정밀한 전경 마스크를 얻고, 그 경계를 erode/dilate해 전경/배경/미확정
     (unknown) 트라이맵 생성 — SAM2 로드에 실패하면(의존성 없음/다운로드 실패
     등) OpenCV GrabCut으로 자동 폴백
  3) ViTMatte(hustvl/vitmatte-*, MIT 라이선스)로 트라이맵의 unknown 영역만
     정교하게 알파를 추정 (털 경계 매팅)

이 파이프라인은 rembg의 세그멘테이션 네트워크(u2net/isnet)를 전혀 쓰지 않습니다.

SAM2Matting(FudanCVL/SAM2Matting, 트라이맵 불필요·SAM2 트래커+매팅 헤드 통짜)은
CC BY-NC-SA 4.0(비상업적 전용) 라이선스라 이 프로젝트(결제/구독이 있는 상업
서비스)에는 쓸 수 없어 제외했습니다. 여기서 쓰는 건 그와 다른, Apache-2.0인
베이스 SAM2(세그멘테이션만)라서 라이선스 문제가 없습니다. 자세한 비교는
docs/매팅_및_리깅_AI_조사.md 참고.

환경변수:
  VITMATTE_MODEL       기본 "hustvl/vitmatte-small-composition-1k"
  VITMATTE_YOLO_MODEL  기본 "yolov8n.pt"
  VITMATTE_DEVICE      "cuda" | "cpu" (기본: cuda 사용 가능하면 cuda, 아니면 cpu)
  VITMATTE_SEGMENTER   "sam2"(기본) | "grabcut" — 1단계 마스크 생성 방식
  VITMATTE_SAM2_MODEL  기본 "facebook/sam2.1-hiera-tiny" (가장 가벼운 체크포인트;
                       품질 우선이면 "facebook/sam2.1-hiera-small"/"-base-plus"/"-large")
"""

from __future__ import annotations

import io
import os
from typing import Any, Optional

import numpy as np
from PIL import Image

try:
    import cv2

    CV2_AVAILABLE = True
except Exception:
    CV2_AVAILABLE = False

# 강아지(16)뿐 아니라 다양한 반려동물/동물 피사체까지 bbox 후보로 잡는다 (COCO 클래스).
_COCO_ANIMAL_CLASS_IDS = (14, 15, 16, 17, 18, 19, 20, 21, 22, 23)
# bird, cat, dog, horse, sheep, cow, elephant, bear, zebra, giraffe

BBox = tuple[int, int, int, int]

_yolo_cache: dict[str, Any] = {}
_vitmatte_cache: dict[str, tuple] = {}
_sam2_cache: dict[str, tuple] = {}


def _get_device(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    env = os.getenv("VITMATTE_DEVICE")
    if env:
        return env
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _load_yolo(model_name: str):
    if model_name not in _yolo_cache:
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise RuntimeError("ultralytics가 필요합니다: pip install ultralytics") from e
        _yolo_cache[model_name] = YOLO(model_name)
    return _yolo_cache[model_name]


def _load_vitmatte(model_name: str, device: str):
    key = f"{model_name}::{device}"
    if key not in _vitmatte_cache:
        try:
            from transformers import VitMatteForImageMatting, VitMatteImageProcessor
        except ImportError as e:
            raise RuntimeError(
                "transformers/torch가 필요합니다: pip install transformers torch"
            ) from e
        processor = VitMatteImageProcessor.from_pretrained(model_name)
        model = VitMatteForImageMatting.from_pretrained(model_name)
        model.to(device)
        model.eval()
        _vitmatte_cache[key] = (processor, model)
    return _vitmatte_cache[key]


def _load_sam2(model_name: str, device: str):
    key = f"{model_name}::{device}"
    if key not in _sam2_cache:
        try:
            from transformers import Sam2Model, Sam2Processor
        except ImportError as e:
            raise RuntimeError(
                "SAM2를 쓰려면 transformers>=4.57(SAM2 지원 버전)가 필요합니다: "
                "pip install -U transformers"
            ) from e
        model = Sam2Model.from_pretrained(model_name)
        model.to(device)
        model.eval()
        processor = Sam2Processor.from_pretrained(model_name)
        _sam2_cache[key] = (processor, model)
    return _sam2_cache[key]


def preload_vitmatte_model(
    model_name: Optional[str] = None,
    yolo_model: Optional[str] = None,
) -> bool:
    """서버 시작 시 미리 로드 — cutout_service.preload_default_model과 동일한 목적."""
    try:
        _load_yolo(yolo_model or os.getenv("VITMATTE_YOLO_MODEL", "yolov8n.pt"))
        _load_vitmatte(
            model_name or os.getenv("VITMATTE_MODEL", "hustvl/vitmatte-small-composition-1k"),
            _get_device(),
        )
        if os.getenv("VITMATTE_SEGMENTER", "sam2").strip().lower() == "sam2":
            _load_sam2(
                os.getenv("VITMATTE_SAM2_MODEL", "facebook/sam2.1-hiera-tiny"),
                _get_device(),
            )
        return True
    except Exception:
        return False


def _detect_subject_bbox(rgb: np.ndarray, yolo_model: str) -> Optional[BBox]:
    """가장 큰 동물 클래스 bbox. 못 찾으면 None (호출자가 전체 프레임으로 폴백)."""
    yolo = _load_yolo(yolo_model)
    results = yolo.predict(
        source=rgb,
        classes=list(_COCO_ANIMAL_CLASS_IDS),
        verbose=False,
        conf=0.25,
    )
    if not results:
        return None
    boxes = results[0].boxes
    if boxes is None or len(boxes) == 0:
        return None

    best: Optional[BBox] = None
    best_area = 0.0
    for i in range(len(boxes)):
        xyxy = boxes.xyxy[i].cpu().numpy()
        x1, y1, x2, y2 = (float(v) for v in xyxy)
        area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        if area > best_area:
            best_area = area
            best = (int(x1), int(y1), int(x2), int(y2))
    return best


def _expand_bbox(bbox: BBox, w: int, h: int, pad_frac: float) -> BBox:
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    bw, bh = (x2 - x1) * (1.0 + pad_frac), (y2 - y1) * (1.0 + pad_frac)
    nx1 = max(0, int(round(cx - bw / 2)))
    ny1 = max(0, int(round(cy - bh / 2)))
    nx2 = min(w, int(round(cx + bw / 2)))
    ny2 = min(h, int(round(cy + bh / 2)))
    return nx1, ny1, nx2, ny2


def _binary_mask_to_trimap(fg_binary: np.ndarray, *, unknown_band_px: int = 12) -> np.ndarray:
    """
    전경/배경 0·255 이진 마스크의 경계를 erode/dilate해 트라이맵으로 변환.
    Returns: uint8 (H, W), 값은 {0=배경, 128=미확정, 255=전경}.
    """
    h, w = fg_binary.shape[:2]
    kernel = np.ones((3, 3), np.uint8)
    band_iters = max(1, unknown_band_px // 3)
    sure_fg = cv2.erode(fg_binary, kernel, iterations=band_iters)
    dilated_fg = cv2.dilate(fg_binary, kernel, iterations=band_iters)

    trimap = np.full((h, w), 128, dtype=np.uint8)  # 기본값: 미확정
    trimap[sure_fg > 0] = 255
    trimap[dilated_fg == 0] = 0
    return trimap


def _grabcut_mask(rgb: np.ndarray, bbox: Optional[BBox], *, grabcut_iters: int = 5) -> np.ndarray:
    """GrabCut으로 대략적인 전경 이진 마스크(0/255) 생성 — bbox를 초기 사각형 시드로 사용."""
    if not CV2_AVAILABLE:
        raise RuntimeError("opencv-python-headless가 필요합니다.")

    h, w = rgb.shape[:2]
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    if bbox is None:
        # 피사체 미검출: 중앙 80% 영역을 전경 후보로 가정.
        pad_w, pad_h = int(w * 0.1), int(h * 0.1)
        rect = (pad_w, pad_h, max(1, w - 2 * pad_w), max(1, h - 2 * pad_h))
    else:
        x1, y1, x2, y2 = bbox
        rect = (x1, y1, max(1, x2 - x1), max(1, y2 - y1))

    mask = np.zeros((h, w), dtype=np.uint8)
    bgd_model = np.zeros((1, 65), dtype=np.float64)
    fgd_model = np.zeros((1, 65), dtype=np.float64)
    cv2.grabCut(bgr, mask, rect, bgd_model, fgd_model, grabcut_iters, cv2.GC_INIT_WITH_RECT)

    return np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)


def _sam2_mask(rgb: np.ndarray, bbox: Optional[BBox], model_name: str, device: str) -> np.ndarray:
    """SAM2에 bbox를 박스 프롬프트로 줘서 정밀 전경 이진 마스크(0/255) 생성.

    bbox가 없으면(피사체 미검출) 이미지 중앙 80% 영역을 박스로 대신 준다 —
    SAM2는 점 프롬프트보다 박스 프롬프트가 훨씬 안정적이라 프롬프트 없이는
    호출하지 않는다.
    """
    import torch

    processor, model = _load_sam2(model_name, device)
    h, w = rgb.shape[:2]

    if bbox is None:
        pad_w, pad_h = int(w * 0.1), int(h * 0.1)
        box = [pad_w, pad_h, w - pad_w, h - pad_h]
    else:
        box = list(bbox)

    inputs = processor(images=Image.fromarray(rgb), input_boxes=[[box]], return_tensors="pt").to(device)

    with torch.no_grad():
        outputs = model(**inputs, multimask_output=False)

    masks = processor.post_process_masks(outputs.pred_masks.cpu(), inputs["original_sizes"])[0]
    # masks: (num_boxes=1, num_masks=1, H, W) bool/float — 박스 1개짜리 단일 마스크.
    best_mask = masks[0, 0].numpy()
    return np.where(best_mask > 0, 255, 0).astype(np.uint8)


def _foreground_trimap(
    rgb: np.ndarray,
    bbox: Optional[BBox],
    *,
    segmenter: str,
    sam2_model: str,
    device: str,
    unknown_band_px: int = 12,
) -> tuple[np.ndarray, str]:
    """1단계 세그멘테이션(SAM2 우선, 실패 시 GrabCut 폴백) → 트라이맵.

    Returns: (trimap, 실제로 쓰인 세그멘터 이름 "sam2" | "grabcut")
    """
    if segmenter == "sam2":
        try:
            fg_binary = _sam2_mask(rgb, bbox, sam2_model, device)
            return _binary_mask_to_trimap(fg_binary, unknown_band_px=unknown_band_px), "sam2"
        except Exception:
            pass  # 의존성 누락/다운로드 실패/OOM 등 — GrabCut으로 폴백
    fg_binary = _grabcut_mask(rgb, bbox)
    return _binary_mask_to_trimap(fg_binary, unknown_band_px=unknown_band_px), "grabcut"


def _pad_to_multiple(arr: np.ndarray, multiple: int = 32) -> tuple[np.ndarray, int, int]:
    """ViTMatte는 입력 해상도가 32의 배수일 때 가장 안정적 — 우측/하단에 반사 패딩."""
    h, w = arr.shape[:2]
    pad_h = (multiple - h % multiple) % multiple
    pad_w = (multiple - w % multiple) % multiple
    if pad_h == 0 and pad_w == 0:
        return arr, h, w
    pad_spec = ((0, pad_h), (0, pad_w)) if arr.ndim == 2 else ((0, pad_h), (0, pad_w), (0, 0))
    return np.pad(arr, pad_spec, mode="reflect"), h, w


def _run_vitmatte(rgb: np.ndarray, trimap: np.ndarray, model_name: str, device: str) -> np.ndarray:
    """Returns float32 alpha in [0, 1], 원본(패딩 전) (H, W) 크기로 크롭해서 반환."""
    import torch

    processor, model = _load_vitmatte(model_name, device)

    padded_rgb, orig_h, orig_w = _pad_to_multiple(rgb)
    padded_trimap, _, _ = _pad_to_multiple(trimap)

    inputs = processor(
        images=Image.fromarray(padded_rgb),
        trimaps=Image.fromarray(padded_trimap),
        return_tensors="pt",
    )
    inputs = {k: v.to(device) for k, v in inputs.items()}

    with torch.no_grad():
        alphas = model(**inputs).alphas

    alpha = alphas[0, 0].detach().cpu().numpy()
    alpha = alpha[:orig_h, :orig_w]
    return np.clip(alpha, 0.0, 1.0)


def matte_foreground_with_meta(
    image_bytes: bytes,
    *,
    model_name: Optional[str] = None,
    yolo_model: Optional[str] = None,
    device: Optional[str] = None,
    bbox_pad_frac: float = 0.15,
    segmenter: Optional[str] = None,
    sam2_model: Optional[str] = None,
) -> tuple[bytes, dict]:
    """
    사진 → (RGBA PNG bytes, 진단 메타). 배경은 투명, 털 경계는 매팅 처리.
    rembg는 전혀 쓰지 않는다: YOLO bbox → SAM2(폴백: GrabCut) trimap → ViTMatte.
    """
    resolved_model = model_name or os.getenv("VITMATTE_MODEL", "hustvl/vitmatte-small-composition-1k")
    resolved_yolo = yolo_model or os.getenv("VITMATTE_YOLO_MODEL", "yolov8n.pt")
    resolved_device = _get_device(device)
    resolved_segmenter = (segmenter or os.getenv("VITMATTE_SEGMENTER", "sam2")).strip().lower()
    resolved_sam2_model = sam2_model or os.getenv("VITMATTE_SAM2_MODEL", "facebook/sam2.1-hiera-tiny")

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    rgb = np.array(img)
    h, w = rgb.shape[:2]

    raw_bbox = _detect_subject_bbox(rgb, resolved_yolo)
    bbox = _expand_bbox(raw_bbox, w, h, bbox_pad_frac) if raw_bbox is not None else None

    trimap, used_segmenter = _foreground_trimap(
        rgb,
        bbox,
        segmenter=resolved_segmenter,
        sam2_model=resolved_sam2_model,
        device=resolved_device,
    )
    alpha = _run_vitmatte(rgb, trimap, resolved_model, resolved_device)

    alpha_u8 = (alpha * 255.0).astype(np.uint8)
    rgba = np.dstack([rgb, alpha_u8])

    out = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(out, format="PNG")

    meta = {
        "method": "vitmatte",
        "segmenter": used_segmenter,
        "segmenter_requested": resolved_segmenter,
        "model": resolved_model,
        "sam2_model": resolved_sam2_model if used_segmenter == "sam2" else None,
        "device": resolved_device,
        "subject_detected": raw_bbox is not None,
        "bbox": list(bbox) if bbox else None,
    }
    return out.getvalue(), meta


def matte_foreground(image_bytes: bytes, **kwargs: Any) -> bytes:
    """matte_foreground_with_meta()의 PNG bytes만 반환하는 얇은 래퍼."""
    png_bytes, _meta = matte_foreground_with_meta(image_bytes, **kwargs)
    return png_bytes
