"""
LivePortrait "액션 20종" 배치 파이프라인 — 2단계 (핵심 산출물).

흐름(영상 1건당): LivePortrait 추론(1단계) → SAM2 배경 강제 블랙(3단계)
→ ffmpeg로 800x480 리사이즈/인코딩 → Supabase Storage 업로드.

★ 처리 순서를 "LivePortrait 먼저, SAM2 배경 정리 나중"으로 정한 이유
LivePortrait Animals 모드는 flag_pasteback=False로 돌리므로(live_portrait_service
참고) 드라이빙 영상의 배경이 애초에 소스에 섞여 들어가지 않는다 — 즉 LivePortrait
출력 자체에 남는 "배경 문제"는 드라이빙 영상 배경이 아니라 LivePortrait 자체의
워핑/생성 과정에서 생기는 경계 아티팩트뿐이다. 그 아티팩트는 LivePortrait가 만들고
난 "이후"에만 존재하므로, SAM2 마스킹은 LivePortrait 추론 결과물에 대해 수행해야
실제로 지워야 할 노이즈를 지울 수 있다(추론 전 원본 사진에 먼저 SAM2를 돌려봐야
소용없음 — 그 노이즈는 아직 존재하지 않는 시점이라).

★ 부분 실패 정책
20개 중 일부가 실패해도(LivePortrait 크래시, ffmpeg 에러 등) 나머지는 계속 처리한다.
각 항목은 매니페스트에 success/error를 개별 기록하고, 실패한 항목은 output_url이
None으로 남는다 — 호출자(워커)가 이 매니페스트를 그대로 DB에 저장해 부분 성공 상태를
사용자/운영자가 볼 수 있게 한다.

출력 해상도: 800x480 고정, letterbox(패딩)로 원본 비율을 유지하면서 검정으로 채운다
(크롭이 아니라 패딩을 선택한 이유: 크롭은 액션에 따라 강아지 일부가 잘릴 위험이 있고,
디스플레이 하드웨어가 이미 800x480 페퍼스 고스트용으로 고정돼 있어 검정 여백은
그대로 "안 보이는 배경"이 되어 시각적으로 문제가 없다).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional, Union

from . import supabase_assets
from .live_portrait_postprocess import force_black_background
from .live_portrait_service import (
    LivePortraitIdentityParams,
    resolve_source_image_to_local_path,
    run_live_portrait_inference,
)

logger = logging.getLogger(__name__)

OUTPUT_WIDTH = 800
OUTPUT_HEIGHT = 480

ProgressCallback = Callable[[int, int, "ActionVideoResult"], None]
StageCallback = Callable[[str, str], None]  # (stage_name, action) — 세밀한 단계별 진행 로그용(선택)


@dataclass
class ActionVideoResult:
    action: str
    driving_video: str
    output_path: Optional[str] = None
    output_url: Optional[str] = None
    duration_sec: Optional[float] = None
    resolution: Optional[str] = None
    success: bool = False
    error: Optional[str] = None


def default_driving_videos_dir() -> Path:
    raw = os.getenv("LIVE_PORTRAIT_DRIVING_VIDEOS_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path(__file__).resolve().parent.parent / "assets" / "driving_videos"


def list_driving_videos(driving_videos_dir: Optional[Path] = None) -> list[Path]:
    """폴더 안의 *.mp4를 이름순으로 반환. 20개가 아니어도 경고만 하고 있는 만큼 처리."""
    d = driving_videos_dir or default_driving_videos_dir()
    if not d.is_dir():
        logger.warning("드라이빙 영상 폴더가 없습니다: %s", d)
        return []
    videos = sorted(d.glob("*.mp4"))
    if len(videos) == 0:
        logger.warning(
            "드라이빙 영상이 0개입니다(%s). backend/assets/driving_videos/README.md "
            "참고해서 실제 영상 파일을 넣어주세요.",
            d,
        )
    elif len(videos) != 20:
        logger.warning(
            "드라이빙 영상이 20개가 아니라 %d개입니다(%s) — 있는 만큼만 처리합니다.",
            len(videos),
            d,
        )
    return videos


def _probe_duration_sec(path: Path) -> Optional[float]:
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return float(proc.stdout.strip())
    except Exception:
        return None


def _resize_pad_to_target(input_path: Path, output_path: Path) -> None:
    """letterbox 패딩으로 정확히 800x480, 배경은 검정으로 채워 ffmpeg 재인코딩."""
    vf = (
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(input_path),
            "-vf", vf,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-movflags", "+faststart",
            str(output_path),
        ],
        capture_output=True, timeout=180, check=True,
    )


def run_live_portrait_batch(
    dog_image: Union[str, Path, bytes],
    *,
    driving_videos_dir: Optional[Path] = None,
    user_id: str = "anonymous",
    content_id: Optional[str] = None,
    identity_params: Optional[LivePortraitIdentityParams] = None,
    progress_cb: Optional[ProgressCallback] = None,
    stage_cb: Optional[StageCallback] = None,
    upload_to_supabase: bool = True,
    local_output_dir: Optional[Path] = None,
) -> list[ActionVideoResult]:
    """
    강아지 사진 1장 → 드라이빙 영상 폴더의 각 항목에 대해 LivePortrait+SAM2+리사이즈
    영상 생성 → (선택) Supabase 업로드. 반환: 항목별 결과 리스트(매니페스트).

    dog_image: URL 문자열 / 로컬 파일 경로 문자열 / bytes 모두 지원(로컬 테스트에서
    Supabase 업로드 없이 바로 파일 경로를 넘길 수 있음 — 예: 고야 사진 테스트 스크립트).

    stage_cb: (stage_name, action)를 각 세부 단계 시작 시 호출 — CLI 테스트 스크립트가
    "LivePortrait 추론 → SAM2 배경 강제 → 리사이즈/인코딩 → 업로드"를 실시간으로 찍는 데 사용
    (backend/scripts/test_live_portrait_goya.py 참고). 운영 워커는 안 써도 무방(progress_cb만
    써서 DB에 항목 단위 진행률만 남기면 충분).
    """

    def _stage(name: str, action: str) -> None:
        if stage_cb:
            try:
                stage_cb(name, action)
            except Exception:
                logger.exception("stage_cb 호출 실패(무시하고 계속)")

    cid = content_id or "batch"
    videos = list_driving_videos(driving_videos_dir)
    total = len(videos)
    results: list[ActionVideoResult] = []

    out_root = Path(local_output_dir) if local_output_dir else Path(
        tempfile.mkdtemp(prefix="eb_lp_batch_")
    )
    out_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="eb_lp_src_") as src_td:
        try:
            src_local = resolve_source_image_to_local_path(dog_image, workdir=Path(src_td))
        except Exception as e:
            raise RuntimeError(f"소스 이미지 로드 실패: {e}") from e

        for idx, driving_video in enumerate(videos, start=1):
            action = driving_video.stem
            result = ActionVideoResult(action=action, driving_video=str(driving_video))
            t0 = time.time()
            try:
                _stage("live_portrait_inference", action)
                raw_out = out_root / f"{action}.raw.mp4"
                run_live_portrait_inference(
                    src_local, driving_video, raw_out, params=identity_params
                )

                _stage("sam2_black_background", action)
                blacked_out = out_root / f"{action}.blacked.mp4"
                force_black_background(str(raw_out), str(blacked_out))

                _stage("ffmpeg_resize_encode", action)
                final_out = out_root / f"{action}.mp4"
                _resize_pad_to_target(blacked_out, final_out)

                result.output_path = str(final_out)
                result.duration_sec = _probe_duration_sec(final_out)
                result.resolution = f"{OUTPUT_WIDTH}x{OUTPUT_HEIGHT}"

                if upload_to_supabase:
                    _stage("supabase_upload", action)
                    import asyncio

                    data = final_out.read_bytes()
                    result.output_url = asyncio.run(
                        supabase_assets.upload_asset_to_storage(
                            f"{user_id}/{cid}/action_videos/{action}.mp4",
                            data,
                            "video/mp4",
                        )
                    )
                result.success = True
            except Exception as e:
                result.error = str(e)
                result.success = False
                logger.exception("액션 '%s' 처리 실패", action)
            finally:
                logger.info(
                    "[%d/%d] %s 처리 완료(%.1fs, success=%s)",
                    idx, total, action, time.time() - t0, result.success,
                )
                results.append(result)
                if progress_cb:
                    try:
                        progress_cb(idx, total, result)
                    except Exception:
                        logger.exception("progress_cb 호출 실패(무시하고 계속)")

    return results


def write_manifest_json(results: list[ActionVideoResult], manifest_path: Union[str, Path]) -> Path:
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
