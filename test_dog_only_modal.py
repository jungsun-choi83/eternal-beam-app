# -*- coding: utf-8 -*-
"""
Test: run process_dog_only_from_video pipeline on Modal GPU via process_dog_only_video_via_modal.
Requires: modal login, deploy modal_cutout_app.py (process_dog_only_video_modal).

Note: modal_cutout_app sets function timeout=900s. Client asyncio wait must be >= 900s
or the local script will raise TimeoutError while Modal may still be running.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

from services.modal_cutout_client import process_dog_only_video_via_modal  # noqa: E402

INPUT_VIDEO = Path(r"C:\Users\choi jungsun\Desktop\goya\goya_input.mp4")
OUTPUT_RGB = Path(r"C:\Users\choi jungsun\Desktop\goya\goya_dog_rgb.mp4")
OUTPUT_ALPHA = Path(r"C:\Users\choi jungsun\Desktop\goya\goya_dog_alpha.mp4")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--timeout",
        type=float,
        default=1200.0,
        help="Client-side max wait seconds (default 1200; must exceed Modal fn timeout 900)",
    )
    ap.add_argument(
        "--max-frames",
        type=int,
        default=120,
        help="Max frames to process (lower = faster test, e.g. 30)",
    )
    args = ap.parse_args()

    if not INPUT_VIDEO.is_file():
        print(f"ERROR: input not found: {INPUT_VIDEO}", flush=True)
        sys.exit(1)

    raw = INPUT_VIDEO.read_bytes()
    print(
        f"start: input={INPUT_VIDEO} ({len(raw)} bytes) max_frames={args.max_frames} timeout={args.timeout}s",
        flush=True,
    )
    t0 = time.perf_counter()

    try:
        result = await asyncio.wait_for(
            process_dog_only_video_via_modal(
                video_bytes=raw,
                replace_bg="white",
                max_frames=args.max_frames,
                use_alpha_matting=False,
            ),
            timeout=args.timeout,
        )
    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - t0
        print(
            f"ERROR: client timeout after {elapsed:.1f}s (limit {args.timeout}s). "
            "Modal may still be running; check https://modal.com logs for the function run.",
            flush=True,
        )
        sys.exit(1)

    elapsed = time.perf_counter() - t0

    if result is None:
        print(
            "ERROR: Modal returned None (modal not configured or import failed). "
            "Check ~/.modal.toml or MODAL_TOKEN_* env.",
            flush=True,
        )
        sys.exit(1)

    rgb = result.get("rgb_mp4")
    alpha = result.get("alpha_mp4")
    if not rgb or not alpha:
        print(f"ERROR: unexpected keys: {list(result.keys())}", flush=True)
        sys.exit(1)

    OUTPUT_RGB.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_RGB.write_bytes(rgb)
    OUTPUT_ALPHA.write_bytes(alpha)

    print(f"done: rgb={OUTPUT_RGB} ({len(rgb)} bytes)", flush=True)
    print(f"done: alpha={OUTPUT_ALPHA} ({len(alpha)} bytes)", flush=True)
    print(f"elapsed: {elapsed:.1f}s", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
