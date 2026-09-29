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

Phase 1 (관측 가능성 + 안전장치) 변경 요약:
  - YOLO 입력을 yolo_input.to_yolo_source() 로 통일 (ndarray=BGR 오해 버그 수정)
  - SAM2 프롬프트 bbox(tight)와 크롭 bbox(padded)를 분리
  - 피사체 미검출 시 "중앙 80% 사각형" 폴백 제거 → SubjectNotDetectedError
  - SAM2 실패를 삼키지 않고 로깅 + 메타에 사유 기록
  - 마스크/알파 면적 및 사각형 유사도 검사 후에만 성공 반환
  - 모든 결과(성공/실패)가 동일한 진단 필드를 남김

환경변수:
  VITMATTE_MODEL       기본 "hustvl/vitmatte-small-composition-1k"
  VITMATTE_YOLO_MODEL  기본 "yolov8n.pt"
  VITMATTE_DEVICE      "cuda" | "cpu" (기본: cuda 사용 가능하면 cuda, 아니면 cpu)
  VITMATTE_SEGMENTER   "sam2"(기본) | "grabcut" — 1단계 마스크 생성 방식
  VITMATTE_SAM2_MODEL  기본 "facebook/sam2.1-hiera-tiny" (가장 가벼운 체크포인트;
                       품질 우선이면 "facebook/sam2.1-hiera-small"/"-base-plus"/"-large")
  VITMATTE_YOLO_CONF   기본 0.25 — YOLO 검출 신뢰도 임계값
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np
from PIL import Image

from .cutout_errors import (
    AlphaEmptyError,
    MaskTooLargeError,
    MaskTooSmallError,
    RectangleLikeMaskError,
    SubjectNotDetectedError,
)
from . import rss_trace
from .person_prompting import PersonAwareResult, apply_person_aware_prompting
from .yolo_input import load_yolo, to_yolo_source

logger = logging.getLogger(__name__)

try:
    import cv2

    CV2_AVAILABLE = True
except Exception:  # pragma: no cover - 환경에 따라 다름
    CV2_AVAILABLE = False

# 강아지(16)뿐 아니라 다양한 반려동물/동물 피사체까지 bbox 후보로 잡는다 (COCO 클래스).
_COCO_ANIMAL_CLASS_IDS = (14, 15, 16, 17, 18, 19, 20, 21, 22, 23)
# bird, cat, dog, horse, sheep, cow, elephant, bear, zebra, giraffe

#: Phase 2B — 사람. 마스크에서 빼야 할 대상.
COCO_PERSON_CLASS_ID = 0

#: 사람 인지 재프롬프팅 on/off (문제 생기면 즉시 Phase 2A 동작으로 롤백).
PERSON_AWARE_PROMPTING_ENABLED = os.getenv(
    "VITMATTE_PERSON_AWARE_PROMPTING", "1"
).strip().lower() in ("1", "true", "yes")

_COCO_ANIMAL_NAMES = {
    14: "bird",
    15: "cat",
    16: "dog",
    17: "horse",
    18: "sheep",
    19: "cow",
    20: "elephant",
    21: "bear",
    22: "zebra",
    23: "giraffe",
}

BBox = tuple[int, int, int, int]

# --- 품질 게이트 임계값 -------------------------------------------------------
# 초기값은 보수적으로 잡았다(명백히 잘못된 결과만 거른다). 실제 실패 사진을
# 모아 분포를 본 뒤 조정할 것 — 여기 숫자에 과적합하지 말 것.

#: 전경 마스크가 전체 프레임에서 차지하는 최소 비율. 이보다 작으면 피사체를
#: 제대로 못 잡은 것으로 본다.
MIN_MASK_AREA_FRACTION = float(os.getenv("CUTOUT_MIN_MASK_AREA_FRACTION", "0.03"))

#: 최대 비율. 이보다 크면 배경까지 전경으로 삼킨 것으로 본다.
MAX_MASK_AREA_FRACTION = float(os.getenv("CUTOUT_MAX_MASK_AREA_FRACTION", "0.85"))

#: 알파가 "있다"고 볼 최소값(0~255). 이하 픽셀은 사실상 투명으로 취급.
ALPHA_PRESENCE_THRESHOLD = int(os.getenv("CUTOUT_ALPHA_PRESENCE_THRESHOLD", "16"))

#: 최종 알파의 최소 면적 비율. 0 이면 완전 투명 PNG 가 나온 것.
MIN_ALPHA_AREA_FRACTION = float(os.getenv("CUTOUT_MIN_ALPHA_AREA_FRACTION", "0.01"))

#: 사각형 유사도 = 마스크 면적 / 마스크 bounding box 면적.
#: 꽉 찬 직사각형은 1.0 에 수렴한다. 실제 동물 실루엣은 보통 0.45~0.75.
#: 0.92 는 "거의 완전한 직사각형"만 걸러내려는 보수적인 초기값.
RECTANGLE_FILL_RATIO_THRESHOLD = float(os.getenv("CUTOUT_RECTANGLE_FILL_RATIO", "0.92"))

#: 사각형 유사 마스크를 422 로 거절할지. 0 이면 메타에 플래그만 남기고 통과.
REJECT_RECTANGLE_LIKE = os.getenv("CUTOUT_REJECT_RECTANGLE_LIKE", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)

#: 디버그 아티팩트 수집 허용 여부(프로덕션 기본 off).
DEBUG_ARTIFACTS_ENABLED = os.getenv("CUTOUT_DEBUG_ENABLED", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)

# --- 접지/투영 그림자 알파 억제 ------------------------------------------------
# ViTMatte 는 트라이맵의 미확정 밴드 안에서 "반투명한 것"을 성실하게 살린다.
# 발밑 접지 그림자와 바닥에 드리운 투영 그림자는 정확히 그 성질이라 **전경
# 알파로 살아남았고**, 하류(키프레임·모션·packed-alpha)까지 얼룩으로 번졌다.
#
# 여기서 하는 일은 "그림자는 배경의 감광(減光) 버전"이라는 물리적 사실 하나를
# 증거로 쓰는 것뿐이다: 색조(chromaticity)는 배경과 같고 휘도만 낮다. 털은
# 자기 색을 가지므로 이 조건에 걸리지 않는다. 판정은 세그멘테이션 **코어 밖**
# 에서만 이뤄지고, 지우려는 양이 임계를 넘으면 아무것도 하지 않는다 —
# 피사체를 깎느니 그림자를 남긴다 (Phase 6.7: 측정 불가 ≠ 손상).
SHADOW_SUPPRESSION_ENABLED = os.getenv(
    "CUTOUT_SHADOW_SUPPRESSION", "1"
).strip().lower() in ("1", "true", "yes")

#: 세그멘테이션 마스크를 이만큼 침식한 영역 = 절대 건드리지 않는 펫 코어.
SHADOW_CORE_ERODE_PX = int(os.getenv("CUTOUT_SHADOW_CORE_ERODE_PX", "6"))

#: 이보다 불투명한 알파는 손대지 않는다 (확실한 전경).
SHADOW_MAX_ALPHA = float(os.getenv("CUTOUT_SHADOW_MAX_ALPHA", "0.92"))

#: 배경 대비 휘도비 하한 — 이보다 어두우면 그림자가 아니라 검은 털/코다.
SHADOW_MIN_LUMA_RATIO = float(os.getenv("CUTOUT_SHADOW_MIN_LUMA_RATIO", "0.35"))

#: 배경 대비 휘도비 상한 — 이보다 밝으면 그림자가 아니다(그림자는 어둡다).
SHADOW_MAX_LUMA_RATIO = float(os.getenv("CUTOUT_SHADOW_MAX_LUMA_RATIO", "0.97"))

#: 배경과의 정규화 색조 거리 상한. 그림자는 배경의 색조를 보존한다.
SHADOW_MAX_CHROMA_DELTA = float(os.getenv("CUTOUT_SHADOW_MAX_CHROMA_DELTA", "0.055"))

#: 전체 알파 질량의 이 비율을 넘게 지우려 하면 억제를 통째로 포기한다.
SHADOW_MAX_REMOVED_FRACTION = float(
    os.getenv("CUTOUT_SHADOW_MAX_REMOVED_FRACTION", "0.30")
)

#: 배경 행 모델을 만들 때 한 행에 필요한 최소 배경 픽셀 수.
_SHADOW_MIN_ROW_BG_PX = 8

# --- Phase 2A: SAM2 후보 선택 -------------------------------------------------
# SAM2 는 박스 프롬프트 1개에 대해 서로 다른 해석 3개를 낸다(예: 개 전체 / 몸통만 /
# 개+바닥). Phase 1 은 multimask_output=False 로 첫 번째만 받아썼다. 여기서는 셋을
# 모두 받아 **모델이 예측한 IoU 하나만 믿지 않고** 형태 지표까지 섞어 고른다.
# (SAM2 의 predicted IoU 는 "이 마스크가 얼마나 정확한가"에 대한 자기 확신이지,
#  "이게 우리가 원하는 피사체인가"에 대한 답이 아니다.)

#: multimask 사용 여부. 0 으로 두면 Phase 1 과 동일하게 단일 마스크만 받는다.
SAM2_MULTIMASK_ENABLED = os.getenv("VITMATTE_SAM2_MULTIMASK", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)

#: 가중치 합은 1.0. 실제 실패 사진을 모아 조정할 것 — 지금은 보수적인 초기값.
SAM2_W_IOU = float(os.getenv("SAM2_SELECT_W_IOU", "0.45"))
SAM2_W_CONTAINMENT = float(os.getenv("SAM2_SELECT_W_CONTAINMENT", "0.25"))
SAM2_W_CENTER = float(os.getenv("SAM2_SELECT_W_CENTER", "0.15"))
SAM2_W_SHAPE = float(os.getenv("SAM2_SELECT_W_SHAPE", "0.15"))

#: 동물 실루엣의 bbox 충전율이 보통 들어가는 구간. 이 안이면 형태 점수 만점,
#: 밖으로 벗어날수록 감점 (1.0 에 가까우면 직사각형 = 배경까지 삼킨 것).
SAM2_SHAPE_FILL_LOW = float(os.getenv("SAM2_SHAPE_FILL_LOW", "0.45"))
SAM2_SHAPE_FILL_HIGH = float(os.getenv("SAM2_SHAPE_FILL_HIGH", "0.85"))

_vitmatte_cache: dict[str, tuple] = {}
_sam2_cache: dict[str, tuple] = {}
#: 모델 키 → 청크 어텐션 설치 상태 (진단용, vitdet_chunked_attention.install_chunked_attention 결과)
_vitmatte_attention_status: dict[str, dict] = {}


@dataclass
class SubjectDetection:
    """YOLO 검출 결과 1건 — bbox 는 패딩 없는 tight box."""

    bbox: BBox
    class_id: int
    class_name: str
    confidence: float


@dataclass
class Sam2Candidate:
    """SAM2 가 낸 마스크 후보 1개 + 선택에 쓰인 모든 지표."""

    index: int
    mask: np.ndarray = field(repr=False)
    predicted_iou: Optional[float] = None
    area_fraction: float = 0.0
    bbox_fill_ratio: Optional[float] = None
    #: 마스크 픽셀 중 프롬프트 bbox 안에 들어있는 비율 (밖으로 샌 정도의 반대)
    prompt_containment: float = 0.0
    #: 마스크 중심이 프롬프트 bbox 중심과 얼마나 일치하는가 (1.0 = 완전 일치)
    center_consistency: float = 0.0
    rectangle_like: bool = False
    valid: bool = True
    rejected_reason: Optional[str] = None
    selection_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """진단용 — 마스크 배열은 제외."""
        return {
            "index": self.index,
            "predicted_iou": self.predicted_iou,
            "area_fraction": round(self.area_fraction, 4),
            "bbox_fill_ratio": self.bbox_fill_ratio,
            "prompt_containment": round(self.prompt_containment, 4),
            "center_consistency": round(self.center_consistency, 4),
            "rectangle_like": self.rectangle_like,
            "valid": self.valid,
            "rejected_reason": self.rejected_reason,
            "selection_score": round(self.selection_score, 4),
        }


@dataclass
class SegmentationOutcome:
    """1단계 세그멘테이션 결과 + 어떤 경로로 나왔는지에 대한 진단."""

    fg_binary: np.ndarray
    trimap: np.ndarray
    segmenter_used: str
    fallback: bool = False
    fallback_reason: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    sam2_score: Optional[float] = None
    # Phase 2A
    sam2_multimask: bool = False
    sam2_candidates: list["Sam2Candidate"] = field(default_factory=list)
    sam2_selected_index: Optional[int] = None
    sam2_selection_reason: Optional[str] = None


@dataclass
class MaskStats:
    area_fraction: float
    bbox_fill_ratio: Optional[float]
    rectangle_like: bool
    mask_bbox: Optional[BBox] = None


@dataclass
class Diagnostics:
    """성공/실패와 무관하게 항상 채워지는 진단 필드 모음."""

    detector: str = "yolo"
    detector_model: Optional[str] = None
    subject_detected: bool = False
    subject_class: Optional[str] = None
    detection_confidence: Optional[float] = None
    raw_bbox: Optional[list[int]] = None
    sam2_prompt_bbox: Optional[list[int]] = None
    crop_bbox: Optional[list[int]] = None
    segmenter_requested: Optional[str] = None
    segmenter_used: Optional[str] = None
    segmenter_fallback: bool = False
    fallback_reason: Optional[str] = None
    segmenter_error: Optional[str] = None
    sam2_score: Optional[float] = None
    mask_area_fraction: Optional[float] = None
    mask_bbox_fill_ratio: Optional[float] = None
    rectangle_like_mask: bool = False
    alpha_area_fraction: Optional[float] = None
    input_width: Optional[int] = None
    input_height: Optional[int] = None
    processing_width: Optional[int] = None
    processing_height: Optional[int] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {
            "detector": self.detector,
            "detector_model": self.detector_model,
            "subject_detected": self.subject_detected,
            "subject_class": self.subject_class,
            "detection_confidence": self.detection_confidence,
            "raw_bbox": self.raw_bbox,
            "sam2_prompt_bbox": self.sam2_prompt_bbox,
            "crop_bbox": self.crop_bbox,
            "segmenter_requested": self.segmenter_requested,
            "segmenter_used": self.segmenter_used,
            "segmenter_fallback": self.segmenter_fallback,
            "fallback_reason": self.fallback_reason,
            "segmenter_error": self.segmenter_error,
            "sam2_score": self.sam2_score,
            "mask_area_fraction": self.mask_area_fraction,
            "mask_bbox_fill_ratio": self.mask_bbox_fill_ratio,
            "rectangle_like_mask": self.rectangle_like_mask,
            "alpha_area_fraction": self.alpha_area_fraction,
            "input_width": self.input_width,
            "input_height": self.input_height,
            "processing_width": self.processing_width,
            "processing_height": self.processing_height,
        }
        out.update(self.extra)
        return out


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
    """yolo_input.load_yolo 로 위임 (dog_image_preprocessing 과 캐시 공유)."""
    return load_yolo(model_name)


_torch_threads_configured = False


def _configure_torch_threads() -> None:
    """
    OOM 최적화 3단계 (저위험): CPU 추론을 intra-op 스레드 1개로 고정한다.

    2GB 워커에서 oneDNN/BLAS 는 스레드마다 스크래치 버퍼를 따로 잡고, 그 위에
    glibc 가 스레드별 malloc 아레나를 늘린다 — 스레드 8개면 같은 forward 가
    RSS 를 수백 MB 더 먹는다. 워커 env(OMP_NUM_THREADS/MKL_NUM_THREADS=1,
    MALLOC_ARENA_MAX=2, render.yaml)와 짝을 이룬다: env 는 OpenMP 런타임 초기화
    전에만 먹히고, 이 호출은 torch 가 이미 초기화된 뒤에도 확실히 적용된다.
    결과 알파는 바뀌지 않는다(같은 연산, 스레드 분할만 다르다). 한 번만 적용.
    """
    global _torch_threads_configured
    if _torch_threads_configured:
        return
    try:
        import torch

        torch.set_num_threads(1)
        _torch_threads_configured = True
    except Exception:  # noqa: BLE001 — 스레드 설정 실패가 로딩을 막으면 안 된다
        logger.exception("torch.set_num_threads(1) failed; keeping default thread count")


def _load_vitmatte(model_name: str, device: str):
    key = f"{model_name}::{device}"
    if key not in _vitmatte_cache:
        try:
            from transformers import VitMatteForImageMatting, VitMatteImageProcessor
        except ImportError as e:
            raise RuntimeError(
                "transformers/torch가 필요합니다: pip install transformers torch"
            ) from e
        _configure_torch_threads()
        processor = VitMatteImageProcessor.from_pretrained(model_name)
        model = VitMatteForImageMatting.from_pretrained(model_name)
        model.to(device)
        model.eval()
        # 전역 어텐션 쿼리 청크 분할 (메모리 최적화 2단계). 수학적으로 같은 결과를
        # 훨씬 작은 순간 메모리로 낸다. 구조가 다르거나 자기 검증에 실패하면
        # 원래 eager 구현을 그대로 둔다 — vitdet_chunked_attention 참고.
        try:
            from .vitdet_chunked_attention import install_chunked_attention

            _vitmatte_attention_status[key] = install_chunked_attention(model)
        except Exception:  # noqa: BLE001 — 최적화가 로딩을 막으면 안 된다
            logger.exception("chunked attention setup failed; using eager attention")
            _vitmatte_attention_status[key] = {"active": False, "reason": "setup_error"}
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
        _configure_torch_threads()
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
        logger.exception("preload_vitmatte_model failed")
        return False


def _detect_subject(
    image: Image.Image, yolo_model: str, *, conf: float
) -> Optional[SubjectDetection]:
    """가장 큰 동물 클래스 검출 1건. 못 찾으면 None.

    입력은 반드시 to_yolo_source() 를 거친다 — ndarray 를 그대로 넘기면
    ultralytics 가 BGR 로 해석해 R/B 가 뒤바뀐 채 추론된다(yolo_input.py 참고).
    """
    yolo = _load_yolo(yolo_model)
    results = yolo.predict(
        source=to_yolo_source(image),
        classes=list(_COCO_ANIMAL_CLASS_IDS),
        verbose=False,
        conf=conf,
    )
    if not results:
        return None
    boxes = results[0].boxes
    if boxes is None or len(boxes) == 0:
        return None

    names = getattr(yolo, "names", None) or {}
    best: Optional[SubjectDetection] = None
    best_area = 0.0
    for i in range(len(boxes)):
        xyxy = boxes.xyxy[i].cpu().numpy()
        x1, y1, x2, y2 = (float(v) for v in xyxy)
        area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        if area <= best_area:
            continue
        cls_id = int(boxes.cls[i])
        best_area = area
        best = SubjectDetection(
            bbox=(int(x1), int(y1), int(x2), int(y2)),
            class_id=cls_id,
            class_name=(
                names.get(cls_id)
                if hasattr(names, "get")
                else None
            )
            or _COCO_ANIMAL_NAMES.get(cls_id, str(cls_id)),
            confidence=round(float(boxes.conf[i]), 4),
        )
    return best


def _detect_persons(image: Image.Image, yolo_model: str, *, conf: float) -> list[BBox]:
    """
    사람(COCO class 0) bbox 목록. Phase 2B 의 음성 프롬프트 근거.

    펫 검출과 별도 호출이다 — `_detect_subject` 의 시그니처/동작을 그대로 두어
    Phase 1 동작과 테스트를 건드리지 않기 위함. YOLOv8n 추가 1패스는 SAM2/ViTMatte
    대비 비용이 미미하다.
    """
    yolo = _load_yolo(yolo_model)
    results = yolo.predict(
        source=to_yolo_source(image),
        classes=[COCO_PERSON_CLASS_ID],
        verbose=False,
        conf=conf,
    )
    if not results:
        return []
    boxes = results[0].boxes
    if boxes is None or len(boxes) == 0:
        return []

    out: list[BBox] = []
    for i in range(len(boxes)):
        xyxy = boxes.xyxy[i].cpu().numpy()
        x1, y1, x2, y2 = (int(float(v)) for v in xyxy)
        if x2 > x1 and y2 > y1:
            out.append((x1, y1, x2, y2))
    return out


def _expand_bbox(bbox: BBox, w: int, h: int, pad_frac: float) -> BBox:
    """크롭/여백용 확장 박스.

    주의: 이 결과를 SAM2 **프롬프트**로 쓰면 안 된다. SAM2 는 박스 안의 지배적인
    객체를 분할하므로, 박스를 넓히면 주인의 팔·목줄·소파 모서리까지 끌어온다.
    프롬프트에는 항상 tight bbox 를 쓰고, 이 확장 박스는 크롭에만 쓴다.
    """
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    bw, bh = (x2 - x1) * (1.0 + pad_frac), (y2 - y1) * (1.0 + pad_frac)
    nx1 = max(0, int(round(cx - bw / 2)))
    ny1 = max(0, int(round(cy - bh / 2)))
    nx2 = min(w, int(round(cx + bw / 2)))
    ny2 = min(h, int(round(cy + bh / 2)))
    return nx1, ny1, nx2, ny2


def full_frame_bbox(rgb: np.ndarray) -> BBox:
    """이미지 전체를 덮는 박스 프롬프트.

    이미 크롭된 누끼(알파가 불투명한 PNG 등)를 통째로 분할하려는 호출자를 위한
    **명시적** 헬퍼다. 피사체 검출에 실패했을 때 쓰던 "중앙 80% 사각형" 추측과는
    다르다 — 그건 제거했고, 이건 "프레임 전체가 대상"이라는 의도를 드러낸다.
    """
    h, w = rgb.shape[:2]
    return 0, 0, int(w), int(h)


def _binary_mask_to_trimap(fg_binary: np.ndarray, *, unknown_band_px: int = 12) -> np.ndarray:
    """
    전경/배경 0·255 이진 마스크의 경계를 erode/dilate해 트라이맵으로 변환.
    Returns: uint8 (H, W), 값은 {0=배경, 128=미확정, 255=전경}.

    (Phase 1 에서는 밴드 폭을 바꾸지 않는다 — 트라이맵 튜닝은 다음 단계.)
    """
    if not CV2_AVAILABLE:
        raise RuntimeError(
            "opencv-python-headless가 필요합니다 (트라이맵 erode/dilate)."
        )
    h, w = fg_binary.shape[:2]
    kernel = np.ones((3, 3), np.uint8)
    band_iters = max(1, unknown_band_px // 3)
    sure_fg = cv2.erode(fg_binary, kernel, iterations=band_iters)
    dilated_fg = cv2.dilate(fg_binary, kernel, iterations=band_iters)

    trimap = np.full((h, w), 128, dtype=np.uint8)  # 기본값: 미확정
    trimap[sure_fg > 0] = 255
    trimap[dilated_fg == 0] = 0
    return trimap


def _grabcut_mask(rgb: np.ndarray, bbox: BBox, *, grabcut_iters: int = 5) -> np.ndarray:
    """GrabCut으로 대략적인 전경 이진 마스크(0/255) 생성 — bbox를 초기 사각형 시드로 사용.

    bbox 는 필수다. 예전에는 피사체 미검출 시 "중앙 80%" 사각형을 시드로 넣었지만,
    그 경로는 네모난 가짜 누끼를 만들어 유료 생성 단계까지 흘려보냈기 때문에 제거했다.
    """
    if not CV2_AVAILABLE:
        raise RuntimeError("opencv-python-headless가 필요합니다.")

    h, w = rgb.shape[:2]
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    x1, y1, x2, y2 = bbox
    rect = (x1, y1, max(1, x2 - x1), max(1, y2 - y1))

    mask = np.zeros((h, w), dtype=np.uint8)
    bgd_model = np.zeros((1, 65), dtype=np.float64)
    fgd_model = np.zeros((1, 65), dtype=np.float64)
    cv2.grabCut(bgr, mask, rect, bgd_model, fgd_model, grabcut_iters, cv2.GC_INIT_WITH_RECT)

    return np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)


def _shape_term(fill_ratio: Optional[float]) -> float:
    """
    bbox 충전율 → 0~1 형태 점수.

    동물 실루엣은 자기 bounding box 를 대략 45~85% 채운다. 1.0 에 가까우면
    직사각형(배경까지 삼킴), 너무 낮으면 몸의 일부만 잡은 것.
    """
    if fill_ratio is None:
        return 0.0
    if SAM2_SHAPE_FILL_LOW <= fill_ratio <= SAM2_SHAPE_FILL_HIGH:
        return 1.0
    if fill_ratio < SAM2_SHAPE_FILL_LOW:
        return max(0.0, fill_ratio / max(1e-6, SAM2_SHAPE_FILL_LOW))
    span = max(1e-6, 1.0 - SAM2_SHAPE_FILL_HIGH)
    return max(0.0, (1.0 - fill_ratio) / span)


def _prompt_containment(binary: np.ndarray, prompt_bbox: BBox) -> float:
    """마스크 픽셀 중 프롬프트 bbox 안에 있는 비율 (밖으로 샌 정도의 반대)."""
    total = int(np.count_nonzero(binary))
    if total == 0:
        return 0.0
    x1, y1, x2, y2 = prompt_bbox
    h, w = binary.shape[:2]
    sub = binary[max(0, y1) : min(h, y2), max(0, x1) : min(w, x2)]
    return float(np.count_nonzero(sub)) / float(total)


def _center_consistency(mask_bbox: Optional[BBox], prompt_bbox: BBox) -> float:
    """마스크 중심과 프롬프트 bbox 중심의 일치도. 1.0 = 완전 일치, 0.0 = 반대편."""
    if mask_bbox is None:
        return 0.0
    mcx = (mask_bbox[0] + mask_bbox[2]) / 2.0
    mcy = (mask_bbox[1] + mask_bbox[3]) / 2.0
    pcx = (prompt_bbox[0] + prompt_bbox[2]) / 2.0
    pcy = (prompt_bbox[1] + prompt_bbox[3]) / 2.0
    half_w = max(1.0, (prompt_bbox[2] - prompt_bbox[0]) / 2.0)
    half_h = max(1.0, (prompt_bbox[3] - prompt_bbox[1]) / 2.0)
    dx = abs(mcx - pcx) / half_w
    dy = abs(mcy - pcy) / half_h
    dist = float(np.hypot(dx, dy)) / float(np.sqrt(2.0))
    return max(0.0, 1.0 - min(1.0, dist))


def score_sam2_candidate(candidate: Sam2Candidate) -> float:
    """
    가중 합산 점수. **predicted IoU 단독으로 고르지 않는다.**

    SAM2 의 predicted IoU 는 "이 마스크가 얼마나 정밀한가"에 대한 자기 확신이라,
    "개 + 개가 앉은 소파"처럼 엉뚱한 대상을 깔끔하게 자른 마스크도 높은 점수를
    받을 수 있다. 그래서 형태(bbox 충전율)·박스 내 포함도·중심 일치도를 함께 본다.
    """
    iou = candidate.predicted_iou if candidate.predicted_iou is not None else 0.0
    return (
        SAM2_W_IOU * float(iou)
        + SAM2_W_CONTAINMENT * candidate.prompt_containment
        + SAM2_W_CENTER * candidate.center_consistency
        + SAM2_W_SHAPE * _shape_term(candidate.bbox_fill_ratio)
    )


def build_sam2_candidate(
    index: int, mask: np.ndarray, predicted_iou: Optional[float], prompt_bbox: BBox
) -> Sam2Candidate:
    """마스크 1장 → 지표 계산 + 유효성 판정 + 점수까지 채운 후보."""
    stats = analyze_mask(mask)
    candidate = Sam2Candidate(
        index=index,
        mask=mask,
        predicted_iou=predicted_iou,
        area_fraction=stats.area_fraction,
        bbox_fill_ratio=stats.bbox_fill_ratio,
        prompt_containment=_prompt_containment(mask > 0, prompt_bbox),
        center_consistency=_center_consistency(stats.mask_bbox, prompt_bbox),
        rectangle_like=stats.rectangle_like,
    )

    # 유효성 — Phase 1 게이트와 **같은 임계값**을 쓴다. 여기서 거른 후보는
    # 선택 대상에서 빠질 뿐이고, 전부 무효면 아래 select 가 최선을 골라
    # 기존 Phase 1 게이트가 정확한 422 코드를 내도록 그대로 흘려보낸다.
    if candidate.area_fraction <= 0.0:
        candidate.valid, candidate.rejected_reason = False, "empty"
    elif candidate.area_fraction < MIN_MASK_AREA_FRACTION:
        candidate.valid, candidate.rejected_reason = False, "mask_too_small"
    elif candidate.area_fraction > MAX_MASK_AREA_FRACTION:
        candidate.valid, candidate.rejected_reason = False, "mask_too_large"
    elif candidate.rectangle_like and REJECT_RECTANGLE_LIKE:
        candidate.valid, candidate.rejected_reason = False, "rectangle_like"

    candidate.selection_score = round(score_sam2_candidate(candidate), 4)
    return candidate


def select_sam2_candidate(
    candidates: list[Sam2Candidate],
) -> tuple[Sam2Candidate, str]:
    """
    후보 목록 → (선택된 후보, 선택 사유).

    유효한 후보가 하나도 없으면 최고 점수의 **무효** 후보를 그대로 돌려준다.
    이렇게 해야 Phase 1 의 마스크 게이트가 원래대로 동작해
    CUTOUT_MASK_TOO_SMALL / _TOO_LARGE / _RECTANGLE_LIKE 중 맞는 422 가 나간다.
    """
    if not candidates:
        raise RuntimeError("SAM2 returned no mask candidates")

    valid = [c for c in candidates if c.valid]
    if len(valid) == 1:
        return valid[0], "only_valid_candidate"
    if valid:
        best = max(valid, key=lambda c: c.selection_score)
        return best, "best_weighted_score"

    best = max(candidates, key=lambda c: c.selection_score)
    return best, "no_valid_candidate_best_effort"


def _sam2_candidates(
    rgb: np.ndarray,
    prompt_bbox: BBox,
    model_name: str,
    device: str,
    *,
    multimask: bool,
    positive_points: Optional[Sequence[tuple[int, int]]] = None,
    negative_points: Optional[Sequence[tuple[int, int]]] = None,
) -> list[Sam2Candidate]:
    """SAM2 를 돌려 마스크 후보들을 지표와 함께 반환.

    Phase 2B: 박스 프롬프트에 더해 양성/음성 포인트를 함께 줄 수 있다.
    음성 포인트는 "여기는 피사체가 아니다"라는 부정 증거로, 박스만으로는
    분리되지 않는 손·팔을 떼어내는 유일한 수단이다.
    """
    import torch

    processor, model = _load_sam2(model_name, device)

    proc_kwargs: dict[str, Any] = {
        "images": Image.fromarray(rgb),
        "input_boxes": [[list(prompt_bbox)]],
        "return_tensors": "pt",
    }

    pts = [list(p) for p in (positive_points or [])] + [
        list(p) for p in (negative_points or [])
    ]
    if pts:
        labels = [1] * len(positive_points or []) + [0] * len(negative_points or [])
        # (batch=1, objects=1, num_points, 2) / (batch=1, objects=1, num_points)
        proc_kwargs["input_points"] = [[pts]]
        proc_kwargs["input_labels"] = [[labels]]

    inputs = processor(**proc_kwargs).to(device)

    # inference_mode: no_grad 에 더해 version counter/뷰 추적까지 끈다 — 추론
    # 전용 텐서라 autograd 메타데이터 할당이 사라진다(출력 알파는 동일).
    with torch.inference_mode():
        outputs = model(**inputs, multimask_output=multimask)

    masks = processor.post_process_masks(outputs.pred_masks.cpu(), inputs["original_sizes"])[0]
    # masks: (num_boxes=1, num_masks=N, H, W) — 박스 1개에 대해 N개 해석.
    stack = masks[0]
    count = int(stack.shape[0])

    ious: list[float] = []
    try:
        raw = getattr(outputs, "iou_scores", None)
        if raw is not None:
            ious = [float(v) for v in raw.detach().cpu().reshape(-1).tolist()]
    except Exception:  # pragma: no cover - 점수는 있으면 좋은 것
        ious = []

    out: list[Sam2Candidate] = []
    for i in range(count):
        arr = stack[i]
        arr = arr.numpy() if hasattr(arr, "numpy") else np.asarray(arr)
        binary = np.where(arr > 0, 255, 0).astype(np.uint8)
        iou = round(ious[i], 4) if i < len(ious) else None
        out.append(build_sam2_candidate(i, binary, iou, prompt_bbox))
    return out


def _sam2_mask(
    rgb: np.ndarray, prompt_bbox: BBox, model_name: str, device: str
) -> tuple[np.ndarray, Optional[float]]:
    """SAM2에 tight bbox를 박스 프롬프트로 줘서 전경 이진 마스크(0/255) 생성.

    하위 호환 래퍼 — 단일 마스크만 필요한 호출자
    (background_inpaint_service, auto_rigging_service)를 위해 유지한다.

    Returns: (mask, predicted_iou)
    """
    candidates = _sam2_candidates(
        rgb, prompt_bbox, model_name, device, multimask=False
    )
    best, _reason = select_sam2_candidate(candidates)
    return best.mask, best.predicted_iou


def _segment_foreground(
    rgb: np.ndarray,
    prompt_bbox: BBox,
    *,
    segmenter: str,
    sam2_model: str,
    device: str,
    unknown_band_px: int = 12,
) -> SegmentationOutcome:
    """1단계 세그멘테이션(SAM2 우선, 실패 시 GrabCut 폴백) → 트라이맵.

    SAM2 실패는 **절대 조용히 넘어가지 않는다** — 예외를 로깅하고 폴백 사유를
    결과에 담아 응답 메타/로그 양쪽에서 확인할 수 있게 한다.
    """
    if segmenter == "sam2":
        try:
            candidates = _sam2_candidates(
                rgb,
                prompt_bbox,
                sam2_model,
                device,
                multimask=SAM2_MULTIMASK_ENABLED,
            )
            best, reason = select_sam2_candidate(candidates)
            logger.info(
                "sam2 candidates=%d selected=%d reason=%s scores=%s",
                len(candidates),
                best.index,
                reason,
                [
                    (c.index, c.predicted_iou, c.selection_score, c.rejected_reason)
                    for c in candidates
                ],
            )
            fg_binary = best.mask
            return SegmentationOutcome(
                fg_binary=fg_binary,
                trimap=_binary_mask_to_trimap(fg_binary, unknown_band_px=unknown_band_px),
                segmenter_used="sam2",
                sam2_score=best.predicted_iou,
                sam2_multimask=SAM2_MULTIMASK_ENABLED,
                sam2_candidates=candidates,
                sam2_selected_index=best.index,
                sam2_selection_reason=reason,
            )
        except Exception as exc:
            logger.exception(
                "SAM2 segmentation failed (model=%s, device=%s) — falling back to GrabCut",
                sam2_model,
                device,
            )
            fg_binary = _grabcut_mask(rgb, prompt_bbox)
            return SegmentationOutcome(
                fg_binary=fg_binary,
                trimap=_binary_mask_to_trimap(fg_binary, unknown_band_px=unknown_band_px),
                segmenter_used="grabcut",
                fallback=True,
                fallback_reason="sam2_failed",
                error_type=type(exc).__name__,
                error_message=str(exc)[:300],
            )

    fg_binary = _grabcut_mask(rgb, prompt_bbox)
    return SegmentationOutcome(
        fg_binary=fg_binary,
        trimap=_binary_mask_to_trimap(fg_binary, unknown_band_px=unknown_band_px),
        segmenter_used="grabcut",
    )


def _run_person_aware_correction(
    rgb: np.ndarray,
    seg: SegmentationOutcome,
    prompt_bbox: BBox,
    person_boxes: list[BBox],
    *,
    sam2_model: str,
    device: str,
    unknown_band_px: int = 12,
) -> tuple[SegmentationOutcome, PersonAwareResult]:
    """
    Phase 2B — 박스 전용 결과(seg)를 기준선으로 두고 사람 인지 재프롬프팅을 시도.

    보정이 채택되면 마스크와 트라이맵을 교체한 새 SegmentationOutcome 을,
    아니면 원래 seg 를 그대로 돌려준다. 어느 쪽이든 진단은 채워진다.
    """
    base_score = None
    base_valid = True
    if seg.sam2_candidates and seg.sam2_selected_index is not None:
        for cand in seg.sam2_candidates:
            if cand.index == seg.sam2_selected_index:
                base_score = cand.selection_score
                base_valid = cand.valid
                break

    def run_sam2(*, positive_points, negative_points):
        return _sam2_candidates(
            rgb,
            prompt_bbox,
            sam2_model,
            device,
            multimask=True,  # 요구사항 7 — 보정 시에도 multimask
            positive_points=positive_points,
            negative_points=negative_points,
        )

    corrected_mask, corrected_candidates, result = apply_person_aware_prompting(
        base_mask=seg.fg_binary,
        base_score=base_score,
        base_valid=base_valid,
        pet_bbox=prompt_bbox,
        person_boxes=person_boxes,
        frame_shape=rgb.shape[:2],
        run_sam2=run_sam2,
        select_candidate=select_sam2_candidate,
    )

    if corrected_mask is None:
        return seg, result

    corrected_score = result.corrected_selected_score
    return (
        SegmentationOutcome(
            fg_binary=corrected_mask,
            trimap=_binary_mask_to_trimap(corrected_mask, unknown_band_px=unknown_band_px),
            segmenter_used=seg.segmenter_used,
            fallback=seg.fallback,
            fallback_reason=seg.fallback_reason,
            error_type=seg.error_type,
            error_message=seg.error_message,
            sam2_score=corrected_score,
            sam2_multimask=True,
            sam2_candidates=corrected_candidates or seg.sam2_candidates,
            sam2_selected_index=next(
                (
                    c.index
                    for c in (corrected_candidates or [])
                    if c.selection_score == corrected_score
                ),
                seg.sam2_selected_index,
            ),
            sam2_selection_reason="person_aware_corrected",
        ),
        result,
    )


def analyze_mask(mask: np.ndarray, *, threshold: int = 0) -> MaskStats:
    """
    마스크 면적 비율과 "사각형 유사도"를 계산한다.

    mask_bbox_fill_ratio = 마스크 면적 / 마스크를 감싸는 bounding box 면적.
    꽉 찬 직사각형이면 1.0 에 가깝고, 실제 동물 실루엣이면 보통 0.45~0.75 다.
    GrabCut 이 사각형 시드를 거의 그대로 돌려주는 실패 사례를 잡기 위한 지표.
    """
    total = int(mask.shape[0]) * int(mask.shape[1])
    if total == 0:
        return MaskStats(area_fraction=0.0, bbox_fill_ratio=None, rectangle_like=False)

    binary = mask > threshold
    area = int(np.count_nonzero(binary))
    area_fraction = area / float(total)
    if area == 0:
        return MaskStats(area_fraction=0.0, bbox_fill_ratio=None, rectangle_like=False)

    rows = np.any(binary, axis=1)
    cols = np.any(binary, axis=0)
    y1, y2 = int(np.argmax(rows)), int(len(rows) - np.argmax(rows[::-1]))
    x1, x2 = int(np.argmax(cols)), int(len(cols) - np.argmax(cols[::-1]))
    bbox_area = max(1, (y2 - y1) * (x2 - x1))
    fill_ratio = round(area / float(bbox_area), 4)

    return MaskStats(
        area_fraction=round(area_fraction, 4),
        bbox_fill_ratio=fill_ratio,
        rectangle_like=fill_ratio >= RECTANGLE_FILL_RATIO_THRESHOLD,
        mask_bbox=(x1, y1, x2, y2),
    )


def validate_cutout_alpha(image_bytes: bytes) -> dict[str, Any]:
    """
    이미 만들어진 누끼 PNG 의 알파를 검사한다 (클라이언트가 보낸 파일 검증용).

    `/api/generate-pet-video` 는 `skip_preprocessing=true` 일 때 클라이언트가 만든
    누끼를 그대로 믿는다. 완전 투명한 PNG 가 그대로 Luma(유료)까지 가는 걸 막기
    위한 최소 게이트.

    알파 채널이 없는 이미지(JPEG 등)는 검증 불가로 보고 통과시킨다 — 목업/테스트
    경로가 JPEG 를 보내기 때문이며, `alpha_checked: False` 로 표시한다.

    Raises:
        AlphaEmptyError: 알파가 사실상 비어 있을 때
    """
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGBA", "LA", "PA"):
        logger.info("validate_cutout_alpha: no alpha channel (mode=%s) — skipping", img.mode)
        return {"alpha_checked": False, "alpha_area_fraction": None}

    alpha = np.array(img.convert("RGBA"))[:, :, 3]
    stats = analyze_mask(alpha, threshold=ALPHA_PRESENCE_THRESHOLD)
    if stats.area_fraction < MIN_ALPHA_AREA_FRACTION:
        raise AlphaEmptyError(
            f"The provided cutout is almost fully transparent "
            f"(alpha coverage {stats.area_fraction:.2%}).",
            diagnostics={
                "alpha_checked": True,
                "alpha_area_fraction": stats.area_fraction,
                "input_width": int(img.size[0]),
                "input_height": int(img.size[1]),
            },
        )
    return {"alpha_checked": True, "alpha_area_fraction": stats.area_fraction}


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

    # 임시 RSS 추적 (rss_trace 참고): 백본/디코더 경계에 측정점을 두되, 훅은 이
    # 호출 동안만 붙였다 뗀다. 비활성이면 훅 자체를 달지 않는다.
    trace = rss_trace.current()
    handles = []
    if trace is not None and trace.enabled:
        backbone = getattr(model, "backbone", None)
        decoder = getattr(model, "decoder", None)
        if backbone is not None and decoder is not None:
            handles.append(backbone.register_forward_hook(lambda m, i, o: trace.mark("after_vitmatte_backbone")))

            def _before_decoder(m, i):
                trace.mark("before_decoder")
                trace.start_peak_window()

            def _after_decoder(m, i, o):
                peak = trace.end_peak_window()
                trace.mark("after_decoder", decoder_peak_rss_mb=peak)

            handles.append(decoder.register_forward_pre_hook(_before_decoder))
            handles.append(decoder.register_forward_hook(_after_decoder))
    try:
        with torch.inference_mode():
            alphas = model(**inputs).alphas
    finally:
        for h in handles:
            h.remove()

    alpha = alphas[0, 0].detach().cpu().numpy()
    alpha = alpha[:orig_h, :orig_w]
    alpha = np.clip(alpha, 0.0, 1.0)
    if trace is not None:
        # 첫 호출은 여기서야 모델이 로드되므로 어텐션 상태를 이 지점에도 남긴다.
        attn = _vitmatte_attention_status.get(f"{model_name}::{device}") or {}
        trace.mark(
            "after_vitmatte",
            alpha_shape=list(alpha.shape),
            chunked_attention_active=attn.get("active"),
            attention_chunk_size=attn.get("chunk_size"),
        )
    return alpha


#: ViTMatte ROI 크롭 (메모리 최적화 1단계).
#:
#: ViTMatte 의 전역 어텐션/디코더 활성값은 입력 픽셀 수에 비례해 커진다. 펫이
#: 프레임 일부만 차지할 때 프레임 전체를 매팅할 이유가 없다 — 트라이맵이 0 이
#: 아닌 영역(= SAM2 마스크의 팽창 영역, 확실한 배경 밖)과 검출 크롭 박스의
#: 합집합에 여백을 둔 ROI 만 ViTMatte 에 넣고, 알파를 원본 크기의 0 캔버스에
#: 같은 좌표로 되돌려 붙인다. **출력 크기·해상도·하류 계약은 바뀌지 않는다.**
#: 리사이즈는 하지 않는다. ROI 계산이 실패하면 기존 전체 프레임 경로로 폴백한다.
#:
#:   VITMATTE_ROI_ENABLED  "1"(기본) | "0" — 운영 킬스위치
#:   VITMATTE_ROI_PAD_PX   ROI 여백(px). 32 미만으로는 내려가지 않는다.
VITMATTE_ROI_ENABLED = os.getenv("VITMATTE_ROI_ENABLED", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
VITMATTE_ROI_MIN_PAD_PX = 32
try:
    VITMATTE_ROI_PAD_PX = max(
        VITMATTE_ROI_MIN_PAD_PX, int(os.getenv("VITMATTE_ROI_PAD_PX", "") or VITMATTE_ROI_MIN_PAD_PX)
    )
except ValueError:
    VITMATTE_ROI_PAD_PX = VITMATTE_ROI_MIN_PAD_PX


def compute_vitmatte_roi(
    crop_bbox: Optional[BBox],
    trimap: np.ndarray,
    *,
    pad_px: int = VITMATTE_ROI_PAD_PX,
) -> Optional[BBox]:
    """ViTMatte 에 넣을 안전 ROI (x1, y1, x2, y2) — 원본 픽셀 좌표, 끝은 배타.

    ROI = union(crop_bbox, bbox(trimap != 0)) 에 pad_px 여백을 더하고 이미지
    경계로 클램프한 것. 트라이맵의 0 은 "확실한 배경"이므로 그 밖은 ViTMatte 가
    볼 필요가 없다. SAM2 마스크는 YOLO 박스 밖으로 나가기도 하므로(꼬리·귀)
    crop_bbox 만 쓰지 않고 반드시 트라이맵 범위와 합친다.

    ROI 를 만들 수 없으면 None — 호출자가 전체 프레임으로 폴백한다.
    """
    if trimap is None or getattr(trimap, "ndim", 0) != 2:
        return None
    h, w = int(trimap.shape[0]), int(trimap.shape[1])
    if h <= 0 or w <= 0:
        return None
    pad = max(int(pad_px), VITMATTE_ROI_MIN_PAD_PX)

    boxes: list[BBox] = []
    if crop_bbox is not None:
        x1, y1, x2, y2 = (int(round(float(v))) for v in crop_bbox)
        if x2 > x1 and y2 > y1:
            boxes.append((x1, y1, x2, y2))
    cols = np.flatnonzero(trimap.any(axis=0))
    rows = np.flatnonzero(trimap.any(axis=1))
    if cols.size and rows.size:
        boxes.append((int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1))
    if not boxes:
        return None

    x1 = max(0, min(b[0] for b in boxes) - pad)
    y1 = max(0, min(b[1] for b in boxes) - pad)
    x2 = min(w, max(b[2] for b in boxes) + pad)
    y2 = min(h, max(b[3] for b in boxes) + pad)
    if x2 - x1 < 1 or y2 - y1 < 1:
        return None
    return (x1, y1, x2, y2)


def _run_vitmatte_roi(
    rgb: np.ndarray,
    trimap: np.ndarray,
    model_name: str,
    device: str,
    *,
    crop_bbox: Optional[BBox],
    pad_px: int = VITMATTE_ROI_PAD_PX,
    enabled: Optional[bool] = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """ROI 만 ViTMatte 에 넣고 원본 크기 알파로 되돌려 붙인다.

    Returns: (float32 alpha (H, W) in [0, 1], ROI 진단 dict).
    _run_vitmatte 의 계약(입력과 같은 (H, W) 알파)은 그대로이고, 여기서는 그
    호출을 ROI 크롭으로 감쌀 뿐이다. ROI 를 만들 수 없거나 계산이 실패하면
    기존과 동일하게 전체 프레임을 한 번 돌린다.
    """
    h, w = int(trimap.shape[0]), int(trimap.shape[1])
    use_roi = VITMATTE_ROI_ENABLED if enabled is None else bool(enabled)
    info: dict[str, Any] = {
        "enabled": use_roi,
        "original_size": [w, h],
        "pad_px": int(pad_px),
        "roi": None,
        "roi_size": None,
        "roi_area_ratio": None,
        "fallback": False,
        "fallback_reason": None,
    }

    roi: Optional[BBox] = None
    if not use_roi:
        info["fallback_reason"] = "disabled"
    else:
        try:
            roi = compute_vitmatte_roi(crop_bbox, trimap, pad_px=pad_px)
            if roi is None:
                info["fallback_reason"] = "roi_unavailable"
        except Exception as exc:  # noqa: BLE001 — ROI 는 최적화일 뿐, 실패해도 매팅은 계속된다
            logger.exception("vitmatte roi computation failed; falling back to full frame")
            roi = None
            info["fallback_reason"] = f"roi_error:{type(exc).__name__}"

    trace = rss_trace.current()
    if trace is not None:
        attn = _vitmatte_attention_status.get(f"{model_name}::{device}") or {}
        trace.mark(
            "before_vitmatte",
            original_size=f"{w}x{h}",
            roi=list(roi) if roi is not None else None,
            roi_size=f"{roi[2] - roi[0]}x{roi[3] - roi[1]}" if roi is not None else f"{w}x{h}",
            roi_area_ratio=(round(((roi[2] - roi[0]) * (roi[3] - roi[1])) / float(w * h), 4) if roi is not None else 1.0),
            roi_fallback_reason=info["fallback_reason"],
            chunked_attention_active=attn.get("active"),
            attention_chunk_size=attn.get("chunk_size"),
            attention_reason=attn.get("reason"),
        )

    if roi is not None:
        x1, y1, x2, y2 = roi
        crop_rgb = np.ascontiguousarray(rgb[y1:y2, x1:x2])
        crop_trimap = np.ascontiguousarray(trimap[y1:y2, x1:x2])
        alpha_crop = _run_vitmatte(crop_rgb, crop_trimap, model_name, device)
        del crop_rgb, crop_trimap
        if tuple(alpha_crop.shape[:2]) != (y2 - y1, x2 - x1):
            # 계약 위반(입력과 다른 크기의 알파). 잘못 붙이느니 전체 프레임으로.
            logger.error(
                "vitmatte roi: alpha shape %s != roi %sx%s — falling back to full frame",
                tuple(alpha_crop.shape[:2]),
                x2 - x1,
                y2 - y1,
            )
            info["fallback_reason"] = "alpha_shape_mismatch"
            roi = None
            alpha_crop = None
        else:
            alpha = np.zeros((h, w), dtype=np.float32)
            alpha[y1:y2, x1:x2] = alpha_crop
            del alpha_crop
            info["roi"] = [x1, y1, x2, y2]
            info["roi_size"] = [x2 - x1, y2 - y1]
            info["roi_area_ratio"] = round(((x2 - x1) * (y2 - y1)) / float(w * h), 4)

    if roi is None:
        info["fallback"] = True
        alpha = _run_vitmatte(rgb, trimap, model_name, device)

    logger.info(
        "vitmatte roi: original=%dx%d roi=%s roi_size=%s area_ratio=%s pad=%d fallback=%s reason=%s",
        w,
        h,
        info["roi"],
        info["roi_size"],
        info["roi_area_ratio"],
        info["pad_px"],
        info["fallback"],
        info["fallback_reason"],
    )
    return alpha, info


def _png_bytes(arr: np.ndarray, mode: str) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr, mode=mode).save(buf, format="PNG")
    return buf.getvalue()


def _checkerboard_composite(rgba: np.ndarray, *, cell: int = 16) -> np.ndarray:
    """알파를 눈으로 확인하기 위한 체커보드 합성 (디버그 전용)."""
    h, w = rgba.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    board = np.where(((yy // cell) + (xx // cell)) % 2 == 0, 235, 180).astype(np.uint8)
    bg = np.dstack([board, board, board]).astype(np.float32)
    alpha = (rgba[:, :, 3:4].astype(np.float32)) / 255.0
    out = rgba[:, :, :3].astype(np.float32) * alpha + bg * (1.0 - alpha)
    return out.clip(0, 255).astype(np.uint8)


def _draw_prompt_points(
    rgb: np.ndarray,
    positives: Sequence[Sequence[int]],
    negatives: Sequence[Sequence[int]],
) -> np.ndarray:
    """디버그: 양성(초록)/음성(빨강) 포인트를 원본 위에 표시."""
    out = rgb.copy()
    h, w = out.shape[:2]
    radius = max(3, min(h, w) // 60)

    def stamp(pt: Sequence[int], color: tuple[int, int, int]) -> None:
        cx, cy = int(pt[0]), int(pt[1])
        y0, y1 = max(0, cy - radius), min(h, cy + radius + 1)
        x0, x1 = max(0, cx - radius), min(w, cx + radius + 1)
        if y1 > y0 and x1 > x0:
            out[y0:y1, x0:x1] = color

    for p in positives:
        stamp(p, (0, 255, 0))
    for p in negatives:
        stamp(p, (255, 0, 0))
    return out


def _draw_box(rgb: np.ndarray, bbox: BBox, color: tuple[int, int, int]) -> np.ndarray:
    out = rgb.copy()
    x1, y1, x2, y2 = bbox
    x1 = max(0, min(x1, out.shape[1] - 1))
    x2 = max(0, min(x2, out.shape[1] - 1))
    y1 = max(0, min(y1, out.shape[0] - 1))
    y2 = max(0, min(y2, out.shape[0] - 1))
    thickness = max(2, min(out.shape[:2]) // 200)
    for t in range(thickness):
        if y1 + t < out.shape[0]:
            out[y1 + t, x1 : x2 + 1] = color
        if y2 - t >= 0:
            out[y2 - t, x1 : x2 + 1] = color
        if x1 + t < out.shape[1]:
            out[y1 : y2 + 1, x1 + t] = color
        if x2 - t >= 0:
            out[y1 : y2 + 1, x2 - t] = color
    return out


# ══════════════════════════════════════════════════════════════════════════
# 접지/투영 그림자 알파 억제
# ══════════════════════════════════════════════════════════════════════════


def _erode_binary(mask: np.ndarray, iterations: int) -> np.ndarray:
    """3×3 최소 필터 반복 침식 — 순수 numpy (cv2 없이도 동작한다).

    테두리는 edge 복제로 패딩한다: 프레임에 닿은 몸통이 테두리에서만 깎여
    코어가 뚫리는 일을 막는다.
    """
    out = mask > 0
    for _ in range(max(0, int(iterations))):
        p = np.pad(out, 1, mode="edge")
        out = (
            p[:-2, :-2] & p[:-2, 1:-1] & p[:-2, 2:]
            & p[1:-1, :-2] & p[1:-1, 1:-1] & p[1:-1, 2:]
            & p[2:, :-2] & p[2:, 1:-1] & p[2:, 2:]
        )
    return out


def _dilate_binary(mask: np.ndarray, iterations: int) -> np.ndarray:
    """3×3 최대 필터 반복 팽창 — 배경 샘플에서 피사체 후광을 빼기 위한 것."""
    out = mask > 0
    for _ in range(max(0, int(iterations))):
        p = np.pad(out, 1, mode="edge")
        out = (
            p[:-2, :-2] | p[:-2, 1:-1] | p[:-2, 2:]
            | p[1:-1, :-2] | p[1:-1, 1:-1] | p[1:-1, 2:]
            | p[2:, :-2] | p[2:, 1:-1] | p[2:, 2:]
        )
    return out


def _luma(rgb: np.ndarray) -> np.ndarray:
    return (
        rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
    ).astype(np.float32)


def rowwise_background_rgb(rgb: np.ndarray, bg_mask: np.ndarray) -> np.ndarray:
    """
    행별 배경색 (H,3). 배경이 한 색이 아니라 벽→바닥 세로 그라디언트인 실제
    산출물에서도 "이 행의 배경"을 맞힌다 (motion_delivery_service 와 같은 모델).
    """
    f = rgb.astype(np.float32)
    h = f.shape[0]
    flat = f.reshape(-1, 3)
    global_bg = (
        np.median(flat[bg_mask.reshape(-1)], axis=0)
        if bool(bg_mask.any())
        else np.median(flat, axis=0)
    )
    rows = np.empty((h, 3), dtype=np.float32)
    for y in range(h):
        m = bg_mask[y]
        rows[y] = np.median(f[y][m], axis=0) if int(m.sum()) >= _SHADOW_MIN_ROW_BG_PX else global_bg
    k = 15
    pad = np.pad(rows, ((k // 2, k // 2), (0, 0)), mode="edge")
    kernel = np.ones(k, dtype=np.float32) / k
    return np.stack(
        [np.convolve(pad[:, c], kernel, mode="valid") for c in range(3)], axis=1
    ).astype(np.float32)


def suppress_cast_shadow_alpha(
    rgb: np.ndarray, alpha: np.ndarray, fg_binary: np.ndarray
) -> tuple[np.ndarray, dict[str, Any]]:
    """
    ViTMatte 알파에서 **펫이 아닌 그림자**만 0 으로 만든다.

    그림자로 판정하려면 다음을 **전부** 만족해야 한다:
      1. 세그멘테이션 코어(침식된 전경) 밖 — 몸통은 정의상 제외된다.
      2. 알파가 완전 불투명이 아니다 (α < SHADOW_MAX_ALPHA).
      3. 같은 행의 배경보다 어둡다 — 그림자는 배경을 밝히지 않는다.
      4. 지나치게 어둡지는 않다 — 검은 털/코/눈은 그림자가 아니다.
      5. 색조가 배경과 같다 — 그림자는 배경의 감광 버전이다.

    안전장치: 위 판정이 전체 알파 질량의 SHADOW_MAX_REMOVED_FRACTION 을 넘게
    지우려 하면 **아무것도 하지 않는다**. 털/발을 깎느니 그림자를 남긴다.

    Returns: (알파, 진단). 알파는 항상 float32 0..1 이다.
    """
    a = alpha.astype(np.float32)
    diag: dict[str, Any] = {
        "version": "shadow-suppression-v1",
        "applied": False,
        "reason": None,
        "thresholds": {
            "core_erode_px": SHADOW_CORE_ERODE_PX,
            "max_alpha": SHADOW_MAX_ALPHA,
            "min_luma_ratio": SHADOW_MIN_LUMA_RATIO,
            "max_luma_ratio": SHADOW_MAX_LUMA_RATIO,
            "max_chroma_delta": SHADOW_MAX_CHROMA_DELTA,
            "max_removed_fraction": SHADOW_MAX_REMOVED_FRACTION,
        },
    }
    if not SHADOW_SUPPRESSION_ENABLED:
        diag["reason"] = "disabled"
        return a, diag

    total = float(a.sum())
    if total <= 0.0:
        diag["reason"] = "empty_alpha"
        return a, diag

    core = _erode_binary(fg_binary, SHADOW_CORE_ERODE_PX)
    bg_mask = ~_dilate_binary(fg_binary, max(1, SHADOW_CORE_ERODE_PX))
    if not bool(bg_mask.any()):
        diag["reason"] = "no_background_sample"
        return a, diag

    f = rgb.astype(np.float32)
    bg_rows = rowwise_background_rgb(rgb, bg_mask)          # (H,3)
    bg = np.broadcast_to(bg_rows[:, None, :], f.shape)      # (H,W,3)

    px_luma = np.maximum(_luma(f), 1.0)
    bg_luma = np.maximum(_luma(bg), 1.0)
    ratio = px_luma / bg_luma

    chroma_px = f / px_luma[..., None]
    chroma_bg = bg / bg_luma[..., None]
    chroma_delta = np.abs(chroma_px - chroma_bg).max(axis=2)

    shadow = (
        (~core)
        & (a < SHADOW_MAX_ALPHA)
        & (a > 0.0)
        & (ratio < SHADOW_MAX_LUMA_RATIO)
        & (ratio > SHADOW_MIN_LUMA_RATIO)
        & (chroma_delta < SHADOW_MAX_CHROMA_DELTA)
    )

    removed = float(a[shadow].sum())
    fraction = removed / total
    diag["candidate_pixels"] = int(shadow.sum())
    diag["removed_alpha_fraction"] = round(fraction, 5)
    diag["background_rgb_mean"] = [int(c) for c in bg_rows.mean(axis=0)]

    if fraction > SHADOW_MAX_REMOVED_FRACTION:
        # 그림자라기엔 너무 많다 — 회색 털 피사체를 깎고 있을 가능성이 크다.
        diag["reason"] = "would_erode_subject"
        logger.info(
            "shadow suppression skipped: removal fraction %.3f > %.3f",
            fraction,
            SHADOW_MAX_REMOVED_FRACTION,
        )
        return a, diag

    out = a.copy()
    out[shadow] = 0.0
    diag["applied"] = True
    diag["reason"] = "shadow_pixels_zeroed"
    return out, diag


def matte_foreground_with_meta(
    image_bytes: bytes,
    *,
    model_name: Optional[str] = None,
    yolo_model: Optional[str] = None,
    device: Optional[str] = None,
    bbox_pad_frac: float = 0.15,
    segmenter: Optional[str] = None,
    sam2_model: Optional[str] = None,
    debug_artifacts: Optional[dict[str, bytes]] = None,
) -> tuple[bytes, dict]:
    """
    사진 → (RGBA PNG bytes, 진단 메타). 배경은 투명, 털 경계는 매팅 처리.
    rembg는 전혀 쓰지 않는다: YOLO bbox → SAM2(폴백: GrabCut) trimap → ViTMatte.

    Raises:
        SubjectNotDetectedError: 지원 동물 미검출 (중앙 사각형 폴백 없음)
        MaskTooSmallError / MaskTooLargeError / RectangleLikeMaskError / AlphaEmptyError:
            세그멘테이션 결과가 명백히 잘못됐을 때

    Args:
        debug_artifacts: dict 를 넘기면 디버그 PNG 들을 여기에 채워 준다
            (원본+박스, 크롭박스, SAM2 마스크, 트라이맵, 알파, 체커보드).
            CUTOUT_DEBUG_ENABLED=1 일 때만 동작한다.
    """
    resolved_model = model_name or os.getenv("VITMATTE_MODEL", "hustvl/vitmatte-small-composition-1k")
    resolved_yolo = yolo_model or os.getenv("VITMATTE_YOLO_MODEL", "yolov8n.pt")
    resolved_device = _get_device(device)
    resolved_segmenter = (segmenter or os.getenv("VITMATTE_SEGMENTER", "sam2")).strip().lower()
    resolved_sam2_model = sam2_model or os.getenv("VITMATTE_SAM2_MODEL", "facebook/sam2.1-hiera-tiny")
    yolo_conf = float(os.getenv("VITMATTE_YOLO_CONF", "0.25"))

    collect_debug = debug_artifacts is not None and DEBUG_ARTIFACTS_ENABLED

    _decoded = Image.open(io.BytesIO(image_bytes))
    # [IMAGE-TRACE] PIL 디코드 직후. 이 파이프라인에는 리사이즈/크롭이 없으므로
    # 이 값이 그대로 input_* / processing_* 진단이 된다. 여기서 이미 작다면
    # 축소는 전적으로 업로드 이전(브라우저 또는 사진 선택기)에서 일어난 것이다.
    logger.info(
        "[IMAGE-TRACE] vitmatte decode: %dx%d mode=%s format=%s bytes=%d",
        _decoded.size[0],
        _decoded.size[1],
        _decoded.mode,
        _decoded.format,
        len(image_bytes),
    )

    img = _decoded.convert("RGB")
    rgb = np.array(img)
    h, w = rgb.shape[:2]

    with rss_trace.Tracer(tag="cutout") as trace:
        return _matte_foreground_traced(
            trace,
            rgb=rgb,
            img=img,
            w=w,
            h=h,
            resolved_model=resolved_model,
            resolved_yolo=resolved_yolo,
            resolved_device=resolved_device,
            resolved_segmenter=resolved_segmenter,
            resolved_sam2_model=resolved_sam2_model,
            yolo_conf=yolo_conf,
            bbox_pad_frac=bbox_pad_frac,
            collect_debug=collect_debug,
            debug_artifacts=debug_artifacts,
        )


def _matte_foreground_traced(
    trace: rss_trace.Tracer,
    *,
    rgb: np.ndarray,
    img: Image.Image,
    w: int,
    h: int,
    resolved_model: str,
    resolved_yolo: str,
    resolved_device: str,
    resolved_segmenter: str,
    resolved_sam2_model: str,
    yolo_conf: float,
    bbox_pad_frac: float,
    collect_debug: bool,
    debug_artifacts: Optional[dict[str, bytes]],
) -> tuple[bytes, dict]:
    """matte_foreground_with_meta 의 본체 — 디코드 이후 전 과정. 측정점만 추가됐다."""
    diag = Diagnostics(
        detector_model=resolved_yolo,
        segmenter_requested=resolved_segmenter,
        input_width=w,
        input_height=h,
        # 이 파이프라인은 리사이즈하지 않는다 — 입력 해상도가 곧 처리 해상도.
        processing_width=w,
        processing_height=h,
    )

    trace.mark("before_yolo", input_size=f"{w}x{h}")
    detection = _detect_subject(img, resolved_yolo, conf=yolo_conf)
    trace.mark("after_yolo", detected=detection is not None)
    if detection is None:
        logger.warning(
            "cutout: no supported animal detected (yolo=%s, conf>=%.2f, size=%dx%d)",
            resolved_yolo,
            yolo_conf,
            w,
            h,
        )
        raise SubjectNotDetectedError(
            "No supported pet was detected in the image.",
            diagnostics=diag.to_dict(),
        )

    diag.subject_detected = True
    diag.subject_class = detection.class_name
    diag.detection_confidence = detection.confidence
    diag.raw_bbox = list(detection.bbox)

    # SAM2 프롬프트는 tight box, 크롭/여백용은 padded box — 절대 섞지 않는다.
    sam2_prompt_bbox: BBox = detection.bbox
    crop_bbox: BBox = _expand_bbox(detection.bbox, w, h, bbox_pad_frac)
    diag.sam2_prompt_bbox = list(sam2_prompt_bbox)
    diag.crop_bbox = list(crop_bbox)

    seg = _segment_foreground(
        rgb,
        sam2_prompt_bbox,
        segmenter=resolved_segmenter,
        sam2_model=resolved_sam2_model,
        device=resolved_device,
    )

    # --- Phase 2B: 사람 인지 재프롬프팅 -------------------------------------
    # 박스 전용 결과를 기준선으로 두고, 사람이 겹칠 때만 음성 포인트를 붙여
    # 다시 돌린다. 명백히 더 나을 때만 교체한다(아니면 기준선 유지).
    person_result = PersonAwareResult()
    if collect_debug:
        debug_artifacts["03b_mask_before_person_aware.png"] = _png_bytes(seg.fg_binary, "L")

    if PERSON_AWARE_PROMPTING_ENABLED and seg.segmenter_used == "sam2":
        try:
            person_boxes = _detect_persons(img, resolved_yolo, conf=yolo_conf)
            seg, person_result = _run_person_aware_correction(
                rgb,
                seg,
                sam2_prompt_bbox,
                person_boxes,
                sam2_model=resolved_sam2_model,
                device=resolved_device,
            )
        except Exception:
            # 보정은 부가 기능이다 — 실패해도 기준선 마스크로 계속 진행한다.
            logger.exception("person-aware prompting failed; keeping box-only mask")
            person_result = PersonAwareResult(skipped_reason="person_aware_stage_error")
    elif seg.segmenter_used != "sam2":
        person_result = PersonAwareResult(skipped_reason="segmenter_not_sam2")
    else:
        person_result = PersonAwareResult(skipped_reason="disabled")

    trace.mark("after_sam2", segmenter=seg.segmenter_used)

    diag.segmenter_used = seg.segmenter_used
    diag.segmenter_fallback = seg.fallback
    diag.fallback_reason = seg.fallback_reason
    diag.sam2_score = seg.sam2_score
    if seg.error_type:
        diag.segmenter_error = f"{seg.error_type}: {seg.error_message}"

    # Phase 2A — 어떤 후보들이 있었고 왜 그걸 골랐는지 그대로 노출.
    diag.extra["sam2_multimask"] = seg.sam2_multimask
    diag.extra["sam2_candidate_count"] = len(seg.sam2_candidates)
    diag.extra["sam2_candidates"] = [c.to_dict() for c in seg.sam2_candidates]
    diag.extra["sam2_selected_index"] = seg.sam2_selected_index
    diag.extra["sam2_selection_reason"] = seg.sam2_selection_reason
    diag.extra["sam2_selection_weights"] = {
        "predicted_iou": SAM2_W_IOU,
        "prompt_containment": SAM2_W_CONTAINMENT,
        "center_consistency": SAM2_W_CENTER,
        "shape": SAM2_W_SHAPE,
    }
    # Phase 2B 진단 — 사람 검출/포인트/보정 채택 여부
    diag.extra.update(person_result.to_dict())

    mask_stats = analyze_mask(seg.fg_binary)
    diag.mask_area_fraction = mask_stats.area_fraction
    diag.mask_bbox_fill_ratio = mask_stats.bbox_fill_ratio
    diag.rectangle_like_mask = mask_stats.rectangle_like

    if collect_debug:
        debug_artifacts["01_detection_bbox.png"] = _png_bytes(
            _draw_box(rgb, sam2_prompt_bbox, (0, 255, 0)), "RGB"
        )
        debug_artifacts["02_crop_bbox.png"] = _png_bytes(
            _draw_box(rgb, crop_bbox, (255, 200, 0)), "RGB"
        )
        debug_artifacts["03_segmentation_mask.png"] = _png_bytes(seg.fg_binary, "L")
        # Phase 2A — 탈락한 후보까지 전부 저장해야 "왜 이걸 골랐나"를 눈으로 본다.
        for cand in seg.sam2_candidates:
            tag = "selected" if cand.index == seg.sam2_selected_index else "rejected"
            if cand.rejected_reason:
                tag = f"invalid-{cand.rejected_reason}"
            debug_artifacts[
                f"03c_sam2_candidate_{cand.index}_{tag}_score{cand.selection_score:.3f}.png"
            ] = _png_bytes(cand.mask, "L")
        if person_result.person_aware_prompting_used:
            # 프롬프트 포인트를 원본 위에 찍어 "어디를 빼라고 했는지" 눈으로 확인.
            debug_artifacts["03d_prompt_points.png"] = _png_bytes(
                _draw_prompt_points(
                    rgb, person_result.positive_points, person_result.negative_points
                ),
                "RGB",
            )
        debug_artifacts["04_trimap.png"] = _png_bytes(seg.trimap, "L")

    if mask_stats.area_fraction < MIN_MASK_AREA_FRACTION:
        raise MaskTooSmallError(
            f"Segmented subject covers only {mask_stats.area_fraction:.1%} of the image "
            f"(minimum {MIN_MASK_AREA_FRACTION:.0%}).",
            diagnostics=diag.to_dict(),
        )
    if mask_stats.area_fraction > MAX_MASK_AREA_FRACTION:
        raise MaskTooLargeError(
            f"Segmented subject covers {mask_stats.area_fraction:.1%} of the image "
            f"(maximum {MAX_MASK_AREA_FRACTION:.0%}) — the background was likely included.",
            diagnostics=diag.to_dict(),
        )
    if mask_stats.rectangle_like and REJECT_RECTANGLE_LIKE:
        raise RectangleLikeMaskError(
            f"Segmentation returned a near-rectangular mask "
            f"(fill ratio {mask_stats.bbox_fill_ratio}), which usually means the subject "
            f"was not separated from the background.",
            diagnostics=diag.to_dict(),
        )

    # ROI 크롭 매팅 — 알파는 원본 (H, W) 그대로 돌아온다 (_run_vitmatte_roi 참고).
    alpha, roi_info = _run_vitmatte_roi(
        rgb, seg.trimap, resolved_model, resolved_device, crop_bbox=crop_bbox
    )
    diag.extra["vitmatte_roi"] = roi_info
    diag.extra["vitmatte_attention"] = dict(
        _vitmatte_attention_status.get(f"{resolved_model}::{resolved_device}") or {"active": False, "reason": "unknown"}
    )
    if trace.enabled:
        diag.extra["rss_trace"] = trace.to_dict()

    # 접지/투영 그림자는 전경이 아니다 — 알파에서 증거 기반으로만 걷어낸다.
    alpha_before_shadow = alpha
    alpha, shadow_diag = suppress_cast_shadow_alpha(rgb, alpha, seg.fg_binary)
    diag.extra["shadow_suppression"] = shadow_diag

    alpha_u8 = (alpha * 255.0).astype(np.uint8)
    alpha_stats = analyze_mask(alpha_u8, threshold=ALPHA_PRESENCE_THRESHOLD)
    diag.alpha_area_fraction = alpha_stats.area_fraction

    rgba = np.dstack([rgb, alpha_u8])

    if collect_debug:
        debug_artifacts["05a_alpha_before_shadow_suppression.png"] = _png_bytes(
            (alpha_before_shadow * 255.0).astype(np.uint8), "L"
        )
        debug_artifacts["05_alpha.png"] = _png_bytes(alpha_u8, "L")
        debug_artifacts["06_checkerboard.png"] = _png_bytes(
            _checkerboard_composite(rgba), "RGB"
        )

    if alpha_stats.area_fraction < MIN_ALPHA_AREA_FRACTION:
        raise AlphaEmptyError(
            f"Matting produced an almost fully transparent image "
            f"(alpha coverage {alpha_stats.area_fraction:.2%}).",
            diagnostics=diag.to_dict(),
        )

    out = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(out, format="PNG")

    meta = {
        "method": "vitmatte",
        "model": resolved_model,
        "sam2_model": resolved_sam2_model if seg.segmenter_used == "sam2" else None,
        "device": resolved_device,
        # 하위 호환: 기존 응답에 있던 키 이름 유지
        "segmenter": seg.segmenter_used,
        "bbox": list(crop_bbox),
        **diag.to_dict(),
    }
    logger.info(
        "cutout ok: class=%s conf=%.3f segmenter=%s fallback=%s mask=%.3f alpha=%.3f",
        diag.subject_class,
        diag.detection_confidence or 0.0,
        diag.segmenter_used,
        diag.segmenter_fallback,
        diag.mask_area_fraction or 0.0,
        diag.alpha_area_fraction or 0.0,
    )
    return out.getvalue(), meta


def matte_foreground(image_bytes: bytes, **kwargs: Any) -> bytes:
    """matte_foreground_with_meta()의 PNG bytes만 반환하는 얇은 래퍼."""
    png_bytes, _meta = matte_foreground_with_meta(image_bytes, **kwargs)
    return png_bytes
