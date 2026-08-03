"""
"내 사진으로 나만의 배경 만들기"(custom_photo_bg) 전체 파이프라인 — 로컬 RTX 4090
워커(backend/workers/background_video_worker.py)가 호출하는 오케스트레이션 함수.

단계: 원본 사진 로드 → (1) SAM2 역마스크 + LaMa 인페인팅(배경_inpaint_service)
→ (2) 인페인팅된 배경 이미지를 Supabase에 업로드해 공개 URL 확보 → (3) Luma로
배경 앰비언트 모션 영상 생성(luma_service, 새 프롬프트는 luma_prompts.
build_background_ambient_prompt) → (4) seamless_loop_service.make_seamless_loop_mp4
를 블랙박스로 호출(내부 수정 없음 — 다른 에이전트가 Render OOM 이슈를 고치는 중인
바로 그 함수) → (5) background_video_sync로 fps/duration을 강아지 영상과 맞춤 →
(6) 최종 mp4를 Supabase Storage에 업로드.

부분 실패 정책: LivePortrait 배치와 달리 이 파이프라인은 산출물이 1개뿐이라
"부분 성공"이라는 개념이 없다 — 어느 단계든 실패하면 예외를 그대로 올려서
호출자(워커)가 status='failed'로 기록하게 한다.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

from . import background_video_sync, supabase_assets
from .background_inpaint_service import inpaint_background_from_photo
from .luma_prompts import build_background_ambient_prompt
from .luma_service import create_generation_and_get_video_url, download_video
from .seamless_loop_service import make_seamless_loop_mp4

logger = logging.getLogger(__name__)

StageCallback = Callable[[str, Optional[str]], None]  # (stage, detail)

MAX_LUMA_RETRIES = int(os.getenv("BACKGROUND_VIDEO_LUMA_MAX_RETRIES", "2"))


@dataclass
class BackgroundVideoResult:
    result_video_url: str
    result_meta: dict


def _looks_like_mp4_url(url: str) -> bool:
    path = (url or "").split("?", 1)[0].split("#", 1)[0]
    return path.lower().endswith(".mp4")


def _resolve_source_image_bytes(source: Union[str, Path, bytes]) -> bytes:
    """URL(http/https) / 로컬 파일 경로 / bytes 모두 지원.

    live_portrait_service.resolve_source_image_to_local_path()와 목적은 같지만,
    다른 에이전트가 소유한 파일을 import하지 않기 위해 이 파이프라인 전용으로
    독립적으로 작게 재구현했다(결합도를 낮춰 두 파이프라인이 서로 영향 없이
    바뀔 수 있게).
    """
    if isinstance(source, bytes):
        return source
    src = str(source)
    if src.startswith("http://") or src.startswith("https://"):
        import requests

        r = requests.get(src, timeout=60)
        r.raise_for_status()
        return r.content
    p = Path(src)
    if not p.is_file():
        raise RuntimeError(f"원본 이미지 파일을 찾을 수 없습니다: {src}")
    return p.read_bytes()


def run_background_video_pipeline(
    source_image: Union[str, Path, bytes],
    *,
    user_id: str = "anonymous",
    content_id: Optional[str] = None,
    target_fps: Optional[float] = None,
    target_duration_sec: Optional[float] = None,
    upload_to_supabase: bool = True,
    stage_cb: Optional[StageCallback] = None,
) -> BackgroundVideoResult:
    def _stage(name: str, detail: Optional[str] = None) -> None:
        logger.info("배경 파이프라인 단계: %s%s", name, f" ({detail})" if detail else "")
        if stage_cb:
            try:
                stage_cb(name, detail)
            except Exception:
                logger.exception("stage_cb 호출 실패(무시하고 계속)")

    cid = content_id or f"bg_{int(time.time())}"

    _stage("loading_source_image")
    raw = _resolve_source_image_bytes(source_image)

    _stage("inpainting")
    inpainted_png, inpaint_meta = inpaint_background_from_photo(raw)
    logger.info("인페인팅 완료: %s", inpaint_meta)

    _stage("uploading_inpainted_image")
    inpainted_url = _upload_or_data_url(
        inpainted_png, f"{user_id}/{cid}/background_source/inpainted.png", "image/png"
    )

    _stage("luma_generation")
    prompt = build_background_ambient_prompt()
    remote_video_url: Optional[str] = None
    last_error: Optional[str] = None
    attempt = 0
    while attempt <= MAX_LUMA_RETRIES:
        try:
            prompt_for_attempt = build_background_ambient_prompt(retry_boost=attempt > 0)
            remote_video_url = create_generation_and_get_video_url_sync(
                inpainted_url, prompt_for_attempt
            )
            if _looks_like_mp4_url(remote_video_url):
                break
        except Exception as e:
            last_error = str(e)
        attempt += 1
    if not remote_video_url or not _looks_like_mp4_url(remote_video_url):
        raise RuntimeError(
            f"Luma 배경 영상 생성 실패({MAX_LUMA_RETRIES}회 재시도 후에도 실패): {last_error}"
        )

    _stage("downloading_luma_video")
    local_path = download_video_sync(remote_video_url)
    try:
        with open(local_path, "rb") as f:
            luma_video_bytes = f.read()
    finally:
        try:
            os.unlink(local_path)
        except Exception:
            pass

    _stage("seamless_loop")
    # 블랙박스 호출 — seamless_loop_service.py 내부는 절대 건드리지 않음
    # (다른 에이전트가 Render 512MB 인스턴스 OOM 이슈를 조사/수정 중인 바로 그 함수).
    # 이 파이프라인은 로컬 RTX 4090 워커에서만 실행되므로 그 OOM 리스크 자체가
    # 적용되지 않지만, 함수 시그니처는 그대로 신뢰해서 호출만 한다.
    looped_bytes, loop_meta = make_seamless_loop_mp4(luma_video_bytes)

    _stage("syncing_fps_duration")
    synced_bytes = background_video_sync.sync_bytes_fps_duration(
        looped_bytes, fps=target_fps, duration_sec=target_duration_sec
    )

    _stage("uploading_final_video")
    result_url = _upload_or_data_url(
        synced_bytes, f"{user_id}/{cid}/background_video/ambient_bg.mp4", "video/mp4"
    )

    result_meta = {
        "inpaint_meta": inpaint_meta,
        "luma_prompt": prompt,
        "luma_video_url": remote_video_url,
        "loop_meta": loop_meta,
        "target_fps": target_fps if target_fps is not None else background_video_sync.target_fps(),
        "target_duration_sec": (
            target_duration_sec
            if target_duration_sec is not None
            else background_video_sync.target_duration_sec()
        ),
    }

    if not upload_to_supabase:
        # 로컬 테스트 편의용 — 실제 운영 경로(워커)는 항상 upload_to_supabase=True.
        with tempfile.TemporaryDirectory(prefix="eb_bg_local_") as td:
            out = Path(td) / "ambient_bg.mp4"
            out.write_bytes(synced_bytes)
            return BackgroundVideoResult(result_video_url=str(out), result_meta=result_meta)

    return BackgroundVideoResult(result_video_url=result_url, result_meta=result_meta)


def _upload_or_data_url(data: bytes, object_path: str, content_type: str) -> str:
    import asyncio

    return asyncio.run(
        supabase_assets.upload_asset_to_storage(object_path, data, content_type)
    )


def create_generation_and_get_video_url_sync(image_url: str, prompt: str) -> str:
    """luma_service의 async 함수를 워커(동기 프로세스)에서 쓰기 위한 얇은 동기 래퍼."""
    import asyncio

    return asyncio.run(create_generation_and_get_video_url(image_url, prompt))


def download_video_sync(url: str) -> str:
    import asyncio

    return asyncio.run(download_video(url))
