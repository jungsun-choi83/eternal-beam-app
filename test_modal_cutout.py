# -*- coding: utf-8 -*-
"""
After Modal returns RGBA MP4, split to RGB-only and alpha-only MP4 via FFmpeg.
Use ASCII log lines so PowerShell (cp949) does not show [??].
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

from services.modal_cutout_client import process_video_via_modal, is_modal_available

INPUT_PATH = Path(r"C:\Users\choi jungsun\Desktop\goya") / "goya_input.mp4"
OUT_DIR = Path(r"C:\Users\choi jungsun\Desktop\goya")
OUT_RGB = OUT_DIR / "goya_rgb.mp4"
OUT_ALPHA = OUT_DIR / "goya_alpha.mp4"
MAX_FRAMES = 120
TIMEOUT = 600.0
MODAL_HEARTBEAT_SEC = 15.0

_VF_RGB = "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p"

# Alpha: Windows FFmpeg ???? ??. ?? ? ?? ?? ??.
_ALPHA_VF_CHAIN = [
    "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=rgba,alphaextract,format=yuv420p",
    "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuva420p,extractplanes=a,format=yuv420p",
    "scale=trunc(iw/2)*2:trunc(ih/2)*2,alphaextract,format=yuv420p",
]


def _ffmpeg(args: list[str], label: str) -> None:
    r = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0:
        err = (r.stderr or "")[-3000:]
        print(f"[FFmpeg FAIL: {label}]\n{err}", flush=True)
        raise subprocess.CalledProcessError(r.returncode, args, r.stdout, r.stderr)


async def _modal_heartbeat() -> None:
    """Print every N sec while Modal runs."""
    t0 = time.perf_counter()
    try:
        while True:
            await asyncio.sleep(MODAL_HEARTBEAT_SEC)
            print(
                f"[WAIT] {time.perf_counter() - t0:.0f}s - Modal GPU (still running)",
                flush=True,
            )
    except asyncio.CancelledError:
        pass


async def main() -> None:
    if not is_modal_available():
        print("[ERR] Modal not configured (~/.modal.toml or MODAL_TOKEN_*)")
        sys.exit(1)
    if not INPUT_PATH.is_file():
        print(f"[ERR] Missing file: {INPUT_PATH}")
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[START] {datetime.now().isoformat(timespec='seconds')}")
    video_bytes = INPUT_PATH.read_bytes()
    print(f"[INPUT] {len(video_bytes)} bytes")
    print("[INFO] Next log may take several minutes (Modal cold start + 120 frames).", flush=True)

    t0 = time.perf_counter()
    hb = asyncio.create_task(_modal_heartbeat())
    try:
        rgba_bytes = await asyncio.wait_for(
            process_video_via_modal(
                video_bytes,
                replace_bg="white",
                max_frames=MAX_FRAMES,
            ),
            timeout=TIMEOUT,
        )
    finally:
        hb.cancel()
        try:
            await hb
        except asyncio.CancelledError:
            pass

    if not rgba_bytes:
        print("[ERR] Modal returned empty")
        sys.exit(1)

    print("[Modal] OK, running local FFmpeg...", flush=True)

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(rgba_bytes)
        tmp_path = Path(tmp.name)

    try:
        _ffmpeg(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "warning",
                "-y",
                "-threads",
                "1",
                "-i",
                str(tmp_path),
                "-vf",
                _VF_RGB,
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(OUT_RGB),
            ],
            "RGB",
        )
        last_err = ""
        for i, vf in enumerate(_ALPHA_VF_CHAIN):
            args = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "warning",
                "-y",
                "-threads",
                "1",
                "-i",
                str(tmp_path),
                "-vf",
                vf,
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(OUT_ALPHA),
            ]
            r = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if r.returncode == 0:
                print(f"[Alpha] OK (filter #{i + 1})", flush=True)
                break
            last_err = (r.stderr or "")[-2000:]
            print(f"[Alpha] filter #{i + 1} failed, trying next...", flush=True)
        else:
            print(f"[FFmpeg FAIL: Alpha] all filters failed\n{last_err}", flush=True)
            raise subprocess.CalledProcessError(1, args, "", last_err)
    finally:
        tmp_path.unlink(missing_ok=True)

    elapsed = time.perf_counter() - t0
    print(f"[DONE] {elapsed:.1f} sec")
    print(f"RGB:   {OUT_RGB} ({OUT_RGB.stat().st_size} bytes)")
    print(f"Alpha: {OUT_ALPHA} ({OUT_ALPHA.stat().st_size} bytes)")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[STOP] Ctrl+C (cancelled Modal or FFmpeg wait)", flush=True)
        sys.exit(130)
