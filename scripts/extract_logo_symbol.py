"""Extract transparent logo symbol from brand PNG."""
from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(
    r"C:\Users\choi jungsun\.cursor\projects\c-Users-choi-jungsun-Desktop-eternal-beam-app\assets"
    r"\c__Users_choi_jungsun_AppData_Roaming_Cursor_User_workspaceStorage_empty-window_images___"
    r"-02f83c7d-9ff8-4bf6-a797-8cc8f8ca56f5.png"
)
OUT = ROOT / "public" / "eternal-beam-logo-symbol.png"


def is_background(r: float, g: float, b: float, a: float) -> bool:
    if a < 12:
        return True
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    warm = r + g * 0.88 - b * 1.08
    return lum < 118 and warm < 68


def flood_clear_background(img: Image.Image) -> Image.Image:
    arr = np.array(img.convert("RGBA"))
    h, w = arr.shape[:2]
    visited = np.zeros((h, w), dtype=bool)
    q: deque[tuple[int, int]] = deque()

    for x in range(w):
        q.append((x, 0))
        q.append((x, h - 1))
    for y in range(h):
        q.append((0, y))
        q.append((w - 1, y))

    while q:
        x, y = q.popleft()
        if x < 0 or y < 0 or x >= w or y >= h or visited[y, x]:
            continue
        visited[y, x] = True
        px = arr[y, x]
        if not is_background(float(px[0]), float(px[1]), float(px[2]), float(px[3])):
            continue
        arr[y, x, 3] = 0
        q.extend([(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)])

    return Image.fromarray(arr)


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"Missing source: {SRC}")

    img = Image.open(SRC).convert("RGBA")
    target_w = 640
    target_h = max(1, int(img.height * (target_w / img.width)))
    img = img.resize((target_w, target_h), Image.LANCZOS)

    clean = flood_clear_background(img)
    w, h = clean.size

    # Icon only — above ETERNAL BEAM text
    sym = clean.crop((int(w * 0.10), int(h * 0.04), int(w * 0.90), int(h * 0.54)))
    bbox = sym.getbbox()
    if not bbox:
        raise SystemExit("Symbol bbox empty")
    sym = sym.crop(bbox)

    pad = max(8, int(max(sym.size) * 0.06))
    side = max(sym.size) + pad * 2
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(sym, ((side - sym.width) // 2, (side - sym.height) // 2), sym)
    canvas = canvas.resize((512, 512), Image.LANCZOS)

    alpha = canvas.split()[3].filter(ImageFilter.GaussianBlur(radius=0.4))
    canvas.putalpha(alpha)
    canvas.save(OUT)
    print(f"Wrote {OUT} ({canvas.size[0]}x{canvas.size[1]})")


if __name__ == "__main__":
    main()
