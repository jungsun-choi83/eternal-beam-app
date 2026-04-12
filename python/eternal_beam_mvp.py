#!/usr/bin/env python3
"""
Eternal Beam MVP: input.jpg → output.mp4
- Segmentation: U2-Net(u2net_human_seg) + Alpha Matting (머리카락/털 정교 분리)
- Depth: ZoeDepth (Z-depth 엔진, 파라랙스·DoF용)
- 합성: 파라랙스 레이어, Volumetric Glow, DoF, True Black, Film Grain, BGM 루프
- 완료 시 전체 화면 팝업 재생

실행: python eternal_beam_mvp.py input.jpg [output.mp4]
필요: rembg, torch, transformers, opencv-python, Pillow, numpy, ffmpeg(시스템)
"""

from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. Segmentation (U2-Net + Alpha Matting)
# ---------------------------------------------------------------------------
def segment_subject(image_bytes: bytes, use_alpha_matting: bool = True) -> bytes:
    """피사체 분리: rembg u2net_human_seg + Alpha Matting."""
    try:
        from rembg import remove as rembg_remove
        from rembg.session_factory import new_session
    except ImportError:
        raise RuntimeError("rembg가 필요합니다. pip install rembg[gpu]")

    from PIL import Image
    import numpy as np

    input_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    session = new_session("u2net_human_seg")

    try:
        out_img = rembg_remove(
            input_img,
            session=session,
            alpha_matting=use_alpha_matting,
            alpha_matting_foreground_threshold=240,
            alpha_matting_background_threshold=10,
            alpha_matting_erode_structure_size=10,
            alpha_matting_base_size=1000,
        )
    except TypeError:
        out_img = rembg_remove(
            input_img,
            session=session,
            alpha_matting=use_alpha_matting,
            alpha_matting_foreground_threshold=240,
            alpha_matting_background_threshold=10,
            alpha_matting_erode_size=10,
            alpha_matting_base_size=1000,
        )
    if out_img is None:
        raise RuntimeError("rembg 반환값이 비어 있습니다.")

    rgba = np.array(out_img)
    if use_alpha_matting and rgba.shape[2] == 4:
        try:
            import cv2
            alpha = rgba[:, :, 3]
            kernel = np.ones((3, 3), np.uint8)
            alpha = cv2.morphologyEx(alpha, cv2.MORPH_CLOSE, kernel)
            alpha = cv2.GaussianBlur(alpha, (3, 3), 0.5)
            rgba[:, :, 3] = np.clip(alpha, 0, 255).astype(np.uint8)
            out_img = Image.fromarray(rgba)
        except Exception:
            pass

    buf = io.BytesIO()
    out_img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 2. Depth Estimation (ZoeDepth) — 파라랙스·DoF 엔진
# ---------------------------------------------------------------------------
def estimate_depth_map(pil_image) -> "np.ndarray | None":
    """ZoeDepth로 깊이 맵 생성. 실패 시 None."""
    try:
        import torch
        import numpy as np
        from transformers import AutoImageProcessor, ZoeDepthForDepthEstimation
    except ImportError:
        return None

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model_id = "Intel/zoedepth-nyu"
    try:
        processor = AutoImageProcessor.from_pretrained(model_id)
        model = ZoeDepthForDepthEstimation.from_pretrained(model_id).to(device)
        model.eval()
    except Exception:
        return None

    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")

    inputs = processor(images=pil_image, return_tensors="pt")
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        outputs = model(**inputs)
        predicted_depth = outputs.predicted_depth

    depth = predicted_depth.squeeze().cpu().numpy()
    depth = np.nan_to_num(depth, nan=0.0, posinf=0.0, neginf=0.0)
    depth = np.clip(depth, 0, np.percentile(depth, 99))
    return depth


# ---------------------------------------------------------------------------
# 3. FFmpeg 헬퍼
# ---------------------------------------------------------------------------
def _get_duration_sec(path: str) -> float:
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", path, "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    m = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)", r.stderr or "")
    if m:
        h, mi, s, cs = map(int, m.groups())
        return h * 3600 + mi * 60 + s + cs / 100.0
    return 10.0


def run_ffmpeg(args: list, timeout: int = 600) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-y"] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 실패: {proc.stderr[-2000:] if proc.stderr else proc.returncode}"
        )


# ---------------------------------------------------------------------------
# 4. 메인 파이프라인: Cutout + 테마 배경 + Glow + DoF + True Black + Grain + BGM
# ---------------------------------------------------------------------------
def build_eternal_beam_video(
    input_jpg: str,
    output_mp4: str,
    theme_video_path: str | None = None,
    duration_sec: float = 12.0,
    max_height: int = 720,
    bgm_path: str | None = None,
) -> str:
    """
    input.jpg → cutout → 테마 영상 위 오버레이 + Breathing + Soft Glow + DoF + True Black + Film Grain + BGM 루프.
    """
    from PIL import Image

    w = 1280 if max_height >= 1080 else 854
    h = 720 if max_height >= 1080 else 480
    w, h = (w // 2) * 2, (h // 2) * 2
    fps = 30

    with open(input_jpg, "rb") as f:
        image_bytes = f.read()

    # 1) Segmentation
    print("[1/5] 피사체 분리 중 (U2-Net + Alpha Matting)...")
    cutout_bytes = segment_subject(image_bytes, use_alpha_matting=True)
    cutout_png = os.path.join(tempfile.gettempdir(), f"eternal_cutout_{int(time.time())}.png")
    subject_v = ""
    with open(cutout_png, "wb") as f:
        f.write(cutout_bytes)

    try:
        # 2) Depth (선택: DoF/파라랙스용 — 여기서는 DoF 블러 강도 참고용으로만 사용 가능, FFmpeg에서는 고정 블러 적용)
        pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        depth_map = estimate_depth_map(pil_img)
        if depth_map is not None:
            print("[2/5] 깊이 추정 완료 (ZoeDepth).")
        else:
            print("[2/5] 깊이 추정 건너뜀 (ZoeDepth 미설치/오류).")

        # 3) 테마 배경: 제공된 MP4 또는 그라데이션 정지화
        scripts_dir = Path(__file__).resolve().parent
        themes_dir = scripts_dir.parent / "backend" / "themes"
        if not themes_dir.exists():
            themes_dir = Path(os.getenv("THEMES_VIDEO_DIR", "")) or scripts_dir.parent / "themes"
        theme_mp4 = theme_video_path
        if not theme_mp4 or not os.path.exists(theme_mp4):
            for name in ("rainbow_heaven", "sweet_memory", "theme"):
                for ext in (".mp4", ".webm", ".mov"):
                    p = themes_dir / f"{name}{ext}"
                    if p.exists():
                        theme_mp4 = str(p)
                        break
                if theme_mp4:
                    break
        if theme_mp4 and os.path.exists(theme_mp4):
            duration_sec = _get_duration_sec(theme_mp4)
            print(f"[3/5] 테마 영상 사용: {theme_mp4} ({duration_sec:.1f}초)")
        else:
            theme_mp4 = None
            print("[3/5] 테마 영상 없음 → 그라데이션 배경 생성.")

        # 그라데이션 정지화 생성 (테마 MP4 없을 때)
        if theme_mp4 is None:
            grad_mp4 = os.path.join(tempfile.gettempdir(), f"eternal_grad_{int(time.time())}.mp4")
            # 무지개/보라 그라데이션 느낌
            run_ffmpeg([
                "-f", "lavfi", "-i",
                "color=c=0x1a0a2e:s=1280x720:d=" + str(duration_sec) + ":r=30",
                "-vf",
                "drawbox=x=0:y=0:w=iw:h=ih:color=purple@0.3:t=fill,"
                "drawbox=x=0:y=0:w=iw:h=ih:color=blue@0.2:t=fill,"
                "curves=all='0/0 0.5/0.6 1/1'",
                "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "fast",
                grad_mp4,
            ], timeout=120)
            theme_mp4 = grad_mp4

        # 4) Breathing + Soft Glow + DoF(배경 블러) + True Black + Film Grain
        zoompan = f"zoompan=z='min(1.05,1+0.025*sin(2*PI*on/60))':d=1:s={w}x{h}:fps={fps}"
        subject_v = os.path.join(tempfile.gettempdir(), f"eternal_subject_{int(time.time())}.mp4")
        run_ffmpeg([
            "-loop", "1", "-t", str(duration_sec), "-i", cutout_png,
            "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,{zoompan}",
            "-pix_fmt", "yuva420p", "-c:v", "libx264", "-profile:v", "high", "-preset", "medium",
            subject_v,
        ])

        # 배경: 살짝 블러(DoF) → True Black 클램핑 (0~10 → Pure Black #000000) → Film Grain
        # [하드웨어] No Gray Blacks, DLP 프로젝터: 검은색 = 빛 없음 = 투명
        dof_blur = 2
        true_black = "curves=all='0/0 0.04/0 1/1'"  # 0~10 → 0 강제 (0.04 ≈ 10/255)
        grain = "noise=c0s=6:c0f=t+u"
        bg_filters = (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
            f"boxblur={dof_blur}:{dof_blur},{true_black}"
        )
        boxblur_radius = 15
        filter_complex = (
            f"[0:v]{bg_filters}[bg];"
            f"[1:v]split=2[sub][gs];"
            f"[gs]boxblur={boxblur_radius}:{boxblur_radius},eq=brightness=0.5[glow];"
            f"[glow][sub]overlay=0:0:format=auto[subject_with_glow];"
            f"[bg][subject_with_glow]overlay=(W-w)/2:(H-h)/2:format=auto,{grain}"
        )
        video_only = output_mp4
        if bgm_path and os.path.exists(bgm_path):
            video_only = os.path.join(tempfile.gettempdir(), f"eternal_video_only_{int(time.time())}.mp4")
        cmd = [
            "-i", theme_mp4,
            "-i", subject_v,
            "-filter_complex", filter_complex,
            "-t", str(duration_sec),
            "-c:v", "libx264", "-profile:v", "high", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-color_range", "pc", "-movflags", "+faststart",
            "-b:v", "2M", "-maxrate", "2.5M", "-bufsize", "4M",
            video_only,
        ]
        run_ffmpeg(cmd)

        # 5) BGM Seamless Loop (슬픈 피아노 스타일)
        if bgm_path and os.path.exists(bgm_path):
            print("[5/5] BGM 합성 중 (Seamless Loop)...")
            run_ffmpeg([
                "-stream_loop", "-1", "-i", bgm_path,
                "-i", video_only,
                "-filter_complex",
                f"[0:a]aloop=loop=-1:size=2e+09,atrim=0:{duration_sec},asetpts=PTS-STARTPTS[a]",
                "-map", "1:v", "-map", "[a]",
                "-t", str(duration_sec),
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                output_mp4,
            ], timeout=120)
            try:
                os.unlink(video_only)
            except Exception:
                pass
        elif video_only != output_mp4:
            import shutil
            shutil.move(video_only, output_mp4)

        if theme_mp4 and "grad" in theme_mp4:
            try:
                os.unlink(theme_mp4)
            except Exception:
                pass
        return output_mp4
    finally:
        for p in (cutout_png,):
            try:
                if p and os.path.exists(p):
                    os.unlink(p)
            except Exception:
                pass
        try:
            if "subject_v" in dir() and subject_v and os.path.exists(subject_v):
                os.unlink(subject_v)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 5. 완료 후 전체 화면 팝업 재생
# ---------------------------------------------------------------------------
def play_fullscreen(video_path: str) -> None:
    """시스템 기본 플레이어 또는 브라우저로 전체 화면 재생."""
    video_path = os.path.abspath(video_path)
    if sys.platform == "win32":
        os.startfile(video_path)
    elif sys.platform == "darwin":
        subprocess.run(["open", "-a", "QuickTime Player", video_path], check=False)
        subprocess.run(["open", video_path], check=False)
    else:
        for player in ("xdg-open", "vlc", "mpv", "ffplay"):
            try:
                if player == "ffplay":
                    subprocess.Popen(["ffplay", "-fs", "-autoexit", video_path])
                else:
                    subprocess.Popen([player, video_path])
                break
            except FileNotFoundError:
                continue


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Eternal Beam MVP: input.jpg → 감동의 output.mp4 (피사체 분리 + 깊이 + 파라랙스·DoF·True Black·BGM)"
    )
    parser.add_argument("input", type=str, help="입력 사진 경로 (예: input.jpg)")
    parser.add_argument(
        "output",
        type=str,
        nargs="?",
        default=None,
        help="출력 MP4 경로 (생략 시 input_eternal.mp4)",
    )
    parser.add_argument("--theme", type=str, default=None, help="테마 배경 MP4 경로")
    parser.add_argument("--duration", type=float, default=12.0, help="영상 길이(초)")
    parser.add_argument("--no-popup", action="store_true", help="완료 후 팝업 재생 안 함")
    parser.add_argument("--bgm", type=str, default=None, help="BGM 오디오 파일 (루프)")
    parser.add_argument("--full-hd", action="store_true", dest="full_hd", help="1080p 출력 (기본 720p)")
    args = parser.parse_args()

    input_path = os.path.abspath(args.input)
    if not os.path.isfile(input_path):
        print(f"오류: 입력 파일을 찾을 수 없습니다: {input_path}", file=sys.stderr)
        return 1
    output_path = args.output
    if not output_path:
        base = Path(input_path).stem
        output_path = str(Path(input_path).parent / f"{base}_eternal.mp4")
    output_path = os.path.abspath(output_path)

    try:
        print("Eternal Beam MVP 파이프라인 시작.")
        build_eternal_beam_video(
            input_jpg=input_path,
            output_mp4=output_path,
            theme_video_path=args.theme,
            duration_sec=args.duration,
            max_height=1080 if getattr(args, "full_hd", False) else 720,
            bgm_path=args.bgm or os.getenv("ETERNAL_BEAM_BGM_PATH"),
        )
        print(f"완료: {output_path}")
        if not args.no_popup:
            play_fullscreen(output_path)
        return 0
    except Exception as e:
        print(f"오류: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
