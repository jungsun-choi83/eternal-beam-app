"""
FFmpeg 합성: RGBA 기반. 검정 배경/glow 제거.
- subject_only: RGBA 출력 (Alpha 그대로, 투명 배경)
- compose_video: 테마 + 피사체 (glow 없음)
"""

import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

# 테마 ID → 로컬 MP4 경로 (없으면 그라데이션 정지화로 생성)
THEMES_DIR = os.getenv("THEMES_VIDEO_DIR", "")
if not THEMES_DIR and __file__:
    THEMES_DIR = str(Path(__file__).resolve().parent.parent / "themes")


def _get_theme_video_path(theme_id: str) -> Optional[str]:
    for ext in (".mp4", ".webm", ".mov"):
        p = Path(THEMES_DIR) / f"{theme_id}{ext}"
        if p.exists():
            return str(p)
    return None


def _get_duration_sec(path: str) -> float:
    cmd = [
        "ffmpeg", "-y", "-i", path,
        "-f", "null", "-",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    import re
    m = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)", r.stderr or "")
    if m:
        h, mi, s, cs = map(int, m.groups())
        return h * 3600 + mi * 60 + s + cs / 100.0
    return 10.0


def run_ffmpeg(args: list, cwd: Optional[str] = None) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-y"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패: {proc.stderr[-2000:] if proc.stderr else proc.returncode}")


def compose_video(
    theme_video_path: str,
    cutout_png_path: str,
    output_path: str,
    max_height: int = 720,
    duration_sec: Optional[float] = None,
) -> str:
    """
    테마 영상 위에 누끼 이미지 오버레이 + Breathing(scale 1.0~1.05) + Soft Glow.
    - Layer 1: 테마 영상 (배경)
    - Layer 2 하단: 누끼 복제 → boxblur(10~20px) → eq=brightness → 은은한 화이트 후광
    - Layer 2 상단: 원본 누끼 (Breathing 동일 적용)
    - 후광과 원본을 합친 뒤 배경 위에 overlay. Breathing이 후광에도 동일 적용되어 일체감.
    """
    if duration_sec is None:
        duration_sec = _get_duration_sec(theme_video_path)

    # 세로 3:4 비율 — 기기 폭 18cm(180mm) Pepper's Ghost 세로형
    # max_height >= 1080 → 810×1080 / 그 외 → 720×960
    w = 810 if max_height >= 1080 else 720
    h = 1080 if max_height >= 1080 else 960
    w = (w // 2) * 2
    h = (h // 2) * 2

    fps = 30
    scale_w = (w // 2) * 2
    scale_h = (h // 2) * 2
    # Breathing: scale 1.0 ~ 1.05, 후광과 원본에 동일 적용
    zoompan = f"zoompan=z='min(1.05,1+0.025*sin(2*PI*on/60))':d=1:s={scale_w}x{scale_h}:fps={fps}"

    subject_v = os.path.join(tempfile.gettempdir(), f"subject_{int(time.time())}.mp4")
    try:
        # 1) 누끼 PNG → Breathing 애니메이션 영상 (이 스트림을 복제해 후광+원본으로 사용)
        run_ffmpeg([
            "-loop", "1", "-t", str(duration_sec), "-i", cutout_png_path,
            "-vf", f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=decrease,pad={scale_w}:{scale_h}:(ow-iw)/2:(oh-ih)/2,{zoompan}",
            "-pix_fmt", "yuva420p",
            "-c:v", "libx264", "-profile:v", "high", "-preset", "medium",
            subject_v,
        ])

        # 2) Soft Glow 필터 체인 + True Black + DLP 프로젝터
        # - [bg]에 비네팅(가장자리 어둡게) 적용 → 프로젝터 투사 시 기기 내부와 자연스럽게 녹아듦
        # - [1:v] split → [sub](원본) [gs](후광용)
        # - [gs] boxblur 10~20px → eq=brightness=0.5 → 은은한 화이트 후광
        # - [glow][sub] overlay → 원본이 후광 위에
        # - [bg][subject_with_glow] overlay → True Black curves (0~10→0) → 최종
        boxblur_radius = 15
        true_black = "curves=all='0/0 0.04/0 1/1'"  # 0~10 → Pure Black, DLP용
        # 비네팅: 가장자리를 살짝 어둡게 (angle=PI/5, x0/y0 중심)
        vignette = "vignette=angle=PI/5:mode=forward"
        run_ffmpeg([
            "-i", theme_video_path,
            "-i", subject_v,
            "-filter_complex",
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,{vignette}[bg];"
            f"[1:v]split=2[sub][gs];"
            f"[gs]boxblur={boxblur_radius}:{boxblur_radius},eq=brightness=0.5[glow];"
            f"[glow][sub]overlay=0:0:format=auto[subject_with_glow];"
            f"[bg][subject_with_glow]overlay=(W-w)/2:(H-h)/2:format=auto,{true_black}",
            "-t", str(duration_sec),
            "-c:v", "libx264", "-profile:v", "high", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-color_range", "pc", "-movflags", "+faststart",
            "-b:v", "2M", "-maxrate", "2.5M", "-bufsize", "4M",
            output_path,
        ])
    finally:
        if os.path.exists(subject_v):
            try:
                os.unlink(subject_v)
            except Exception:
                pass

    return output_path


def compose_subject_only_rgba(
    cutout_png_path: str,
    output_path: str,
    max_height: int = 720,
    duration_sec: float = 10.0,
    prores: bool = False,
) -> str:
    """
    피사체만 RGBA 출력 — 검정 배경 없음. Alpha 그대로 보존.
    광고 촬영용: Unity에서 texture alpha 그대로 사용.
    - prores=True: ProRes 4444 (yuva444p10le)
    - prores=False: MP4 yuva420p (기본)
    """
    w = 810 if max_height >= 1080 else 720
    h = 1080 if max_height >= 1080 else 960
    w = (w // 2) * 2
    h = (h // 2) * 2

    fps = 30
    scale_w = (w // 2) * 2
    scale_h = (h // 2) * 2
    zoompan = f"zoompan=z='min(1.05,1+0.025*sin(2*PI*on/60))':d=1:s={scale_w}x{scale_h}:fps={fps}"

    # RGBA만 출력 — 검정 배경 없음. Alpha 그대로 (투명 패딩).
    vf = (
        f"format=rgba,scale={scale_w}:{scale_h}:force_original_aspect_ratio=decrease,"
        f"pad={scale_w}:{scale_h}:(ow-iw)/2:(oh-ih)/2:color=#00000000,"
        f"{zoompan}"
    )

    if prores:
        run_ffmpeg([
            "-loop", "1", "-t", str(duration_sec), "-i", cutout_png_path,
            "-vf", vf,
            "-c:v", "prores_ks", "-profile:v", "4444",
            "-pix_fmt", "yuva444p10le",
            output_path,
        ])
    else:
        run_ffmpeg([
            "-loop", "1", "-t", str(duration_sec), "-i", cutout_png_path,
            "-vf", vf,
            "-c:v", "libx264", "-profile:v", "high", "-preset", "medium",
            "-pix_fmt", "yuva420p", "-movflags", "+faststart",
            "-b:v", "2M", "-maxrate", "2.5M", "-bufsize", "4M",
            output_path,
        ])

    return output_path


def compose_video_from_urls(
    theme_video_path_or_url: str,
    cutout_png_path: str,
    output_path: str,
    max_height: int = 720,
) -> str:
    """
    theme_video_path_or_url가 로컬 경로가 아니면 다운로드 후 사용.
    """
    if theme_video_path_or_url.startswith("http"):
        import urllib.request
        local_theme = os.path.join(tempfile.gettempdir(), f"theme_{int(time.time())}.mp4")
        urllib.request.urlretrieve(theme_video_path_or_url, local_theme)
        try:
            return compose_video(local_theme, cutout_png_path, output_path, max_height)
        finally:
            try:
                os.unlink(local_theme)
            except Exception:
                pass
    return compose_video(theme_video_path_or_url, cutout_png_path, output_path, max_height)
