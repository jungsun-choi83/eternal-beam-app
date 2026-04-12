"""
RGBA MP4를 녹색 배경에 합성 → 마스크가 제대로 적용됐는지 확인
투명 영역이 녹색으로 보이면 누끼 정상.
"""
import sys
import subprocess
import os


def main():
    if len(sys.argv) < 2:
        print("사용: python scripts/check_rgba_over_green.py <rgba.mp4>")
        print("예: python scripts/check_rgba_over_green.py C:\\...\\xxx_rgba.mp4")
        sys.exit(1)
    inp = sys.argv[1]
    if not os.path.exists(inp):
        print(f"파일 없음: {inp}")
        sys.exit(1)

    base = os.path.splitext(inp)[0]
    out = base + "_on_green_preview.mp4"

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", inp],
        capture_output=True, text=True, timeout=15,
    )
    w, h = 1280, 720
    if probe.returncode == 0 and probe.stdout.strip():
        parts = probe.stdout.strip().split(",")
        if len(parts) >= 2:
            w, h = int(parts[0]), int(parts[1])

    # 녹색 배경 + RGBA 오버레이 (preset ultrafast로 빠르게)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=green:s={w}x{h}:r=24",
        "-i", inp,
        "-filter_complex", "[1:v]format=rgba[fg];[0:v][fg]overlay=format=auto",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-shortest", out,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        print(f"FFmpeg 실패: {r.stderr[-800:] if r.stderr else r.stdout}")
        sys.exit(1)
    print(f"저장: {out}")
    print("  → 녹색이 보이면 = 투명(알파) 정상.")


if __name__ == "__main__":
    main()
