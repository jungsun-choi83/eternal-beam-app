"""
RGBA PNG에서 손/팔 살색 잔여물 제거 (MediaPipe Hand + 오른쪽 ROI 살색 마스크).

MediaPipe 0.10+ Tasks API 사용. 모델은 %LOCALAPPDATA%\\mediapipe_models\\hand_landmarker.task
에 캐시됩니다.

  cd backend && set PYTHONPATH=. && python -m scripts.remove_hands_rgba --input "path/to.png" --output "out.png"
"""

from __future__ import annotations

import argparse
import os
import urllib.request

import cv2
import numpy as np
from PIL import Image

from mediapipe.tasks.python.core import base_options as bo
from mediapipe.tasks.python.vision import HandLandmarker, HandLandmarkerOptions
from mediapipe.tasks.python.vision.core.image import Image as mpImage
from mediapipe.tasks.python.vision.core.image import ImageFormat

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
)


def _ensure_model(path: str) -> None:
    if os.path.isfile(path) and os.path.getsize(path) > 1000:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    urllib.request.urlretrieve(MODEL_URL, path)


def hand_mask_from_landmarks(
    rgb: np.ndarray,
    w: int,
    h: int,
    model_path: str,
    dilate_px: int = 38,
    blur_px: int = 27,
) -> np.ndarray:
    options = HandLandmarkerOptions(
        base_options=bo.BaseOptions(model_asset_path=model_path),
        num_hands=4,
        min_hand_detection_confidence=0.12,
        min_hand_presence_confidence=0.12,
        min_tracking_confidence=0.12,
    )
    landmarker = HandLandmarker.create_from_options(options)
    try:
        mp_img = mpImage(ImageFormat.SRGB, rgb)
        result = landmarker.detect(mp_img)
    finally:
        landmarker.close()

    mask = np.zeros((h, w), dtype=np.uint8)
    for hand in result.hand_landmarks:
        pts = np.array([[int(lm.x * w), int(lm.y * h)] for lm in hand], dtype=np.int32)
        hull = cv2.convexHull(pts)
        cv2.fillConvexPoly(mask, hull, 255)
    if mask.max() == 0:
        return mask
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_px, dilate_px))
    mask = cv2.dilate(mask, k, iterations=2)
    b = blur_px if blur_px % 2 == 1 else blur_px + 1
    mask = cv2.GaussianBlur(mask, (b, b), 0)
    return mask


def right_strip_skin_mask(
    bgr: np.ndarray,
    w: int,
    h: int,
    x_start_frac: float = 0.80,
    edge_px: int = 14,
) -> np.ndarray:
    """
    오른쪽 **좁은 띠**에서만 살색 후보를 보고, **이미지 오른쪽 끝과 연결된** 영역만 지움.
    (넓은 ROI + YCrCb는 황갈색 털을 피부로 오인해 강아지 몸을 지우는 문제가 있었음.)
    """
    rx0 = int(w * x_start_frac)
    strip = np.zeros((h, w), dtype=np.uint8)
    strip[:, rx0:] = 255

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m1 = cv2.inRange(hsv, np.array([0, 28, 38]), np.array([35, 255, 255]))
    m2 = cv2.inRange(hsv, np.array([155, 28, 38]), np.array([180, 255, 255]))
    skin_hsv = cv2.bitwise_or(m1, m2)

    lum = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    # 검은 배경 위의 살색만 (강아지 밝은 털은 대부분 lum 높거나 strip 밖)
    on_dark = (skin_hsv > 0) & (strip > 0) & (lum < 108)

    cand = on_dark.astype(np.uint8) * 255
    if cand.max() == 0:
        return np.zeros((h, w), dtype=np.float32)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    keep = np.zeros((h, w), dtype=np.uint8)
    right_slice = slice(max(0, w - edge_px), w)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 80:  # 노이즈
            continue
        m = labels == i
        if m[:, right_slice].any():
            keep[m] = 255

    if keep.max() == 0:
        return np.zeros((h, w), dtype=np.float32)

    sk = cv2.dilate(keep, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21)), iterations=2)
    sk = cv2.GaussianBlur(sk.astype(np.float32), (17, 17), 0) / 255.0
    return sk


def process_rgba(arr: np.ndarray, x_start_frac: float = 0.80) -> np.ndarray:
    h, w = arr.shape[:2]
    bgr = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGBA2BGR)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    cache = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "mediapipe_models",
    )
    model_path = os.path.join(cache, "hand_landmarker.task")
    _ensure_model(model_path)

    hm = hand_mask_from_landmarks(rgb, w, h, model_path).astype(np.float32) / 255.0
    sm = right_strip_skin_mask(bgr, w, h, x_start_frac=x_start_frac, edge_px=14)
    remove = np.clip(np.maximum(hm, sm), 0.0, 1.0)

    a = arr[:, :, 3].astype(np.float32)
    arr[:, :, 3] = np.clip(a * (1.0 - remove), 0, 255).astype(np.uint8)
    return arr


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="RGBA PNG 경로")
    ap.add_argument("--output", required=True)
    ap.add_argument(
        "--skin-from-x",
        type=float,
        default=0.80,
        help="이 비율 이후(오른쪽)에서만 살색 후보 탐색. 기본 0.80 (가장자리 손만).",
    )
    args = ap.parse_args()

    img = Image.open(args.input).convert("RGBA")
    arr = np.array(img)
    arr = process_rgba(arr, x_start_frac=args.skin_from_x)
    Image.fromarray(arr).save(args.output)
    print("saved", args.output)


if __name__ == "__main__":
    main()
