"""
Action(달려오기/짖기/배깔기) 리깅 파이프라인 1단계 — 반려동물 사진에서 관절 키포인트 추정.

★ 이 모듈의 위치
SAM2 세그멘테이션(vitmatte_service._sam2_mask 재사용) → **이 모듈(키포인트 추정)**
→ auto_rigging_service(뼈대+이미지 워핑) → spine_action_curves(배깔기 등 모션 곡선)
→ Spine JSON(skeleton.json+atlas+PNG) 출력. Idle/Luma, LivePortrait 파이프라인과는
완전히 별개(둘 다 건드리지 않음).

★ 백엔드 2종 + 이유
1) "deeplabcut_superanimal" (연구/설치 필요, 이 샌드박스에서 미검증)
   DeepLabCut 3.0(pytorch 엔진)의 SuperAnimal-Quadruped 사전학습 모델
   (`deeplabcut.pose_estimation_pytorch.apis.superanimal_analyze_images`)을 쓴다.
   - 39개 키포인트, "재학습 없이" 바로 쓸 수 있는(zero-shot) 사전학습 모델 —
     2026년 기준 DeepLabCut 3.x pytorch 엔진에 정식 포함되어 있고, **정지 이미지
     1장**에도 동작함(비디오 전용이 아님) — 이 문서/코드 조사 시점에
     `superanimal_analyze_images(images=[...])`가 이미지 리스트를 직접 받는 것을
     GitHub 소스에서 확인함.
   - 이 샌드박스(Windows, GPU 없음, torch 2.10.0+cpu)에서 `dlclibrary`(가벼운
     모델 다운로드 helper, 0.0.12)는 실제로 설치/실행해 `get_available_models
     ("superanimal_quadruped")`가 `['hrnet_w32', 'resnet_50', 'rtmpose_s']`를
     반환하는 것까지 확인했다 — 즉 API 자체는 살아있고 현재(2026)도 유지되는
     모델이라는 근거는 확실하다.
   - 그러나 실제 추론에 필요한 **`deeplabcut` 풀 패키지**(torch/torchvision 외에도
     pandas, statsmodels, scikit-image, numba 등 무거운 의존성 체인)는 이 세션의
     느린 샌드박스 환경(pip/shell 호출 1건당 수십~100초 이상)에서 설치를
     시도하지 않았다 — 실제 GPU 워커 머신에서 설치/검증 필요.
   - **키포인트 이름 매핑 불확실성**: SuperAnimal-Quadruped는 39개 키포인트를
     쓰는데, 정확한 이름 스펠링을 담은 별도 소형 config 파일을 이 세션에서
     찾지 못했다(HuggingFace 저장소에는 .pt 체크포인트만 있고, 이름 목록은
     `deeplabcut` 패키지 소스 내부(예: modelzoo 변환 테이블)에 있음). 아래
     `_DLC_KEYPOINT_NAME_GUESS`는 Quadruped-80K/AnimalPose류 데이터셋에서 흔히
     쓰이는 이름을 근거로 한 **추정치**이며, 실제 설치 후
     `predictions[image]["bodyparts"]`(또는 반환되는 DataFrame 컬럼)로 정확한
     이름을 재확인해야 한다 — 틀린 이름은 조용히 스킵되고 경고만 남도록
     방어적으로 구현했다(크래시하지 않음).

2) "heuristic_mask_geometry" (이 샌드박스에서 실제로 동작 확인됨 — 기본값)
   추가 의존성 없이(numpy/opencv만) SAM2(또는 알파채널) 마스크의 **실루엣 기하
   구조**만으로 18개 키포인트를 근사 추정한다. DeepLabCut/MMPose 같은 학습된
   포즈 모델이 전혀 없을 때의 최후 수단이며, 아래 "알려진 한계" 참고.

★ 왜 18개 키포인트인가 (사용자 요청의 "15~20개"에 맞춤)
nose, head_top, neck, spine_mid, tail_base, tail_tip (6) +
4다리 × (shoulder/hip, elbow/knee, paw) = 12 → 총 18개.
전체 명칭은 KEYPOINT_NAMES 참고.

★ 알려진 한계(heuristic_mask_geometry) — 반드시 읽을 것
- **근/원위(near/far) 다리 구분 불가**: 측면(side-view) 사진 1장의 실루엣만으로는
  카메라에 가까운 다리와 먼 다리가 겹쳐 보이는 경우, 이 둘을 기하학적으로
  구분할 방법이 원천적으로 없다(사람이 봐도 애매한 경우가 많음). 학습된 포즈
  모델(DeepLabCut/MMPose)은 텍스처·음영·학습 데이터의 사전지식으로 이 문제를
  상당 부분 해결하지만, 실루엣 하나로는 안 된다. 이 구현은 검출된 다리 기둥이
  4개 미만이면 앞다리 쌍/뒷다리 쌍 내에서 좌우를 "복제"한다(살짝 오프셋) —
  즉 앞다리 2개가 사실상 같은 픽셀 위치를 공유할 수 있다.
- 머리/꼬리 방향 판별은 "실루엣에서 더 높이 솟은 쪽(귀)"이라는 매우 단순한
  가정에 의존한다 — 앉은 자세, 머리를 숙인 자세, 꼬리가 위로 말리지 않는
  견종(예: 처진 꼬리)에서는 틀릴 수 있다.
- 관절(팔꿈치/무릎) 위치는 실측이 아니라 "어깨/엉덩이~발끝 사이의 단순 중점"
  근사이며, 실제 다리가 굽은 자세에서는 부정확하다.
- 결론: 정지 사진 1장 기준 학습 기반 포즈 모델(DeepLabCut/MMPose) 없이는
  "그럭저럭 봐줄 만한" 수준 이상을 기대하기 어렵다. 이 heuristic은 파이프라인
  전체(세그멘테이션→키포인트→리깅→애니메이션→Spine 출력)가 배관적으로
  끝까지 동작하는지 확인하기 위한 자리채움(placeholder)에 가깝다.

환경변수:
  POSE_ESTIMATION_BACKEND   "heuristic" (기본) | "deeplabcut_superanimal" | "auto"
                             "auto"는 deeplabcut_superanimal을 먼저 시도하고
                             (미설치/에러 시) heuristic으로 폴백(vitmatte_service의
                             sam2→grabcut 폴백과 동일한 패턴).
  DLC_SUPERANIMAL_MODEL      기본 "hrnet_w32" (dlclibrary 기준 사용 가능:
                             hrnet_w32 | resnet_50 | rtmpose_s)
  DLC_SUPERANIMAL_DETECTOR   기본 "fasterrcnn_resnet50_fpn_v2"
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

try:
    import cv2

    CV2_AVAILABLE = True
except Exception:
    CV2_AVAILABLE = False

logger = logging.getLogger(__name__)

# 우리 리깅 파이프라인 전체가 공유하는 18개 키포인트 스키마.
KEYPOINT_NAMES: tuple[str, ...] = (
    "nose",
    "head_top",
    "neck",
    "spine_mid",
    "tail_base",
    "tail_tip",
    "front_left_shoulder",
    "front_left_elbow",
    "front_left_paw",
    "front_right_shoulder",
    "front_right_elbow",
    "front_right_paw",
    "back_left_hip",
    "back_left_knee",
    "back_left_paw",
    "back_right_hip",
    "back_right_knee",
    "back_right_paw",
)


@dataclass
class Keypoint:
    x: float
    y: float
    confidence: float = 1.0
    visible: bool = True


@dataclass
class PoseResult:
    keypoints: dict[str, Keypoint]
    image_width: int
    image_height: int
    backend: str
    head_side: str = "left"  # "left" | "right" — 이미지 상에서 머리가 있는 쪽
    warnings: list[str] = field(default_factory=list)

    def get(self, name: str) -> Optional[Keypoint]:
        return self.keypoints.get(name)


# --------------------------------------------------------------------------
# 백엔드 1: DeepLabCut SuperAnimal-Quadruped (연구/설치 필요, 미검증)
# --------------------------------------------------------------------------

# 근거/불확실성은 파일 상단 docstring 참고. 왼쪽=우리 스키마, 오른쪽=DLC 추정 이름.
# 실제 설치 후 반드시 검증 후 필요시 수정할 것.
_DLC_KEYPOINT_NAME_GUESS: dict[str, str] = {
    "nose": "nose",
    "head_top": "right_earbase",  # 좌/우 귀 중 하나 — side-view라 카메라 쪽 귀가 더 신뢰도 높음(휴리스틱으로 선택)
    "neck": "neck_base",
    "spine_mid": "back_middle",
    "tail_base": "tail_base",
    "tail_tip": "tail_end",
    "front_left_shoulder": "front_left_thai",  # DLC 표기가 'thigh'가 아니라 'thai'인 경우가 보고됨(오타 관용 표기) — 미확인
    "front_left_elbow": "front_left_knee",
    "front_left_paw": "front_left_paw",
    "front_right_shoulder": "front_right_thai",
    "front_right_elbow": "front_right_knee",
    "front_right_paw": "front_right_paw",
    "back_left_hip": "back_left_thai",
    "back_left_knee": "back_left_knee",
    "back_left_paw": "back_left_paw",
    "back_right_hip": "back_right_thai",
    "back_right_knee": "back_right_knee",
    "back_right_paw": "back_right_paw",
}


def _estimate_pose_deeplabcut_superanimal(
    rgb: np.ndarray,
    *,
    model_name: Optional[str] = None,
    detector_name: Optional[str] = None,
) -> PoseResult:
    """DeepLabCut SuperAnimal-Quadruped로 39개 키포인트 추론 후 18개로 매핑.

    NOTE: 이 함수는 이 세션의 샌드박스(GPU 없음)에서 실행/검증되지 않았다 —
    `deeplabcut` pytorch 엔진 풀 패키지가 설치된 로컬 RTX 4090 워커에서 처음
    실행할 때, 예외 메시지와 반환되는 실제 bodyparts 이름을 보고
    `_DLC_KEYPOINT_NAME_GUESS`를 검증/수정해야 한다.
    """
    try:
        from deeplabcut.pose_estimation_pytorch.apis import superanimal_analyze_images
    except ImportError as e:
        raise RuntimeError(
            "DeepLabCut(pytorch 엔진)이 설치되어 있지 않습니다. "
            "pip install 'deeplabcut[pytorch]' 로 설치하세요 "
            "(무거운 의존성: torch/torchvision/pandas/statsmodels/scikit-image/numba 등). "
            "docs/Spine2D_리깅_파이프라인_진행상황.md 의 설치 절 참고."
        ) from e

    model_name = model_name or os.getenv("DLC_SUPERANIMAL_MODEL", "hrnet_w32")
    detector_name = detector_name or os.getenv(
        "DLC_SUPERANIMAL_DETECTOR", "fasterrcnn_resnet50_fpn_v2"
    )

    h, w = rgb.shape[:2]
    with tempfile.TemporaryDirectory(prefix="eb_dlc_") as td:
        from PIL import Image as PILImage

        img_path = Path(td) / "frame.png"
        PILImage.fromarray(rgb).save(img_path)

        predictions = superanimal_analyze_images(
            superanimal_name="superanimal_quadruped",
            model_name=model_name,
            detector_name=detector_name,
            images=[str(img_path)],
            max_individuals=1,
            out_folder=td,
            plot_skeleton=False,
        )

    # 반환 구조는 DLC 버전에 따라 다를 수 있어 방어적으로 파싱.
    if not predictions:
        raise RuntimeError("DeepLabCut SuperAnimal 추론 결과가 비어 있습니다.")
    first_key = next(iter(predictions))
    raw = predictions[first_key]
    bodyparts_xy: dict[str, tuple[float, float, float]] = {}
    try:
        # 흔한 형태: {"bodyparts": np.ndarray[(N,3)], "bodypart_names": [...]}
        names = raw.get("bodypart_names") or raw.get("bodyparts_names")
        coords = raw.get("bodyparts") or raw.get("coordinates")
        if names is not None and coords is not None:
            for name, (x, y, conf) in zip(names, coords):
                bodyparts_xy[name] = (float(x), float(y), float(conf))
    except Exception:
        logger.warning("DeepLabCut 예측 결과 파싱 실패 — 반환 형식이 예상과 다릅니다: %s", type(raw))

    if not bodyparts_xy:
        raise RuntimeError(
            "DeepLabCut 예측 결과를 파싱하지 못했습니다 — API 반환 형식이 바뀌었을 수 있습니다. "
            f"raw type={type(raw)!r}"
        )

    warnings: list[str] = []
    keypoints: dict[str, Keypoint] = {}
    for our_name, dlc_name in _DLC_KEYPOINT_NAME_GUESS.items():
        entry = bodyparts_xy.get(dlc_name)
        if entry is None:
            warnings.append(f"DLC 키포인트 '{dlc_name}'(→{our_name}) 못찾음 — 이름 매핑 확인 필요")
            continue
        x, y, conf = entry
        keypoints[our_name] = Keypoint(x=x, y=y, confidence=conf, visible=conf > 0.1)

    missing = set(KEYPOINT_NAMES) - set(keypoints)
    if missing:
        warnings.append(f"매핑 실패로 누락된 키포인트: {sorted(missing)}")

    head_x = keypoints.get("head_top", keypoints.get("nose"))
    tail_x = keypoints.get("tail_base")
    head_side = "left"
    if head_x is not None and tail_x is not None:
        head_side = "left" if head_x.x < tail_x.x else "right"

    return PoseResult(
        keypoints=keypoints,
        image_width=w,
        image_height=h,
        backend="deeplabcut_superanimal",
        head_side=head_side,
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# 백엔드 2: 마스크 실루엣 기반 휴리스틱 (의존성 없음, 이 샌드박스에서 테스트됨)
# --------------------------------------------------------------------------


def _column_top_bottom_profiles(mask_bool: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """마스크의 각 열(column)마다 최상단/최하단 y와 '유효 열' 불리언 배열을 계산."""
    h, w = mask_bool.shape
    row_idx = np.arange(h, dtype=np.int32)[:, None]
    valid_cols = mask_bool.any(axis=0)

    top = np.where(mask_bool, row_idx, h + 1).min(axis=0)
    bottom = np.where(mask_bool, row_idx, -1).max(axis=0)
    return top, bottom, valid_cols


def _find_local_maxima_1d(values: np.ndarray, *, min_distance: int, min_value: float) -> list[int]:
    """values(예: bottom-profile)에서 지역 최댓값(다리 발끝 후보) 인덱스들을 찾는다.

    scipy 의존성 없이 간단히 구현 — 후보가 min_distance보다 가까우면 더 큰 값만 남긴다.
    """
    n = len(values)
    candidates = [
        i
        for i in range(1, n - 1)
        if values[i] >= values[i - 1]
        and values[i] >= values[i + 1]
        and values[i] >= min_value
    ]
    candidates.sort(key=lambda i: -values[i])
    kept: list[int] = []
    for i in candidates:
        if all(abs(i - k) >= min_distance for k in kept):
            kept.append(i)
    kept.sort()
    return kept


def _estimate_pose_heuristic_mask_geometry(rgb: np.ndarray, mask: np.ndarray) -> PoseResult:
    """SAM2(또는 알파채널) 이진 마스크의 실루엣 기하만으로 18개 키포인트를 근사.

    mask: uint8 (H,W), 0 또는 255 (또는 그 사이 값 — 127 기준 이진화).
    알려진 한계는 파일 상단 docstring 참고.
    """
    if not CV2_AVAILABLE:
        raise RuntimeError("opencv-python-headless가 필요합니다(heuristic 포즈 추정).")

    h, w = mask.shape[:2]
    mask_bool = mask > 127
    if not mask_bool.any():
        raise RuntimeError("마스크가 완전히 비어 있습니다 — 세그멘테이션을 먼저 확인하세요.")

    # 가장 큰 연결 컴포넌트만 사용(잡음 제거).
    num_labels, labels = cv2.connectedComponents(mask_bool.astype(np.uint8))
    if num_labels > 2:
        sizes = [(labels == i).sum() for i in range(1, num_labels)]
        biggest = int(np.argmax(sizes)) + 1
        mask_bool = labels == biggest

    ys, xs = np.where(mask_bool)
    xmin, xmax = int(xs.min()), int(xs.max())
    ymin, ymax = int(ys.min()), int(ys.max())
    bbox_w = max(1, xmax - xmin)
    bbox_h = max(1, ymax - ymin)

    top, bottom, valid_cols = _column_top_bottom_profiles(mask_bool)

    warnings: list[str] = []

    # 1) 머리 방향: bbox 바깥쪽 40% 구간 중 실루엣이 더 높이(=더 작은 y) 솟은 쪽.
    outer_frac = 0.40
    left_end = xmin + int(bbox_w * outer_frac)
    right_start = xmax - int(bbox_w * outer_frac)

    def _min_top(x0: int, x1: int) -> float:
        cols = np.arange(x0, x1 + 1)
        cols = cols[(cols >= 0) & (cols < w)]
        cols = cols[valid_cols[cols]]
        if len(cols) == 0:
            return float("inf")
        return float(top[cols].min())

    left_top = _min_top(xmin, left_end)
    right_top = _min_top(right_start, xmax)
    head_side = "left" if left_top <= right_top else "right"
    tail_side = "right" if head_side == "left" else "left"

    def _region_bounds(side: str, frac: float) -> tuple[int, int]:
        if side == "left":
            return xmin, xmin + int(bbox_w * frac)
        return xmax - int(bbox_w * frac), xmax

    head_x0, head_x1 = _region_bounds(head_side, outer_frac)
    tail_x0, tail_x1 = _region_bounds(tail_side, 0.30)

    def _valid_cols_in(x0: int, x1: int) -> np.ndarray:
        cols = np.arange(max(0, x0), min(w, x1 + 1))
        return cols[valid_cols[cols]] if len(cols) else cols

    head_cols = _valid_cols_in(head_x0, head_x1)
    if len(head_cols) == 0:
        raise RuntimeError("머리 쪽 실루엣 열을 찾지 못했습니다 — 마스크 품질을 확인하세요.")

    head_top_x = int(head_cols[np.argmin(top[head_cols])])
    head_top_y = int(top[head_top_x])

    # 2) 코: 머리 영역의 상위 45% 높이 범위 내에서, 몸통 중심에서 가장 먼(=옆으로 튀어나온) 점.
    head_h_range = (head_top_y, head_top_y + int(bbox_h * 0.45))
    region_pixels_mask = np.zeros_like(mask_bool)
    region_pixels_mask[
        max(0, head_h_range[0]) : min(h, head_h_range[1] + 1), head_x0 : head_x1 + 1
    ] = mask_bool[
        max(0, head_h_range[0]) : min(h, head_h_range[1] + 1), head_x0 : head_x1 + 1
    ]
    rys, rxs = np.where(region_pixels_mask)
    if len(rxs) > 0:
        nose_x = int(rxs.min()) if head_side == "left" else int(rxs.max())
        nose_y = int(rys[rxs == nose_x].mean())
    else:
        nose_x, nose_y = head_top_x, head_top_y
        warnings.append("코 위치 추정 실패 — head_top으로 대체")

    # 3) 목: 머리에서 몸통 쪽으로 bbox의 12%만큼 이동한 지점의 실루엣 상단.
    sign = 1 if head_side == "left" else -1
    neck_x = int(np.clip(head_top_x + sign * int(bbox_w * 0.12), xmin, xmax))
    neck_cols = _valid_cols_in(neck_x - 2, neck_x + 2)
    neck_y = int(top[neck_cols].min()) if len(neck_cols) else head_top_y

    # 4) 등 중앙: bbox 수평 중심의 실루엣 상단.
    spine_x = (xmin + xmax) // 2
    spine_cols = _valid_cols_in(spine_x - 3, spine_x + 3)
    spine_y = int(top[spine_cols].min()) if len(spine_cols) else (ymin + head_top_y) // 2

    # 5) 꼬리: 몸통-꼬리 경계(tail_base)와 꼬리 끝(tail_tip, 위로 말린 지점 우선).
    tail_cols = _valid_cols_in(tail_x0, tail_x1)
    if len(tail_cols) == 0:
        tail_base_x, tail_base_y = xmax if head_side == "left" else xmin, spine_y
        tail_tip_x, tail_tip_y = tail_base_x, tail_base_y
        warnings.append("꼬리 쪽 실루엣 열을 찾지 못함 — spine 위치로 대체")
    else:
        tail_base_x = int(tail_cols[0]) if head_side == "left" else int(tail_cols[-1])
        tail_base_y = int((top[tail_base_x] + bottom[tail_base_x]) / 2)
        tail_tip_idx = int(np.argmin(top[tail_cols]))
        tail_tip_x = int(tail_cols[tail_tip_idx])
        tail_tip_y = int(top[tail_tip_x])

    # 6) 다리: 몸통 핵심 구간(머리/꼬리 바깥쪽 제외)의 하단(bottom) 프로파일에서 지역 최댓값.
    core_x0 = min(head_x1, tail_x1) if head_side == "left" else min(tail_x1, head_x1)
    core_x0, core_x1 = sorted([head_x1, tail_x1])
    core_cols = _valid_cols_in(core_x0, core_x1)
    if len(core_cols) < 3:
        core_cols = _valid_cols_in(xmin, xmax)

    bottom_profile = bottom[core_cols].astype(np.float64)
    baseline = float(np.percentile(bottom_profile, 60))
    peak_idx_local = _find_local_maxima_1d(
        bottom_profile, min_distance=max(3, bbox_w // 40), min_value=baseline - bbox_h * 0.05
    )
    peak_cols = [int(core_cols[i]) for i in peak_idx_local]

    if len(peak_cols) == 0:
        warnings.append("다리(발끝) 후보를 찾지 못함 — 몸통 중앙 하단으로 전부 대체")
        peak_cols = [spine_x]

    # 앞다리 후보(목에 더 가까움) vs 뒷다리 후보(꼬리에 더 가까움)로 분리.
    def _dist_to_neck(c: int) -> float:
        return abs(c - neck_x)

    def _dist_to_tail(c: int) -> float:
        return abs(c - tail_base_x)

    front_candidates = sorted(
        [c for c in peak_cols if _dist_to_neck(c) <= _dist_to_tail(c)],
        key=lambda c: bottom[c] * -1,
    )
    back_candidates = sorted(
        [c for c in peak_cols if _dist_to_neck(c) > _dist_to_tail(c)],
        key=lambda c: bottom[c] * -1,
    )
    if not front_candidates and back_candidates:
        front_candidates = back_candidates[:1]
        warnings.append("앞다리 후보가 없어 뒷다리 후보를 재사용")
    if not back_candidates and front_candidates:
        back_candidates = front_candidates[:1]
        warnings.append("뒷다리 후보가 없어 앞다리 후보를 재사용")

    def _pick_pair(cands: list[int]) -> tuple[int, int]:
        top2 = sorted(cands[:2])
        if len(top2) == 1:
            warnings.append("근/원위 다리 구분 불가 — 동일 위치를 좌우에 복제(문서의 알려진 한계 참고)")
            return top2[0], top2[0]
        return top2[0], top2[1]

    front_a, front_b = _pick_pair(front_candidates)
    back_a, back_b = _pick_pair(back_candidates)
    # 머리 쪽이 왼쪽이면 좌표상 더 작은 x가 몸의 앞쪽에 더 가까운 다리 — 이름(left/right)은
    # 카메라 기준 임의 배정(실루엣만으로는 진짜 좌/우 구분 불가, 이름은 관습적 표기일 뿐).
    front_left_x, front_right_x = front_a, front_b
    back_left_x, back_right_x = back_a, back_b

    def _leg_points(col: int, hip_shoulder_y: float) -> tuple[tuple[int, float], tuple[int, float], tuple[int, int]]:
        paw_y = int(bottom[col]) if valid_cols[col] else int(ymax)
        attach_y = hip_shoulder_y + bbox_h * 0.08
        mid_y = (attach_y + paw_y) / 2.0
        return (col, attach_y), (col, mid_y), (col, paw_y)

    fl_attach, fl_mid, fl_paw = _leg_points(front_left_x, top[front_left_x] if valid_cols[front_left_x] else spine_y)
    fr_attach, fr_mid, fr_paw = _leg_points(front_right_x, top[front_right_x] if valid_cols[front_right_x] else spine_y)
    bl_attach, bl_mid, bl_paw = _leg_points(back_left_x, top[back_left_x] if valid_cols[back_left_x] else spine_y)
    br_attach, br_mid, br_paw = _leg_points(back_right_x, top[back_right_x] if valid_cols[back_right_x] else spine_y)

    def kp(pt: tuple[float, float], conf: float = 0.5) -> Keypoint:
        return Keypoint(x=float(pt[0]), y=float(pt[1]), confidence=conf, visible=True)

    keypoints = {
        "nose": kp((nose_x, nose_y), 0.5),
        "head_top": kp((head_top_x, head_top_y), 0.6),
        "neck": kp((neck_x, neck_y), 0.55),
        "spine_mid": kp((spine_x, spine_y), 0.6),
        "tail_base": kp((tail_base_x, tail_base_y), 0.5),
        "tail_tip": kp((tail_tip_x, tail_tip_y), 0.4),
        "front_left_shoulder": kp(fl_attach, 0.4),
        "front_left_elbow": kp(fl_mid, 0.3),
        "front_left_paw": kp(fl_paw, 0.45),
        "front_right_shoulder": kp(fr_attach, 0.4),
        "front_right_elbow": kp(fr_mid, 0.3),
        "front_right_paw": kp(fr_paw, 0.45),
        "back_left_hip": kp(bl_attach, 0.4),
        "back_left_knee": kp(bl_mid, 0.3),
        "back_left_paw": kp(bl_paw, 0.45),
        "back_right_hip": kp(br_attach, 0.4),
        "back_right_knee": kp(br_mid, 0.3),
        "back_right_paw": kp(br_paw, 0.45),
    }

    return PoseResult(
        keypoints=keypoints,
        image_width=w,
        image_height=h,
        backend="heuristic_mask_geometry",
        head_side=head_side,
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# 공개 API
# --------------------------------------------------------------------------


def estimate_pose(
    rgb: np.ndarray,
    mask: np.ndarray,
    *,
    backend: Optional[str] = None,
) -> PoseResult:
    """RGB 이미지 + 이진 마스크(0/255) → 18개 키포인트.

    backend: "heuristic" | "deeplabcut_superanimal" | "auto" | None
             None이면 POSE_ESTIMATION_BACKEND 환경변수(기본 "heuristic") 사용.
    """
    resolved = (backend or os.getenv("POSE_ESTIMATION_BACKEND", "heuristic")).strip().lower()

    if resolved in ("deeplabcut_superanimal", "deeplabcut", "dlc"):
        return _estimate_pose_deeplabcut_superanimal(rgb)

    if resolved == "auto":
        try:
            return _estimate_pose_deeplabcut_superanimal(rgb)
        except Exception as e:
            logger.warning("DeepLabCut SuperAnimal 사용 불가(%s) — heuristic으로 폴백", e)
            result = _estimate_pose_heuristic_mask_geometry(rgb, mask)
            result.warnings.insert(0, f"DeepLabCut 폴백 이유: {e}")
            return result

    return _estimate_pose_heuristic_mask_geometry(rgb, mask)


def keypoints_to_dict(pose: PoseResult) -> dict[str, dict]:
    """디버그 출력/JSON 직렬화용."""
    return {
        name: {"x": kp.x, "y": kp.y, "confidence": kp.confidence, "visible": kp.visible}
        for name, kp in pose.keypoints.items()
    }
