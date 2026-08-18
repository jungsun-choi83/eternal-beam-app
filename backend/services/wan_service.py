"""
fal.ai Wan 2.2 A14B Turbo Image-to-Video — 개발/테스트용 저가 프로바이더.

luma_service.py 와 같은 모양의 공개 함수를 제공한다(create_generation /
poll_until_complete / create_generation_and_get_video_url). 호출부는
video_generation.py 디스패처를 통해서만 이 모듈을 만나므로, fal 고유 응답
객체는 이 파일 밖으로 절대 새어 나가지 않는다.

★ 이 프로바이더는 임시 개발용이다. 프로덕션 품질 경로는 Luma 이며
  luma_service.py 는 이 작업으로 전혀 수정되지 않았다.

환경변수:
  FAL_KEY              필수 (또는 FAL_API_KEY)
  WAN_MODEL            기본 fal-ai/wan/v2.2-a14b/image-to-video/turbo
  WAN_RESOLUTION       기본 480p   (fal enum: 480p | 580p | 720p)
  WAN_ASPECT_RATIO     기본 9:16   (fal enum: auto | 16:9 | 9:16 | 1:1)
  WAN_QUEUE_BASE       기본 https://queue.fal.run
  WAN_POLL_MAX_SEC     기본 LUMA_POLL_MAX_SEC 와 동일한 1200

세로(portrait) 고정에 대하여:
  fal 의 aspect_ratio 기본값은 "auto" 이고, auto 는 입력 이미지에 따라 16:9
  가로 영상을 낼 수 있다. 현재 아이들 경로는 세로(Luma 는 720x1280)를 전제로
  하므로 기본값을 "9:16" 으로 못박는다 — 명시적으로 override 하지 않는 한
  가로 영상이 나오는 일은 없다.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_WAN_MODEL = "fal-ai/wan/v2.2-a14b/image-to-video/turbo"
DEFAULT_WAN_RESOLUTION = "480p"
DEFAULT_WAN_ASPECT_RATIO = "9:16"
DEFAULT_QUEUE_BASE = "https://queue.fal.run"

# fal 큐 상태값. COMPLETED 외에는 계속 대기하고, 명시적 실패는 즉시 예외.
_STATUS_COMPLETED = "COMPLETED"
_STATUS_FAILED = ("FAILED", "ERROR", "CANCELLED")

try:
    import requests
except ImportError:  # pragma: no cover - luma_service 와 동일한 방어
    requests = None


@dataclass
class WanSubmission:
    """fal 큐 제출 결과. status/response URL 은 fal 이 준 값을 그대로 쓴다."""

    request_id: str
    status_url: str
    response_url: str


def _fal_key() -> str:
    return (os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or "").strip()


def model_name() -> str:
    return (os.getenv("WAN_MODEL") or DEFAULT_WAN_MODEL).strip()


def _resolution() -> str:
    return (os.getenv("WAN_RESOLUTION") or DEFAULT_WAN_RESOLUTION).strip()


def _aspect_ratio() -> str:
    return (os.getenv("WAN_ASPECT_RATIO") or DEFAULT_WAN_ASPECT_RATIO).strip()


def _queue_base() -> str:
    return (os.getenv("WAN_QUEUE_BASE") or DEFAULT_QUEUE_BASE).strip().rstrip("/")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Key {_fal_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _require_client() -> None:
    if not _fal_key():
        raise RuntimeError(
            "FAL_KEY가 설정되지 않았습니다 (VIDEO_PROVIDER=wan 사용 시 필수)."
        )
    if not requests:
        raise RuntimeError("requests 패키지가 필요합니다: pip install requests")


def build_input_payload(
    image_url: str,
    prompt: str,
    *,
    resolution: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
) -> dict[str, Any]:
    """
    fal Wan i2v turbo 입력 스키마.

    turbo 변형은 num_frames / frames_per_second 를 노출하지 않으므로 보내지 않는다.
    프롬프트는 파이프라인이 만든 문자열을 그대로 전달한다 — 프롬프트 생성 로직은
    이 작업에서 건드리지 않는다.
    """
    return {
        "image_url": image_url,
        "prompt": prompt,
        "resolution": (resolution or _resolution()),
        "aspect_ratio": (aspect_ratio or _aspect_ratio()),
    }


async def create_generation(
    image_url: str,
    prompt: str,
    *,
    model: Optional[str] = None,
    resolution: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    webhook_url: Optional[str] = None,
) -> WanSubmission:
    """
    fal 큐에 제출하고 request_id / status_url / response_url 을 돌려준다.

    webhook_url 을 주면 fal 큐의 `fal_webhook` 쿼리 파라미터로 붙는다 — 완료 시
    fal 이 그 주소로 POST 한다(비동기 System B 경로). 없으면 예전처럼 폴링용
    URL 만 돌려준다(동기 System A 경로는 그대로).
    """
    _require_client()
    mdl = (model or model_name()).strip().strip("/")
    payload = build_input_payload(
        image_url, prompt, resolution=resolution, aspect_ratio=aspect_ratio
    )
    url = f"{_queue_base()}/{mdl}"
    if webhook_url and webhook_url.strip():
        from urllib.parse import quote

        url = f"{url}?fal_webhook={quote(webhook_url.strip(), safe='')}"

    def _post() -> dict[str, Any]:
        r = requests.post(url, headers=_headers(), json=payload, timeout=60)
        if not r.ok:
            raise RuntimeError(
                f"fal API HTTP {r.status_code} for POST {mdl}. Body: {(r.text or '')[:1200]}"
            )
        return r.json()

    data = await asyncio.get_event_loop().run_in_executor(None, _post)

    request_id = data.get("request_id") or data.get("requestId") or ""
    if not request_id:
        raise RuntimeError(f"fal 응답에 request_id가 없습니다: {str(data)[:400]}")

    # fal 이 준 URL 을 그대로 쓴다 — 큐 경로 규칙이 바뀌어도 안전하다.
    status_url = data.get("status_url") or f"{_queue_base()}/{mdl}/requests/{request_id}/status"
    response_url = data.get("response_url") or f"{_queue_base()}/{mdl}/requests/{request_id}"
    return WanSubmission(request_id=request_id, status_url=status_url, response_url=response_url)


def extract_video_url(result: dict[str, Any]) -> str:
    """fal 결과 payload → 비디오 URL. fal 고유 구조는 여기서 끝난다."""
    video = result.get("video")
    if isinstance(video, dict):
        url = (video.get("url") or "").strip()
        if url:
            return url
    # 일부 모델은 videos[] 로 준다.
    videos = result.get("videos")
    if isinstance(videos, list) and videos:
        first = videos[0]
        if isinstance(first, dict) and (first.get("url") or "").strip():
            return str(first["url"]).strip()
    raise RuntimeError(f"fal 결과에 video URL이 없습니다: {str(result)[:400]}")


async def poll_until_complete(
    submission: WanSubmission,
    poll_interval: float = 5.0,
    max_wait: float = 1200.0,
) -> str:
    """COMPLETED 까지 대기 후 비디오 URL 반환."""
    _require_client()
    loop = asyncio.get_event_loop()
    waited = 0.0

    def _get(url: str) -> dict[str, Any]:
        r = requests.get(url, headers=_headers(), timeout=30)
        if not r.ok:
            raise RuntimeError(
                f"fal API HTTP {r.status_code} for GET. Body: {(r.text or '')[:600]}"
            )
        return r.json()

    while waited < max_wait:
        status_body = await loop.run_in_executor(None, _get, submission.status_url)
        status = str(status_body.get("status") or "").upper()

        if status == _STATUS_COMPLETED:
            result = await loop.run_in_executor(None, _get, submission.response_url)
            return extract_video_url(result)

        if status in _STATUS_FAILED:
            detail = status_body.get("error") or status_body.get("detail") or status
            raise RuntimeError(f"fal 생성 실패: {detail}")

        await asyncio.sleep(poll_interval)
        waited += poll_interval

    raise RuntimeError(f"fal 타임아웃 ({max_wait}초 초과, request_id={submission.request_id})")


async def create_generation_and_get_video_url(
    image_url: str,
    prompt: str,
    model: Optional[str] = None,
    resolution: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    poll_interval: float = 5.0,
    poll_max_wait: Optional[float] = None,
) -> str:
    """제출 → 큐 대기 → 비디오 URL. luma_service 의 동명 함수와 계약이 같다."""
    wait = (
        poll_max_wait
        if poll_max_wait is not None
        else float(os.getenv("WAN_POLL_MAX_SEC", os.getenv("LUMA_POLL_MAX_SEC", "1200")))
    )
    submission = await create_generation(
        image_url, prompt, model=model, resolution=resolution, aspect_ratio=aspect_ratio
    )
    logger.info(
        "wan: submitted request_id=%s model=%s resolution=%s aspect_ratio=%s",
        submission.request_id,
        (model or model_name()),
        (resolution or _resolution()),
        (aspect_ratio or _aspect_ratio()),
    )
    return await poll_until_complete(submission, poll_interval=poll_interval, max_wait=wait)


async def fetch_status(request_id: str, model: Optional[str] = None) -> dict[str, Any]:
    """
    단발 상태 조회 (대기하지 않는다) — 리컨사일러용.

    request_id + model 만으로 큐 URL 을 재구성한다. 제출 시 받은 status_url 을
    DB 에 따로 저장하지 않아도 되도록 하기 위함이다.

    Returns: {"status": <fal status>, "video_url": <완료 시>, "error": <실패 시>}
    """
    _require_client()
    mdl = (model or model_name()).strip().strip("/")
    base = f"{_queue_base()}/{mdl}/requests/{request_id}"
    loop = asyncio.get_event_loop()

    def _get(url: str) -> dict[str, Any]:
        r = requests.get(url, headers=_headers(), timeout=30)
        if not r.ok:
            raise RuntimeError(f"fal API HTTP {r.status_code} for GET {url}: {(r.text or '')[:400]}")
        return r.json()

    body = await loop.run_in_executor(None, _get, f"{base}/status")
    status = str(body.get("status") or "").upper()

    if status == _STATUS_COMPLETED:
        result = await loop.run_in_executor(None, _get, base)
        return {"status": status, "video_url": extract_video_url(result), "error": None}
    if status in _STATUS_FAILED:
        return {
            "status": status,
            "video_url": None,
            "error": str(body.get("error") or body.get("detail") or status),
        }
    return {"status": status, "video_url": None, "error": None}
