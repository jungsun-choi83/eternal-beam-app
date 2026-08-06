"""
Ultralytics YOLO 입력 채널 순서 정규화.

배경
----
Ultralytics 는 `np.ndarray` source 를 **HWC / BGR**(OpenCV 관례)로 해석한다.
이 저장소는 두 곳에서 `Image.open(...).convert("RGB")` 로 만든 **RGB** 배열을
그대로 `yolo.predict(source=rgb)` 에 넘기고 있었다 — 즉 R 과 B 가 뒤바뀐 채로
추론했다. (`_grabcut_mask` 는 `cv2.cvtColor(rgb, COLOR_RGB2BGR)` 를 제대로
호출하고 있어서, ndarray=BGR 규칙 자체는 알고 있었으나 ultralytics 쪽만 빠진
것으로 보인다.)

이 저장소의 `public/demo/goya-cutout.png` 로 실측한 결과:

    numpy RGB (기존 코드)   -> dog conf=0.9402  box=[241, 17, 456, 354]
    numpy BGR (채널 반전)   -> dog conf=0.9510  box=[241, 18, 456, 354]
    PIL RGB Image          -> dog conf=0.9510  box=[241, 18, 456, 354]

`numpy BGR` 과 `PIL RGB` 결과가 **정확히 일치**한다는 점이 근거다. 즉 PIL 이미지를
그대로 넘기는 것과 ndarray 를 BGR 로 뒤집어 넘기는 것이 동일하며, 기존 코드만
다른 결과를 낸다.

배경이 단색이고 피사체가 큰 이 샘플에서는 차이가 0.011 로 작지만, 채널이 바뀌면
색상 의존적으로 신뢰도가 흔들린다 — `conf=0.25` 임계값 근처의 사진(황갈색/적갈색
털처럼 R 채널이 지배적인 경우)에서는 검출 성공/실패가 갈릴 수 있다.

정책
----
**PIL `Image` 를 그대로 넘기는 것을 표준 입력 경로로 삼는다.** ndarray 를 뒤집는
것보다 의도가 드러나고, 나중에 누가 `.copy()` 나 슬라이싱을 만져도 깨지지 않는다.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional, Union

import numpy as np
from PIL import Image

YoloSource = Union[Image.Image, np.ndarray]

_yolo_cache: dict[str, Any] = {}


def load_yolo(model_name: str) -> Any:
    """YOLO 모델 로더 + 프로세스 캐시.

    예전에는 `dog_image_preprocessing` 이 요청마다 `YOLO(...)` 를 새로 만들었다.
    (vitmatte_service 는 캐시하고 있었음) 두 경로가 같은 캐시를 쓰도록 여기로 모았다.
    """
    if model_name not in _yolo_cache:
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise RuntimeError("ultralytics가 필요합니다: pip install ultralytics") from e
        _yolo_cache[model_name] = YOLO(model_name)
    return _yolo_cache[model_name]


def to_yolo_source(image: YoloSource) -> Image.Image:
    """
    YOLO 표준 입력으로 변환한다.

    - PIL Image  → RGB 로 보장해서 그대로 사용 (ultralytics 가 RGB 로 해석)
    - np.ndarray → **RGB 배열이라고 가정**하고 PIL 로 감싼다.
      (ndarray 를 직접 넘기면 ultralytics 가 BGR 로 읽어 R/B 가 뒤바뀐다)
    """
    if isinstance(image, Image.Image):
        return image if image.mode == "RGB" else image.convert("RGB")

    arr = np.asarray(image)
    if arr.ndim == 2:
        return Image.fromarray(arr).convert("RGB")
    if arr.ndim == 3 and arr.shape[2] == 4:
        return Image.fromarray(arr.astype(np.uint8), mode="RGBA").convert("RGB")
    if arr.ndim == 3 and arr.shape[2] == 3:
        return Image.fromarray(arr.astype(np.uint8), mode="RGB")
    raise ValueError(f"Unsupported image array shape for YOLO input: {arr.shape}")


def _summarize_boxes(result: Any, names: Any) -> list[dict[str, Any]]:
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return []
    out: list[dict[str, Any]] = []
    for i in range(len(boxes)):
        cls_id = int(boxes.cls[i])
        xyxy = [float(v) for v in boxes.xyxy[i]]
        out.append(
            {
                "class_id": cls_id,
                "class_name": (names or {}).get(cls_id, str(cls_id))
                if hasattr(names, "get")
                else str(cls_id),
                "confidence": round(float(boxes.conf[i]), 4),
                "bbox": [int(round(v)) for v in xyxy],
            }
        )
    out.sort(key=lambda d: d["confidence"], reverse=True)
    return out


def compare_yolo_color_paths(
    image: Image.Image,
    yolo_model: Any,
    *,
    classes: Optional[Iterable[int]] = None,
    conf: float = 0.10,
) -> dict[str, list[dict[str, Any]]]:
    """
    같은 이미지를 세 가지 입력 방식으로 YOLO 에 넣고 결과를 비교하는 진단 유틸.

    반환:
        {
          "numpy_rgb": [...],   # 기존(버그) 경로 — ultralytics 가 BGR 로 오해함
          "numpy_bgr": [...],   # 채널 반전
          "pil_rgb":   [...],   # 표준 경로 (to_yolo_source)
        }

    `numpy_bgr` 과 `pil_rgb` 가 같고 `numpy_rgb` 만 다르면, ndarray 가 BGR 로
    해석된다는 뜻이다. 테스트(test_yolo_input.py)와 로컬 통합 점검
    (`scripts/diagnose_yolo_color.py`)이 이 함수를 공유한다.
    """
    rgb_img = image if image.mode == "RGB" else image.convert("RGB")
    rgb = np.array(rgb_img)
    bgr = np.ascontiguousarray(rgb[:, :, ::-1])

    kwargs: dict[str, Any] = {"verbose": False, "conf": conf}
    if classes is not None:
        kwargs["classes"] = list(classes)

    names = getattr(yolo_model, "names", {})
    sources = {
        "numpy_rgb": rgb,
        "numpy_bgr": bgr,
        "pil_rgb": to_yolo_source(rgb_img),
    }

    report: dict[str, list[dict[str, Any]]] = {}
    for label, source in sources.items():
        results = yolo_model.predict(source=source, **kwargs)
        report[label] = _summarize_boxes(results[0], names) if results else []
    return report
