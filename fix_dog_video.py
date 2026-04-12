"""
fix_dog_video.py
- rembg 처리된 MP4(검정 배경 + 강아지)에서 할로 픽셀 제거
- 밝기 임계값 마스크 + 침식(erode)으로 경계 오염 제거
- Unity VideoLayer용 dog_rgb.mp4, dog_alpha.mp4 출력
"""

import cv2
import numpy as np
import os

INPUT        = "idle1_mp4.mp4"
OUTPUT_RGB   = "dog_rgb.mp4"
OUTPUT_ALPHA = "dog_alpha.mp4"

cap   = cv2.VideoCapture(INPUT)
fps   = cap.get(cv2.CAP_PROP_FPS)
W     = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
H     = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
print(f"입력: {W}x{H}, {fps:.1f}fps, {total}프레임")

fourcc   = cv2.VideoWriter_fourcc(*'mp4v')
wr_rgb   = cv2.VideoWriter(OUTPUT_RGB,   fourcc, fps, (W, H))
wr_alpha = cv2.VideoWriter(OUTPUT_ALPHA, fourcc, fps, (W, H))

# 형태학적 연산용 커널
k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
k_erode = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

idx = 0
while True:
    ret, frame = cap.read()
    if not ret:
        break

    # ── 1. 밝기 임계값으로 마스크 ──
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 12, 255, cv2.THRESH_BINARY)

    # ── 2. 마스크 정제 ──
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k_close)  # 구멍 메우기
    mask = cv2.erode(mask, k_erode, iterations=2)             # 할로 경계 제거 ★

    # ── 3. 경계 부드럽게 (딱딱한 절단선 방지) ──
    mask_soft = cv2.GaussianBlur(mask, (11, 11), 0)

    # ── 4. Pre-multiplied RGB 만들기 ──
    # Blend One OneMinusSrcAlpha 방식 전용
    # 반드시 RGB에 알파를 미리 곱해야 경계 할로가 사라짐
    a = mask_soft.astype(np.float32) / 255.0
    premul = np.clip(
        frame.astype(np.float32) * a[:, :, np.newaxis],
        0, 255
    ).astype(np.uint8)

    # ── 5. 저장 ──
    wr_rgb.write(premul)
    wr_alpha.write(cv2.cvtColor(mask_soft, cv2.COLOR_GRAY2BGR))

    idx += 1
    if idx % 30 == 0:
        print(f"  {idx}/{total} ({idx/total*100:.0f}%)")

cap.release()
wr_rgb.release()
wr_alpha.release()
print(f"\n완료:")
print(f"  {OUTPUT_RGB}   → Unity Clips Element 0")
print(f"  {OUTPUT_ALPHA} → Unity Alpha Clips Element 0")
