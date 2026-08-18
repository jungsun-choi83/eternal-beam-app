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
