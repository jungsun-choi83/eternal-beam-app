#!/usr/bin/env python3
"""
Luma / packed 배경 → Unity BackgroundDepthStack 용 RGB 2종 (알파 없음)

Unity (EternalBeam/Assets/Scripts/BackgroundDepthStack.cs):
  light_rgb/{themeId}/background_forest.mp4  — 뒤 Quad (원경 숲)
  light_rgb/{themeId}/foreground_light.mp4  — 앞 Quad (빛내림·안개, Additive)

입력:
  - 일반 mp4/mov (RGB만)
  - Alpha Packed vstack (상단 RGB / 하단 마스크) → 상단만 자동 사용

사용:
  python unity_light_depth_prepare.py ^
    -i "C:\\...\\bgluma.mp4" ^
    -o "C:\\...\\EternalBeam_Demo\\assets\\Backgrounds\\light_rgb" ^
    --theme-id snow_forest

  # 초점(선명도) 기반 앞나무/뒤숲 (Luma 보케 영상):
  python unity_light_depth_prepare.py -i bgluma.mp4 -o ./out --mode focus --theme-id snow_forest
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from background_depth_split import split_depth_layers
from background_layer_split import resolve_ffmpeg, split_background_layers


def probe_height(ffmpeg_exe: str, path: Path) -> int:
    ffprobe = Path(ffmpeg_exe).with_name("ffprobe.exe")
    probe = str(ffprobe) if ffprobe.is_file() else "ffprobe"
    cmd = [
        probe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=height",
        "-of",
        "json",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        return 0
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or [{}]
    return int(streams[0].get("height") or 0)


def is_likely_packed(ffmpeg_exe: str, path: Path) -> bool:
    """파일명 또는 높이 2배 패턴으로 packed 추정."""
    name = path.name.lower()
    if "packed" in name or name.endswith("_pack.mp4"):
        return True
    h = probe_height(ffmpeg_exe, path)
    # packed는 보통 짝수 높이 (vstack 2배)
    return h >= 4 and h % 2 == 0


def extract_rgb_from_packed(
    ffmpeg_exe: str,
    packed_mp4: Path,
    out_mp4: Path,
) -> Path:
    """vstack packed → 상단 RGB만 잘라 일반 mp4."""
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    vf = "crop=iw:ih/2:0:0"
    cmd = [
        ffmpeg_exe,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(packed_mp4),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(out_mp4),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"packed RGB 추출 실패:\n{(proc.stderr or '')[-2000:]}")
    print(f"[prepare] packed → RGB: {out_mp4.name}")
    return out_mp4


def resolve_rgb_source(ffmpeg_exe: str, source: Path, work_dir: Path) -> Path:
    if not is_likely_packed(ffmpeg_exe, source):
        return source
    cached = work_dir / f"{source.stem}_rgb.mp4"
    if cached.is_file() and cached.stat().st_mtime >= source.stat().st_mtime:
        print(f"[prepare] 캐시 RGB 사용: {cached.name}")
        return cached
    return extract_rgb_from_packed(ffmpeg_exe, source, cached)


def prepare_light_depth(
    source: Path,
    output_root: Path,
    *,
    theme_id: str,
    mode: str = "light",
    ffmpeg_exe: str | None = None,
    work_dir: Path | None = None,
    **kwargs,
) -> dict[str, Path]:
    """
    mode:
      light — 루미넌스 빛내림 / 원경 숲 (BackgroundDepthStack 기본)
      focus — 선명도 near/far (Luma 보케 깊이)
    """
    ff = resolve_ffmpeg(ffmpeg_exe)
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)

    theme_dir = output_root.expanduser().resolve() / theme_id
    theme_dir.mkdir(parents=True, exist_ok=True)

    wd = work_dir or Path(tempfile.gettempdir()) / "eternal_beam_depth_prep"
    wd.mkdir(parents=True, exist_ok=True)
    rgb_src = resolve_rgb_source(ff, source, wd)

    if mode == "focus":
        result = split_depth_layers(
            rgb_src,
            theme_dir,
            ffmpeg_exe=ff,
            packed=False,
            preset=kwargs.get("preset", "fast"),
            crf=kwargs.get("crf", 20),
            name_suffix=kwargs.get("name_suffix", ""),
            near_percentile=kwargs.get("near_percentile", 70.0),
            far_percentile=kwargs.get("far_percentile", 42.0),
            gap_fill=kwargs.get("gap_fill", True),
        )
        # Unity BackgroundDepthStack 파일명에 맞게 복사/이름 변경
        fg = theme_dir / "foreground_light.mp4"
        bg = theme_dir / "background_forest.mp4"
        _link_or_copy(result.foreground_near, fg)
        _link_or_copy(result.background_far, bg)
        out = {"foreground_light": fg, "background_forest": bg}
    else:
        result = split_background_layers(
            rgb_src,
            theme_dir,
            ffmpeg_exe=ff,
            rgb_only=True,
            foreground_name="foreground_light.mp4",
            background_name="background_forest.mp4",
            luma_threshold=kwargs.get("luma_threshold", 0.72),
            highlight_blur=kwargs.get("highlight_blur", 2.5),
            bg_suppress=kwargs.get("bg_suppress", 0.88),
            fg_mode=kwargs.get("fg_mode", "godray"),
            preset=kwargs.get("preset", "fast"),
            crf=kwargs.get("crf", 20),
        )
        out = {
            "foreground_light": result.foreground_packed,
            "background_forest": result.background_packed,
        }

    print(f"[prepare] Unity → {theme_dir}")
    for k, p in out.items():
        print(f"  {k}: {p}")
    return out


def _link_or_copy(src: Path, dst: Path) -> None:
    if src.resolve() == dst.resolve():
        return
    if dst.exists():
        dst.unlink()
    try:
        import os

        os.link(src, dst)
    except OSError:
        import shutil

        shutil.copy2(src, dst)


def main() -> None:
    p = argparse.ArgumentParser(description="Unity BackgroundDepthStack용 RGB 전·후경 생성")
    p.add_argument("-i", "--input", required=True, help="bgluma.mp4 등 (packed 가능)")
    p.add_argument(
        "-o",
        "--output-root",
        required=True,
        help="light_rgb 루트 (예: .../Assets/Backgrounds/light_rgb)",
    )
    p.add_argument("--theme-id", default="default", help="하위 폴더명 (테마 ID)")
    p.add_argument(
        "--mode",
        choices=("light", "focus"),
        default="light",
        help="light=빛내림+숲(기본), focus=선명도 near/far (보케 영상)",
    )
    p.add_argument("--ffmpeg", default=None)
    p.add_argument("--preset", default="fast")
    p.add_argument("--crf", type=int, default=20)
    p.add_argument("--luma-threshold", type=float, default=0.72)
    p.add_argument("--highlight-blur", type=float, default=2.5)
    p.add_argument("--bg-suppress", type=float, default=0.88)
    p.add_argument(
        "--fg-mode",
        choices=("godray", "luma"),
        default="godray",
        help="light 모드: godray=안개/빛줄기, luma=밝은 픽셀",
    )
    p.add_argument("--work-dir", default=None, help="packed RGB 추출 캐시 폴더")
    args = p.parse_args()

    work = Path(args.work_dir) if args.work_dir else None
    out = prepare_light_depth(
        Path(args.input),
        Path(args.output_root),
        theme_id=args.theme_id,
        mode=args.mode,
        ffmpeg_exe=args.ffmpeg,
        work_dir=work,
        preset=args.preset,
        crf=args.crf,
        luma_threshold=args.luma_threshold,
        highlight_blur=args.highlight_blur,
        bg_suppress=args.bg_suppress,
        fg_mode=args.fg_mode,
    )
    print(json.dumps({k: str(v) for k, v in out.items()}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
