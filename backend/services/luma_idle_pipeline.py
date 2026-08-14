"""
Luma 아이들(Idle) 5종 세트 생성 파이프라인.

SAM2 누끼(vitmatte_service) → Luma 영상 생성(template별) → 검증(mp4 확장자 +
블랙 배경 + SSIM vs reference) → 실패 시 재시도 → seamless loop.

에러 방지:
  - Luma가 반환한 video URL의 확장자가 .mp4가 아니면 실패로 간주하고 재시도.
  - 생성된 영상의 첫 프레임 모서리 4곳 평균 밝기가 임계값보다 높으면(배경이
    검정이 아님) 프롬프트에 블랙 배경 보강 문구를 추가해 재시도.
  - 첫 프레임 vs reference cutout SSIM이 낮으면(다른 반려견/큰 포즈 변화) 재시도.
  - ffmpeg가 없거나 프레임 추출에 실패하면 검증을 건너뛰고(경고만) 통과시켜
    파이프라인 전체가 멈추지 않게 한다.
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .idle_validation_service import IdleValidationResult, validate_idle_video
from .luma_idle_templates import build_idle_variant_prompt
from .luma_service import download_video  # HTTP GET — 프로바이더 무관
from .video_generation import (
    create_generation_and_get_video_url,
    is_luma,
    looks_like_video_url,
    sniff_video_container,
)

# 모서리 평균 밝기(0~255)가 이 값 이하면 "블랙 배경"으로 판정.
BLACK_BG_LUMINANCE_THRESHOLD = float(os.getenv("LUMA_IDLE_BLACK_BG_THRESHOLD", "42"))


def _validation_enabled() -> bool:
    return os.getenv("IDLE_VALIDATION_ENABLED", "true").lower() in ("1", "true", "yes")


def _black_bg_check_enabled() -> bool:
    """
    블랙 배경 검증(ffmpeg로 첫 프레임 추출) on/off. 기본 off.

    2026-07-21: 라이브 Render(512MB, generate-pet-video/generate-idle-variant가
    같은 프로세스·메모리를 공유)에서 실측 — generate-pet-video는 연속 성공했지만
    generate-idle-variant만 반복적으로 컨테이너를 죽이는(재시작 후 /health까지
    503) 현상을 확인. generate-idle-variant에만 있고 generate-pet-video에는
    없는 유일한 무거운 동작이 이 ffmpeg 프레임 추출(subprocess)이라 이게
    OOM의 남은 원인일 가능성이 큼. check_black_background()는 검증 불가 시
    이미 (True, None)로 통과시키도록 설계되어 있어(품질 체크일 뿐 필수 아님)
    기본 off로 꺼도 파이프라인 동작에는 영향 없음(재시도 트리거만 못 함).
    """
    return os.getenv("LUMA_IDLE_BLACK_BG_CHECK_ENABLED", "false").lower() in ("1", "true", "yes")


@dataclass
class IdleVariantResult:
    template_key: str
    prompt: str
    remote_video_url: str
    video_bytes: bytes
    is_black_background: bool
    background_luminance: Optional[float]
    is_mp4: bool
    retries_used: int
    validation: Optional[IdleValidationResult] = None
    validation_history: list[dict[str, Any]] = field(default_factory=list)


def _looks_like_mp4_url(url: str) -> bool:
    """URL 경로(쿼리스트링 제외)가 .mp4로 끝나는지 확인."""
    path = (url or "").split("?", 1)[0].split("#", 1)[0]
    return path.lower().endswith(".mp4")


def _extract_first_frame_png(video_bytes: bytes) -> Optional[bytes]:
    """ffmpeg로 영상의 첫 프레임을 PNG bytes로 추출. 실패하면 None."""
    with tempfile.TemporaryDirectory(prefix="eb_idle_bgcheck_") as td:
        inp = Path(td) / "in.mp4"
        out = Path(td) / "frame.png"
        inp.write_bytes(video_bytes)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", str(inp),
                    "-ss", "0.15", "-frames:v", "1",
                    str(out),
                ],
                capture_output=True,
                timeout=30,
                check=True,
            )
        except Exception:
            return None
        if not out.is_file():
            return None
        return out.read_bytes()


def check_black_background(
    video_bytes: bytes, *, threshold: float = BLACK_BG_LUMINANCE_THRESHOLD
) -> tuple[bool, Optional[float]]:
    """
    영상 첫 프레임의 네 모서리(피사체가 중앙에 있다고 가정) 평균 밝기로 블랙
    배경 여부 판정. 검증 불가(ffmpeg/PIL 없음 등) 시 (True, None)로 통과시킴.
    LUMA_IDLE_BLACK_BG_CHECK_ENABLED=1이 아니면 ffmpeg 프레임 추출 자체를
    건너뛰고 (True, None) 통과 — Render 512MB에서 이 ffmpeg subprocess가
    컨테이너를 OOM으로 죽이는 사례 확인.
    """
    if not _black_bg_check_enabled():
        return True, None
    frame_png = _extract_first_frame_png(video_bytes)
    if not frame_png:
        return True, None
    try:
        from PIL import Image
    except Exception:
        return True, None

    try:
        img = Image.open(io.BytesIO(frame_png)).convert("RGB")
        w, h = img.size
        pad_w, pad_h = max(1, int(w * 0.06)), max(1, int(h * 0.06))
        corners = [
            img.crop((0, 0, pad_w, pad_h)),
            img.crop((w - pad_w, 0, w, pad_h)),
            img.crop((0, h - pad_h, pad_w, h)),
            img.crop((w - pad_w, h - pad_h, w, h)),
        ]
        lums: list[float] = []
        for c in corners:
            pixels = list(c.getdata())
            if not pixels:
                continue
            total = sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in pixels)
            lums.append(total / len(pixels))
        if not lums:
            return True, None
        avg = sum(lums) / len(lums)
        return avg <= threshold, avg
    except Exception:
        return True, None


async def generate_idle_variant(
    image_url: str,
    template_key: str,
    *,
    reference_image_bytes: Optional[bytes] = None,
    max_retries: int = 2,
    poll_max_wait: Optional[float] = None,
) -> IdleVariantResult:
    """
    템플릿 1개에 대해: Luma 생성 → 다운로드 → mp4/블랙배경/SSIM 검증 → 실패 시
    보강 프롬프트로 재시도(최대 max_retries회) → 최종 결과 반환.
    """
    wait = poll_max_wait if poll_max_wait is not None else float(
        os.getenv("LUMA_POLL_MAX_SEC", "1200")
    )

    attempt = 0
    last_error: Optional[str] = None
    validation_history: list[dict[str, Any]] = []

    while attempt <= max_retries:
        retry_boost = attempt > 0
        prompt = build_idle_variant_prompt(template_key, retry_boost=retry_boost)

        try:
            remote_url = await create_generation_and_get_video_url(
                image_url, prompt, poll_max_wait=wait
            )
        except Exception as e:
            last_error = str(e)
            attempt += 1
            continue

        # Luma 는 예전과 똑같이 URL 의 .mp4 접미사로 판정한다.
        # 다른 프로바이더(fal 등)는 확장자 없는 CDN URL 을 주는 게 정상이라
        # 접미사만으로 실패 처리하면 성공한 유료 생성을 버리고 재생성이 돈다.
        is_mp4 = looks_like_video_url(remote_url)

        local_path = await download_video(remote_url)
        try:
            with open(local_path, "rb") as f:
                video_bytes = f.read()
        finally:
            try:
                os.unlink(local_path)
            except Exception:
                pass

        # 비-Luma 프로바이더에서 URL 로 판정하지 못했을 때만 실제 바이트로 확인.
        # Luma 경로는 예전 그대로 URL 판정 결과를 유지한다(재시도 동작 보존).
        if not is_mp4 and not is_luma():
            is_mp4 = sniff_video_container(video_bytes) is not None

        is_black, luminance = check_black_background(video_bytes)

        validation: Optional[IdleValidationResult] = None
        validation_ok = True
        if _validation_enabled() and reference_image_bytes:
            validation = validate_idle_video(
                video_bytes,
                reference_image_bytes,
                template_key=template_key,
            )
            validation_history.append(validation.to_dict())
            validation_ok = validation.passed

        if is_mp4 and is_black and validation_ok:
            return IdleVariantResult(
                template_key=template_key,
                prompt=prompt,
                remote_video_url=remote_url,
                video_bytes=video_bytes,
                is_black_background=is_black,
                background_luminance=luminance,
                is_mp4=is_mp4,
                retries_used=attempt,
                validation=validation,
                validation_history=validation_history,
            )

        if attempt >= max_retries:
            # 재시도 소진 — 마지막 결과를 그대로 반환(호출자가 진단 필드로 판단).
            return IdleVariantResult(
                template_key=template_key,
                prompt=prompt,
                remote_video_url=remote_url,
                video_bytes=video_bytes,
                is_black_background=is_black,
                background_luminance=luminance,
                is_mp4=is_mp4,
                retries_used=attempt,
                validation=validation,
                validation_history=validation_history,
            )

        attempt += 1

    raise RuntimeError(
        f"Luma 아이들 생성 실패({template_key}), {max_retries}회 재시도 후에도 실패: "
        f"{last_error}"
    )
