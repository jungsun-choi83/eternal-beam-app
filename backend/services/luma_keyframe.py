"""
Luma I2V keyframe prep: RGBA cutouts often fail moderation; composite onto white JPEG.
"""

from __future__ import annotations

import io
from typing import Tuple

from PIL import Image


def flatten_rgba_to_jpeg_bytes(
    image_bytes: bytes,
    bg_rgb: Tuple[int, int, int] = (255, 255, 255),
    quality: int = 92,
    max_side: int = 2048,
) -> bytes:
    """
    Open PNG/JPEG bytes; if RGBA, flatten onto bg_rgb; optionally downscale long edge.
    Returns JPEG bytes.
    """
    im = Image.open(io.BytesIO(image_bytes))
    im = im.convert("RGBA")
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    bg = Image.new("RGB", im.size, bg_rgb)
    bg.paste(im, mask=im.split()[3])
    out = io.BytesIO()
    bg.save(out, format="JPEG", quality=quality)
    return out.getvalue()
