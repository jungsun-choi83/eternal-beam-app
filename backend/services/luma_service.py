"""
Luma AI Dream Machine API - Image-to-Video
- 사용자 사진 → 강아지 모션 영상 자동 생성
- 환경변수: LUMA_API_KEY
- 프롬프트: 이미지 주요 색상 분석 → 블랙탄 여부에 따라 배경(흑/백) 동적 결정
"""

import io
import os
import asyncio
import tempfile
from typing import Optional, Tuple

LUMA_API_KEY = os.getenv("LUMA_API_KEY")
LUMA_API_BASE = "https://api.lumalabs.ai/dream-machine/v1"

# Dual I2V prompts (same keyframe URL; used by /generate-pet-video)
LUMA_PROMPT_IDLE = (
    "The dog is sitting calmly, looking at the camera, breathing naturally, "
    "subtle motion only."
)
LUMA_PROMPT_ACTION = (
    "The dog suddenly gets up and runs playfully towards the camera."
)

# 배경 결정 임계값: 평균 luminance < 이 값이면 블랙탄(검정색 강아지) → 흰 배경
LUMINANCE_THRESHOLD_BLACK_DOG = 80

try:
    import requests
except ImportError:
    requests = None

try:
    from PIL import Image
except ImportError:
    Image = None


def analyze_image_dominant_luminance(image_bytes: bytes) -> float:
    """
    이미지의 주된 밝기(평균 luminance) 반환.
    중앙 60% 영역을 우선 분석 (피사체가 중앙에 있다고 가정).

    Returns:
        평균 luminance (0~255). 낮을수록 어두운 이미지.
    """
    if not Image:
        return 150.0  # PIL 없으면 기본값(밝은쪽) → Case A
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        w, h = img.size
        # 중앙 60% 크롭 (피사체가 중앙에 있다고 가정)
        pad_w, pad_h = int(w * 0.2), int(h * 0.2)
        box = (pad_w, pad_h, w - pad_w, h - pad_h)
        if box[2] > box[0] and box[3] > box[1]:
            img = img.crop(box)
        img = img.resize((80, 80))
        pixels = list(img.getdata())
        if not pixels:
            return 150.0
        total = sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in pixels)
        return total / len(pixels)
    except Exception:
        return 150.0


def is_black_tan_dog(image_bytes: bytes) -> bool:
    """
    이미지가 블랙탄/검정색 강아지 위주인지 판별.

    Returns:
        True → 흰 배경 (Case B), False → 검정 배경 (Case A)
    """
    avg_lum = analyze_image_dominant_luminance(image_bytes)
    return avg_lum < LUMINANCE_THRESHOLD_BLACK_DOG


def build_luma_prompt(
    image_bytes: bytes,
    dog_breed: str = "dog",
) -> str:
    """
    업로드 이미지 색상 분석 → 배경 결정 → 최종 Luma API용 프롬프트 생성.

    - Case A (일반/밝은색): "on a solid black background"
    - Case B (블랙탄/검정색): "on a solid white background"
    """
    base_prompt = (
        f"A photorealistic {dog_breed} from the uploaded photo, "
        "breathing naturally, wagging its tail gently, looking at the camera, "
        "cinematic 3D depth, extreme detail on fur, studio lighting, high contrast"
    )
    if is_black_tan_dog(image_bytes):
        background_suffix = "on a solid white background"
    else:
        background_suffix = "on a solid black background"
    return f"{base_prompt}, {background_suffix}"


async def create_generation(
    image_url: str,
    prompt: str = "A cute dog sitting calmly, gentle breathing, subtle movement, natural pose, cinematic lighting",
    model: str = "ray-2",
    resolution: str = "720p",
) -> str:
    """
    Luma Dream Machine Image-to-Video 생성 요청.

    Args:
        image_url: 공개 접근 가능한 이미지 URL (Supabase 등)
        prompt: 모션 설명
        model: ray-2 | ray-flash-2
        resolution: 540p | 720p | 1080p | 4k

    Returns:
        generation_id (폴링용)
    """
    key = (os.getenv("LUMA_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("LUMA_API_KEY가 설정되지 않았습니다.")
    if not requests:
        raise RuntimeError("requests 패키지가 필요합니다: pip install requests")

    payload = {
        "prompt": prompt,
        "model": model,
        "resolution": resolution,
        "keyframes": {
            "frame0": {
                "type": "image",
                "url": image_url,
            }
        },
    }

    def _post():
        r = requests.post(
            f"{LUMA_API_BASE}/generations",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
            timeout=30,
        )
        if not r.ok:
            detail = (r.text or "")[:1200]
            raise RuntimeError(
                f"Luma API HTTP {r.status_code} for POST /generations. "
                f"Body: {detail}"
            )
        return r.json()

    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _post)
    gen_id = data.get("id")
    if not gen_id:
        raise RuntimeError(f"Luma API 응답에 id가 없습니다: {data}")
    return gen_id


async def poll_until_complete(
    generation_id: str,
    poll_interval: float = 5.0,
    max_wait: float = 300.0,
) -> str:
    """
    생성 완료까지 폴링.

    Returns:
        video_url (다운로드용)
    """
    key = (os.getenv("LUMA_API_KEY") or "").strip()
    if not key or not requests:
        raise RuntimeError("LUMA_API_KEY 및 requests 필요")

    waited = 0.0

    def _get():
        r = requests.get(
            f"{LUMA_API_BASE}/generations/{generation_id}",
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
            },
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    loop = asyncio.get_event_loop()

    while waited < max_wait:
        data = await loop.run_in_executor(None, _get)
        state = (data.get("state") or "").lower()

        if state == "completed":
            assets = data.get("assets") or {}
            video_url = assets.get("video")
            if video_url:
                return video_url
            raise RuntimeError(f"Luma 완료되었으나 video URL 없음: {data}")

        if state in ("failed", "error"):
            reason = data.get("failure_reason") or data.get("failureReason") or "Unknown"
            raise RuntimeError(f"Luma 생성 실패: {reason}")

        await asyncio.sleep(poll_interval)
        waited += poll_interval

    raise RuntimeError(f"Luma 타임아웃 ({max_wait}초 초과)")


def build_minimal_idle_action_prompts() -> Tuple[str, str]:
    """Short prompts when full prompts + image keep failing moderation."""
    idle = "A dog sitting calmly, subtle motion, looking at the camera. Studio shot."
    action = "A dog runs playfully toward the camera. Studio shot."
    return idle, action


def build_idle_action_prompts(image_bytes: bytes) -> Tuple[str, str]:
    """
    Append solid background hint from luminance (black-tan dog -> white bg) for Luma quality.
    """
    suffix = (
        " on a solid white background, photorealistic, cinematic lighting."
        if is_black_tan_dog(image_bytes)
        else " on a solid black background, photorealistic, cinematic lighting."
    )
    idle = f"{LUMA_PROMPT_IDLE}{suffix}"
    action = f"{LUMA_PROMPT_ACTION}{suffix}"
    return idle, action


async def create_generation_and_get_video_url(
    image_url: str,
    prompt: str,
    model: str = "ray-2",
    resolution: str = "720p",
    poll_interval: float = 5.0,
    poll_max_wait: float = float(os.getenv("LUMA_POLL_MAX_SEC", "1200")),
) -> str:
    """
    Luma I2V: create job, poll until complete, return hosted video URL (no local download).
    """
    gen_id = await create_generation(
        image_url,
        prompt=prompt,
        model=model,
        resolution=resolution,
    )
    return await poll_until_complete(
        gen_id,
        poll_interval=poll_interval,
        max_wait=poll_max_wait,
    )


async def download_video(url: str) -> str:
    """비디오 URL 다운로드 → 임시 파일 경로 반환."""
    if not requests:
        raise RuntimeError("requests 필요")

    def _dl():
        r = requests.get(url, stream=True, timeout=60)
        r.raise_for_status()
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        for chunk in r.iter_content(chunk_size=8192):
            tmp.write(chunk)
        tmp.close()
        return tmp.name

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _dl)


async def image_to_video_full(
    image_url: str,
    prompt: Optional[str] = None,
    output_path: Optional[str] = None,
) -> str:
    """
    전체 파이프라인: Luma I2V → 비디오 다운로드.

    Returns:
        로컬 MP4 파일 경로
    """
    gen_id = await create_generation(
        image_url,
        prompt=prompt or "A cute dog sitting calmly, gentle breathing, subtle movement, natural pose",
    )
    video_url = await poll_until_complete(gen_id)
    tmp_path = await download_video(video_url)

    if output_path:
        import shutil
        shutil.move(tmp_path, output_path)
        return output_path
    return tmp_path
