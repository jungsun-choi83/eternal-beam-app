"""
FLUX Fill 용 캔버스/마스크 오프라인 생성기 — fal 을 호출하지 않는다.

입력  : 머리(또는 상반신)만 있는 누끼 RGBA PNG
출력  : canvas.png, mask.png, overlay_preview.png, preserve_region_preview.png

마스크 극성 (fal 예제 mask_knight.jpeg 로 실측 확인):
    WHITE(255) = 생성할 영역
    BLACK(0)   = 보존할 영역

설계:
  - 원본 종횡비를 유지한 채 머리를 캔버스 상단 약 25~30% 에 배치
  - 중성 회색 배경 위에 flatten (FLUX Fill 은 알파를 쓰지 않는다)
  - 보존 영역 = alpha>=ALPHA_CORE 를 ERODE_PX 만큼 침식한 코어
      → 반투명 털 프린지는 생성 영역으로 넘겨 후광(halo)을 피한다
  - 보존 영역 하단에서 BLEND_BAND_PX 만큼을 생성 영역으로 되돌려
    생성된 몸통이 기존 가슴/목과 자연스럽게 이어지게 한다

사용:
    python scripts/build_fill_canvas.py --cutout <path> --out outputs/fill_test
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

CANVAS_W, CANVAS_H = 768, 1024
BG_GREY = (180, 180, 180)
HEAD_HEIGHT_FRAC = 0.27      # 캔버스 높이 대비 머리 높이 (25~30% 범위)
TOP_MARGIN_FRAC = 0.04
ALPHA_CORE = 200             # ViTMatte 매트가 매우 soft — 255 는 0.04%뿐이라 200 을 코어로 본다
ERODE_PX = 1                 # 반투명 프린지만 살짝 밀어낸다 (귀 윤곽 보존)
BLEND_BAND_PX = 20           # 목/가슴 전환부 블렌딩 밴드 (20~25px 범위의 하단)
BAND_ZONE_FRAC = 0.20        # 밴드는 보존 bbox 하단 20% 구간에서만 적용 (볼/귀로 안 올라오게)


def build(cutout_path: str, out_dir: str) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    src = Image.open(cutout_path).convert("RGBA")
    a = np.array(src)[:, :, 3]
    ys, xs = np.where(a > 16)
    if len(xs) == 0:
        raise SystemExit("누끼에 보이는 픽셀이 없습니다.")
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    bw, bh = x1 - x0 + 1, y1 - y0 + 1

    # 1) 종횡비 유지 스케일 + 배치
    target_h = int(round(CANVAS_H * HEAD_HEIGHT_FRAC))
    scale = target_h / bh
    nw, nh = int(round(bw * scale)), int(round(bh * scale))
    px = (CANVAS_W - nw) // 2
    py = int(round(CANVAS_H * TOP_MARGIN_FRAC))

    crop = src.crop((x0, y0, x1 + 1, y1 + 1)).resize((nw, nh), Image.Resampling.LANCZOS)

    # 2) 캔버스: 중성 회색 위에 flatten (FLUX Fill 은 알파를 무시한다)
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (*BG_GREY, 255))
    canvas.alpha_composite(crop, (px, py))
    canvas_rgb = canvas.convert("RGB")
    canvas_rgb.save(out / "canvas.png")

    # 3) 보존 코어 — 캔버스 좌표계에서 계산
    placed_alpha = np.zeros((CANVAS_H, CANVAS_W), dtype=np.uint8)
    placed_alpha[py : py + nh, px : px + nw] = np.array(crop)[:, :, 3]

    core = (placed_alpha >= ALPHA_CORE).astype(np.uint8)
    if cv2 is not None and ERODE_PX > 0:
        k = np.ones((ERODE_PX * 2 + 1, ERODE_PX * 2 + 1), np.uint8)
        core = cv2.erode(core, k)
    core = core.astype(bool)

    # 4) 블렌딩 밴드 — 목/가슴 전환부에만. 보존 bbox 하단 BAND_ZONE_FRAC 구간에
    #    바닥이 있는 열에만 적용해 볼·귀 윤곽까지 타고 올라가지 않게 한다.
    keep = core.copy()
    band = np.zeros_like(keep)
    cys, cxs = np.where(core)
    if len(cys):
        ktop, kbot = int(cys.min()), int(cys.max())
        kh = kbot - ktop + 1
        zone_top = kbot - int(round(kh * BAND_ZONE_FRAC))  # 이 아래에 바닥이 있는 열만
        for c in np.unique(cxs):
            rows = np.where(core[:, c])[0]
            bottom = int(rows.max())
            if bottom < zone_top:
                continue  # 볼/귀 등 위쪽에서 끝나는 열 — 건드리지 않는다
            lo = max(bottom - BLEND_BAND_PX + 1, int(rows.min()))
            keep[lo : bottom + 1, c] = False
            band[lo : bottom + 1, c] = True

    # 5) 마스크: 흰색=생성, 검정=보존
    mask = np.full((CANVAS_H, CANVAS_W), 255, dtype=np.uint8)
    mask[keep] = 0
    Image.fromarray(mask, "L").save(out / "mask.png")

    # 6) overlay_preview — 생성될 영역을 마젠타로
    ov = np.array(canvas_rgb).copy()
    white = mask == 255
    ov[white] = (0.45 * ov[white] + 0.55 * np.array([255, 0, 255])).astype(np.uint8)
    Image.fromarray(ov, "RGB").save(out / "overlay_preview.png")

    # 7) preserve_region_preview — 보존 픽셀은 원본 그대로, 나머지는 어둡게 + 밴드 표시
    pr = np.array(canvas_rgb).copy()
    pr[~keep] = (pr[~keep] * 0.18).astype(np.uint8)
    pr[band] = (0.5 * pr[band] + 0.5 * np.array([255, 190, 0])).astype(np.uint8)
    Image.fromarray(pr, "RGB").save(out / "preserve_region_preview.png")

    # 8) head_zoom.png — 머리 영역 3배 확대, 보존/생성 대비
    kys, kxs = np.where(keep)
    if len(kys):
        bx0, bx1 = int(kxs.min()), int(kxs.max())
        by0, by1 = int(kys.min()), int(kys.max())
        box = (max(0, bx0 - 30), max(0, by0 - 30), min(CANVAS_W, bx1 + 30), min(CANVAS_H, by1 + 40))
        Z = 3
        left = canvas_rgb.crop(box)
        left = left.resize((left.width * Z, left.height * Z), Image.Resampling.NEAREST)
        gen = np.array(canvas_rgb).copy()
        m = ~keep
        gen[m] = (0.35 * gen[m] + 0.65 * np.array([255, 0, 255])).astype(np.uint8)
        right = Image.fromarray(gen, "RGB").crop(box)
        right = right.resize((right.width * Z, right.height * Z), Image.Resampling.NEAREST)
        zoom = Image.new("RGB", (left.width * 2 + 12, left.height), (15, 15, 17))
        zoom.paste(left, (0, 0))
        zoom.paste(right, (left.width + 12, 0))
        zoom.save(out / "head_zoom.png")

    visible = placed_alpha > 16
    stats = {
        "source": os.path.basename(cutout_path),
        "source_size": src.size,
        "source_bbox": (x0, y0, x1, y1),
        "source_bbox_wh": (bw, bh),
        "scale": round(scale, 4),
        "placed_size": (nw, nh),
        "placed_at": (px, py),
        "head_top_pct": round(py / CANVAS_H * 100, 1),
        "head_bottom_pct": round((py + nh) / CANVAS_H * 100, 1),
        "room_below_px": CANVAS_H - (py + nh),
        "room_below_pct": round((CANVAS_H - (py + nh)) / CANVAS_H * 100, 1),
        "blend_band_px": BLEND_BAND_PX,
        "band_zone_frac": BAND_ZONE_FRAC,
        "blend_band_pct_of_head": round(BLEND_BAND_PX / nh * 100, 1),
        "mask_white_pct": round(white.mean() * 100, 2),
        "mask_black_pct": round((~white).mean() * 100, 2),
        "visible_dog_px_a16": int(visible.sum()),
        "solid_dog_px_a128": int((placed_alpha >= 128).sum()),
        "preserved_px": int(keep.sum()),
        "preserved_pct_of_visible_a16": round(keep.sum() / max(visible.sum(), 1) * 100, 1),
        "preserved_pct_of_solid_a128": round(
            (keep & (placed_alpha >= 128)).sum() / max((placed_alpha >= 128).sum(), 1) * 100, 1
        ),
        "band_px": int(band.sum()),
    }
    return stats


# ---------------------------------------------------------------------------
# 생성 후 재합성 — FLUX 결과 + 원본 보존 코어
# ---------------------------------------------------------------------------

RECOMPOSITE_FEATHER_PX = 2


def recomposite(
    canvas_path: str,
    mask_path: str,
    flux_result_path: str,
    out_path: str,
    feather_px: int = RECOMPOSITE_FEATHER_PX,
) -> dict:
    """
    FLUX Fill 은 전체 이미지를 재인코딩해서 돌려주므로, 검정(보존) 영역조차
    코덱을 한 번 통과한다. 그래서 생성 후 원본 보존 픽셀을 그대로 되돌린다.

        final = flux * (1 - a) + canvas * a,   a = blur(keep, feather_px)

    feather 는 아주 얇게(기본 2px)만 준다 — 되돌린 픽셀과 생성 픽셀의 경계가
    딱딱하게 보이지 않을 정도. 그 이상 번지면 보존 자체가 흐려진다.
    """
    canvas = Image.open(canvas_path).convert("RGB")
    flux = Image.open(flux_result_path).convert("RGB")
    if flux.size != canvas.size:
        flux = flux.resize(canvas.size, Image.Resampling.LANCZOS)
    mask = np.array(Image.open(mask_path).convert("L"))
    keep = (mask == 0).astype(np.float32)

    if feather_px > 0:
        if cv2 is not None:
            k = feather_px * 2 + 1
            alpha = cv2.GaussianBlur(keep, (k, k), feather_px / 2.0)
        else:
            alpha = np.array(
                Image.fromarray((keep * 255).astype(np.uint8)).filter(
                    ImageFilter.GaussianBlur(feather_px)
                )
            ).astype(np.float32) / 255.0
    else:
        alpha = keep

    c = np.array(canvas).astype(np.float32)
    f = np.array(flux).astype(np.float32)
    out = f * (1.0 - alpha[..., None]) + c * alpha[..., None]
    Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB").save(out_path)

    restored = keep > 0.5
    exact = int((np.abs(out[restored] - c[restored]) < 0.5).all(axis=-1).sum())
    return {
        "out": out_path,
        "feather_px": feather_px,
        "restored_px": int(restored.sum()),
        "pixel_exact_px": exact,
        "pixel_exact_pct": round(exact / max(int(restored.sum()), 1) * 100, 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="FLUX Fill 캔버스/마스크 생성 (fal 호출 없음)")
    ap.add_argument("--cutout")
    ap.add_argument("--out", default="outputs/fill_test")
    ap.add_argument("--recomposite", metavar="FLUX_RESULT",
                    help="생성 결과 경로. 주면 캔버스/마스크로 원본 보존 코어를 되돌린다.")
    ap.add_argument("--feather", type=int, default=RECOMPOSITE_FEATHER_PX)
    args = ap.parse_args()

    if args.recomposite:
        r = recomposite(
            f"{args.out}/canvas.png", f"{args.out}/mask.png",
            args.recomposite, f"{args.out}/final_recomposited.png", args.feather,
        )
        for k, v in r.items():
            print(f"  {k:<16} : {v}")
        return 0

    if not args.cutout:
        ap.error("--cutout 또는 --recomposite 중 하나가 필요합니다")
    s = build(args.cutout, args.out)
    w = max(len(k) for k in s)
    for k, v in s.items():
        print(f"  {k:<{w}} : {v}")
    print(f"\n  wrote canvas.png / mask.png / overlay_preview.png / preserve_region_preview.png -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
