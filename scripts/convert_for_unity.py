#!/usr/bin/env python3
"""
mp4v → H.264 변환 (Unity VideoPlayer 호환)
WindowsMediaFoundation 오류 방지
"""
import subprocess
import sys
import os

def convert(path: str, out_path: str = None) -> bool:
    if out_path is None:
        out_path = path.replace(".mp4", "_h264.mp4")
    if out_path == path:
        out_path = path.replace(".mp4", "_h264.mp4")
    cmd = [
        "ffmpeg", "-y", "-i", path,
        "-c:v", "libx264", "-profile:v", "main", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        out_path
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(r.stderr[-500:] if r.stderr else "ffmpeg failed")
        return False
    print(f"OK: {out_path}")
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python convert_for_unity.py <video.mp4> [output.mp4]")
        sys.exit(1)
    path = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None
    if not os.path.exists(path):
        print(f"Not found: {path}")
        sys.exit(1)
    ok = convert(path, out)
    sys.exit(0 if ok else 1)
