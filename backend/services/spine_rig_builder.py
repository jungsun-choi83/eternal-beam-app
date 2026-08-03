"""
Spine2D 스켈레톤(.json) + 텍스처 아틀라스(.atlas + PNG) 저수준 빌더.

이 모듈은 "관절 좌표를 안다"는 사실만으로 유효한 Spine 런타임 에셋(스켈레톤
JSON, 아틀라스 텍스트, 패킹된 아틀라스 PNG)을 만들어내는 범용 유틸리티다.
반려동물별 뼈대 구조(어떤 키포인트가 어떤 뼈가 되는지)는 이 모듈이 모르고
`auto_rigging_service.py`가 결정한다 — 이 파일은 순수하게 "기하 변환 +
포맷 직렬화"만 담당한다.

★ 출력 포맷은 device-renderer/src/renderer/spine_renderer.cpp의
`SpineRenderer::loadAsset()`이 기대하는 그대로다:
  <출력폴더>/skeleton.json
  <출력폴더>/skeleton.atlas
  (atlas가 참조하는 텍스처 PNG, 기본 이름 skeleton.png)

★ Spine 좌표계/부모-자식 변환 공식(이 파일 전체가 이 규칙을 따른다):
  자식의 월드 원점 = 부모의 월드 원점 + Rotate(부모의 월드 회전) · (자식.x, 자식.y)
  자식의 월드 회전 = 부모의 월드 회전 + 자식.rotation
  (스케일/시어는 이 프로토타입에서 다루지 않음 — 전부 1.0/0.0)
  즉 "이미 알고 있는 월드 좌표/월드 회전"에서 로컬 (x,y,rotation)을 역산하려면:
    자식.rotation = 자식_월드회전 - 부모_월드회전
    (자식.x, 자식.y) = InverseRotate(부모_월드회전) · (자식_월드원점 - 부모_월드원점)
  `world_to_local()`이 바로 이 역산을 구현한다.

★ RegionAttachment 배치(사각형 워프 이미지 부착) 공식:
  최종(월드) 회전 = 부착된 본의 월드 회전 + attachment.rotation
  최종(월드) 위치 = 본의 월드 원점 + Rotate(본의 월드 회전) · (attachment.x, attachment.y)
  → "원본 사진에서 축에 정렬되지 않은(회전된) 사각형을 그대로 크롭"해서 붙이려면
    attachment.rotation = -본의 셋업 월드 회전 로 상쇄시키면, 셋업(rest) 포즈에서는
    사진이 회전 없이(원본 그대로) 보이고, 애니메이션이 본을 추가로 회전시키면
    그 추가분(Δ)만큼만 이미지가 따라 회전한다 — `region_attachment_transform()`이
    이 계산을 구현한다.
"""

from __future__ import annotations

import io
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image


def normalize_angle_deg(deg: float) -> float:
    """각도를 (-180, 180] 범위로 정규화(가독성/일관성 목적 — 수치적으로는 어느 값이든 sin/cos 결과가 같음)."""
    a = math.fmod(deg + 180.0, 360.0)
    if a <= 0:
        a += 360.0
    return a - 180.0


def rotate_vec(x: float, y: float, deg: float) -> tuple[float, float]:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return x * c - y * s, x * s + y * c


def inverse_rotate_vec(x: float, y: float, deg: float) -> tuple[float, float]:
    return rotate_vec(x, y, -deg)


def world_to_local(
    child_world_pos: tuple[float, float],
    child_world_rotation_deg: float,
    parent_world_pos: tuple[float, float],
    parent_world_rotation_deg: float,
) -> tuple[float, float, float]:
    """부모의 월드 변환을 알 때, 자식의 로컬(x,y,rotation)을 역산."""
    dx = child_world_pos[0] - parent_world_pos[0]
    dy = child_world_pos[1] - parent_world_pos[1]
    lx, ly = inverse_rotate_vec(dx, dy, parent_world_rotation_deg)
    lrot = normalize_angle_deg(child_world_rotation_deg - parent_world_rotation_deg)
    return lx, ly, lrot


def region_attachment_transform(
    bone_world_pos: tuple[float, float],
    bone_world_rotation_deg: float,
    crop_center_world: tuple[float, float],
) -> tuple[float, float, float]:
    """크롭 이미지 중심의 월드 좌표 → RegionAttachment의 로컬 (x,y,rotation).

    attachment.rotation = -bone_world_rotation_deg 로 고정해서, 셋업 포즈에서
    크롭 이미지가 "회전 없이 원본 그대로" 보이도록 만든다(파일 상단 docstring 참고).
    """
    dx = crop_center_world[0] - bone_world_pos[0]
    dy = crop_center_world[1] - bone_world_pos[1]
    lx, ly = inverse_rotate_vec(dx, dy, bone_world_rotation_deg)
    return lx, ly, normalize_angle_deg(-bone_world_rotation_deg)


@dataclass
class BoneSetup:
    name: str
    parent: Optional[str]
    local_x: float
    local_y: float
    local_rotation: float
    length: float
    world_pos: tuple[float, float]
    world_rotation: float


@dataclass
class RegionSpec:
    """아틀라스에 패킹할 개별 이미지 조각 + 그 조각이 붙는 슬롯/본 정보."""

    name: str  # 아틀라스 리전 이름 = 어태치먼트 이름(관례상 슬롯명과 동일하게 사용)
    image: np.ndarray  # RGBA uint8 (h,w,4)
    bone_name: str
    attachment_x: float
    attachment_y: float
    attachment_rotation: float


def pack_atlas(
    regions: list[RegionSpec], *, padding: int = 2, page_name: str = "skeleton.png"
) -> tuple[str, Image.Image, dict[str, tuple[int, int, int, int]]]:
    """단순 shelf(선반) 패킹 — 프로토타입 수준(공간 효율 최적화는 하지 않음).

    Returns: (atlas_text, packed_page_image(RGBA), {region_name: (x,y,w,h)})
    """
    items = sorted(regions, key=lambda r: -r.image.shape[0])
    max_page_w = 2048

    shelves: list[dict] = []  # {"y": int, "height": int, "x_cursor": int}
    placements: dict[str, tuple[int, int, int, int]] = {}

    for r in items:
        h, w = r.image.shape[:2]
        placed = False
        for shelf in shelves:
            if shelf["x_cursor"] + w + padding <= max_page_w and h <= shelf["height"]:
                placements[r.name] = (shelf["x_cursor"], shelf["y"], w, h)
                shelf["x_cursor"] += w + padding
                placed = True
                break
        if not placed:
            y = 0 if not shelves else shelves[-1]["y"] + shelves[-1]["height"] + padding
            shelves.append({"y": y, "height": h, "x_cursor": w + padding})
            placements[r.name] = (0, y, w, h)

    page_w = max_page_w
    page_h = (shelves[-1]["y"] + shelves[-1]["height"] + padding) if shelves else 1
    # 다음 2의 거듭제곱으로 올리지 않음(프로토타입) — 필요시 실제 텍스처 규격에 맞춰 조정.
    page = Image.new("RGBA", (page_w, page_h), (0, 0, 0, 0))

    for r in items:
        x, y, w, h = placements[r.name]
        tile = Image.fromarray(r.image, mode="RGBA")
        page.paste(tile, (x, y))

    lines = [
        page_name,
        f"size: {page_w},{page_h}",
        "format: RGBA8888",
        "filter: Linear,Linear",
        "repeat: none",
    ]
    for r in items:
        x, y, w, h = placements[r.name]
        lines += [
            r.name,
            "  rotate: false",
            f"  xy: {x}, {y}",
            f"  size: {w}, {h}",
            f"  orig: {w}, {h}",
            "  offset: 0, 0",
            "  index: -1",
        ]
    atlas_text = "\n".join(lines) + "\n"
    return atlas_text, page, placements


def build_skeleton_json(
    *,
    bones: list[BoneSetup],
    slots: list[str],
    attachments: dict[str, dict],
    skin_name: str = "default",
    animations: Optional[dict] = None,
    skeleton_name: str = "pet",
    fps: int = 30,
) -> dict:
    """Spine JSON(3.8/4.x 계열 텍스트 포맷과 호환되는 최소 구조) 생성.

    attachments: {slot_name: {"name": region_name, "x":..,"y":..,"rotation":..,
                               "width":..,"height":..}}
    """
    bones_json = []
    for b in bones:
        entry: dict = {"name": b.name}
        if b.parent:
            entry["parent"] = b.parent
        if abs(b.local_x) > 1e-6:
            entry["x"] = round(b.local_x, 3)
        if abs(b.local_y) > 1e-6:
            entry["y"] = round(b.local_y, 3)
        if abs(b.local_rotation) > 1e-6:
            entry["rotation"] = round(b.local_rotation, 3)
        if b.length > 1e-6:
            entry["length"] = round(b.length, 3)
        bones_json.append(entry)

    slots_json = [{"name": s, "bone": s, "attachment": s} for s in slots]

    skins_json = {
        skin_name: {
            slot: {attachments[slot]["name"]: _attachment_json(attachments[slot])}
            for slot in slots
            if slot in attachments
        }
    }

    return {
        "skeleton": {
            "hash": "eternal-beam-auto-rig",
            "spine": "4.1.24",
            "x": 0,
            "y": 0,
            "width": 0,
            "height": 0,
            "images": "",
            "audio": "",
        },
        "bones": bones_json,
        "slots": slots_json,
        "skins": skins_json,
        "animations": animations or {},
    }


def _attachment_json(spec: dict) -> dict:
    return {
        "type": "region",
        "name": spec["name"],
        "x": round(spec.get("x", 0.0), 3),
        "y": round(spec.get("y", 0.0), 3),
        "rotation": round(spec.get("rotation", 0.0), 3),
        "width": round(spec.get("width", 1.0), 3),
        "height": round(spec.get("height", 1.0), 3),
    }


def write_spine_asset(
    output_dir: Path,
    *,
    skeleton_json: dict,
    atlas_text: str,
    atlas_page_image: Image.Image,
    page_filename: str = "skeleton.png",
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "skeleton.json").write_text(
        json.dumps(skeleton_json, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (output_dir / "skeleton.atlas").write_text(atlas_text, encoding="utf-8")
    atlas_page_image.save(output_dir / page_filename)
