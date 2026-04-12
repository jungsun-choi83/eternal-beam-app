# -*- coding: utf-8 -*-
"""
Upload a pre-cut PNG (external remove.bg etc.) and run Luma idle + action only.
Requires: STORAGE_BACKEND + credentials, LUMA_API_KEY (e.g. backend/.env).

  cd eternal-beam-app
  python luma_from_cutout.py "C:\\path\\to\\cutout.png"
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

try:
    from dotenv import load_dotenv

    # Order: project root then backend (prefer env.local if .env is encrypted by AnySign4PC)
    _env_paths = (
        ROOT / "env.local",
        ROOT / ".env.local",
        ROOT / ".env",
        ROOT / "backend" / "env.local",
        ROOT / "backend" / ".env.local",
        ROOT / "backend" / ".env",
    )
    for _p in _env_paths:
        if _p.is_file():
            try:
                load_dotenv(_p, override=True, encoding="utf-8-sig")
            except TypeError:
                load_dotenv(_p, override=True)
except ImportError:
    pass

_luma = (os.getenv("LUMA_API_KEY") or "").strip()
if not _luma:
    print(
        "ERROR: LUMA_API_KEY is empty. Add to backend/.env.local for example:\n"
        "  LUMA_API_KEY=luma-xxxxxxxx\n"
        "(Luma keys usually start with luma-). No quotes. Save as UTF-8.",
        file=sys.stderr,
    )
    sys.exit(1)
if not _luma.startswith("luma-"):
    print(
        "WARNING: Luma API keys normally start with 'luma-'. Check you copied the full key.",
        file=sys.stderr,
    )


def _is_moderation_fail(err: BaseException) -> bool:
    s = str(err).lower()
    return "moderate" in s or "400" in s and "fail" in s


async def _run(image_path: Path, user_id: str) -> None:
    from services.luma_keyframe import flatten_rgba_to_jpeg_bytes
    from services.luma_service import (
        build_idle_action_prompts,
        build_minimal_idle_action_prompts,
        create_generation_and_get_video_url,
    )
    from services.object_storage import upload_bytes

    raw = image_path.read_bytes()
    cid = f"content_{uuid.uuid4().hex[:12]}"
    use_jpeg = os.getenv("LUMA_KEYFRAME_AS_JPEG", "1").strip().lower() not in ("0", "false", "no")

    if not use_jpeg:
        dog_url = await upload_bytes(
            f"{user_id}/{cid}_dog_only_nobg.png",
            raw,
            "image/png",
        )
        idle_p, action_p = build_idle_action_prompts(raw)
        try:
            idle_u, action_u = await asyncio.gather(
                create_generation_and_get_video_url(dog_url, prompt=idle_p),
                create_generation_and_get_video_url(dog_url, prompt=action_p),
            )
        except Exception as e:
            raise RuntimeError(f"Luma failed: {e}") from e
        print("luma_keyframe_url:", dog_url)
        print("idle_video_url:", idle_u)
        print("action_video_url:", action_u)
        print("content_id:", cid)
        return

    # JPEG variants: white/black bg, resolution, prompts — Luma often rejects RGBA or certain composites
    variants: list[tuple[str, tuple[int, int, int], int, str, str]] = [
        ("w2048", (255, 255, 255), 2048, "full", "ray-2"),
        ("b2048", (0, 0, 0), 2048, "full", "ray-2"),
        ("w1024", (255, 255, 255), 1024, "full", "ray-2"),
        ("b1024", (0, 0, 0), 1024, "full", "ray-2"),
        ("w1024m", (255, 255, 255), 1024, "minimal", "ray-2"),
        ("b1024m", (0, 0, 0), 1024, "minimal", "ray-2"),
        ("w1024f", (255, 255, 255), 1024, "minimal", "ray-flash-2"),
    ]

    last_err: BaseException | None = None
    for label, bg, max_side, prompt_mode, model in variants:
        up_bytes = flatten_rgba_to_jpeg_bytes(raw, bg_rgb=bg, max_side=max_side)
        object_name = f"{user_id}/{cid}_keyframe_{label}.jpg"
        dog_url = await upload_bytes(object_name, up_bytes, "image/jpeg")
        if prompt_mode == "minimal":
            idle_p, action_p = build_minimal_idle_action_prompts()
        else:
            idle_p, action_p = build_idle_action_prompts(raw)
        print(
            f"Trying Luma: {label} bg={bg} max_side={max_side} prompts={prompt_mode} model={model} ...",
            flush=True,
        )
        try:
            idle_u, action_u = await asyncio.gather(
                create_generation_and_get_video_url(
                    dog_url, prompt=idle_p, model=model
                ),
                create_generation_and_get_video_url(
                    dog_url, prompt=action_p, model=model
                ),
            )
        except Exception as e:
            last_err = e
            if _is_moderation_fail(e):
                print(f"  -> moderation or job failed, next variant...", flush=True)
                continue
            raise
        print("luma_keyframe_url:", dog_url)
        print("idle_video_url:", idle_u)
        print("action_video_url:", action_u)
        print("content_id:", cid)
        return

    raise RuntimeError(
        "All keyframe variants failed. Last error: "
        + (str(last_err) if last_err else "unknown")
        + " Try another photo or contact Luma support."
    )


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python luma_from_cutout.py "<path-to.png>" [user_id]')
        sys.exit(1)
    p = Path(sys.argv[1])
    uid = sys.argv[2] if len(sys.argv) > 2 else "anonymous"
    if not p.is_file():
        print(f"ERROR: file not found: {p}")
        sys.exit(1)
    asyncio.run(_run(p, uid))


if __name__ == "__main__":
    main()
