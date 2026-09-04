"""
9:16 비디오 앵커 (Phase 6 라이브 계약) 테스트.

계약: 펫 픽셀 무변형 보존 · 결정론적 중앙 배치 · 테두리색 패딩 · 정확한 종횡비.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw

from backend.services import video_anchor as va


def _png(w: int, h: int, *, bg=(216, 216, 216), mode="RGB") -> bytes:
    im = Image.new(mode, (w, h), bg if mode == "RGB" else (*bg, 255))
    d = ImageDraw.Draw(im)
    d.ellipse((w // 4, h // 4, 3 * w // 4, 3 * h // 4), fill=(120, 80, 50) if mode == "RGB" else (120, 80, 50, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_needs_anchor():
    assert va.needs_anchor(1024, 1024, "9:16") is True     # 1:1 키프레임 (라이브 사례)
    assert va.needs_anchor(1080, 1920, "9:16") is False    # 이미 정확한 9:16
    assert va.needs_anchor(0, 0, "9:16") is False
    assert va.needs_anchor(1024, 1024, "bogus") is False


def test_anchor_geometry_for_1024_square_keyframe():
    """라이브 NEUTRAL_IDLE 키프레임 (1024×1024) → 1026×1824 캔버스, 중앙 배치."""
    anchor, meta = va.build_anchor_image(_png(1024, 1024))
    assert meta["canvas_size"] == [1026, 1824]  # k=57: 18k × 32k — 정확한 9:16, 양변 짝수
    assert meta["canvas_size"][0] * 16 == meta["canvas_size"][1] * 9
    assert meta["offset"] == [1, 400]
    with Image.open(io.BytesIO(anchor)) as im:
        assert im.size == (1026, 1824)


def test_anchor_preserves_source_pixels_exactly():
    src = _png(200, 150)
    anchor, meta = va.build_anchor_image(src)
    ox, oy = meta["offset"]
    with Image.open(io.BytesIO(src)) as s, Image.open(io.BytesIO(anchor)) as a:
        s_arr = np.asarray(s.convert("RGB"))
        a_arr = np.asarray(a.convert("RGB"))
    crop = a_arr[oy : oy + 150, ox : ox + 200]
    assert np.array_equal(crop, s_arr)  # 리샘플링/재생성 없음 — 픽셀 동일


def test_anchor_is_deterministic():
    src = _png(300, 300)
    a1, m1 = va.build_anchor_image(src)
    a2, m2 = va.build_anchor_image(src)
    assert a1 == a2 and m1 == m2


def test_anchor_padding_uses_border_median_color():
    src = _png(100, 100, bg=(210, 211, 212))
    anchor, meta = va.build_anchor_image(src)
    assert meta["background_rgb"] == [210, 211, 212]
    with Image.open(io.BytesIO(anchor)) as a:
        arr = np.asarray(a.convert("RGB"))
    assert tuple(arr[0, 0]) == (210, 211, 212)  # 패딩이 키프레임 배경과 이어진다


def test_tall_source_fits_by_height():
    anchor, meta = va.build_anchor_image(_png(100, 400))
    cw, ch = meta["canvas_size"]
    assert cw * 16 == ch * 9
    assert cw >= 100 and ch >= 400  # 원본이 통째로 들어간다


def test_rgba_cutout_composites_over_background():
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((40, 40, 60, 60), fill=(120, 80, 50, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    anchor, meta = va.build_anchor_image(buf.getvalue())
    with Image.open(io.BytesIO(anchor)) as a:
        assert a.mode == "RGB"  # 영상 입력 — 알파 없음


def test_invalid_aspect_raises():
    with pytest.raises(ValueError):
        va.build_anchor_image(_png(100, 100), aspect_ratio="nonsense")
