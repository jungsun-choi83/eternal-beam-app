"""
Eternal Beam — 소비자 사진 → 홀로그램 자동 생성 파이프라인

[사용법]
  python python/generate_hologram.py --image 사진경로.jpg --name 이름

[처리 순서]
  1. 사진 → rembg 누끼 추출 (투명 PNG)
  2. 누끼 이미지 → Runway idle 영상 생성
  3. 누끼 이미지 → Runway action 영상 생성
  4. 두 영상 → 저장 (python/composited/ 폴더)

[출력]
  python/composited/no_slot_idle.mp4   (기본 idle)
  python/composited/no_slot_action.mp4 (손 감지 action)
"""

import os
import sys
import time
import base64
import argparse
import requests
import numpy as np
from io import BytesIO
from pathlib import Path
from PIL import Image
from dotenv import load_dotenv
from rembg import remove, new_session

load_dotenv()

# ── 설정 ──────────────────────────────────────────────────────────
BASE_URL       = "https://api.dev.runwayml.com/v1"
RUNWAY_API_KEY = os.environ.get("RUNWAY_API_KEY", "")
REMBG_MODEL    = "birefnet-general"   # 고품질 누끼 모델
OUT_DIR        = Path(__file__).resolve().parent / "composited"
OUT_DIR.mkdir(exist_ok=True)

# Runway 프롬프트
IDLE_PROMPT = (
    "The dog stands still, gently breathing, tail wagging slowly, "
    "subtle natural idle motion, pure black background, cinematic 4K"
)
ACTION_PROMPT = (
    "The dog runs and jumps toward the camera with excitement and joy, "
    "dynamic running motion getting closer, pure black background, cinematic 4K"
)


# ── 1단계: rembg 누끼 추출 ────────────────────────────────────────
def fit_to_9x16(img: Image.Image) -> Image.Image:
    """
    어떤 비율 사진이든 → 9:16 세로형으로 자동 변환
    - 사진은 중앙 크롭 후 패딩으로 9:16 맞춤
    - 배경은 검정색 (DLP 투명 처리)
    """
    TARGET_W, TARGET_H = 768, 1368   # 9:16

    orig_w, orig_h = img.size
    orig_ratio = orig_w / orig_h
    target_ratio = TARGET_W / TARGET_H

    # 1. 중앙 크롭 (비율 맞추기)
    if orig_ratio > target_ratio:
        # 가로가 더 넓음 → 좌우 크롭
        new_w = int(orig_h * target_ratio)
        left = (orig_w - new_w) // 2
        img = img.crop((left, 0, left + new_w, orig_h))
    else:
        # 세로가 더 길음 → 상하 크롭 (얼굴/피사체 위쪽 기준)
        new_h = int(orig_w / target_ratio)
        top = 0   # 위에서부터 크롭 (얼굴 살리기)
        img = img.crop((0, top, orig_w, top + new_h))

    # 2. 목표 해상도로 리사이즈
    img = img.resize((TARGET_W, TARGET_H), Image.LANCZOS)
    return img


def remove_background(image_path: Path, session) -> bytes:
    """이미지에서 배경 제거 → 9:16 투명 PNG bytes 반환"""
    print(f"\n[1단계] 누끼 추출 중... ({image_path.name})")

    img = Image.open(image_path).convert("RGBA")

    # 9:16 비율로 자동 변환 (Runway 비율 불일치 방지)
    img_fitted = fit_to_9x16(img)
    print(f"  원본 크기: {img.size} → 9:16 변환: {img_fitted.size}")

    result = remove(img_fitted, session=session)

    # 투명 PNG로 저장
    buf = BytesIO()
    result.save(buf, format="PNG")
    buf.seek(0)

    # 미리보기 저장
    preview_path = image_path.parent / f"{image_path.stem}_nobg.png"
    result.save(preview_path)
    print(f"  누끼 미리보기 저장: {preview_path}")

    return buf.read()


# ── 2단계: Runway 영상 생성 ──────────────────────────────────────
def encode_to_data_uri(img_bytes: bytes) -> str:
    b64 = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def create_runway_job(img_bytes: bytes, prompt: str, label: str) -> str:
    """Runway에 영상 생성 요청 → job_id 반환"""
    print(f"\n[2단계] Runway {label} 영상 생성 요청 중...")
    
    headers = {
        "Authorization": f"Bearer {RUNWAY_API_KEY}",
        "Content-Type": "application/json",
        "X-Runway-Version": "2024-11-06",
    }
    payload = {
        "model":       "gen3a_turbo",
        "promptText":  prompt,
        "promptImage": encode_to_data_uri(img_bytes),
        "duration":    5,
        "ratio":       "768:1280",
        "seed":        42,
    }
    
    resp = requests.post(f"{BASE_URL}/image_to_video", json=payload, headers=headers)
    
    if resp.status_code != 200:
        print(f"  오류: {resp.status_code} {resp.text}")
        resp.raise_for_status()
    
    job_id = resp.json()["id"]
    print(f"  job_id: {job_id}")
    return job_id


def wait_and_download(job_id: str, out_path: Path, label: str):
    """Runway 작업 완료 대기 → 영상 다운로드"""
    print(f"\n[3단계] {label} 영상 완료 대기 중...")
    headers = {
        "Authorization": f"Bearer {RUNWAY_API_KEY}",
        "X-Runway-Version": "2024-11-06",
    }
    
    dots = 0
    while True:
        resp = requests.get(f"{BASE_URL}/tasks/{job_id}", headers=headers)
        resp.raise_for_status()
        data   = resp.json()
        status = data["status"]
        
        if status == "SUCCEEDED":
            url = data["output"][0]
            if isinstance(url, dict):
                url = url.get("url", "")
            print(f"\n  완료! 다운로드 중...")
            r = requests.get(url, stream=True)
            r.raise_for_status()
            with out_path.open("wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            print(f"  저장: {out_path}")
            return
        
        if status == "FAILED":
            raise RuntimeError(f"Runway 생성 실패: {data}")
        
        dots += 1
        print(f"  대기 중{'.' * (dots % 4)}   ", end="\r")
        time.sleep(5)


# ── 메인 파이프라인 ──────────────────────────────────────────────
def run_pipeline(image_path: Path):
    if not RUNWAY_API_KEY:
        print("❌ .env 파일에 RUNWAY_API_KEY가 없습니다.")
        sys.exit(1)
    
    if not image_path.exists():
        print(f"❌ 이미지 파일 없음: {image_path}")
        sys.exit(1)
    
    print("=" * 50)
    print(f"  Eternal Beam 홀로그램 생성 파이프라인")
    print(f"  입력: {image_path.name}")
    print("=" * 50)
    
    # 1단계: 누끼 추출
    print(f"\nrembg 모델 로드 중... ({REMBG_MODEL})")
    session  = new_session(REMBG_MODEL)
    img_bytes = remove_background(image_path, session)
    print("  누끼 추출 완료 ✅")
    
    # 2단계: Runway idle + action 순차 생성
    idle_job_id   = create_runway_job(img_bytes, IDLE_PROMPT,   "idle")
    action_job_id = create_runway_job(img_bytes, ACTION_PROMPT, "action")
    
    # 3단계: 결과 다운로드
    idle_out   = OUT_DIR / "no_slot_idle.mp4"
    action_out = OUT_DIR / "no_slot_action.mp4"
    
    wait_and_download(idle_job_id,   idle_out,   "idle")
    wait_and_download(action_job_id, action_out, "action")
    
    print("\n" + "=" * 50)
    print("  완료!")
    print(f"  idle   → {idle_out}")
    print(f"  action → {action_out}")
    print("=" * 50)
    print("\n다음 단계:")
    print("  Unity의 Videos 폴더에 두 파일을 드래그해서 교체하세요!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Eternal Beam 홀로그램 생성")
    parser.add_argument("--image", required=True, help="입력 사진 경로 (jpg/png)")
    args = parser.parse_args()
    
    run_pipeline(Path(args.image))
