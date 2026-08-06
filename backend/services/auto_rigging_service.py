"""
Action(달려오기/짖기/배깔기) 리깅 파이프라인 2단계 — 키포인트 → Spine2D 뼈대+이미지.

흐름: pose_estimation_service(18개 키포인트) + SAM2/알파 마스크 → **이 모듈**
→ 본(bone) 계층 생성 → 본마다 원본 사진에서 사각형 영역을 크롭(회전 워프 없이
축 정렬 크롭 + attachment.rotation으로 상쇄, 이유는 spine_rig_builder.py 상단
docstring 참고) → 아틀라스 패킹 → skeleton.json/skeleton.atlas/skeleton.png.

★ "완전 자동 메쉬 스키닝"이 아니라 "본당 독립 사각형 워프"를 택한 이유
사용자 요청에 명시된 대로, 사진 1장에서 프로덕션급 스킨드 메쉬를 완전 자동
생성하는 것은 현재 기술로 실질적으로 미해결 문제다. 이 프로토타입은 요청된
현실적 MVP 축소판을 그대로 구현한다: "본당 별도 RegionAttachment(사각형)를
그 본에 붙이고, 본이 회전하면 사각형 이미지가 통째로 같이 회전"하는 방식 —
관절 부분에서 사각형끼리 벌어지거나 겹치는 시각적 결함이 날 수 있음을 인지하고
있다(메쉬 스키닝이면 이 문제가 없지만, 그건 다음 단계 개선 과제).

★ 본 계층(14개 + root)
root
 └ pelvis (tail_base → spine_mid)
    ├ spine (spine_mid → neck)
    │  ├ neck (neck → head_top)
    │  │  └ head (head_top → nose)
    │  ├ front_left_upper/lower (shoulder→elbow→paw)
    │  └ front_right_upper/lower
    ├ tail1/tail2 (tail_base → tail_mid → tail_tip)
    ├ back_left_upper/lower (hip→knee→paw)
    └ back_right_upper/lower

★ 좌표계: 원점 = tail_base 키포인트(이미지 픽셀), Spine 월드 좌표는 이미지
Y축을 뒤집어 사용(이미지 Y는 아래로 증가, Spine 월드 Y는 위로 증가).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image

from .pose_estimation_service import KEYPOINT_NAMES, PoseResult
from .spine_rig_builder import (
    BoneSetup,
    RegionSpec,
    build_skeleton_json,
    pack_atlas,
    region_attachment_transform,
    write_spine_asset,
)

logger = logging.getLogger(__name__)

# (본 이름, 부모 본 이름(None=root), 시작 키포인트, 끝 키포인트)
BONE_SPECS: list[tuple[str, Optional[str], str, str]] = [
    ("pelvis", None, "tail_base", "spine_mid"),
    ("spine", "pelvis", "spine_mid", "neck"),
    ("neck", "spine", "neck", "head_top"),
    ("head", "neck", "head_top", "nose"),
    ("tail1", "pelvis", "tail_base", "tail_mid"),
    ("tail2", "tail1", "tail_mid", "tail_tip"),
    ("front_left_upper", "spine", "front_left_shoulder", "front_left_elbow"),
    ("front_left_lower", "front_left_upper", "front_left_elbow", "front_left_paw"),
    ("front_right_upper", "spine", "front_right_shoulder", "front_right_elbow"),
    ("front_right_lower", "front_right_upper", "front_right_elbow", "front_right_paw"),
    ("back_left_upper", "pelvis", "back_left_hip", "back_left_knee"),
    ("back_left_lower", "back_left_upper", "back_left_knee", "back_left_paw"),
    ("back_right_upper", "pelvis", "back_right_hip", "back_right_knee"),
    ("back_right_lower", "back_right_upper", "back_right_knee", "back_right_paw"),
]

# 본 세그먼트에 이미지를 씌울 때 쓰는 폭(전체 몸통 bbox 높이에 대한 비율).
WIDTH_FRACTIONS: dict[str, float] = {
    "pelvis": 0.42,
    "spine": 0.42,
    "neck": 0.28,
    "head": 0.34,
    "tail1": 0.14,
    "tail2": 0.09,
    "front_left_upper": 0.15,
    "front_left_lower": 0.11,
    "front_right_upper": 0.15,
    "front_right_lower": 0.11,
    "back_left_upper": 0.17,
    "back_left_lower": 0.12,
    "back_right_upper": 0.17,
    "back_right_lower": 0.12,
}

# 그리는 순서(뒤→앞). Spine 슬롯 배열은 앞쪽 항목이 먼저(=더 뒤에) 그려진다.
SLOT_DRAW_ORDER: list[str] = [
    "tail2",
    "tail1",
    "back_right_lower",
    "back_right_upper",
    "back_left_lower",
    "back_left_upper",
    "pelvis",
    "spine",
    "front_right_lower",
    "front_right_upper",
    "front_left_lower",
    "front_left_upper",
    "neck",
    "head",
]


@dataclass
class RigResult:
    skeleton_json: dict
    atlas_text: str
    atlas_page_image: Image.Image
    bone_lengths: dict[str, float]
    bone_world_transforms: dict[str, tuple[tuple[float, float], float]]
    bone_local_rotations: dict[str, float]
    bone_local_positions: dict[str, tuple[float, float]]
    pose: PoseResult
    warnings: list[str] = field(default_factory=list)


def _ensure_all_keypoints(pose: PoseResult) -> list[str]:
    """누락된 키포인트를 spine_mid(몸통 중앙)으로 대체 — 크래시 없이 계속 진행."""
    warnings: list[str] = []
    spine_mid = pose.keypoints.get("spine_mid")
    default = spine_mid or next(iter(pose.keypoints.values()), None)
    if default is None:
        raise RuntimeError("키포인트가 하나도 없습니다 — 포즈 추정이 완전히 실패했습니다.")
    for name in KEYPOINT_NAMES:
        if name not in pose.keypoints:
            warnings.append(f"키포인트 '{name}' 누락 — spine_mid(또는 임의 기본값)으로 대체")
            pose.keypoints[name] = default
    return warnings


def _kp_img(pose: PoseResult, name: str) -> tuple[float, float]:
    if name == "tail_mid":
        a, b = pose.keypoints["tail_base"], pose.keypoints["tail_tip"]
        return ((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
    kp = pose.keypoints[name]
    return (kp.x, kp.y)


def _mask_bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.where(mask > 127)
    if len(xs) == 0:
        raise RuntimeError("마스크가 비어 있습니다.")
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _crop_rotated_region(
    rgba: np.ndarray,
    p0_img: tuple[float, float],
    p1_img: tuple[float, float],
    width_px: float,
    *,
    pad_frac: float = 0.12,
) -> tuple[np.ndarray, tuple[float, float]]:
    """p0→p1 본 세그먼트를 감싸는 회전 사각형의 '축 정렬 바운딩 박스'를 크롭.

    픽셀 자체를 회전시키지 않는다(spine_rig_builder.py 상단 docstring 참고) —
    대신 RegionAttachment의 rotation 필드로 최종 표시 시 상쇄한다. 반환:
    (crop_rgba, crop_center_in_image_coords)
    """
    h_img, w_img = rgba.shape[:2]
    dx, dy = p1_img[0] - p0_img[0], p1_img[1] - p0_img[1]
    length = math.hypot(dx, dy) or 1.0
    angle = math.atan2(dy, dx)
    pad = length * pad_frac
    local_corners = [
        (-pad, -width_px / 2),
        (length + pad, -width_px / 2),
        (length + pad, width_px / 2),
        (-pad, width_px / 2),
    ]
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    xs, ys = [], []
    for lx, ly in local_corners:
        xs.append(p0_img[0] + lx * cos_a - ly * sin_a)
        ys.append(p0_img[1] + lx * sin_a + ly * cos_a)

    x0, x1 = max(0, int(math.floor(min(xs)))), min(w_img, int(math.ceil(max(xs))))
    y0, y1 = max(0, int(math.floor(min(ys)))), min(h_img, int(math.ceil(max(ys))))
    if x1 - x0 < 2 or y1 - y0 < 2:
        cx, cy = int(p0_img[0]), int(p0_img[1])
        x0, x1 = max(0, cx - 5), min(w_img, cx + 5)
        y0, y1 = max(0, cy - 5), min(h_img, cy + 5)

    crop = rgba[y0:y1, x0:x1].copy()
    crop_center_img = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    return crop, crop_center_img


def build_rig_from_pose(
    rgba: np.ndarray,
    mask: np.ndarray,
    pose: PoseResult,
    *,
    skin_name: str = "default",
) -> RigResult:
    """RGBA 사진 + 마스크 + 18개 키포인트 → 셋업 포즈 Spine 스켈레톤(애니메이션 없음).

    애니메이션(예: 배깔기)은 spine_action_curves.build_lie_down_animation()으로
    별도로 만든 뒤 반환된 skeleton_json["animations"]에 합쳐 쓴다.
    """
    warnings = list(pose.warnings) + _ensure_all_keypoints(pose)

    xmin, ymin, xmax, ymax = _mask_bbox(mask)
    bbox_h = max(1, ymax - ymin)

    origin_img = _kp_img(pose, "tail_base")

    def to_world(img_xy: tuple[float, float]) -> tuple[float, float]:
        return (img_xy[0] - origin_img[0], -(img_xy[1] - origin_img[1]))

    # world_state[bone_name] = (world_pos, world_rotation_deg)
    world_state: dict[str, tuple[tuple[float, float], float]] = {"root": ((0.0, 0.0), 0.0)}
    bones: list[BoneSetup] = [
        BoneSetup(
            name="root", parent=None, local_x=0.0, local_y=0.0, local_rotation=0.0,
            length=0.0, world_pos=(0.0, 0.0), world_rotation=0.0,
        )
    ]
    bone_lengths: dict[str, float] = {}
    start_img_points: dict[str, tuple[float, float]] = {}
    end_img_points: dict[str, tuple[float, float]] = {}

    for name, parent, start_kp, end_kp in BONE_SPECS:
        parent_name = parent or "root"
        parent_world_pos, parent_world_rot = world_state[parent_name]

        start_img = _kp_img(pose, start_kp)
        end_img = _kp_img(pose, end_kp)
        start_world = to_world(start_img)
        end_world = to_world(end_img)
        dxw, dyw = end_world[0] - start_world[0], end_world[1] - start_world[1]
        length = math.hypot(dxw, dyw)
        world_rot = math.degrees(math.atan2(dyw, dxw)) if length > 1e-6 else parent_world_rot

        from .spine_rig_builder import world_to_local

        lx, ly, lrot = world_to_local(start_world, world_rot, parent_world_pos, parent_world_rot)

        world_state[name] = (start_world, world_rot)
        bone_lengths[name] = length
        start_img_points[name] = start_img
        end_img_points[name] = end_img
        bones.append(
            BoneSetup(
                name=name, parent=parent_name, local_x=lx, local_y=ly, local_rotation=lrot,
                length=length, world_pos=start_world, world_rotation=world_rot,
            )
        )

    # --- 본별 이미지 리전 크롭 + 아틀라스 패킹 ---
    regions: list[RegionSpec] = []
    attachments: dict[str, dict] = {}
    for name in SLOT_DRAW_ORDER:
        if name not in WIDTH_FRACTIONS:
            continue
        width_px = max(6.0, WIDTH_FRACTIONS[name] * bbox_h)
        crop, crop_center_img = _crop_rotated_region(
            rgba, start_img_points[name], end_img_points[name], width_px
        )
        if crop.shape[0] < 1 or crop.shape[1] < 1:
            warnings.append(f"본 '{name}' 이미지 크롭 실패 — 슬롯을 비워둠")
            continue

        bone_world_pos, bone_world_rot = world_state[name]
        crop_center_world = to_world(crop_center_img)
        ax, ay, arot = region_attachment_transform(bone_world_pos, bone_world_rot, crop_center_world)

        region_name = f"{name}_img"
        regions.append(
            RegionSpec(
                name=region_name,
                image=crop,
                bone_name=name,
                attachment_x=ax,
                attachment_y=ay,
                attachment_rotation=arot,
            )
        )
        attachments[name] = {
            "name": region_name,
            "x": ax,
            "y": ay,
            "rotation": arot,
            "width": crop.shape[1],
            "height": crop.shape[0],
        }

    atlas_text, atlas_page, _placements = pack_atlas(regions)

    slots = [s for s in SLOT_DRAW_ORDER if s in attachments]
    skeleton_json = build_skeleton_json(
        bones=bones,
        slots=slots,
        attachments=attachments,
        skin_name=skin_name,
    )

    bone_world_transforms = {name: world_state[name] for name in world_state}
    bone_local_rotations = {b.name: b.local_rotation for b in bones}
    bone_local_positions = {b.name: (b.local_x, b.local_y) for b in bones}

    return RigResult(
        skeleton_json=skeleton_json,
        atlas_text=atlas_text,
        atlas_page_image=atlas_page,
        bone_lengths=bone_lengths,
        bone_world_transforms=bone_world_transforms,
        bone_local_rotations=bone_local_rotations,
        bone_local_positions=bone_local_positions,
        pose=pose,
        warnings=warnings,
    )


def bone_parent_map() -> dict[str, str]:
    """spine_action_curves 등에서 참고할 수 있는 (본 → 부모 본) 매핑."""
    result = {"root": None}
    for name, parent, _s, _e in BONE_SPECS:
        result[name] = parent or "root"
    return result


# --------------------------------------------------------------------------
# 큐 워커/CLI 테스트 스크립트가 공유하는 상위 오케스트레이션 함수
# --------------------------------------------------------------------------


def _derive_mask_for_pipeline(rgba: np.ndarray) -> tuple[np.ndarray, str]:
    """알파채널 → SAM2 → GrabCut 순으로 시도(vitmatte_service의 기존 폴백 함수 재사용).

    Returns: (mask, 실제로 쓰인 소스 이름)
    """
    alpha = rgba[:, :, 3]
    if alpha.min() < 250:
        return alpha, "alpha_channel"

    rgb = rgba[:, :, :3]
    # 입력이 이미 크롭된 누끼라 프레임 전체가 대상 — 예전에는 bbox=None 을 넘겨
    # "중앙 80% 사각형" 폴백을 탔지만, 이제는 전체 프레임 박스를 명시한다.
    try:
        from .vitmatte_service import _get_device, _sam2_mask, full_frame_bbox

        mask, _score = _sam2_mask(
            rgb, full_frame_bbox(rgb), "facebook/sam2.1-hiera-tiny", _get_device()
        )
        return mask, "sam2"
    except Exception:
        logger.exception("SAM2 segmentation failed — falling back to GrabCut")
        from .vitmatte_service import _grabcut_mask, full_frame_bbox

        return _grabcut_mask(rgb, full_frame_bbox(rgb)), "grabcut"


def run_auto_rigging_pipeline(
    pet_image,
    *,
    actions: Optional[list[str]] = None,
    pose_backend: str = "heuristic",
    user_id: str = "anonymous",
    content_id: Optional[str] = None,
    upload_to_supabase: bool = True,
    local_output_dir=None,
    progress_cb=None,
) -> dict:
    """사진 1장 → (세그멘테이션 → 포즈추정 → 리깅 → 액션 애니메이션) → Spine 에셋.

    action_video_jobs 워커의 `_process_one_job()` / live_portrait_batch.
    run_live_portrait_batch()와 같은 위상의 함수 — backend/workers/
    auto_rigging_worker.py와 backend/scripts/test_auto_rigging_goya.py 양쪽에서
    공유하기 위해 여기(서비스 레이어)에 둔다.

    pet_image: URL 문자열 / 로컬 파일 경로 문자열 / bytes 모두 지원.
    """
    import tempfile
    from pathlib import Path as _Path

    from PIL import Image as _PILImage

    from .live_portrait_service import resolve_source_image_to_local_path
    from .pose_estimation_service import estimate_pose
    from .spine_action_curves import available_actions, build_lie_down_animation
    from .spine_rig_builder import write_spine_asset

    def _progress(stage: str, detail: str = "") -> None:
        if progress_cb:
            try:
                progress_cb(stage, detail)
            except Exception:
                logger.exception("progress_cb 호출 실패(무시하고 계속)")

    requested = actions or available_actions()
    cid = content_id or "auto-rigging"
    out_root = _Path(local_output_dir) if local_output_dir else _Path(tempfile.mkdtemp(prefix="eb_rig_"))
    out_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="eb_rig_src_") as src_td:
        _progress("segmentation", "소스 이미지 로드")
        src_local = resolve_source_image_to_local_path(pet_image, workdir=_Path(src_td))
        rgba = np.array(_PILImage.open(src_local).convert("RGBA"))

        mask, mask_source = _derive_mask_for_pipeline(rgba)

        _progress("pose_estimation", f"backend={pose_backend}")
        pose = estimate_pose(rgba[:, :, :3], mask, backend=pose_backend)

        _progress("rigging", "본 생성 + 이미지 워핑 + 아틀라스 패킹")
        rig = build_rig_from_pose(rgba, mask, pose)

        _progress("animation", f"actions={requested}")
        for action in requested:
            if action == "lie_down":
                anim = build_lie_down_animation(
                    bone_local_rotations=rig.bone_local_rotations,
                    bone_local_positions=rig.bone_local_positions,
                    bone_lengths=rig.bone_lengths,
                    head_side=pose.head_side,
                )
                rig.skeleton_json["animations"].update(anim)
            else:
                rig.warnings.append(f"액션 '{action}'은 아직 미구현 — 스킵(문서의 다음 단계 참고)")

        pet_dir = out_root / (cid)
        write_spine_asset(
            pet_dir,
            skeleton_json=rig.skeleton_json,
            atlas_text=rig.atlas_text,
            atlas_page_image=rig.atlas_page_image,
        )

        result: dict = {
            "mask_source": mask_source,
            "pose_backend_used": pose.backend,
            "head_side": pose.head_side,
            "animations": list(rig.skeleton_json["animations"].keys()),
            "warnings": pose.warnings + rig.warnings,
            "local_dir": str(pet_dir),
        }

        if upload_to_supabase:
            _progress("uploading", "Supabase Storage 업로드")
            import asyncio

            from . import supabase_assets

            urls = {}
            for fname in ("skeleton.json", "skeleton.atlas", "skeleton.png"):
                fpath = pet_dir / fname
                if not fpath.is_file():
                    continue
                content_type = {
                    "skeleton.json": "application/json",
                    "skeleton.atlas": "text/plain",
                    "skeleton.png": "image/png",
                }[fname]
                urls[fname] = asyncio.run(
                    supabase_assets.upload_asset_to_storage(
                        f"{user_id}/{cid}/auto_rig/{fname}", fpath.read_bytes(), content_type
                    )
                )
            result["uploaded_urls"] = urls

        return result
