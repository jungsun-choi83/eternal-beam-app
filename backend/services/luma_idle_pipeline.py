"""
Luma 아이들(Idle) 5종 세트 생성 파이프라인.

SAM2 누끼(vitmatte_service) → Luma 영상 생성(template별) → 검증(mp4 확장자 +
블랙 배경) → 실패 시 재시도 → seamless loop.

에러 방지:
  - Luma가 반환한 video URL의 확장자가 .mp4가 아니면 실패로 간주하고 재시도.
  - 생성된 영상의 첫 프레임 모서리 4곳 평균 밝기가 임계값보다 높으면(배경이
    검정이 아님) 프롬프트에 블랙 배경 보강 문구를 추가해 재시도.
  - ffmpeg가 없거나 프레임 추출에 실패하면 검증을 건너뛰고(경고만) 통과시켜
    파이프라인 전체가 멈추지 않게 한다.
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .luma_idle_templates import build_idle_variant_prompt
from .luma_service import create_generation_and_get_video_url, download_video

# 모서리 평균 밝기(0~255)가 이 값 이하면 "블랙 배경"으로 판정.
BLACK_BG_LUMINANCE_THRESHOLD = float(os.getenv("LUMA_IDLE_BLACK_BG_THRESHOLD", "42"))


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
    """
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
    max_retries: int = 2,
    poll_max_wait: Optional[float] = None,
) -> IdleVariantResult:
    """
    템플릿 1개에 대해: Luma 생성 → 다운로드 → mp4/블랙배경 검증 → 실패 시
    보강 프롬프트로 재시도(최대 max_retries회) → 최종 결과 반환.
    """
    wait = poll_max_wait if poll_max_wait is not None else float(
        os.getenv("LUMA_POLL_MAX_SEC", "1200")
    )

    attempt = 0
    last_error: Optional[str] = None
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

        is_mp4 = _looks_like_mp4_url(remote_url)

        local_path = await download_video(remote_url)
        try:
            with open(local_path, "rb") as f:
                video_bytes = f.read()
        finally:
            try:
                os.unlink(local_path)
            except Exception:
                pass

        is_black, luminance = check_black_background(video_bytes)

        if is_mp4 and is_black:
            return IdleVariantResult(
                template_key=template_key,
                prompt=prompt,
                remote_video_url=remote_url,
                video_bytes=video_bytes,
                is_black_background=is_black,
                background_luminance=luminance,
                is_mp4=is_mp4,
                retries_used=attempt,
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
            )

        attempt += 1

    raise RuntimeError(
        f"Luma 아이들 생성 실패({template_key}), {max_retries}회 재시도 후에도 실패: "
        f"{last_error}"
    )
