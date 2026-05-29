"""
Luma IDLE mp4 → 첫/끝 프레임 크로스페이드 루프 (FFmpeg).

환경변수:
  SEAMLESS_LOOP_ENABLED=1 (기본 true)
  SEAMLESS_LOOP_FADE_SEC=0.45
  SEAMLESS_LOOP_MAX_SEC=4.0
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .compose_video_service import run_ffmpeg


def _enabled() -> bool:
    return os.getenv("SEAMLESS_LOOP_ENABLED", "true").lower() in ("1", "true", "yes")


def _probe_duration_sec(path: str) -> float:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    m = re.search(
        r"Duration:\s*(\d+):(\d+):(\d+)[.](\d+)",
        proc.stderr or "",
    )
    if not m:
        return 0.0
    h, mi, s, cs = map(int, m.groups())
    return h * 3600 + mi * 60 + s + cs / 100.0


def make_seamless_loop_mp4(
    mp4_bytes: bytes,
    *,
    fade_sec: Optional[float] = None,
    max_duration_sec: Optional[float] = None,
) -> tuple[bytes, dict]:
    """
    tail fade_sec 와 head fade_sec 를 xfade 후 본편과 concat.
    Returns (output_bytes, meta).
    """
    if not _enabled() or not mp4_bytes:
        return mp4_bytes, {"skipped": True, "reason": "disabled_or_empty"}

    fade = float(fade_sec if fade_sec is not None else os.getenv("SEAMLESS_LOOP_FADE_SEC", "0.45"))
    max_dur = float(
        max_duration_sec
        if max_duration_sec is not None
        else os.getenv("SEAMLESS_LOOP_MAX_SEC", "4.0")
    )

    with tempfile.TemporaryDirectory(prefix="eb_loop_") as td:
        td_path = Path(td)
        inp = td_path / "in.mp4"
        out = td_path / "loop.mp4"
        inp.write_bytes(mp4_bytes)

        dur = _probe_duration_sec(str(inp))
        if dur < 0.8:
            return mp4_bytes, {"skipped": True, "reason": "too_short", "duration_sec": dur}

        fade = min(fade, dur * 0.35, max_dur * 0.35)
        body_end = max(0.05, dur - fade)

        # [body 0..body_end] + xfade(tail, head)
        fc = (
            f"[0:v]trim=end={body_end:.4f},setpts=PTS-STARTPTS[body];"
            f"[0:v]trim=start={body_end:.4f},setpts=PTS-STARTPTS[tail];"
            f"[0:v]trim=end={fade:.4f},setpts=PTS-STARTPTS[head];"
            f"[tail][head]xfade=transition=fade:duration={fade:.4f}:offset=0[x];"
            f"[body][x]concat=n=2:v=1:a=0[vout]"
        )

        try:
            run_ffmpeg(
                [
                    "-i",
                    str(inp),
                    "-filter_complex",
                    fc,
                    "-map",
                    "[vout]",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "23",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    "-t",
                    str(min(max_dur, dur + fade)),
                    str(out),
                ]
            )
        except Exception as e:
            return mp4_bytes, {"skipped": True, "reason": "ffmpeg_failed", "error": str(e)}

        if not out.is_file():
            return mp4_bytes, {"skipped": True, "reason": "no_output"}

        return out.read_bytes(), {
            "skipped": False,
            "duration_sec": dur,
            "fade_sec": fade,
            "max_duration_sec": max_dur,
        }
