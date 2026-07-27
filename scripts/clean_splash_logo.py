"""Remove gray background residue from splash logo PNG and crop to content."""
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "public" / "eternal-beam-logo-full.png"
OUT = ROOT / "public" / "eternal-beam-logo-splash.png"


def clean_logo(src: Path, out: Path) -> tuple[int, int]:
    im = Image.open(src).convert("RGBA")
    arr = np.array(im, dtype=np.float32)
    r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    sat = np.maximum.reduce([np.abs(r - g), np.abs(g - b), np.abs(r - b)])

    # Neutral gray matte (top residue) -> transparent; keep gold logo/text
    bg = (sat < 12) & (lum < 98)
    alpha = np.where(bg, 0.0, a)
    alpha = np.where((sat < 8) & (lum < 110) & (alpha < 220), 0.0, alpha)

    out_arr = np.zeros_like(arr, dtype=np.uint8)
    out_arr[..., 0] = np.clip(r, 0, 255).astype(np.uint8)
    out_arr[..., 1] = np.clip(g, 0, 255).astype(np.uint8)
    out_arr[..., 2] = np.clip(b, 0, 255).astype(np.uint8)
    out_arr[..., 3] = np.clip(alpha, 0, 255).astype(np.uint8)

    mask = out_arr[..., 3] > 10
    ys, xs = np.where(mask)
    y0, y1 = max(0, ys.min() - 8), min(out_arr.shape[0], ys.max() + 8)
    x0, x1 = max(0, xs.min() - 8), min(out_arr.shape[1], xs.max() + 8)
    final = out_arr[y0:y1, x0:x1]

    Image.fromarray(final).save(out, optimize=True)
    return final.shape[1], final.shape[0]


if __name__ == "__main__":
    w, h = clean_logo(SRC, OUT)
    print(f"saved {w}x{h} -> {OUT}")
