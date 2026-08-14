"""
검정 배경 펫 MP4 → 리포지토리 packed-alpha 포맷(vstack) 변환기 — **개념검증 전용**.

출력 규격은 웹이 이미 쓰고 있는 규약(src/lib/packed-alpha-canvas.ts)을 그대로 따른다:

    ┌───────────────┐
    │  RGB (상단)    │  premultiplied,  halfH
    ├───────────────┤
    │  ALPHA (하단)  │  grayscale R=G=B, halfH
    └───────────────┘
    최종 높이 = halfH * 2 (짝수), 폭은 원본 그대로.

⚠️ 이 스크립트의 알파는 **임시 소스**다. idle-loop-video.tsx 의
removeNearBackgroundAlpha() 를 그대로 옮겨 온 것이라, 이미 검정으로 뭉개진
영상에서 되살릴 수 있는 만큼만 복원한다. 어두운 털은 원본 플레이트 단계에서
이미 손실됐으므로 여기서 되살아나지 않는다 — 진짜 해법은 ViTMatte 알파를
생성 단계부터 들고 내려오는 것이다. 여기서는 **포맷/전송 경로 증명**이 목적.

사용:
    python scripts/pack_alpha_video.py --input <clip.mp4> --output <clip_packed.mp4>
    python scripts/pack_alpha_video.py --input <clip.mp4> --output <o.mp4> --measure
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

# ── idle-loop-video.tsx 상수와 1:1 대응 ──────────────────────────────────────
ALPHA_CUTOFF = 0.12
MIN_LUM_SPAN = 60
FG_PERCENTILE = 0.85
DEFAULT_LUM_SPAN = 140
EROSION_RADIUS_PER_PX = 3 / 480
EROSION_RADIUS_MIN = 1
EROSION_RADIUS_MAX = 6
DELTA = 18.0


def _lum(rgb: np.ndarray) -> np.ndarray:
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def _corner_bg_luminance(rgb: np.ndarray) -> float:
    h, w = rgb.shape[:2]
    s = max(2, min(w, h) // 20)
    patches = [rgb[0:s, 0:s], rgb[0:s, w - s : w], rgb[h - s : h, 0:s], rgb[h - s : h, w - s : w]]
    return float(np.mean([_lum(p).mean() for p in patches]))


def _flood_reached(mask: np.ndarray) -> np.ndarray:
    """테두리에서 배경만 타고 8방향 확산 (TS flood fill 과 동등)."""
    from collections import deque

    ih, iw = mask.shape
    reached = np.zeros_like(mask, dtype=bool)
    dq: deque = deque()

    def push(y: int, x: int) -> None:
        if not mask[y, x] and not reached[y, x]:
            reached[y, x] = True
            dq.append((y, x))

    for xx in range(iw):
        push(0, xx)
        push(ih - 1, xx)
    for yy in range(ih):
        push(yy, 0)
        push(yy, iw - 1)
    while dq:
        py, px = dq.popleft()
        for dy in (-1, 0, 1):
            ny = py + dy
            if ny < 0 or ny >= ih:
                continue
            for dx in (-1, 0, 1):
                nx = px + dx
                if nx < 0 or nx >= iw or (dx == 0 and dy == 0):
                    continue
                push(ny, nx)
    return reached


def _erode(mask: np.ndarray, radius: int) -> np.ndarray:
    """TS 의 분리형(수평→수직) 이진 침식."""
    need = radius + 1
    ih, iw = mask.shape

    def fwd(m: np.ndarray, vertical: bool) -> np.ndarray:
        out = np.zeros(m.shape, dtype=np.int32)
        if vertical:
            c = np.zeros(iw, dtype=np.int32)
            for yy in range(ih):
                c = np.where(m[yy, :], c + 1, 0)
                out[yy, :] = c
        else:
            c = np.zeros(ih, dtype=np.int32)
            for xx in range(iw):
                c = np.where(m[:, xx], c + 1, 0)
                out[:, xx] = c
        return out

    def bwd(m: np.ndarray, vertical: bool) -> np.ndarray:
        out = np.zeros(m.shape, dtype=np.int32)
        if vertical:
            c = np.zeros(iw, dtype=np.int32)
            for yy in range(ih - 1, -1, -1):
                c = np.where(m[yy, :], c + 1, 0)
                out[yy, :] = c
        else:
            c = np.zeros(ih, dtype=np.int32)
            for xx in range(iw - 1, -1, -1):
                c = np.where(m[:, xx], c + 1, 0)
                out[:, xx] = c
        return out

    row = (fwd(mask, False) >= need) & (bwd(mask, False) >= need)
    return (fwd(row, True) >= need) & (bwd(row, True) >= need)


def derive_alpha(rgb_u8: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    프레임 RGB → (alpha 0..1, un-premultiplied RGB float).

    removeNearBackgroundAlpha() 와 동일한 4단계:
      실루엣 M → 구멍 메우기 → 침식으로 core → band 소프트 알파 + 배경 오염 제거
    """
    rgb = rgb_u8.astype(np.float64)
    ih, iw = rgb.shape[:2]
    bg = _corner_bg_luminance(rgb)
    is_bright = bg >= 128
    lum = _lum(rgb)

    mask = (lum < bg - DELTA) if is_bright else (lum > bg + DELTA)
    candidates = int(mask.sum())

    holes = (~mask) & (~_flood_reached(mask))
    mask_filled = mask | holes

    span = DEFAULT_LUM_SPAN
    if candidates > 0:
        hist = np.bincount(np.clip(np.round(lum[mask]), 0, 255).astype(int), minlength=256)
        target = int(candidates * ((1 - FG_PERCENTILE) if is_bright else FG_PERCENTILE))
        acc = 0
        fg = 0 if is_bright else 255
        for v in range(256):
            acc += hist[v]
            if acc > target:
                fg = v
                break
        span = abs(fg - bg)
    span = max(span, MIN_LUM_SPAN)

    radius = int(round(min(iw, ih) * EROSION_RADIUS_PER_PX))
    radius = max(EROSION_RADIUS_MIN, min(EROSION_RADIUS_MAX, radius))
    core = _erode(mask_filled, radius)
    band = mask_filled & (~core)

    a = np.clip(((bg - lum) if is_bright else (lum - bg)) / span, 0.0, 1.0)
    a = np.where(a < ALPHA_CUTOFF, 0.0, a)

    alpha = np.zeros((ih, iw), dtype=np.float64)
    alpha[band] = a[band]
    alpha[core] = 1.0

    # band 는 배경이 섞여 있으므로 un-premultiply 로 원색 복원.
    out = rgb.copy()
    sel = band & (alpha > 0) & (alpha < 1)
    if sel.any():
        av = alpha[sel][:, None]
        out[sel] = np.clip((rgb[sel] - (1 - av) * bg) / av, 0, 255)
    return alpha, out


def pack_frame(rgb_u8: np.ndarray) -> np.ndarray:
    """한 프레임 → vstack packed (상단 premultiplied RGB / 하단 grayscale alpha)."""
    alpha, straight = derive_alpha(rgb_u8)
    # 규약: 상단 RGB 는 **premultiplied**. 웹이 invA 로 되돌린다.
    premul = np.clip(straight * alpha[..., None], 0, 255).astype(np.uint8)
    a8 = np.clip(np.round(alpha * 255), 0, 255).astype(np.uint8)
    matte = np.dstack([a8, a8, a8])  # R=G=B → chroma 0
    return np.vstack([premul, matte])


def average_chroma(rgb_u8: np.ndarray) -> float:
    """packed-alpha-canvas.ts averageChroma() 와 동일한 식."""
    a = rgb_u8.astype(np.int32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return float((np.abs(r - g) + np.abs(g - b) + np.abs(r - b)).mean())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--crf", default="20")
    ap.add_argument("--measure", action="store_true", help="결과 파일의 절반별 chroma 측정")
    args = ap.parse_args()

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ffmpeg/ffprobe 가 필요합니다.", file=sys.stderr)
        return 2

    src = Path(args.input)
    if not src.is_file():
        print(f"입력 없음: {src}", file=sys.stderr)
        return 2

    fps = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "default=nw=1:nk=1", str(src)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    num, _, den = fps.partition("/")
    fps_val = float(num) / float(den or 1)

    with tempfile.TemporaryDirectory(prefix="eb_pack_") as td:
        tdp = Path(td)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), f"{tdp}/f%05d.png"],
            check=True,
        )
        frames = sorted(tdp.glob("f*.png"))
        if not frames:
            print("프레임 추출 실패", file=sys.stderr)
            return 2

        outdir = tdp / "packed"
        outdir.mkdir()
        for i, f in enumerate(frames, 1):
            arr = np.array(Image.open(f).convert("RGB"))
            Image.fromarray(pack_frame(arr)).save(outdir / f"p{i:05d}.png")
            if i % 25 == 0 or i == len(frames):
                print(f"  packed {i}/{len(frames)}", flush=True)

        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-framerate", f"{fps_val:g}", "-i", f"{outdir}/p%05d.png",
             "-c:v", "libx264", "-preset", "slow", "-crf", args.crf,
             "-pix_fmt", "yuv420p", "-an", args.output],
            check=True,
        )

    if args.measure:
        with tempfile.TemporaryDirectory() as td2:
            probe = Path(td2) / "p.png"
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", args.output,
                 "-ss", "0.2", "-frames:v", "1", str(probe)],
                check=True,
            )
            a = np.array(Image.open(probe).convert("RGB"))
            half = a.shape[0] // 2
            top, bottom = average_chroma(a[:half]), average_chroma(a[half:])
            print(f"\n  결과: {a.shape[1]}x{a.shape[0]}  (halfH={half}, 짝수={a.shape[0] % 2 == 0})")
            print(f"  top chroma    = {top:.2f}   (RGB 절반이어야 함)")
            print(f"  bottom chroma = {bottom:.2f}   (매트 절반 — 6.0 미만이어야 함)")
            print(f"  color/matte ratio = {top / max(bottom, 1e-6):.2f}   (2.0 이상이어야 함)")
    print(f"\n저장: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
