"""
SAM2 역(inverse) 마스크 + LaMa 인페인팅 — "내 사진으로 나만의 배경 만들기"
(custom_photo_bg) 파이프라인의 1~2단계.

  1) vitmatte_service의 SAM2 로더(_load_sam2/_sam2_mask)를 그대로 재사용해 사용자
     사진에서 강아지(피사체) 이진 마스크를 구한다 — 새 SAM2 로딩 코드를 만들지
     않음(요청사항 그대로).
  2) 그 마스크를 반전(역마스크) + 약하게 dilate해서 "강아지가 있던 구멍(hole)"
     영역을 얻는다 — 순수 반전만 쓰면 털 경계에 얇게 강아지 잔상이 남을 수 있어
     dilate로 여유를 둔다(live_portrait_postprocess.py가 이미 같은 이유로 마스크를
     dilate하는 것과 동일한 트레이드오프).
  3) LaMa(big-lama, Apache-2.0, `simple-lama-inpainting` pip 패키지)로 그 구멍을
     채워 "강아지 없는 완전한 배경"을 만든다.

## 왜 LaMa인가 (Stable Diffusion Inpainting과 비교)

이 유스케이스는 "이미 있는 사물을 지우고 그 자리를 자연스럽게 메꾼다"(object
removal inpainting)이지, 새로운 걸 창작하는 게 아니다(텍스트 프롬프트로 새 배경
요소를 생성하는 게 아니라, 사진에 원래 있던 배경 텍스처를 복원/확장하는 것).
LaMa는 정확히 이 문제만을 위해 만들어진 가볍고 순수 CNN(Fourier Convolution) 기반
모델이라 이 케이스에 더 적합하다고 판단했다:

  - 라이선스: LaMa(advimman/lama)는 Apache-2.0 — 상업적 이용 가능. 체크포인트도
    Apache-2.0 하에 배포됨(https://github.com/advimman/lama, HuggingFace
    smartywu/big-lama 미러).
  - 모델 크기: big-lama 체크포인트 ~200MB(safetensors 변환본) ~ 400MB(원본
    TorchScript .pt) — SD Inpainting(수 GB, UNet+VAE+텍스트 인코더 전체)보다
    훨씬 작다.
  - 텍스트 프롬프트 불필요: 우리는 "무엇을 그릴지"가 아니라 "주변 텍스처로 구멍만
    메꾸면" 되므로 프롬프트 기반 제어가 필요 없다 — SD Inpainting의 장점(프롬프트로
    새 콘텐츠 생성)이 오히려 여기서는 리스크(엉뚱한 사물이 배경에 생성될 위험)다.
  - 속도/자원: CNN 순전파 1회로 끝나 SD의 반복 디퓨전 스텝(수십 회)보다 훨씬
    빠르고, 공식 이슈들에서 CPU로도 동작 확인됨(우리는 로컬 RTX 4090에서 돌리므로
    속도 문제는 실질적으로 없음, CPU 폴백 여지도 있다는 의미).
  - `simple-lama-inpainting`(PyPI, Apache-2.0) pip 패키지가 모델 다운로드/추론을
    감싸줘서 별도 리포 clone 없이 `pip install`만으로 통합 가능
    (LivePortrait처럼 서브프로세스로 별도 리포를 부르는 무거운 통합이 필요 없음).

결론: 이 케이스엔 LaMa가 더 적합 — Stable Diffusion Inpainting은 쓰지 않는다.

환경변수:
  BACKGROUND_INPAINT_DEVICE       "cuda" | "cpu" (기본: cuda 가능하면 cuda)
  BACKGROUND_INPAINT_MASK_DILATE_PX  기본 "20" (강아지 구멍 여유 마진, px)
  BACKGROUND_INPAINT_SEGMENTER    "sam2"(기본) | "grabcut" — vitmatte_service와 동일 옵션
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

# 요청사항: vitmatte_service의 SAM2 로더/마스크 함수를 그대로 재사용(새로 구현하지 않음).
from .vitmatte_service import (
    _detect_subject_bbox,
    _expand_bbox,
    _get_device,
    _grabcut_mask,
    _sam2_mask,
)

_lama_cache: dict[str, Any] = {}


def _mask_dilate_px() -> int:
    return max(0, int(os.getenv("BACKGROUND_INPAINT_MASK_DILATE_PX", "20")))


def _load_lama(device: str):
    """simple-lama-inpainting의 SimpleLama(big-lama, Apache-2.0) 지연 로딩 + 캐시."""
    if device not in _lama_cache:
        try:
            from simple_lama_inpainting import SimpleLama
        except ImportError as e:
            raise RuntimeError(
                "simple-lama-inpainting이 필요합니다: pip install simple-lama-inpainting "
                "(최초 실행 시 big-lama 체크포인트를 자동 다운로드합니다)"
            ) from e
        _lama_cache[device] = SimpleLama(device=device)
    return _lama_cache[device]


def preload_lama_model(device: Optional[str] = None) -> bool:
    """서버/워커 기동 시 미리 로드 — preload_vitmatte_model()과 동일한 목적(선택적)."""
    try:
        _load_lama(_get_device(device))
        return True
    except Exception:
        return False


def _dog_hole_mask(
    rgb: np.ndarray,
    *,
    segmenter: str,
    device: str,
    dilate_px: int,
) -> tuple[np.ndarray, str]:
    """
    강아지(피사체) 이진 마스크(SAM2 우선, 실패 시 GrabCut 폴백) → dilate해서
    "메꿔야 할 구멍" 마스크(255=인페인팅 대상, 0=원본 유지) 반환.

    Returns: (hole_mask uint8 (H, W), 실제로 쓰인 세그멘터 이름)
    """
    h, w = rgb.shape[:2]
    raw_bbox = _detect_subject_bbox(rgb, os.getenv("VITMATTE_YOLO_MODEL", "yolov8n.pt"))
    bbox = _expand_bbox(raw_bbox, w, h, 0.15) if raw_bbox is not None else None

    used_segmenter = segmenter
    if segmenter == "sam2":
        try:
            sam2_model = os.getenv("VITMATTE_SAM2_MODEL", "facebook/sam2.1-hiera-tiny")
            fg_binary = _sam2_mask(rgb, bbox, sam2_model, device)
            used_segmenter = "sam2"
        except Exception:
            fg_binary = _grabcut_mask(rgb, bbox)
            used_segmenter = "grabcut"
    else:
        fg_binary = _grabcut_mask(rgb, bbox)
        used_segmenter = "grabcut"

    if dilate_px > 0 and CV2_AVAILABLE:
        kernel = np.ones((dilate_px, dilate_px), np.uint8)
        fg_binary = cv2.dilate(fg_binary, kernel, iterations=1)

    return fg_binary, used_segmenter


def inpaint_background_from_photo(
    image_bytes: bytes,
    *,
    device: Optional[str] = None,
    segmenter: Optional[str] = None,
    mask_dilate_px: Optional[int] = None,
) -> tuple[bytes, dict]:
    """
    사용자 원본 사진(강아지 포함) → (강아지가 지워지고 자연스럽게 메꿔진 배경
    PNG bytes, 진단 메타).

    이 결과 이미지에는 강아지가 없고, 구멍도 없어야 한다 — 다음 단계
    (background_video_pipeline.py)가 이 이미지를 그대로 Luma에 보내 앰비언트
    모션 영상을 만든다.
    """
    resolved_device = _get_device(device)
    resolved_segmenter = (segmenter or os.getenv("BACKGROUND_INPAINT_SEGMENTER", "sam2")).strip().lower()
    resolved_dilate = mask_dilate_px if mask_dilate_px is not None else _mask_dilate_px()

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    rgb = np.array(img)

    hole_mask, used_segmenter = _dog_hole_mask(
        rgb, segmenter=resolved_segmenter, device=resolved_device, dilate_px=resolved_dilate
    )

    hole_frac = float(np.count_nonzero(hole_mask)) / float(hole_mask.size or 1)

    lama = _load_lama(resolved_device)
    mask_img = Image.fromarray(hole_mask).convert("L")
    result_img = lama(img, mask_img)

    out = io.BytesIO()
    result_img.save(out, format="PNG")

    meta = {
        "method": "lama",
        "segmenter": used_segmenter,
        "segmenter_requested": resolved_segmenter,
        "device": resolved_device,
        "mask_dilate_px": resolved_dilate,
        "hole_area_fraction": round(hole_frac, 4),
    }
    return out.getvalue(), meta
