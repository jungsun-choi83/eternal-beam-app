"""
LivePortrait 출력 영상의 배경을 순수 블랙(0,0,0)으로 강제하는 SAM2 후처리 — 3단계.

목적: LivePortrait(Animals 모드, flag_pasteback=False)는 드라이빙 영상의 배경을
소스에 붙이지 않지만, 워핑 경계 주변에 자체 생성 노이즈/아티팩트가 남을 수 있다.
이 모듈은 SAM2로 강아지 실루엣을 구해 그 외 영역을 전부 (0,0,0)으로 밀어버려서
페퍼스 고스트(반사 디스플레이) 장치에서 배경이 완전히 안 보이게 만든다.

★ 왜 매 프레임 SAM2를 돌리지 않는가 (엔지니어링 트레이드오프)
`backend/services/vitmatte_service.py`의 `_load_sam2`/`_sam2_mask`를 그대로 재사용해서
SAM2를 돌리는 건 어렵지 않지만, 영상 1건이 수백 프레임이고 액션이 20건이라 매
프레임 SAM2 추론은 (GPU에서도) 배치 처리 시간이 크게 늘어난다. 그래서:

  - N프레임(기본 10)마다 "키프레임"으로 지정해 SAM2를 실제로 돌려 정밀 마스크를 얻고
  - 키프레임 사이 구간은 Farneback optical flow로 이전 마스크를 현재 프레임으로
    "워프"해서 재사용한다(마스크 전파, video object segmentation에서 흔히 쓰는 절충안).
  - 워프된 마스크는 근사값이라 경계에 약간의 오차가 생길 수 있어, 최종적으로
    마스크를 약하게 dilate해서 강아지 몸통을 깎아먹지 않는 쪽으로 안전하게 둔다
    (배경에 옅은 그림자 픽셀이 살짝 남는 것보다, 강아지 몸통이 깎이는 게 더 눈에
    띄는 결함이라 판단).

이 모듈은 로컬 RTX 4090 워커 프로세스 안에서, LivePortrait 추론 직후 같은 프로세스
내에서 순차 호출된다(별도 Modal 함수로 안 쪼갠 이유: 애초에 Modal 대신 로컬 GPU를
1차 실행 장소로 쓰기로 했으므로, 굳이 배경 강제 단계만 다른 프로세스/venue로
나눌 이유가 없다 — 같은 GPU 메모리에 SAM2+LivePortrait를 함께 올려 두고 순차
처리하는 편이 단순하고 오버헤드도 적다).

환경변수:
  LIVE_PORTRAIT_SAM2_KEYFRAME_INTERVAL  기본 "10" (프레임)
  LIVE_PORTRAIT_SAM2_MASK_DILATE_PX     기본 "3" (경계 안전마진)
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np

try:
    import cv2

    CV2_AVAILABLE = True
except Exception:
    CV2_AVAILABLE = False

from .vitmatte_service import _get_device, _load_sam2  # noqa: F401  (기존 SAM2 로더 재사용)

BBox = tuple[int, int, int, int]


def _keyframe_interval() -> int:
    return max(1, int(os.getenv("LIVE_PORTRAIT_SAM2_KEYFRAME_INTERVAL", "10")))


def _mask_dilate_px() -> int:
    return max(0, int(os.getenv("LIVE_PORTRAIT_SAM2_MASK_DILATE_PX", "3")))


def _full_frame_box(w: int, h: int, pad_frac: float = 0.06) -> BBox:
    pad_w, pad_h = int(w * pad_frac), int(h * pad_frac)
    return pad_w, pad_h, w - pad_w, h - pad_h


def _bbox_from_mask(mask: np.ndarray, pad_frac: float = 0.08) -> Optional[BBox]:
    ys, xs = np.where(mask > 0)
    if ys.size == 0:
        return None
    h, w = mask.shape[:2]
    x1, x2 = int(xs.min()), int(xs.max())
    y1, y2 = int(ys.min()), int(ys.max())
    bw, bh = (x2 - x1) * pad_frac, (y2 - y1) * pad_frac
    return (
        max(0, int(x1 - bw)),
        max(0, int(y1 - bh)),
        min(w, int(x2 + bw)),
        min(h, int(y2 + bh)),
    )


def _sam2_mask_from_box(
    rgb: np.ndarray, box: BBox, model_name: str, device: str
) -> np.ndarray:
    """vitmatte_service._sam2_mask와 동일 패턴(SAM2 로드+박스 프롬프트)이지만
    이미 있는 bbox를 그대로 받아 강아지 전용으로 특화한 얇은 버전."""
    import torch
    from PIL import Image

    processor, model = _load_sam2(model_name, device)
    inputs = processor(
        images=Image.fromarray(rgb), input_boxes=[[list(box)]], return_tensors="pt"
    ).to(device)
    with torch.no_grad():
        outputs = model(**inputs, multimask_output=False)
    masks = processor.post_process_masks(outputs.pred_masks.cpu(), inputs["original_sizes"])[0]
    best_mask = masks[0, 0].numpy()
    return np.where(best_mask > 0, 255, 0).astype(np.uint8)


def _warp_mask_with_flow(prev_gray: np.ndarray, cur_gray: np.ndarray, prev_mask: np.ndarray) -> np.ndarray:
    """Farneback optical flow로 이전 프레임의 마스크를 현재 프레임으로 근사 전파."""
    flow = cv2.calcOpticalFlowFarneback(
        prev_gray, cur_gray, None, 0.5, 3, 15, 3, 5, 1.2, 0
    )
    h, w = prev_mask.shape[:2]
    grid_x, grid_y = np.meshgrid(np.arange(w), np.arange(h))
    map_x = (grid_x + flow[..., 0]).astype(np.float32)
    map_y = (grid_y + flow[..., 1]).astype(np.float32)
    warped = cv2.remap(prev_mask, map_x, map_y, interpolation=cv2.INTER_NEAREST)
    return warped


def force_black_background(
    input_video_path: str,
    output_video_path: str,
    *,
    sam2_model: Optional[str] = None,
    device: Optional[str] = None,
    keyframe_interval: Optional[int] = None,
) -> Path:
    """
    입력 mp4의 배경을 SAM2 키프레임 마스크 + optical-flow 전파로 순수 블랙 처리.
    해상도/fps는 원본을 유지한다(800x480 리사이즈는 batch 파이프라인의 다음 단계에서 함).
    """
    if not CV2_AVAILABLE:
        raise RuntimeError("opencv-python-headless가 필요합니다.")

    resolved_model = sam2_model or os.getenv("VITMATTE_SAM2_MODEL", "facebook/sam2.1-hiera-tiny")
    resolved_device = _get_device(device)
    interval = keyframe_interval or _keyframe_interval()
    dilate_px = _mask_dilate_px()

    cap = cv2.VideoCapture(str(input_video_path))
    if not cap.isOpened():
        raise RuntimeError(f"영상을 열 수 없습니다: {input_video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    out_path = Path(output_video_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    dilate_kernel = (
        np.ones((max(1, dilate_px), max(1, dilate_px)), np.uint8) if dilate_px > 0 else None
    )

    with tempfile.TemporaryDirectory(prefix="eb_lp_sam2_") as td:
        raw_out = Path(td) / "raw_no_audio.mp4"
        writer = cv2.VideoWriter(
            str(raw_out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h)
        )

        prev_gray: Optional[np.ndarray] = None
        prev_mask: Optional[np.ndarray] = None
        last_box: Optional[BBox] = _full_frame_box(w, h)
        frame_idx = 0

        try:
            while True:
                ok, frame_bgr = cap.read()
                if not ok:
                    break
                rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

                is_keyframe = (frame_idx % interval == 0) or prev_mask is None
                if is_keyframe:
                    try:
                        mask = _sam2_mask_from_box(rgb, last_box or _full_frame_box(w, h), resolved_model, resolved_device)
                        new_box = _bbox_from_mask(mask)
                        if new_box:
                            last_box = new_box
                    except Exception:
                        # SAM2 실패(의존성/OOM 등) — 이전 마스크가 있으면 그대로 재사용,
                        # 없으면(첫 프레임부터 실패) 전체 프레임을 전경으로 간주해 배경을 안 지움.
                        mask = prev_mask if prev_mask is not None else np.full((h, w), 255, dtype=np.uint8)
                else:
                    mask = _warp_mask_with_flow(prev_gray, gray, prev_mask)

                if dilate_kernel is not None:
                    mask = cv2.dilate(mask, dilate_kernel, iterations=1)

                out_frame = frame_bgr.copy()
                out_frame[mask == 0] = (0, 0, 0)
                writer.write(out_frame)

                prev_gray, prev_mask = gray, mask
                frame_idx += 1
        finally:
            cap.release()
            writer.release()

        _mux_with_ffmpeg_audio_passthrough(str(input_video_path), str(raw_out), str(out_path))

    return out_path


def _mux_with_ffmpeg_audio_passthrough(original_path: str, video_only_path: str, output_path: str) -> None:
    """cv2.VideoWriter는 오디오를 안 넣으므로, 원본에 오디오가 있으면 ffmpeg로 다시 붙인다.
    원본에 오디오가 없거나 ffmpeg가 없으면 비디오만 있는 결과를 그대로 최종 출력으로 사용."""
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", video_only_path,
                "-i", original_path,
                "-map", "0:v:0", "-map", "1:a:0?",
                "-c:v", "copy", "-c:a", "copy",
                "-shortest",
                output_path,
            ],
            capture_output=True,
            timeout=120,
            check=True,
        )
    except Exception:
        import shutil

        shutil.copyfile(video_only_path, output_path)
