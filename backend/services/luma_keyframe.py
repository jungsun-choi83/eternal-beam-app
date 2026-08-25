"""
Luma I2V keyframe prep: RGBA cutouts often fail moderation; composite onto a solid JPEG.

The flatten colour must match the background the prompt asks for. I2V follows the
keyframe far more strongly than the prompt text, and the player keys the pet out by
removing near-black pixels — a white keyframe therefore renders as an opaque box.
"""

from __future__ import annotations

import io
import os
from typing import Optional, Tuple

from PIL import Image

BG_BLACK: Tuple[int, int, int] = (0, 0, 0)
BG_WHITE: Tuple[int, int, int] = (255, 255, 255)


def resolve_keyframe_bg_rgb(image_bytes: Optional[bytes] = None) -> Tuple[int, int, int]:
    """
    LUMA_KEYFRAME_BG=black|white forces a colour; auto (default) reuses the repo-wide
    convention of black-tan dog -> white, everything else -> black.
    """
    forced = (os.getenv("LUMA_KEYFRAME_BG") or "auto").strip().lower()
    if forced == "black":
        return BG_BLACK
    if forced == "white":
        return BG_WHITE
    if image_bytes is None:
        return BG_BLACK
    try:
        from .luma_service import is_black_tan_dog

        return BG_WHITE if is_black_tan_dog(image_bytes) else BG_BLACK
    except Exception:
        return BG_BLACK


def flatten_rgba_to_jpeg_bytes(
    image_bytes: bytes,
    bg_rgb: Optional[Tuple[int, int, int]] = None,
    quality: int = 92,
    max_side: int = 2048,
) -> bytes:
    """
    Open PNG/JPEG bytes; if RGBA, flatten onto bg_rgb; optionally downscale long edge.
    bg_rgb=None resolves the colour from LUMA_KEYFRAME_BG / coat luminance.
    Returns JPEG bytes.
    """
    if bg_rgb is None:
        bg_rgb = resolve_keyframe_bg_rgb(image_bytes)
    im = Image.open(io.BytesIO(image_bytes))
    im = im.convert("RGBA")
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    bg = Image.new("RGB", im.size, bg_rgb)
    bg.paste(im, mask=im.split()[3])
    out = io.BytesIO()
    bg.save(out, format="JPEG", quality=quality)
    return out.getvalue()


def scene_to_jpeg_bytes(
    scene_bytes: bytes, quality: int = 92, max_side: int = 2048
) -> bytes:
    """
    승인된 **정본 장면**을 프로바이더 입력 JPEG 으로 바꾼다.

    ── 왜 합성하지 않는가 ─────────────────────────────────────────────────
    장면은 이미 합성돼 있다. 배경·펫·크기·위치·접지가 고객이 승인한 그대로 한 장에
    들어 있고, 여기서 할 일은 포맷을 맞추는 것뿐이다. 다시 얹으면 펫이 두 번
    그려진다.
    """
    im = Image.open(io.BytesIO(scene_bytes))
    if im.mode in ("RGBA", "LA", "P"):
        # 장면에 알파가 남아 있으면(투명 PNG) 검정 위에 눌러 붙인다 — JPEG 에는
        # 알파가 없고, 그대로 저장하면 투명 영역이 예측 불가한 색이 된다.
        im = im.convert("RGBA")
        flat = Image.new("RGB", im.size, BG_BLACK)
        flat.paste(im, mask=im.split()[3])
        im = flat
    else:
        im = im.convert("RGB")
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=quality)
    return out.getvalue()


def build_keyframe_jpeg(
    cutout_bytes: bytes,
    *,
    scene_bytes: Optional[bytes] = None,
    luminance_source: Optional[bytes] = None,
) -> bytes:
    """
    **모든 생성 경로가 지나가는 단 하나의 키프레임 seam.**

    장면이 있으면 장면이 곧 키프레임이다(배경 구움). 없으면 예전 그대로 누끼를
    단색 판에 눌러 붙인다(레거시).

    행동(BREATHING·BLINK·EAR·HEAD_TILT·TAIL·COME_CLOSER)마다 배경 처리를 따로 두지
    않는 이유가 이 함수다 — 행동은 프롬프트만 다르고 입력 그림은 전부 여기서 나온다.
    """
    if scene_bytes:
        return scene_to_jpeg_bytes(scene_bytes)
    return flatten_rgba_to_jpeg_bytes(
        cutout_bytes, bg_rgb=resolve_keyframe_bg_rgb(luminance_source or cutout_bytes)
    )
