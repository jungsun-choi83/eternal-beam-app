#!/usr/bin/env python3
"""
Runway 영상 → RGBA PNG 시퀀스 / RGBA 영상

광고 촬영 수준 홀로그램: RGB와 Alpha 완전 분리
- 프레임 추출 → rembg 배경 제거 → RGBA 출력
- 털 디테일, 꼬리 끊김 없음, halo 없음, 배경 완전 제거

사용:
  python scripts/video_to_rgba.py input.mp4 -o ./output
  python scripts/video_to_rgba.py input.mp4 -o ./output --video  # RGBA MP4
  python scripts/video_to_rgba.py input.mp4 -o ./output --max-frames 30  # 테스트
"""
import argparse
import os
import sys

# backend 경로 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.video_cutout_service import (
    process_video_to_rgba,
    png_sequence_to_rgba_video,
)


def main():
    parser = argparse.ArgumentParser(description="Runway 영상 → RGBA (배경 제거)")
    parser.add_argument("input", help="입력 영상 경로 (Runway 등)")
    parser.add_argument("-o", "--output-dir", default="./output", help="출력 디렉토리")
    parser.add_argument(
        "--video",
        action="store_true",
        help="PNG 시퀀스 대신 RGBA MP4 한 파일로 출력",
    )
    parser.add_argument(
        "--model",
        default="isnet-general-use",
        help="rembg 모델 (isnet-general-use | u2net_human_seg)",
    )
    parser.add_argument("--max-frames", type=int, default=None, help="처리할 최대 프레임 수 (테스트용)")
    parser.add_argument(
        "--alpha-matting",
        action="store_true",
        help="Alpha Matting 활성화 (기본: 비활성=빠름. 켜면 경계 부드러움, CPU에선 매우 느림)",
    )
    parser.add_argument(
        "--black-dog",
        action="store_true",
        help="블랙탄/검정 강아지: 배경을 흰색으로 교체 후 rembg (누끼 품질 향상)",
    )
    parser.add_argument(
        "--light-dog",
        action="store_true",
        help="밝은색 강아지: 배경을 검정으로 교체 후 rembg",
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="Proxy Masking 비활성화 (원본 해상도 유지, 느림)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"오류: 파일 없음 {args.input}")
        sys.exit(1)

    replace_bg = None
    if args.black_dog:
        replace_bg = "white"
        print("  (블랙탄 모드: 배경→흰색 교체 후 rembg)")
    elif args.light_dog:
        replace_bg = "black"

    output_resolution = None if args.no_proxy else (1280, 720)
    if not args.no_proxy:
        print("  (Proxy Masking: 360p rembg → 720p 출력)")

    def progress(i, total):
        print(f"  {i}/{total or '?'} 프레임 처리 중...")

    print(f"처리: {args.input} → {args.output_dir}")
    print("  (rembg 배경 제거 중...)")

    try:
        result = process_video_to_rgba(
            args.input,
            args.output_dir,
            output_format="rgba_video" if args.video else "png_sequence",
            model_name=args.model,
            use_alpha_matting=args.alpha_matting,
            max_frames=args.max_frames,
            progress_callback=progress,
            replace_bg_before_rembg=replace_bg,
            output_resolution=output_resolution,
        )
    except Exception as e:
        print(f"오류: {e}")
        sys.exit(1)

    if args.video:
        print(f"완료: {result[0]}")
    else:
        count = len([p for p in result if p.endswith(".png")])
        print(f"완료: {count}개 PNG → {args.output_dir}")

    print("\n다음 단계:")
    if args.video:
        print("  - Unity: RGBA MP4를 clips에 등록, UseAlphaTex=0")
    else:
        print("  - PNG 시퀀스 → RGBA MP4: python scripts/video_to_rgba.py ... --video")
        print("  - 또는 preprocess_video.py로 전처리 후 Unity 사용")


if __name__ == "__main__":
    main()
