#!/usr/bin/env python3
"""
Eternal Beam - Content_ID 기반 레이어 재생
라즈베리 파이 / PC 하드웨어용

- NFC 리더 → Content_ID 읽기
- API로 레이어 URL 조회
- 캐시 우선 → 없으면 다운로드
- 레이어 분리 재생 (배경 어둡고 흐릿, 피사체 밝고 선명)
- Scale/Position 적용
- True Black 클램핑 (DLP 프로젝터용)
"""

import json
import os
import sys
from pathlib import Path

try:
    import cv2
    import numpy as np
    import requests
except ImportError:
    print("필요 패키지: pip install opencv-python numpy requests", file=sys.stderr)
    sys.exit(1)

# 설정
BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = Path(os.getenv("ETERNAL_BEAM_CACHE_DIR", str(BASE_DIR / "cache")))
API_ENDPOINT = os.getenv("ETERNAL_BEAM_API_URL", "http://localhost:8000")
PURE_BLACK_THRESHOLD = 10  # True Black 클램핑


def clamp_pure_black(frame: np.ndarray) -> np.ndarray:
    """True Black: 흑레벨 0~10 → Pure Black(#000000), DLP 프로젝터용"""
    if frame is None or frame.size == 0:
        return frame
    out = frame.copy()
    gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    mask = gray <= PURE_BLACK_THRESHOLD
    out[mask] = [0, 0, 0]
    return out


def fetch_content(content_id: str) -> dict:
    """
    Content_ID로 콘텐츠 메타데이터 가져오기
    캐시 우선 → 없으면 API
    """
    cache_file = CACHE_DIR / f"{content_id}.json"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if cache_file.exists():
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)

    url = f"{API_ENDPOINT.rstrip('/')}/api/content/{content_id}"
    resp = requests.get(url, timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(f"Content not found: {content_id} (HTTP {resp.status_code})")

    content = resp.json()
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False)
    return content


def download_if_needed(url: str, local_path: Path) -> None:
    """파일 다운로드 (없을 때만)"""
    if local_path.exists():
        return
    print(f"Downloading: {url}")
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with open(local_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    print(f"Downloaded: {local_path}")


def play_layered_content(content: dict) -> None:
    """
    레이어 분리 재생
    - 배경: 어둡고 흐릿
    - 피사체: 밝고 선명, Scale/Position 적용
    """
    scale = content.get("scale", 1.0)
    pos_x = content.get("position_x", 0)
    pos_y = content.get("position_y", 0)
    bg_url = content.get("background_url", "")
    subj_url = content.get("subject_url", "")

    if not bg_url or not subj_url:
        raise ValueError("background_url, subject_url 필요")

    bg_id = bg_url.split("/")[-1].replace(".mp4", "")
    subj_id = subj_url.split("/")[-1].replace(".mp4", "")
    bg_path = CACHE_DIR / f"bg_{bg_id}.mp4"
    subj_path = CACHE_DIR / f"subj_{subj_id}.mp4"

    download_if_needed(bg_url, bg_path)
    download_if_needed(subj_url, subj_path)

    bg_cap = cv2.VideoCapture(str(bg_path))
    subj_cap = cv2.VideoCapture(str(subj_path))

    if not bg_cap.isOpened() or not subj_cap.isOpened():
        raise RuntimeError("영상 파일을 열 수 없습니다.")

    cv2.namedWindow("Eternal Beam", cv2.WINDOW_NORMAL)
    cv2.setWindowProperty("Eternal Beam", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    ret_bg, bg_frame = bg_cap.read()
    if not ret_bg:
        raise RuntimeError("배경 영상 읽기 실패")
    h, w = bg_frame.shape[:2]

    while True:
        ret_bg, bg_frame = bg_cap.read()
        ret_subj, subj_frame = subj_cap.read()

        if not ret_bg:
            bg_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            continue
        if not ret_subj:
            subj_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            continue

        # 배경 처리: 어둡고 흐릿
        bg_dimmed = (bg_frame * 0.5).astype(np.uint8)
        bg_blurred = cv2.GaussianBlur(bg_dimmed, (5, 5), 2)
        bg_blurred = clamp_pure_black(bg_blurred)

        # 피사체 스케일
        subj_h, subj_w = subj_frame.shape[:2]
        new_w = int(subj_w * scale)
        new_h = int(subj_h * scale)
        new_w = max(32, min(w * 2, new_w))
        new_h = max(32, min(h * 2, new_h))
        subj_scaled = cv2.resize(subj_frame, (new_w, new_h))

        # Position: 중앙 기준 + offset
        center_x = (w - new_w) // 2
        center_y = (h - new_h) // 2
        offset_x = int(w * pos_x / 200)
        offset_y = int(h * pos_y / 200)
        x = max(0, min(center_x + offset_x, w - new_w))
        y = max(0, min(center_y + offset_y, h - new_h))

        # 알파 블렌딩 (RGBA 피사체)
        result = bg_blurred.copy()
        if subj_scaled.shape[2] == 4:
            fg_rgb = subj_scaled[:, :, :3]
            alpha = subj_scaled[:, :, 3] / 255.0
            roi = result[y : y + new_h, x : x + new_w]
            result[y : y + new_h, x : x + new_w] = (
                (alpha[:, :, np.newaxis] * fg_rgb + (1 - alpha[:, :, np.newaxis]) * roi)
            ).astype(np.uint8)
        else:
            result[y : y + new_h, x : x + new_w] = subj_scaled

        result = clamp_pure_black(result)
        cv2.imshow("Eternal Beam", result)

        if cv2.waitKey(33) & 0xFF == 27:
            break

    bg_cap.release()
    subj_cap.release()
    cv2.destroyAllWindows()


def on_nfc_detected(nfc_data: dict) -> None:
    """NFC 슬롯 인식 시 호출 (예시)"""
    content_id = nfc_data.get("content_id")
    if not content_id:
        print("Error: No content_id in NFC data")
        return
    print(f"Content_ID: {content_id}")
    content = fetch_content(content_id)
    play_layered_content(content)


if __name__ == "__main__":
    # 테스트용: content_id 인자 또는 기본값
    test_content_id = sys.argv[1] if len(sys.argv) > 1 else "your-test-content-id"
    try:
        content = fetch_content(test_content_id)
        play_layered_content(content)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
