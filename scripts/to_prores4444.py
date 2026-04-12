#!/usr/bin/env python3
"""
ProRes 4444 RGBA 출력 (Unity 최종 입력용)
- RGB+Alpha 분리 → 단일 RGBA ProRes 4444
- 또는 기존 RGBA → ProRes 4444 변환

사용:
  python scripts/to_prores4444.py input_rgb.mp4 input_alpha.mp4 -o output.mov
  python scripts/to_prores4444.py input_rgba.mp4 -o output.mov
"""
import argparse
import subprocess
import sys
import os

def convert_rgba_to_prores(rgb_path: str, alpha_path: str, output_path: str) -> bool:
    """RGB + Alpha 분리 → ProRes 4444 RGBA"""
    cmd = [
        "ffmpeg", "-y",
        "-i", rgb_path,
        "-i", alpha_path,
        "-filter_complex",
        "[0:v]format=rgba[rgb];[1:v]format=gray[alpha];[rgb][alpha]alphamerge=format=rgba[out]",
        "-map", "[out]",
        "-c:v", "prores_ks",
        "-profile:v", "4444",
        "-pix_fmt", "yuva444p10le",
        output_path
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print(r.stderr[-800:] if r.stderr else "ffmpeg failed")
        return False
    print(f"OK: {output_path}")
    return True

def convert_single_to_prores(input_path: str, output_path: str) -> bool:
    """단일 RGBA → ProRes 4444"""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-c:v", "prores_ks",
        "-profile:v", "4444",
        "-pix_fmt", "yuva444p10le",
        output_path
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print(r.stderr[-800:] if r.stderr else "ffmpeg failed")
        return False
    print(f"OK: {output_path}")
    return True

def main():
    parser = argparse.ArgumentParser(description="ProRes 4444 RGBA 변환")
    parser.add_argument("input", nargs="+", help="RGB 입력 (단일) 또는 RGB, Alpha (2개)")
    parser.add_argument("-o", "--output", required=True, help="출력 .mov 경로")
    args = parser.parse_args()

    if len(args.input) == 1:
        ok = convert_single_to_prores(args.input[0], args.output)
    elif len(args.input) == 2:
        ok = convert_rgba_to_prores(args.input[0], args.input[1], args.output)
    else:
        print("Usage: to_prores4444.py <rgb.mp4> [alpha.mp4] -o output.mov")
        sys.exit(1)

    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
