"""COME_CLOSER 실제 마지막 프레임 → 런별 파생 CLOSE_POSITION (2026-10-23, 단순화).

COME_CLOSER 는 이미 일반 QA 를 통과해 발행됐으므로 이 단계는 그 영상의 **리터럴 마지막
프레임**을 그대로 신뢰한다. 종단 구간 스캔·이른 프레임 선택·가시성/크롭/블러/해부학/
근접 임계 검사·신원 복원·이미지 편집을 **하지 않는다**. 마지막 프레임을 손실 없는
PNG 로 박제해 파생 CLOSE_POSITION 으로 저장하고, 그 자산이 RETURN_HOME 의 시작
이미지가 된다.
"""

from __future__ import annotations

import io
import logging
from types import SimpleNamespace
from typing import Optional

import numpy as np

from . import motion_video_qa, pet_reference_service, supabase_assets

logger = logging.getLogger(__name__)

#: 파생 CLOSE_POSITION 의 ledger derived_kind (pet_reference_images.role=derived).
DERIVED_KIND_CLOSE_POSITION = "close_position"

#: 마지막 프레임 = 영상 분율 1.0 (sample_frames/extract_frame_png 의 "마지막 디코딩 프레임").
FINAL_FRACTION = 1.0


def extract_final_frame_png(video_bytes: bytes) -> Optional[bytes]:
    """영상의 **실제 마지막 프레임**을 손실 없는 PNG 로 1회 추출한다 — 편집·리사이즈·
    크롭·색보정·기하 변환·프레임 선택 없음. 우선 ffmpeg 로 직접 PNG 추출, 안 되면
    디코딩된 마지막 RGB 를 PIL 로 1회 손실 없이 인코딩한다. 디코딩이 불가능하면
    (ffmpeg 없음 등) None — 호출자는 RETURN_HOME 을 건너뛴다."""
    direct = motion_video_qa.extract_frame_png(video_bytes, FINAL_FRACTION)
    if direct:
        return direct
    frames = motion_video_qa.sample_frames(video_bytes, fractions=(FINAL_FRACTION,))
    rgb = frames[0] if frames else None
    if rgb is None:
        return None
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgb.astype(np.uint8)), "RGB").save(buf, format="PNG")
    return buf.getvalue()


async def persist_close_position(
    *,
    user_id: str,
    content_id: str,
    pet_id: str,
    come_closer_run_id: str,
    come_closer_motion_version_id: Optional[str],
    selected_candidate_id: Optional[str],
    source_video_object_path: Optional[str],
    png: bytes,
) -> SimpleNamespace:
    """COME_CLOSER 의 마지막 프레임 PNG 를 **그대로** 파생 CLOSE_POSITION 으로 저장한다
    (이미지 편집 없음). 반환: SimpleNamespace(bucket, object_path, reference_id) — RETURN_HOME 의
    start_image_override 로 쓴다. 계보(lineage)는 record_derived 의 diagnostics 에 담는다."""
    object_path = f"{user_id}/{content_id}/derived/close_position_{come_closer_run_id}.png"
    await supabase_assets.upload_asset_to_storage(object_path, png, "image/png")
    ref = await pet_reference_service.record_derived(
        user_id=user_id,
        content_id=content_id,
        object_path=object_path,
        derived_kind=DERIVED_KIND_CLOSE_POSITION,
        mime_type="image/png",
        diagnostics={
            "close_position": {
                "pet_id": pet_id,
                "come_closer_run_id": come_closer_run_id,
                "come_closer_motion_version_id": come_closer_motion_version_id,
                "selected_candidate_id": selected_candidate_id,
                "source_video_object_path": source_video_object_path,
                "frame": "final",
                "close_position_object_path": object_path,
                "identity_corrected": False,  # 이 단계는 신원 복원 없음 (그대로 사용).
            }
        },
    )
    return SimpleNamespace(
        bucket=supabase_assets.BUCKET,
        object_path=object_path,
        reference_id=getattr(ref, "id", None),
    )
