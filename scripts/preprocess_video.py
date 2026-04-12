#!/usr/bin/env python3
"""
Eternal Beam - RGBA 비디오 전처리 파이프라인

입력: RGBA 영상만 (Alpha 채널 필수). Luma/threshold 기반 alpha 생성 없음.
처리: edge despill, alpha erosion, fringe suppress, denoise, edge blur.
출력: RGBA 비디오 또는 RGB+Alpha 분리 (--rgba | 기본 분리).

사용:
  python scripts/preprocess_video.py input_rgba.mp4 -o ./output
  python scripts/preprocess_video.py input_rgba.mp4 -o ./output --rgba
"""

import argparse
import os
import sys
from pathlib import Path

import cv2
import numpy as np


def edge_despill(bgra: np.ndarray) -> np.ndarray:
    """
    경계에 묻은 배경색(청록/시안) 제거.
    OpenCV BGR 입력.
    """
    bgr = bgra[:, :, :3].astype(np.float32) / 255.0
    alpha = bgra[:, :, 3].astype(np.float32) / 255.0

    # spill: blue(B)가 R,G보다 dominant한 영역
    b, g, r = bgr[:, :, 0], bgr[:, :, 1], bgr[:, :, 2]
    spill = np.clip(b - np.maximum(r, g), 0, 1)
    spill *= (1 - alpha)

    # despill: blue 감소, red/green 살짝 보정
    bgr[:, :, 0] = np.clip(b - spill * 0.5, 0, 1)
    bgr[:, :, 2] = np.clip(r + spill * 0.1, 0, 1)

    out = (np.clip(bgr, 0, 1) * 255).astype(np.uint8)
    return np.dstack([out, bgra[:, :, 3]])


def cyan_blue_fringe_suppress(bgr: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """
    cyan/blue fringe 억제: 경계 근처에서 blue 채널 감소.
    OpenCV BGR 입력.
    """
    bgr = bgr.astype(np.float32) / 255.0
    alpha = alpha.astype(np.float32) / 255.0

    # 경계 마스크 (alpha 0.2~0.6 구간)
    edge = 4.0 * alpha * (1.0 - alpha)
    edge = np.clip(edge, 0, 1)

    # blue(B) 억제, red(R) 살짝 보정 → warm
    bgr[:, :, 0] = bgr[:, :, 0] * (1.0 - edge * 0.4)
    bgr[:, :, 2] = np.minimum(bgr[:, :, 2] + edge * 0.05, 1.0)

    return (np.clip(bgr, 0, 1) * 255).astype(np.uint8)


def alpha_erosion(alpha: np.ndarray, px: int = 1) -> np.ndarray:
    """
    알파 경계 1~2px erosion (fringe 축소).
    """
    kernel = np.ones((px * 2 + 1, px * 2 + 1), np.uint8)
    alpha_uint8 = (alpha * 255).astype(np.uint8)
    eroded = cv2.erode(alpha_uint8, kernel)
    return eroded.astype(np.float32) / 255.0


def alpha_edge_blur(alpha: np.ndarray, sigma: float = 0.5) -> np.ndarray:
    """
    알파 경계에 아주 작은 블러 (~0.5px).
    """
    k = max(3, int(sigma * 4) | 1)
    return cv2.GaussianBlur(alpha, (k, k), sigma)


def denoise_slight(rgb: np.ndarray) -> np.ndarray:
    """
    약한 디노이즈 (AI artifact 완화).
    """
    return cv2.fastNlMeansDenoisingColored(rgb, None, 3, 7, 21)


def process_frame(
    frame: np.ndarray,
    alpha: np.ndarray,
    erosion_px: int = 1,
    edge_blur_sigma: float = 0.5,
    denoise: bool = True,
    despill: bool = True,
    fringe_suppress: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """
    단일 프레임 전처리.
    frame: BGR (OpenCV) 또는 RGBA
    alpha: 0~255 또는 0~1
    """
    if frame.shape[2] == 4:
        rgba = frame
        rgb = frame[:, :, :3]
        alpha_in = frame[:, :, 3].astype(np.float32) / 255.0
    else:
        rgb = frame.copy()
        alpha_in = alpha.astype(np.float32)
        if alpha_in.max() > 1.0:
            alpha_in /= 255.0
        rgba = np.dstack([rgb, (alpha_in * 255).astype(np.uint8)])

    # 1. edge despill (BGR)
    if despill:
        rgba = edge_despill(rgba)
        rgb = rgba[:, :, :3]

    # 2. alpha erosion
    alpha_out = alpha_erosion(alpha_in, erosion_px)

    # 3. cyan/blue fringe suppress (eroded alpha 기준)
    if fringe_suppress:
        rgb = cyan_blue_fringe_suppress(rgb, alpha_out)

    # 4. denoise
    if denoise:
        rgb = denoise_slight(rgb)

    # 5. alpha edge blur
    alpha_out = alpha_edge_blur(alpha_out, edge_blur_sigma)
    alpha_out = np.clip(alpha_out, 0, 1)

    # BGR 반환 (OpenCV 호환)
    return rgb, alpha_out


def extract_alpha_from_rgb(rgb: np.ndarray, threshold: float = 0.1) -> np.ndarray:
    """
    RGB만 있을 때 luma 기반 alpha 추정 (폴백).
    """
    gray = cv2.cvtColor(rgb, cv2.COLOR_BGR2GRAY)
    alpha = np.clip((gray.astype(np.float32) - threshold * 255) / (255 * 0.3), 0, 1)
    return alpha


def process_video(
    input_path: str,
    output_dir: str,
    erosion_px: int = 1,
    edge_blur_sigma: float = 0.5,
    denoise: bool = True,
    despill: bool = True,
    fringe_suppress: bool = True,
) -> str:
    """
    RGBA 비디오 전처리. 출력: RGBA MP4.
    """
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError(f"비디오 열기 실패: {input_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    os.makedirs(output_dir, exist_ok=True)
    base = Path(input_path).stem
    out_path = os.path.join(output_dir, f"{base}_preprocessed.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_path, fourcc, fps, (w, h), True)

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame.shape[2] != 4:
            raise RuntimeError(
                f"프레임 {frame_idx}: RGBA 입력 필요. Alpha 채널이 없는 영상은 지원하지 않습니다. "
                "video_to_rgba.py로 먼저 배경 제거 후 사용하세요."
            )
        alpha = frame[:, :, 3]

        rgb_out, alpha_out = process_frame(
            frame,
            alpha,
            erosion_px=erosion_px,
            edge_blur_sigma=edge_blur_sigma,
            denoise=denoise,
            despill=despill,
            fringe_suppress=fringe_suppress,
        )

        alpha_uint8 = (alpha_out * 255).astype(np.uint8)
        bgra = np.dstack([rgb_out, alpha_uint8])
        writer.write(bgra)

        frame_idx += 1
        if frame_idx % 30 == 0:
            print(f"  {frame_idx}/{total}")

    cap.release()
    writer.release()
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Eternal Beam RGBA 비디오 전처리")
    parser.add_argument("input", help="RGBA 입력 비디오 경로")
    parser.add_argument("--output-dir", "-o", default="./output", help="출력 디렉토리")
    parser.add_argument("--erosion", type=int, default=1, help="알파 erosion 픽셀 (1~2)")
    parser.add_argument("--edge-blur", type=float, default=0.5, help="알파 경계 블러 sigma")
    parser.add_argument("--no-denoise", action="store_true", help="디노이즈 비활성화")
    parser.add_argument("--no-despill", action="store_true", help="despill 비활성화")
    parser.add_argument("--no-fringe", action="store_true", help="fringe 억제 비활성화")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"오류: 파일 없음 {args.input}")
        sys.exit(1)

    print(f"전처리: {args.input} -> {args.output_dir}")
    out_path = process_video(
        args.input,
        args.output_dir,
        erosion_px=args.erosion,
        edge_blur_sigma=args.edge_blur,
        denoise=not args.no_denoise,
        despill=not args.no_despill,
        fringe_suppress=not args.no_fringe,
    )
    print(f"완료: {out_path}")
    print("\nUnity: RGBA 클립을 VideoLayer clips에 등록")


if __name__ == "__main__":
    main()
