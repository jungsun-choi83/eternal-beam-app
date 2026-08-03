"""
고야(Goya) 사진으로 "SAM2+포즈추정+Spine2D 자동 리깅" 파이프라인을 로컬에서
바로 시험하는 스크립트 — backend/scripts/test_live_portrait_goya.py와 같은 목적
(큐/워커를 거치지 않고 파이프라인 함수를 직접 호출해 빠르게 눈으로 확인).

이 스크립트는 **이 저장소의 기본 개발 환경(Windows, GPU 없음)에서도 끝까지
동작한다** — LivePortrait 테스트 스크립트와 달리 기본 백엔드(`heuristic`)는
추가 의존성이 없다(numpy/opencv/Pillow만 사용). `--backend deeplabcut_superanimal`
을 쓰려면 `deeplabcut[pytorch]`가 설치된 머신이 필요하다(문서 참고, 이 세션에서
미검증).

실행(리포 루트에서):
    python -m backend.scripts.test_auto_rigging_goya

    # 옵션:
    python -m backend.scripts.test_auto_rigging_goya \
        --image "누끼딴고야.png" \
        --output-dir outputs/goya_auto_rigging_test \
        --backend heuristic \
        --pet-name goya

출력(--output-dir/--pet-name 폴더 안):
  skeleton.json, skeleton.atlas, skeleton.png  — device-renderer가 그대로 읽을 수 있는 형식
  debug_keypoints.png                          — 키포인트+본을 원본 사진에 오버레이한 디버그 이미지
  manifest.json                                — 백엔드/경고/본 개수 등 요약
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# Windows 콘솔(cp949 등)에서 한글/이모지/em-dash 등이 깨지지 않도록 stdout/stderr를 UTF-8로 강제.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IMAGE = _REPO_ROOT / "누끼딴고야.png"
DEFAULT_OUTPUT_DIR = _REPO_ROOT / "outputs" / "goya_auto_rigging_test"

_DEBUG_COLORS = {
    "nose": (255, 0, 0),
    "head_top": (255, 128, 0),
    "neck": (255, 255, 0),
    "spine_mid": (0, 255, 0),
    "tail_base": (0, 255, 255),
    "tail_tip": (0, 128, 255),
}
_LEG_COLOR_GROUPS = [
    (["front_left_shoulder", "front_left_elbow", "front_left_paw"], (255, 0, 255)),
    (["front_right_shoulder", "front_right_elbow", "front_right_paw"], (200, 0, 150)),
    (["back_left_hip", "back_left_knee", "back_left_paw"], (0, 200, 255)),
    (["back_right_hip", "back_right_knee", "back_right_paw"], (0, 130, 200)),
]


def _load_rgba(image_path: Path) -> np.ndarray:
    img = Image.open(image_path).convert("RGBA")
    return np.array(img)


def _derive_mask(rgba: np.ndarray, *, use_sam2: bool) -> np.ndarray:
    """마스크 소스 우선순위: (1) 실제 알파채널이 있으면 그대로 사용 (2) 없으면
    기본은 GrabCut(오프라인, 의존성 없음, vitmatte_service의 기존 폴백 코드 재사용)
    (3) --use-sam2-mask 지정 시 SAM2(vitmatte_service._sam2_mask, 최초 실행 시
    모델 다운로드 필요 — 네트워크가 느리면 오래 걸릴 수 있음)를 명시적으로 사용.

    참고: 이번 테스트 이미지(누끼딴고야.png)는 실제로는 알파채널이 없는 RGB
    이미지였다(배경의 '체크무늬'는 투명도가 아니라 픽셀에 실제로 그려진
    색상이었다 — `mode=RGB, alpha 전부 255`로 확인됨). 그래서 기본 경로는
    항상 GrabCut/SAM2 세그멘테이션을 실제로 타게 된다(알파 숏컷은 못 씀).
    """
    alpha = rgba[:, :, 3]
    has_real_alpha = alpha.min() < 250
    if not use_sam2 and has_real_alpha:
        print("  마스크 소스: 이미지 알파채널(이미 누끼된 PNG) 사용")
        return alpha

    rgb = rgba[:, :, :3]
    if use_sam2:
        print("  마스크 소스: SAM2(vitmatte_service._sam2_mask) 재사용 — 최초 실행 시 모델 다운로드로 오래 걸릴 수 있습니다")
        from ..services.vitmatte_service import _get_device, _sam2_mask

        device = _get_device()
        return _sam2_mask(rgb, None, "facebook/sam2.1-hiera-tiny", device)

    print("  마스크 소스: GrabCut(vitmatte_service._grabcut_mask 재사용, 오프라인) — 알파채널이 없어 기본 폴백 사용")
    from ..services.vitmatte_service import _grabcut_mask

    return _grabcut_mask(rgb, None)


def _draw_debug_overlay(rgb: np.ndarray, pose, rig) -> Image.Image:
    img = Image.fromarray(rgb).convert("RGB")
    draw = ImageDraw.Draw(img)

    def _pt(name: str) -> tuple[float, float]:
        kp = pose.keypoints[name]
        return (kp.x, kp.y)

    r = 6
    for name, color in _DEBUG_COLORS.items():
        x, y = _pt(name)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color, outline=(0, 0, 0))
    for names, color in _LEG_COLOR_GROUPS:
        pts = [_pt(n) for n in names]
        draw.line(pts, fill=color, width=3)
        for x, y in pts:
            draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=color, outline=(0, 0, 0))

    draw.text((10, 10), f"backend={pose.backend} head_side={pose.head_side}", fill=(255, 255, 255))
    return img


def _validate_skeleton_json(rig) -> list[str]:
    problems: list[str] = []
    sk = rig.skeleton_json
    for key in ("skeleton", "bones", "slots", "skins", "animations"):
        if key not in sk:
            problems.append(f"필수 키 누락: {key}")
    try:
        json.dumps(sk)
    except Exception as e:
        problems.append(f"JSON 직렬화 실패: {e}")

    region_names_in_atlas = set()
    for line in rig.atlas_text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("size:", "format:", "filter:", "repeat:", "rotate:", "xy:", "size:", "orig:", "offset:", "index:")) and not stripped.endswith(".png"):
            region_names_in_atlas.add(stripped)

    for skin_name, skin in sk.get("skins", {}).items():
        for slot_name, slot_attachments in skin.items():
            for attach_name in slot_attachments:
                if attach_name not in region_names_in_atlas:
                    problems.append(
                        f"스킨 '{skin_name}' 슬롯 '{slot_name}'의 어태치먼트 '{attach_name}'이 "
                        "atlas에 없습니다"
                    )

    bone_names = {b["name"] for b in sk.get("bones", [])}
    for slot in sk.get("slots", []):
        if slot["bone"] not in bone_names:
            problems.append(f"슬롯 '{slot['name']}'이 존재하지 않는 본 '{slot['bone']}'을 참조")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=str, default=str(DEFAULT_IMAGE))
    parser.add_argument("--output-dir", type=str, default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--pet-name", type=str, default="goya")
    parser.add_argument(
        "--backend", type=str, default="heuristic",
        choices=["heuristic", "deeplabcut_superanimal", "auto"],
        help="포즈 추정 백엔드(기본 heuristic — 의존성 없이 이 환경에서도 바로 동작)",
    )
    parser.add_argument(
        "--use-sam2-mask", action="store_true",
        help="이미지에 이미 알파채널이 있어도 SAM2로 마스크를 다시 계산(재사용성 시연용)",
    )
    args = parser.parse_args()

    image_path = Path(args.image).expanduser()
    if not image_path.is_file():
        print(f"[에러] 소스 이미지를 찾을 수 없습니다: {image_path}")
        return 1

    print(f"소스 이미지: {image_path}")
    rgba = _load_rgba(image_path)
    print(f"이미지 크기: {rgba.shape[1]}x{rgba.shape[0]}")

    mask = _derive_mask(rgba, use_sam2=args.use_sam2_mask)

    from ..services.pose_estimation_service import estimate_pose, keypoints_to_dict

    print(f"\n[1/3] 포즈 추정 (backend={args.backend}) ...")
    try:
        pose = estimate_pose(rgba[:, :, :3], mask, backend=args.backend)
    except Exception as e:
        print(f"[에러] 포즈 추정 실패: {e}")
        return 1

    print(f"  실제 사용된 백엔드: {pose.backend}, head_side={pose.head_side}")
    if pose.warnings:
        print("  경고:")
        for w in pose.warnings:
            print(f"    - {w}")
    print("  키포인트:")
    for name, kp in keypoints_to_dict(pose).items():
        print(f"    {name:24s} x={kp['x']:.1f} y={kp['y']:.1f} conf={kp['confidence']:.2f}")

    from ..services.auto_rigging_service import build_rig_from_pose

    print("\n[2/3] 뼈대 생성 + 이미지 워핑 + 아틀라스 패킹 ...")
    try:
        rig = build_rig_from_pose(rgba, mask, pose)
    except Exception as e:
        print(f"[에러] 리깅 실패: {e}")
        return 1
    if rig.warnings:
        print("  경고:")
        for w in rig.warnings:
            print(f"    - {w}")
    n_bones = len(rig.skeleton_json["bones"])
    n_slots = len(rig.skeleton_json["slots"])
    print(f"  본 {n_bones}개, 슬롯 {n_slots}개, 아틀라스 페이지 {rig.atlas_page_image.size}")

    from ..services.spine_action_curves import build_lie_down_animation

    print("\n[3/3] 배깔기(lie_down) 애니메이션 곡선 재타겟 ...")
    anim = build_lie_down_animation(
        bone_local_rotations=rig.bone_local_rotations,
        bone_local_positions=rig.bone_local_positions,
        bone_lengths=rig.bone_lengths,
        head_side=pose.head_side,
    )
    rig.skeleton_json["animations"].update(anim)
    print(f"  애니메이션 '{list(anim.keys())[0]}' 추가 완료 (본 {len(list(anim.values())[0]['bones'])}개에 키프레임 적용)")

    problems = _validate_skeleton_json(rig)
    if problems:
        print("\n[검증 실패] 아래 문제를 확인하세요:")
        for p in problems:
            print(f"  - {p}")
    else:
        print("\n[검증 통과] skeleton.json 구조 자체 검증 OK (실제 spine-cpp 렌더링은 미검증)")

    out_dir = Path(args.output_dir) / args.pet_name
    from ..services.spine_rig_builder import write_spine_asset

    write_spine_asset(
        out_dir,
        skeleton_json=rig.skeleton_json,
        atlas_text=rig.atlas_text,
        atlas_page_image=rig.atlas_page_image,
    )
    print(f"\nSpine 에셋 저장됨: {out_dir} (skeleton.json / skeleton.atlas / skeleton.png)")

    debug_img = _draw_debug_overlay(rgba[:, :, :3], pose, rig)
    debug_path = out_dir / "debug_keypoints.png"
    debug_img.save(debug_path)
    print(f"디버그 오버레이 저장됨: {debug_path}")

    manifest = {
        "image": str(image_path),
        "pose_backend": pose.backend,
        "head_side": pose.head_side,
        "pose_warnings": pose.warnings,
        "rig_warnings": rig.warnings,
        "validation_problems": problems,
        "n_bones": n_bones,
        "n_slots": n_slots,
        "atlas_page_size": list(rig.atlas_page_image.size),
        "animations": list(rig.skeleton_json["animations"].keys()),
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"매니페스트 저장됨: {manifest_path}")

    return 0 if not problems else 2


if __name__ == "__main__":
    sys.exit(main())
