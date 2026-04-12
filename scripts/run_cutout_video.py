"""
로컬 영상 → rembg 누끼 → RGBA MP4
실행: python -m scripts.run_cutout_video "C:\path\to\video.mp4" [최대프레임]
"""

import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from backend.services.video_cutout_service import process_video_to_rgba
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("사용법: python -m scripts.run_cutout_video <영상경로> [최대프레임]")
        print("예: python -m scripts.run_cutout_video \"C:\\Downloads\\video.mp4\" 90")
        sys.exit(1)

    input_path = sys.argv[1]
    max_frames = int(sys.argv[2]) if len(sys.argv) > 2 else None  # None = 전체

    if not os.path.exists(input_path):
        print(f"파일 없음: {input_path}")
        sys.exit(1)

    base = Path(input_path).stem
    output_dir = os.path.join(os.path.dirname(input_path), f"{base}_cutout")
    os.makedirs(output_dir, exist_ok=True)

    def progress(i, total):
        pct = (i / total * 100) if total else 0
        print(f"\r프레임 {i}/{total or '?'} ({pct:.0f}%)", end="")

    print(f"입력: {input_path}")
    print(f"출력 폴더: {output_dir}")
    print("rembg 누끼 처리 중... (프레임마다 rembg 호출, 시간 소요)")
    result = process_video_to_rgba(
        input_path,
        output_dir,
        output_format="rgba_video",
        model_name="isnet-general-use",
        use_alpha_matting=True,
        max_frames=max_frames,
        progress_callback=progress,
    )
    out_mp4 = result[0]
    print(f"\n완료: {out_mp4}")


if __name__ == "__main__":
    main()
