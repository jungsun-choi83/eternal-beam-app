from pathlib import Path

import numpy as np
from PIL import Image

src = Path(
    r"C:\Users\choi jungsun\.cursor\projects\c-Users-choi-jungsun-Desktop-eternal-beam-app\assets\c__Users_choi_jungsun_AppData_Roaming_Cursor_User_workspaceStorage_empty-window_images___-02f83c7d-9ff8-4bf6-a797-8cc8f8ca56f5.png"
)
out_sym = Path(__file__).resolve().parents[1] / "public" / "eternal-beam-logo-symbol.png"

im = Image.open(src).convert("RGBA")
arr = np.array(im, dtype=np.float32)
h, w = arr.shape[:2]
r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
lum = 0.2126 * r + 0.7152 * g + 0.0722 * b

col = (lum > 35).sum(axis=0)
cx = np.where(col > col.max() * 0.05)[0]
x0, x1 = int(max(0, cx.min() - 24)), int(min(w, cx.max() + 24))

sub = arr[:, x0:x1]
sr, sg, sb, sa = sub[..., 0], sub[..., 1], sub[..., 2], sub[..., 3]
slum = 0.2126 * sr + 0.7152 * sg + 0.0722 * sb
ssat = np.maximum.reduce([np.abs(sr - sg), np.abs(sg - sb), np.abs(sr - sb)])

row = (slum > 35).sum(axis=1)
ry = np.where(row > row.max() * 0.05)[0]
y0_all, y1_all = int(ry.min()), int(ry.max())
sym_h = int((y1_all - y0_all) * 0.62)
y0, y1 = y0_all, y0_all + sym_h

patch = sub[y0:y1]
pr, pg, pb, pa = patch[..., 0], patch[..., 1], patch[..., 2], patch[..., 3]
plum = 0.2126 * pr + 0.7152 * pg + 0.0722 * pb
psat = np.maximum.reduce([np.abs(pr - pg), np.abs(pg - pb), np.abs(pr - pb)])

alpha = np.where((plum < 42) & (psat < 28), 0.0, pa)
alpha = np.where((plum < 58) & (psat < 16), 0.0, alpha)
alpha = np.clip(alpha, 0, 255)

out = np.zeros_like(patch, dtype=np.uint8)
out[..., 0] = np.clip(pr, 0, 255).astype(np.uint8)
out[..., 1] = np.clip(pg, 0, 255).astype(np.uint8)
out[..., 2] = np.clip(pb, 0, 255).astype(np.uint8)
out[..., 3] = alpha.astype(np.uint8)

mask = out[..., 3] > 8
ys, xs = np.where(mask)
cy0, cy1 = max(0, ys.min() - 6), min(out.shape[0], ys.max() + 6)
cx0, cx1 = max(0, xs.min() - 6), min(out.shape[1], xs.max() + 6)
final = out[cy0:cy1, cx0:cx1]

Image.fromarray(final).save(out_sym, optimize=True)
print(f"symbol {final.shape[1]}x{final.shape[0]} -> {out_sym}")
