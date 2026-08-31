"""Optional, non-blocking Shaker V2 post-process after canonical V1 succeeds.

The expensive AI generation is never repeated.  This worker downloads the
already-stored baked BREATHING clip, performs real per-frame matting with
motion-compensated temporal stabilization, packs premultiplied RGB + grayscale
alpha into the existing vertical H.264 contract, copies/normalizes the pet-free
background, runs structural alpha QA, and only then publishes READY.

Failure is contained to the V2 manifest.  V1 storage, registration, SSIM and the
HTTP generation response are never modified.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import cv2
import httpx
import numpy as np

from . import shaker_layered_assets, supabase_assets
from .luma_service import download_video
from .video_cutout_service import process_video_to_rgba

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LayeredPostProcessInput:
    user_id: str
    pet_id: str
    content_id: str
    scene_id: str
    v1_video_url: str
    background_type: str
    background_url: str


def enabled() -> bool:
    # Explicit rollout gate: temporal matting is CPU/RAM heavy.  Keeping this
    # off cannot affect V1; enabling it requires the migration and ffmpeg.
    return os.getenv("SHAKER_LAYERED_V2_POSTPROCESS", "0").strip().lower() in (
        "1", "true", "yes"
    )


def _allowed_hosts() -> set[str]:
    hosts = {"device.eternalbeam.com", "localhost", "127.0.0.1"}
    for key in (
        "SUPABASE_URL",
        "VITE_SUPABASE_URL",
        "PUBLIC_WEB_BASE_URL",
        "SHAKER_LAYERED_BACKGROUND_ALLOWED_ORIGINS",
    ):
        for raw in (os.getenv(key) or "").split(","):
            host = (urlparse(raw.strip()).hostname or "").lower()
            if host:
                hosts.add(host)
    return hosts


def background_url_allowed(url: str) -> bool:
    try:
        parsed = urlparse((url or "").strip())
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or not host or host not in _allowed_hosts():
            return False
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_loopback and os.getenv("ENV", "").lower() != "production"
        except ValueError:
            return True
    except Exception:
        return False


async def _download_background(url: str, target: Path) -> str:
    if not background_url_allowed(url):
        raise RuntimeError("layered background origin is not allowed")
    limit = int(os.getenv("SHAKER_LAYERED_BACKGROUND_MAX_BYTES", str(50 * 1024 * 1024)))
    total = 0
    content_type = ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            if not background_url_allowed(str(response.url)):
                raise RuntimeError("layered background redirected to a disallowed origin")
            content_type = (response.headers.get("content-type") or "").split(";")[0].lower()
            with target.open("wb") as output:
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > limit:
                        raise RuntimeError("layered background exceeds size limit")
                    output.write(chunk)
    if total == 0:
        raise RuntimeError("empty layered background")
    return content_type


def _video_meta(path: str) -> tuple[int, int, float]:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError("could not open V1 video")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
    cap.release()
    if width < 2 or height < 2:
        raise RuntimeError("invalid V1 video dimensions")
    return width, height, max(1.0, fps)


def _cover_image(source: Path, target: Path, width: int, height: int) -> None:
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("could not decode layered image background")
    ih, iw = image.shape[:2]
    scale = max(width / iw, height / ih)
    resized = cv2.resize(
        image,
        (max(width, int(round(iw * scale))), max(height, int(round(ih * scale)))),
        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
    )
    y = max(0, (resized.shape[0] - height) // 2)
    x = max(0, (resized.shape[1] - width) // 2)
    frame = resized[y : y + height, x : x + width]
    if frame.shape[:2] != (height, width) or not cv2.imwrite(
        str(target), frame, [cv2.IMWRITE_JPEG_QUALITY, 90]
    ):
        raise RuntimeError("could not normalize layered image background")


def _cover_video(source: Path, target: Path, width: int, height: int) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(source),
        "-vf",
        f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},format=yuv420p",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-movflags", "+faststart", str(target),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0 or not target.exists():
        raise RuntimeError(f"could not normalize layered video background: {result.stderr[-500:]}")


def _pack_rgba_frames(paths: tuple[str, ...], output: Path, fps: float, work: Path) -> None:
    packed_dir = work / "packed"
    packed_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for index, path in enumerate(paths):
        bgra = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if bgra is None or bgra.ndim != 3 or bgra.shape[2] != 4:
            raise RuntimeError(f"invalid RGBA matte frame {index}")
        bgr = bgra[:, :, :3].astype(np.float32)
        alpha = bgra[:, :, 3]
        premultiplied = np.clip(bgr * (alpha[:, :, None].astype(np.float32) / 255.0), 0, 255)
        alpha_bgr = cv2.merge([alpha, alpha, alpha])
        packed = np.vstack([premultiplied.astype(np.uint8), alpha_bgr])
        if packed.shape[1] % 2:
            # H.264 yuv420p requires even dimensions. Preserve the source
            # pixels and add one transparent column instead of cropping fur.
            packed = cv2.copyMakeBorder(
                packed, 0, 0, 0, 1, cv2.BORDER_CONSTANT, value=(0, 0, 0)
            )
        if not cv2.imwrite(str(packed_dir / f"frame_{index:05d}.png"), packed):
            raise RuntimeError("could not write packed frame")
        count += 1
    if count < 2:
        raise RuntimeError("not enough matted frames")

    cmd = [
        "ffmpeg", "-y", "-framerate", f"{fps:.6f}",
        "-i", str(packed_dir / "frame_%05d.png"),
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0 or not output.exists():
        raise RuntimeError(f"packed alpha encode failed: {result.stderr[-500:]}")


def _contact_shadow_metadata(
    alpha_bounds: list[tuple[int, int, int, int]], width: int, height: int
) -> dict[str, object] | None:
    if not alpha_bounds or width <= 0 or height <= 0:
        return None
    centers = [((x_min + x_max) / 2) for x_min, x_max, _, _ in alpha_bounds]
    widths = [(x_max - x_min + 1) for x_min, x_max, _, _ in alpha_bounds]
    bottoms = [y_max for _, _, _, y_max in alpha_bounds]
    pet_width_pct = float(np.median(widths)) / width * 100
    return {
        "kind": "css-contact",
        "opacity": 0.24,
        "blur_px": 11,
        "center_x_pct": round(float(np.median(centers)) / width * 100, 3),
        "bottom_pct": round(
            max(0.0, min(80.0, (1 - float(np.median(bottoms)) / height) * 100)),
            3,
        ),
        "width_pct": round(max(12.0, min(42.0, pet_width_pct * 0.48)), 3),
        "height_pct": round(max(1.5, min(4.0, pet_width_pct * 0.048)), 3),
    }


def _anchored_placement_metadata(
    alpha_bounds: list[tuple[int, int, int, int]], width: int, height: int
) -> dict[str, object] | None:
    """Crop transparent canvas space while preserving the pet's scene placement.

    `scene-frame` uses cover-cropping and therefore zooms a portrait transparent
    canvas several times on a landscape phone.  A union of sampled alpha bounds
    gives us a stable pet rectangle without tracking individual moving frames.
    """
    if not alpha_bounds or width <= 0 or height <= 0:
        return None
    pad_x = max(4, int(round(width * 0.02)))
    pad_y = max(4, int(round(height * 0.02)))
    x_min = max(0, min(bound[0] for bound in alpha_bounds) - pad_x)
    x_max = min(width - 1, max(bound[1] for bound in alpha_bounds) + pad_x)
    y_min = max(0, min(bound[2] for bound in alpha_bounds) - pad_y)
    y_max = min(height - 1, max(bound[3] for bound in alpha_bounds) + pad_y)
    crop_width = max(1, x_max - x_min + 1)
    crop_height = max(1, y_max - y_min + 1)
    return {
        "mode": "anchored",
        "center_x_pct": round(((x_min + x_max + 1) / 2) / width * 100, 4),
        "bottom_pct": round((height - (y_max + 1)) / height * 100, 4),
        "height_pct": round(crop_height / height * 100, 4),
        "crop_x_min": round(x_min / width, 6),
        "crop_x_max": round((x_max + 1) / width, 6),
        "crop_y_min": round(y_min / height, 6),
        "crop_y_max": round((y_max + 1) / height, 6),
    }


def validate_packed_alpha(path: str) -> dict[str, object]:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError("packed alpha QA could not open video")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    packed_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if width < 64 or packed_height < 128 or packed_height % 2 or frames < 2:
        cap.release()
        raise RuntimeError("packed alpha QA invalid dimensions/frame count")
    sample_indexes = sorted({
        min(frames - 1, int(round(i * (frames - 1) / 7))) for i in range(8)
    })
    coverage: list[float] = []
    alpha_std: list[float] = []
    alpha_bounds: list[tuple[int, int, int, int]] = []
    for index in sample_indexes:
        cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = cap.read()
        if not ok:
            continue
        alpha = cv2.cvtColor(frame[packed_height // 2 :, :], cv2.COLOR_BGR2GRAY)
        visible = alpha > 16
        coverage.append(float(np.mean(visible)))
        alpha_std.append(float(np.std(alpha)))
        ys, xs = np.where(visible)
        if xs.size and ys.size:
            alpha_bounds.append(
                (int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max()))
            )
    cap.release()
    if len(coverage) < 4:
        raise RuntimeError("packed alpha QA insufficient samples")
    min_coverage = min(coverage)
    max_coverage = max(coverage)
    mean_std = float(np.mean(alpha_std))
    passed = min_coverage >= 0.003 and max_coverage <= 0.92 and mean_std >= 4.0
    result: dict[str, object] = {
        "passed": passed,
        "width": width,
        "frame_height": packed_height // 2,
        "frame_count": frames,
        "alpha_samples": len(coverage),
        "alpha_coverage_min": round(min_coverage, 6),
        "alpha_coverage_max": round(max_coverage, 6),
        "alpha_std_mean": round(mean_std, 4),
        "temporal_stabilization": "optical-flow-validated-current-support-v2",
    }
    contact_shadow = _contact_shadow_metadata(
        alpha_bounds, width, packed_height // 2
    )
    if contact_shadow:
        result["contact_shadow"] = contact_shadow
    placement = _anchored_placement_metadata(
        alpha_bounds, width, packed_height // 2
    )
    if placement:
        result["placement"] = placement
    if not passed:
        raise RuntimeError(f"packed alpha QA failed: {result}")
    return result


def _matte_and_pack(v1_path: str, output: Path, work: Path) -> tuple[dict[str, object], int, int]:
    _source_width, _source_height, fps = _video_meta(v1_path)
    rgba_dir = work / "rgba"
    rgba_dir.mkdir(parents=True, exist_ok=True)
    paths = process_video_to_rgba(
        v1_path,
        str(rgba_dir),
        output_format="png_sequence",
        use_alpha_matting=True,
        output_resolution=None,
        temporal_alpha_stabilization=True,
        require_alpha_matting=True,
    )
    _pack_rgba_frames(paths, output, fps, work)
    qa = validate_packed_alpha(str(output))
    # rembg may deliberately downscale an unusually large source to stay
    # within its memory budget. Normalize the independent background to the
    # actual approved packed frame, not to the pre-matte source dimensions.
    return qa, int(qa["width"]), int(qa["frame_height"])


async def run_postprocess(input: LayeredPostProcessInput) -> None:
    if not enabled():
        return
    if input.background_type not in ("image", "video") or not input.background_url:
        return

    existing = await shaker_layered_assets.processing_or_ready_for_scene(
        user_id=input.user_id, pet_id=input.pet_id, scene_id=input.scene_id
    )
    if existing:
        if existing.status == shaker_layered_assets.PROCESSING and shaker_layered_assets.processing_is_stale(existing):
            await shaker_layered_assets.mark_failed(
                existing.asset_id,
                "interrupted layered post-process exceeded stale timeout",
            )
        else:
            return

    try:
        reservation = await shaker_layered_assets.reserve(
            user_id=input.user_id,
            pet_id=input.pet_id,
            content_id=input.content_id,
            scene_id=input.scene_id,
            placement={"mode": "scene-frame", "crop_x_min": 0, "crop_x_max": 1},
        )
    except shaker_layered_assets.LayeredAssetError:
        # Most commonly the partial unique index won a concurrent reservation.
        # V1 is already READY, so a duplicate optional task can end quietly.
        logger.info(
            "Shaker V2 reservation skipped (pet=%s scene=%s)",
            input.pet_id,
            input.scene_id,
        )
        return
    try:
        with tempfile.TemporaryDirectory(prefix="eternal-beam-v2-") as temp:
            work = Path(temp)
            v1_path = await download_video(input.v1_video_url)
            try:
                packed_path = work / "pet_packed.mp4"
                qa, width, height = await asyncio.to_thread(
                    _matte_and_pack, v1_path, packed_path, work
                )
            finally:
                try:
                    os.unlink(v1_path)
                except OSError:
                    pass

            raw_background = work / "background_source"
            await _download_background(input.background_url, raw_background)
            if input.background_type == "video":
                normalized_background = work / "background.mp4"
                await asyncio.to_thread(
                    _cover_video, raw_background, normalized_background, width, height
                )
                background_name = "background.mp4"
                background_content_type = "video/mp4"
            else:
                normalized_background = work / "background.jpg"
                await asyncio.to_thread(
                    _cover_image, raw_background, normalized_background, width, height
                )
                background_name = "background.jpg"
                background_content_type = "image/jpeg"

            pet_object = shaker_layered_assets.versioned_object_path(
                pet_id=input.pet_id,
                scene_id=input.scene_id,
                asset_version=reservation.asset_version,
                filename="pet_packed.mp4",
            )
            background_object = shaker_layered_assets.versioned_object_path(
                pet_id=input.pet_id,
                scene_id=input.scene_id,
                asset_version=reservation.asset_version,
                filename=background_name,
            )
            await supabase_assets.upload_asset_to_storage(
                pet_object, packed_path.read_bytes(), "video/mp4"
            )
            await supabase_assets.upload_asset_to_storage(
                background_object,
                normalized_background.read_bytes(),
                background_content_type,
            )
            await shaker_layered_assets.publish_ready(
                reservation.asset_id,
                pet=shaker_layered_assets.StorageRef(supabase_assets.BUCKET, pet_object),
                background_type=input.background_type,
                background=shaker_layered_assets.StorageRef(
                    supabase_assets.BUCKET, background_object
                ),
                qa=qa,
                # The existing alpha QA samples supply a median feet/width
                # anchor. This is metadata only; playback adds no decoder.
                shadow=(
                    dict(qa["contact_shadow"])
                    if isinstance(qa.get("contact_shadow"), dict)
                    else None
                ),
                placement=(
                    dict(qa["placement"])
                    if isinstance(qa.get("placement"), dict)
                    else None
                ),
            )
        logger.info(
            "Shaker V2 READY — pet=%s scene=%s asset=%s",
            input.pet_id, input.scene_id, reservation.asset_id,
        )
    except Exception as exc:  # noqa: BLE001 — V2 failure must leave V1 untouched
        await shaker_layered_assets.mark_failed(reservation.asset_id, str(exc))
        logger.exception(
            "Shaker V2 post-process failed — V1 remains active (pet=%s scene=%s)",
            input.pet_id, input.scene_id,
        )
