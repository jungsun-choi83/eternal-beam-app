"""Private, browser-compatible MP4 exports for the Archive handoff.

This module is deliberately separate from the motion delivery pipeline.  It reads an
already-stored raw motion artifact and writes one deterministic sibling object.  It
never touches the raw or packed-alpha objects and never calls an AI provider. A
process-local lock prevents duplicate work; cross-process races rely on a private,
non-upserting storage write and validation of the winner.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import asyncio
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from . import supabase_assets


ARCHIVE_BROWSER_EXPORT_VERSION = "archive-browser-mp4-v1"
ARCHIVE_MAX_VIDEO_BYTES = 40 * 1024 * 1024
ARCHIVE_MAX_SOURCE_VIDEO_BYTES = 256 * 1024 * 1024
ARCHIVE_MAX_SOURCE_DURATION_SEC = 120.0
ARCHIVE_MAX_SOURCE_PIXELS = 3840 * 2160
ARCHIVE_FFMPEG_MAX_CONCURRENCY = 2
DEFAULT_DURATION_TOLERANCE_SEC = 0.25
_STORAGE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_MP4_FORMAT_MARKERS = {"mp4", "mov", "m4a", "3gp", "3g2", "mj2"}


class ArchiveBrowserExportError(RuntimeError):
    """A safe, actionable failure while preparing an Archive browser export."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class BrowserVideoProbe:
    codec_name: str
    pixel_format: str
    format_name: str
    width: int
    height: int
    duration_sec: float
    fps: float | None
    has_audio: bool
    audio_codecs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArchiveBrowserExportResult:
    bucket: str
    storage_path: str
    source_raw_path: str
    size_bytes: int
    converted: bool
    deduplicated: bool
    probe: BrowserVideoProbe
    background_suitability: str
    customer_delivery_ready: bool
    export_version: str = ARCHIVE_BROWSER_EXPORT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "storage_path": self.storage_path,
            "source_raw_path": self.source_raw_path,
            "size_bytes": self.size_bytes,
            "converted": self.converted,
            "deduplicated": self.deduplicated,
            "probe": {
                "codec_name": self.probe.codec_name,
                "pixel_format": self.probe.pixel_format,
                "format_name": self.probe.format_name,
                "width": self.probe.width,
                "height": self.probe.height,
                "duration_sec": self.probe.duration_sec,
                "fps": self.probe.fps,
                "has_audio": self.probe.has_audio,
                "audio_codecs": list(self.probe.audio_codecs),
            },
            # Codec compatibility does not establish that a customer-facing
            # background is desirable.  That decision belongs to the caller.
            "background_suitability": self.background_suitability,
            "customer_delivery_ready": self.customer_delivery_ready,
            "export_version": self.export_version,
        }


@dataclass(frozen=True)
class ArchiveRawMp4RepairResult:
    """Result of an operator-controlled, same-path raw MP4 repair."""

    bucket: str
    storage_path: str
    size_bytes: int
    remuxed: bool
    deduplicated: bool
    probe: BrowserVideoProbe


DownloadFn = Callable[[str, str], Awaitable[bytes]]
UploadFn = Callable[[str, bytes, str, str], Awaitable[Any]]
ReplaceFn = Callable[[str, bytes, str, str], Awaitable[Any]]
BackgroundValidator = Callable[[bytes, BrowserVideoProbe], bool]
BucketPrivacyVerifier = Callable[[str], Awaitable[bool]]
CommandRunner = Callable[..., subprocess.CompletedProcess[bytes]]


_EXPORT_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
_EXPORT_LOCKS_GUARD = threading.Lock()
_FFMPEG_SLOTS = threading.BoundedSemaphore(ARCHIVE_FFMPEG_MAX_CONCURRENCY)


def deterministic_browser_object_path(
    application_id: str,
    generation_run_id: str,
) -> str:
    """Return the private, deterministic Archive export object path."""

    app = _storage_segment(application_id, "application_id")
    run = _storage_segment(generation_run_id, "generation_run_id")
    return f"archive-intake/{app}/runs/{run}/browser.mp4"


def _storage_segment(value: str, name: str) -> str:
    text = str(value or "").strip()
    if not _STORAGE_SEGMENT.fullmatch(text):
        raise ArchiveBrowserExportError(
            "INVALID_EXPORT_ID",
            f"{name} must be a single safe storage path segment.",
        )
    return text


def _run_command(args: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[bytes]:
    command = list(args)
    try:
        return subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(command, 127, b"", str(exc).encode())
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(command, 124, b"", str(exc).encode())
    except OSError as exc:
        return subprocess.CompletedProcess(command, 126, b"", str(exc).encode())


async def _run_command_async(
    runner: CommandRunner,
    args: Sequence[str],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    def _run() -> subprocess.CompletedProcess[bytes]:
        with _FFMPEG_SLOTS:
            return runner(args, timeout=timeout)

    return await asyncio.to_thread(_run)


def _export_lock(bucket: str, target_path: str) -> asyncio.Lock:
    key = (bucket, target_path)
    with _EXPORT_LOCKS_GUARD:
        lock = _EXPORT_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _EXPORT_LOCKS[key] = lock
        return lock


def _is_missing_storage_error(exc: Exception) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True
    for attribute in ("status_code", "status", "code"):
        value = getattr(exc, attribute, None)
        if str(value).strip() == "404":
            return True
    return False


async def _read_storage_object(
    read: DownloadFn,
    path: str,
    bucket: str,
    *,
    missing_ok: bool,
) -> bytes | None:
    try:
        data = await read(path, bucket)
    except Exception as exc:
        if missing_ok and _is_missing_storage_error(exc):
            return None
        code = "STORAGE_OBJECT_NOT_FOUND" if _is_missing_storage_error(exc) else "STORAGE_READ_FAILED"
        message = "The requested storage object was not found." if code == "STORAGE_OBJECT_NOT_FOUND" else "The storage object could not be read."
        raise ArchiveBrowserExportError(code, message) from exc
    if data is None:
        return None if missing_ok else b""
    if not isinstance(data, (bytes, bytearray)):
        raise ArchiveBrowserExportError("STORAGE_READ_FAILED", "Storage returned invalid object data.")
    return bytes(data)


def _output_text(result: subprocess.CompletedProcess[bytes]) -> str:
    value = result.stderr or result.stdout or b""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


async def _probe(
    path: Path,
    *,
    runner: CommandRunner,
    timeout: float,
) -> BrowserVideoProbe:
    result = await _run_command_async(
        runner,
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        timeout=timeout,
    )
    if result.returncode != 0:
        raise ArchiveBrowserExportError(
            "FFPROBE_FAILED",
            f"ffprobe could not inspect the raw/export MP4: {_output_text(result)[-400:]}",
        )
    try:
        payload = json.loads(result.stdout or b"{}")
        streams = payload.get("streams") or []
        video = next(stream for stream in streams if stream.get("codec_type") == "video")
        fmt = payload.get("format") or {}
        duration = float(video.get("duration") or fmt.get("duration") or 0.0)
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        codec = str(video.get("codec_name") or "").lower()
        pixel_format = str(video.get("pix_fmt") or "").lower()
        format_name = str(fmt.get("format_name") or "").lower()
        fps = _parse_fps(video.get("r_frame_rate"))
    except (StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ArchiveBrowserExportError(
            "FFPROBE_INVALID",
            "ffprobe returned incomplete or invalid video metadata.",
        ) from exc
    if width <= 0 or height <= 0 or duration <= 0.0 or not codec or not pixel_format:
        raise ArchiveBrowserExportError(
            "VIDEO_METADATA_INVALID",
            "The video must have positive dimensions/duration and a codec/pixel format.",
        )
    return BrowserVideoProbe(
        codec_name=codec,
        pixel_format=pixel_format,
        format_name=format_name,
        width=width,
        height=height,
        duration_sec=duration,
        fps=fps,
        has_audio=any(stream.get("codec_type") == "audio" for stream in streams),
        audio_codecs=tuple(
            sorted(
                {
                    str(stream.get("codec_name") or "").lower()
                    for stream in streams
                    if stream.get("codec_type") == "audio"
                    and stream.get("codec_name")
                }
            )
        ),
    )


def _parse_fps(value: Any) -> float | None:
    text = str(value or "")
    if "/" not in text:
        try:
            return float(text) if text else None
        except ValueError:
            return None
    numerator, denominator = text.split("/", 1)
    try:
        return float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return None


def _format_is_mp4(probe: BrowserVideoProbe) -> bool:
    names = {part.strip() for part in probe.format_name.split(",") if part.strip()}
    return bool(names & _MP4_FORMAT_MARKERS)


def _browser_compatible(probe: BrowserVideoProbe) -> bool:
    return (
        _format_is_mp4(probe)
        and probe.codec_name == "h264"
        and probe.pixel_format == "yuv420p"
        and probe.width % 2 == 0
        and probe.height % 2 == 0
    )


def _mp4_box(data: bytes, offset: int) -> tuple[str, int] | None:
    if offset + 8 > len(data):
        return None
    size = int.from_bytes(data[offset : offset + 4], "big")
    kind = data[offset + 4 : offset + 8].decode("ascii", errors="ignore")
    header = 8
    if size == 1:
        if offset + 16 > len(data):
            return None
        size = int.from_bytes(data[offset + 8 : offset + 16], "big")
        header = 16
    elif size == 0:
        size = len(data) - offset
    if size < header or offset + size > len(data):
        return None
    return kind, size


def _has_faststart_mp4(data: bytes) -> bool:
    """Check that the moov box precedes mdat for progressive browser startup."""

    offset = 0
    saw_ftyp = False
    saw_moov = False
    while offset < len(data):
        box = _mp4_box(data, offset)
        if box is None:
            return False
        kind, size = box
        if kind == "ftyp":
            saw_ftyp = True
        elif kind == "moov":
            saw_moov = True
        elif kind == "mdat":
            return saw_ftyp and saw_moov
        offset += size
    return False


def _tail_seek_seconds(probe: BrowserVideoProbe) -> float | None:
    """Choose a seek point that lands inside the final decodable frame window.

    ``-sseof -0.001`` can fall between the final frame timestamp and the
    container duration.  FFmpeg then correctly emits no frame even though the
    MP4 is valid.  Use roughly two frame intervals, bounded away from zero and
    never farther than half the clip, so the second decode still exercises the
    end of the video without treating a timestamp gap as corruption.
    """

    if probe.duration_sec <= 0.2:
        return None
    frame_interval = 1.0 / probe.fps if probe.fps and probe.fps > 0 else 0.125
    return min(max(0.05, frame_interval * 2.0), probe.duration_sec / 2.0)


async def _validate_opaque_frames(
    path: Path,
    probe: BrowserVideoProbe,
    *,
    runner: CommandRunner,
    timeout: float,
) -> None:
    # Decode both ends to RGB.  yuv420p has no alpha plane; requiring decoded
    # RGB bytes also prevents a metadata-only success on a broken MP4.
    tail_seek = _tail_seek_seconds(probe)
    seeks = [None] if tail_seek is None else [None, f"-{tail_seek:.6f}"]
    expected_bytes = probe.width * probe.height * 3
    for seek in seeks:
        args = ["ffmpeg", "-v", "error"]
        if seek is not None:
            args.extend(["-sseof", seek])
        args.extend(
            [
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ]
        )
        result = await _run_command_async(runner, args, timeout=timeout)
        if result.returncode != 0 or len(result.stdout or b"") < expected_bytes:
            raise ArchiveBrowserExportError(
                "OPAQUE_FRAME_DECODE_FAILED",
                "The browser export did not decode to a complete opaque RGB frame.",
            )


async def _convert_to_browser_mp4(
    source: Path,
    destination: Path,
    *,
    runner: CommandRunner,
    timeout: float,
) -> None:
    result = await _run_command_async(
        runner,
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-an",
            "-f",
            "mp4",
            str(destination),
        ],
        timeout=timeout,
    )
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size <= 0:
        raise ArchiveBrowserExportError(
            "FFMPEG_FAILED",
            f"FFmpeg could not produce the browser MP4: {_output_text(result)[-400:]}",
        )


async def _remux_faststart_mp4(
    source: Path,
    destination: Path,
    *,
    runner: CommandRunner,
    timeout: float,
) -> None:
    """Move the MP4 index without changing encoded audio/video samples."""

    result = await _run_command_async(
        runner,
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(destination),
        ],
        timeout=timeout,
    )
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size <= 0:
        raise ArchiveBrowserExportError(
            "FFMPEG_FASTSTART_FAILED",
            f"FFmpeg could not remux the MP4: {_output_text(result)[-400:]}",
        )


def _copy_remux_compatible(probe: BrowserVideoProbe) -> bool:
    return _browser_compatible(probe) and not {
        codec for codec in probe.audio_codecs if codec not in {"aac"}
    }


async def _default_download(path: str, bucket: str) -> bytes:
    return await supabase_assets.download_asset_from_storage(path, bucket=bucket)


async def _default_upload(path: str, data: bytes, content_type: str, bucket: str) -> str:
    return await supabase_assets.upload_private_asset_to_storage(
        path,
        data,
        content_type,
        bucket=bucket,
        required_prefix="archive-intake/",
    )


async def _default_replace(path: str, data: bytes, content_type: str, bucket: str) -> str:
    return await supabase_assets.replace_private_asset_to_storage(
        path,
        data,
        content_type,
        bucket=bucket,
    )


async def _default_verify_private_bucket(bucket: str) -> bool:
    return await supabase_assets.verify_private_storage_bucket(bucket)


async def export_archive_browser_mp4(
    *,
    raw_video_path: str,
    application_id: str,
    generation_run_id: str,
    bucket: str | None = None,
    download_fn: DownloadFn | None = None,
    upload_fn: UploadFn | None = None,
    bucket_privacy_verifier: BucketPrivacyVerifier | None = None,
    background_validator: BackgroundValidator | None = None,
    max_bytes: int = ARCHIVE_MAX_VIDEO_BYTES,
    max_source_bytes: int = ARCHIVE_MAX_SOURCE_VIDEO_BYTES,
    max_source_duration_sec: float = ARCHIVE_MAX_SOURCE_DURATION_SEC,
    max_source_pixels: int = ARCHIVE_MAX_SOURCE_PIXELS,
    duration_tolerance_sec: float = DEFAULT_DURATION_TOLERANCE_SEC,
    command_runner: CommandRunner = _run_command,
    command_timeout_sec: float = 600.0,
) -> ArchiveBrowserExportResult:
    """Create or reuse one private, browser-compatible Archive MP4.

    ``background_suitability`` is intentionally ``not_evaluated`` unless the
    caller supplies a validator.  Codec compatibility alone never authorizes a
    customer-facing background choice.
    """

    source_path = str(raw_video_path or "").strip()
    if not source_path:
        raise ArchiveBrowserExportError("RAW_VIDEO_PATH_MISSING", "raw_video_path is required.")
    if max_bytes <= 0 or max_source_bytes <= 0 or max_source_duration_sec <= 0 or max_source_pixels <= 0:
        raise ArchiveBrowserExportError("INVALID_SIZE_LIMIT", "max_bytes must be positive.")
    target_bucket = str(bucket or "").strip()
    if not target_bucket:
        raise ArchiveBrowserExportError("PRIVATE_BUCKET_REQUIRED", "An explicit private storage bucket is required.")
    target_path = deterministic_browser_object_path(application_id, generation_run_id)
    read = download_fn or _default_download
    write = upload_fn or _default_upload
    verify_bucket = bucket_privacy_verifier or _default_verify_private_bucket

    async with _export_lock(target_bucket, target_path):
        try:
            verified = await verify_bucket(target_bucket)
        except Exception as exc:
            raise ArchiveBrowserExportError(
                "PRIVATE_BUCKET_UNVERIFIED",
                "The Archive export bucket privacy could not be verified.",
            ) from exc
        if verified is not True:
            raise ArchiveBrowserExportError(
                "PRIVATE_BUCKET_UNVERIFIED",
                "The Archive export bucket is not verified as private.",
            )

        existing = await _read_storage_object(read, target_path, target_bucket, missing_ok=True)
        if existing is not None:
            return await _validate_export_bytes(
                existing,
                source_duration_sec=None,
                max_bytes=max_bytes,
                duration_tolerance_sec=duration_tolerance_sec,
                command_runner=command_runner,
                command_timeout_sec=command_timeout_sec,
                background_validator=background_validator,
                source_path=source_path,
                target_bucket=target_bucket,
                target_path=target_path,
                converted=False,
                deduplicated=True,
            )

        raw = await _read_storage_object(read, source_path, target_bucket, missing_ok=False)
        if not raw:
            raise ArchiveBrowserExportError("RAW_VIDEO_EMPTY", "The raw MP4 is empty.")
        if len(raw) > max_source_bytes:
            raise ArchiveBrowserExportError(
                "RAW_VIDEO_TOO_LARGE",
                f"The raw MP4 is {len(raw)} bytes; maximum input is {max_source_bytes} bytes.",
            )

        with tempfile.TemporaryDirectory(prefix="eb_archive_browser_") as temp_dir:
            temp = Path(temp_dir)
            source = temp / "raw.mp4"
            output = temp / "browser.mp4"
            source.write_bytes(raw)
            source_probe = await _probe(source, runner=command_runner, timeout=command_timeout_sec)
            if source_probe.duration_sec > max_source_duration_sec:
                raise ArchiveBrowserExportError(
                    "RAW_VIDEO_DURATION_TOO_LONG",
                    "The raw MP4 exceeds the configured input duration limit.",
                )
            if source_probe.width * source_probe.height > max_source_pixels:
                raise ArchiveBrowserExportError(
                    "RAW_VIDEO_DIMENSIONS_TOO_LARGE",
                    "The raw MP4 exceeds the configured input pixel limit.",
                )

            faststart = _has_faststart_mp4(raw)
            converted = not (_browser_compatible(source_probe) and faststart)
            if not faststart and _copy_remux_compatible(source_probe):
                await _remux_faststart_mp4(
                    source, output, runner=command_runner, timeout=command_timeout_sec
                )
                output_bytes = output.read_bytes()
                output_probe = await _probe(output, runner=command_runner, timeout=command_timeout_sec)
            elif converted:
                await _convert_to_browser_mp4(
                    source, output, runner=command_runner, timeout=command_timeout_sec
                )
                output_bytes = output.read_bytes()
                output_probe = await _probe(output, runner=command_runner, timeout=command_timeout_sec)
            else:
                output_bytes = raw
                output_probe = source_probe

            result = await _validate_export_bytes(
                output_bytes,
                source_duration_sec=source_probe.duration_sec,
                max_bytes=max_bytes,
                duration_tolerance_sec=duration_tolerance_sec,
                command_runner=command_runner,
                command_timeout_sec=command_timeout_sec,
                background_validator=background_validator,
                source_path=source_path,
                target_bucket=target_bucket,
                target_path=target_path,
                converted=converted,
                deduplicated=False,
                probe=output_probe,
            )
            try:
                await write(target_path, output_bytes, "video/mp4", target_bucket)
            except Exception as exc:
                competing = await _read_storage_object(read, target_path, target_bucket, missing_ok=True)
                if competing is None:
                    raise ArchiveBrowserExportError(
                        "STORAGE_WRITE_FAILED",
                        "The browser MP4 could not be stored without overwriting another object.",
                    ) from exc
                return await _validate_export_bytes(
                    competing,
                    source_duration_sec=None,
                    max_bytes=max_bytes,
                    duration_tolerance_sec=duration_tolerance_sec,
                    command_runner=command_runner,
                    command_timeout_sec=command_timeout_sec,
                    background_validator=background_validator,
                    source_path=source_path,
                    target_bucket=target_bucket,
                    target_path=target_path,
                    converted=False,
                    deduplicated=True,
                )
            return result


async def repair_archive_raw_mp4(
    *,
    storage_path: str,
    bucket: str,
    download_fn: DownloadFn | None = None,
    replace_fn: ReplaceFn | None = None,
    bucket_privacy_verifier: BucketPrivacyVerifier | None = None,
    command_runner: CommandRunner = _run_command,
    command_timeout_sec: float = 600.0,
    max_bytes: int = ARCHIVE_MAX_VIDEO_BYTES,
    max_source_bytes: int = ARCHIVE_MAX_SOURCE_VIDEO_BYTES,
    max_source_duration_sec: float = ARCHIVE_MAX_SOURCE_DURATION_SEC,
    max_source_pixels: int = ARCHIVE_MAX_SOURCE_PIXELS,
    duration_tolerance_sec: float = DEFAULT_DURATION_TOLERANCE_SEC,
) -> ArchiveRawMp4RepairResult:
    """Repair one raw MP4 in place after complete validation.

    The source object is read and left untouched while FFmpeg writes a temporary
    file.  The replacement callback is called only after browser validation has
    decoded the repaired output.  The deterministic same-path write preserves
    the candidate/run lineage and makes a repeated repair a no-op once valid.
    """

    path = str(storage_path or "").strip()
    target_bucket = str(bucket or "").strip()
    if not path:
        raise ArchiveBrowserExportError("RAW_VIDEO_PATH_MISSING", "storage_path is required.")
    if not target_bucket:
        raise ArchiveBrowserExportError("PRIVATE_BUCKET_REQUIRED", "A private bucket is required.")
    if max_bytes <= 0 or max_source_bytes <= 0 or max_source_duration_sec <= 0 or max_source_pixels <= 0:
        raise ArchiveBrowserExportError("INVALID_SIZE_LIMIT", "Repair limits must be positive.")

    read = download_fn or _default_download
    replace = replace_fn or _default_replace
    verify_bucket = bucket_privacy_verifier or _default_verify_private_bucket

    async with _export_lock(target_bucket, path):
        try:
            verified = await verify_bucket(target_bucket)
        except Exception as exc:
            raise ArchiveBrowserExportError(
                "PRIVATE_BUCKET_UNVERIFIED",
                "The raw MP4 bucket privacy could not be verified.",
            ) from exc
        if verified is not True:
            raise ArchiveBrowserExportError(
                "PRIVATE_BUCKET_UNVERIFIED",
                "The raw MP4 bucket is not verified as private.",
            )

        raw = await _read_storage_object(read, path, target_bucket, missing_ok=False)
        if not raw:
            raise ArchiveBrowserExportError("RAW_VIDEO_EMPTY", "The raw MP4 is empty.")
        if len(raw) > max_source_bytes:
            raise ArchiveBrowserExportError("RAW_VIDEO_TOO_LARGE", "The raw MP4 exceeds the input limit.")

        with tempfile.TemporaryDirectory(prefix="eb_archive_raw_repair_") as temp_dir:
            temp = Path(temp_dir)
            source = temp / "source.mp4"
            repaired = temp / "repaired.mp4"
            source.write_bytes(raw)
            source_probe = await _probe(source, runner=command_runner, timeout=command_timeout_sec)
            if source_probe.duration_sec > max_source_duration_sec:
                raise ArchiveBrowserExportError(
                    "RAW_VIDEO_DURATION_TOO_LONG", "The raw MP4 exceeds the input duration limit."
                )
            if source_probe.width * source_probe.height > max_source_pixels:
                raise ArchiveBrowserExportError(
                    "RAW_VIDEO_DIMENSIONS_TOO_LARGE", "The raw MP4 exceeds the input pixel limit."
                )

            if _has_faststart_mp4(raw):
                validated = await _validate_export_bytes(
                    raw,
                    source_duration_sec=None,
                    max_bytes=max_bytes,
                    duration_tolerance_sec=duration_tolerance_sec,
                    command_runner=command_runner,
                    command_timeout_sec=command_timeout_sec,
                    background_validator=None,
                    source_path=path,
                    target_bucket=target_bucket,
                    target_path=path,
                    converted=False,
                    deduplicated=True,
                    probe=source_probe,
                )
                return ArchiveRawMp4RepairResult(
                    bucket=target_bucket,
                    storage_path=path,
                    size_bytes=validated.size_bytes,
                    remuxed=False,
                    deduplicated=True,
                    probe=validated.probe,
                )

            if not _copy_remux_compatible(source_probe):
                raise ArchiveBrowserExportError(
                    "BROWSER_VIDEO_INCOMPATIBLE",
                    "Only browser-compatible MP4s can be repaired with a copy-only remux.",
                )
            await _remux_faststart_mp4(
                source, repaired, runner=command_runner, timeout=command_timeout_sec
            )
            repaired_bytes = repaired.read_bytes()
            repaired_probe = await _probe(
                repaired, runner=command_runner, timeout=command_timeout_sec
            )
            validated = await _validate_export_bytes(
                repaired_bytes,
                source_duration_sec=source_probe.duration_sec,
                max_bytes=max_bytes,
                duration_tolerance_sec=duration_tolerance_sec,
                command_runner=command_runner,
                command_timeout_sec=command_timeout_sec,
                background_validator=None,
                source_path=path,
                target_bucket=target_bucket,
                target_path=path,
                converted=True,
                deduplicated=False,
                probe=repaired_probe,
            )
            try:
                await replace(path, repaired_bytes, "video/mp4", target_bucket)
            except Exception as exc:
                # A concurrent operator may have completed the same deterministic
                # repair. Accept it only after reading and validating the winner.
                competing = await _read_storage_object(read, path, target_bucket, missing_ok=False)
                try:
                    winner = await _validate_export_bytes(
                        competing,
                        source_duration_sec=None,
                        max_bytes=max_bytes,
                        duration_tolerance_sec=duration_tolerance_sec,
                        command_runner=command_runner,
                        command_timeout_sec=command_timeout_sec,
                        background_validator=None,
                        source_path=path,
                        target_bucket=target_bucket,
                        target_path=path,
                        converted=False,
                        deduplicated=True,
                    )
                except ArchiveBrowserExportError:
                    raise ArchiveBrowserExportError(
                        "STORAGE_REPLACE_FAILED",
                        "The repaired raw MP4 could not be stored safely.",
                    ) from exc
                return ArchiveRawMp4RepairResult(
                    bucket=target_bucket,
                    storage_path=path,
                    size_bytes=winner.size_bytes,
                    remuxed=False,
                    deduplicated=True,
                    probe=winner.probe,
                )

            return ArchiveRawMp4RepairResult(
                bucket=target_bucket,
                storage_path=path,
                size_bytes=validated.size_bytes,
                remuxed=True,
                deduplicated=False,
                probe=validated.probe,
            )


async def prepare_browser_mp4_bytes(
    data: bytes,
    *,
    bucket: str,
    storage_path: str,
    bucket_privacy_verifier: BucketPrivacyVerifier | None = None,
    command_runner: CommandRunner | None = None,
    command_timeout_sec: float = 600.0,
) -> tuple[bytes, ArchiveRawMp4RepairResult]:
    """Return validated browser bytes without writing to storage.

    This is the authenticated Archive retrieval fallback. It reuses the exact
    repair validation/remux path, but its replacement callback only captures the
    validated temporary result in memory. The selected raw storage object is
    never modified.
    """

    prepared: dict[str, bytes] = {}

    async def read_source(path: str, target_bucket: str) -> bytes:
        return bytes(data)

    async def capture_repaired(path: str, repaired: bytes, content_type: str, target_bucket: str) -> None:
        if content_type != "video/mp4":
            raise ArchiveBrowserExportError(
                "BROWSER_VIDEO_INCOMPATIBLE",
                "The repaired Archive video did not have MP4 content type.",
            )
        prepared["data"] = bytes(repaired)

    result = await repair_archive_raw_mp4(
        storage_path=storage_path,
        bucket=bucket,
        download_fn=read_source,
        replace_fn=capture_repaired,
        bucket_privacy_verifier=bucket_privacy_verifier,
        command_runner=command_runner or _run_command,
        command_timeout_sec=command_timeout_sec,
    )
    return prepared.get("data", bytes(data)), result


async def _validate_export_bytes(
    data: bytes,
    *,
    source_duration_sec: float | None,
    max_bytes: int,
    duration_tolerance_sec: float,
    command_runner: CommandRunner,
    command_timeout_sec: float,
    background_validator: BackgroundValidator | None,
    source_path: str,
    target_bucket: str,
    target_path: str,
    converted: bool,
    deduplicated: bool,
    probe: BrowserVideoProbe | None = None,
) -> ArchiveBrowserExportResult:
    if len(data) > max_bytes:
        raise ArchiveBrowserExportError(
            "BROWSER_VIDEO_TOO_LARGE",
            f"Browser MP4 is {len(data)} bytes; maximum is {max_bytes} bytes.",
        )
    with tempfile.TemporaryDirectory(prefix="eb_archive_browser_validate_") as temp_dir:
        output = Path(temp_dir) / "browser.mp4"
        output.write_bytes(data)
        output_probe = probe or await _probe(output, runner=command_runner, timeout=command_timeout_sec)
        if not _browser_compatible(output_probe):
            raise ArchiveBrowserExportError(
                "BROWSER_VIDEO_INCOMPATIBLE",
                "Browser MP4 must be an even-dimension H.264 yuv420p MP4.",
            )
        if output_probe.duration_sec > ARCHIVE_MAX_SOURCE_DURATION_SEC:
            raise ArchiveBrowserExportError(
                "BROWSER_VIDEO_DURATION_TOO_LONG",
                "Browser MP4 exceeds the permitted duration.",
            )
        unsupported_audio = {
            codec for codec in output_probe.audio_codecs if codec not in {"aac"}
        }
        if unsupported_audio:
            raise ArchiveBrowserExportError(
                "BROWSER_AUDIO_INCOMPATIBLE",
                "Browser MP4 contains an unsupported audio codec.",
            )
        if not _has_faststart_mp4(data):
            raise ArchiveBrowserExportError(
                "BROWSER_VIDEO_NOT_FASTSTART",
                "Browser MP4 must place its moov box before mdat.",
            )
        if source_duration_sec is not None and abs(
            output_probe.duration_sec - source_duration_sec
        ) > duration_tolerance_sec:
            raise ArchiveBrowserExportError(
                "BROWSER_VIDEO_DURATION_CHANGED",
                "Browser export duration differs materially from the raw artifact.",
            )
        await _validate_opaque_frames(
            output,
            output_probe,
            runner=command_runner,
            timeout=command_timeout_sec,
        )
        background_suitability = "not_evaluated"
        if background_validator is not None:
            try:
                valid_background = bool(background_validator(data, output_probe))
            except Exception as exc:
                raise ArchiveBrowserExportError(
                    "BACKGROUND_VALIDATION_FAILED",
                    "The caller-supplied background validator failed.",
                ) from exc
            if not valid_background:
                raise ArchiveBrowserExportError(
                    "BACKGROUND_UNSUITABLE",
                    "The caller-supplied background validator rejected the video.",
                )
            background_suitability = "validated_by_caller"
        return ArchiveBrowserExportResult(
            bucket=target_bucket,
            storage_path=target_path,
            source_raw_path=source_path,
            size_bytes=len(data),
            converted=converted,
            deduplicated=deduplicated,
            probe=output_probe,
            background_suitability=background_suitability,
            customer_delivery_ready=background_validator is not None,
        )


async def validate_archive_browser_mp4_bytes(
    data: bytes,
    *,
    bucket: str,
    storage_path: str,
    max_bytes: int = ARCHIVE_MAX_VIDEO_BYTES,
    command_runner: CommandRunner | None = None,
) -> ArchiveBrowserExportResult:
    """Validate an already-exported MP4 without converting or uploading it."""

    return await _validate_export_bytes(
        data,
        source_duration_sec=None,
        max_bytes=max_bytes,
        duration_tolerance_sec=DEFAULT_DURATION_TOLERANCE_SEC,
        command_runner=command_runner or _run_command,
        command_timeout_sec=600.0,
        background_validator=None,
        source_path=storage_path,
        target_bucket=bucket,
        target_path=storage_path,
        converted=False,
        deduplicated=True,
    )
