from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

from backend.services import archive_browser_export as export
from backend.services import supabase_assets


RAW_PATH = "archive_app_123/archive_app_123/motions/breathing/v1/model_a1_raw.mp4"
PACKED_PATH = "archive_app_123/archive_app_123/motions/breathing/v1/model_a1_packed.mp4"
BUCKET = "user-assets"
APPROVED_FIXTURE = Path(
    os.getenv(
        "ARCHIVE_APPROVED_MP4_FIXTURE",
        str(
            Path(__file__).resolve().parents[2].parent
            / "eternalbeam-threads-ops"
            / "tests"
            / "fixtures"
            / "approved-playable-raw.mp4"
        ),
    )
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _private_bucket_verifier(bucket: str) -> bool:
    return bucket == BUCKET


class _FakeStorageBucket:
    def __init__(self):
        self.uploads = []

    def upload(self, path, data, options):
        self.uploads.append((path, data, options))

    def get_public_url(self, path):  # pragma: no cover - must never be called
        raise AssertionError("private upload must not request public URLs")

    def create_signed_url(self, path, seconds):  # pragma: no cover
        raise AssertionError("private upload must not request signed URLs")


class _FakeStorageClient:
    def __init__(self, public=False):
        self.public = public
        self.bucket = _FakeStorageBucket()
        self.storage = self

    def get_bucket(self, bucket):
        return {"public": self.public}

    def from_(self, bucket):
        return self.bucket


def _box(kind: str, payload: bytes = b"") -> bytes:
    size = 8 + len(payload)
    return size.to_bytes(4, "big") + kind.encode("ascii") + payload


def _mp4_bytes(*, faststart: bool = True, marker: bytes = b"raw") -> bytes:
    ftyp = _box("ftyp", b"isom0000")
    moov = _box("moov", marker)
    mdat = _box("mdat", b"payload")
    return ftyp + moov + mdat if faststart else ftyp + mdat + moov


def _probe_json(
    *,
    codec: str = "h264",
    pix_fmt: str = "yuv420p",
    width: int = 4,
    height: int = 4,
    duration: float = 2.0,
    audio_codec: str | None = None,
) -> bytes:
    streams = [
        {
            "codec_type": "video",
            "codec_name": codec,
            "pix_fmt": pix_fmt,
            "width": width,
            "height": height,
            "duration": str(duration),
            "r_frame_rate": "24/1",
        }
    ]
    if audio_codec:
        streams.append({"codec_type": "audio", "codec_name": audio_codec})
    return json.dumps(
        {
            "streams": streams,
            "format": {
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                "duration": str(duration),
            },
        }
    ).encode()


class FakeCommands:
    def __init__(self, tmp_path: Path, *, source_probe: bytes, output_probe: bytes | None = None):
        self.tmp_path = tmp_path
        self.source_probe = source_probe
        self.output_probe = output_probe or source_probe
        self.calls: list[list[str]] = []
        self.fail_ffprobe = False
        self.fail_ffmpeg = False

    def __call__(self, args, *, timeout):
        args = list(args)
        self.calls.append(args)
        if args[0] == "ffprobe":
            if self.fail_ffprobe:
                return subprocess.CompletedProcess(args, 1, b"", b"probe failed")
            payload = self.output_probe if str(args[-1]).endswith("browser.mp4") else self.source_probe
            return subprocess.CompletedProcess(args, 0, payload, b"")
        if args[0] == "ffmpeg":
            if self.fail_ffmpeg:
                return subprocess.CompletedProcess(args, 1, b"", b"encode failed")
            if args[-1] == "-":
                return subprocess.CompletedProcess(args, 0, b"\x80" * (4 * 4 * 3), b"")
            Path(args[-1]).write_bytes(_mp4_bytes(marker=b"converted"))
            return subprocess.CompletedProcess(args, 0, b"", b"")
        raise AssertionError(args)


@pytest.fixture
def storage():
    return {RAW_PATH: _mp4_bytes(marker=b"raw-original"), PACKED_PATH: b"packed-original"}


def _storage_functions(storage):
    downloads: list[str] = []
    uploads: list[str] = []

    async def download(path: str, bucket: str) -> bytes:
        downloads.append(path)
        if path not in storage:
            raise FileNotFoundError(path)
        return storage[path]

    async def upload(path: str, data: bytes, content_type: str, bucket: str):
        assert content_type == "video/mp4"
        uploads.append(path)
        storage[path] = data
        return path

    return download, upload, downloads, uploads


def test_tail_seek_uses_a_decodable_frame_window():
    probe = export.BrowserVideoProbe(
        codec_name="h264",
        pixel_format="yuv420p",
        format_name="mov,mp4",
        width=832,
        height=480,
        duration_sec=5.03125,
        fps=32.0,
        has_audio=False,
    )
    assert export._tail_seek_seconds(probe) == 0.0625


@pytest.mark.anyio
async def test_approved_archive_fixture_passes_real_opaque_frame_validation():
    if not APPROVED_FIXTURE.is_file():
        pytest.fail(f"Approved Archive fixture is missing: {APPROVED_FIXTURE}")
    result = await export.validate_archive_browser_mp4_bytes(
        APPROVED_FIXTURE.read_bytes(),
        bucket=BUCKET,
        storage_path="archive-fixture/raw.mp4",
    )
    assert result.probe.codec_name == "h264"
    assert result.probe.pixel_format == "yuv420p"
    assert result.probe.fps == 32.0


@pytest.mark.anyio
async def test_truncated_archive_fixture_is_rejected_by_real_decode():
    if not APPROVED_FIXTURE.is_file():
        pytest.fail(f"Approved Archive fixture is missing: {APPROVED_FIXTURE}")
    with pytest.raises(export.ArchiveBrowserExportError) as exc:
        await export.validate_archive_browser_mp4_bytes(
            APPROVED_FIXTURE.read_bytes()[:-4096],
            bucket=BUCKET,
            storage_path="archive-fixture/truncated.mp4",
        )
    assert exc.value.code in {
        "FFPROBE_FAILED",
        "FFPROBE_INVALID",
        "OPAQUE_FRAME_DECODE_FAILED",
        "BROWSER_VIDEO_NOT_FASTSTART",
    }


@pytest.mark.anyio
async def test_transparent_video_probe_is_rejected_before_frame_decode(tmp_path, storage):
    download, upload, _, uploads = _storage_functions(storage)
    commands = FakeCommands(tmp_path, source_probe=_probe_json(pix_fmt="yuva420p"))

    with pytest.raises(export.ArchiveBrowserExportError) as exc:
        await export.export_archive_browser_mp4(
            raw_video_path=RAW_PATH,
            application_id="archive_app_123",
            generation_run_id="run_transparent",
            bucket=BUCKET,
            download_fn=download,
            upload_fn=upload,
            bucket_privacy_verifier=_private_bucket_verifier,
            command_runner=commands,
        )

    assert exc.value.code == "BROWSER_VIDEO_INCOMPATIBLE"
    assert uploads == []


@pytest.mark.anyio
async def test_successful_export_is_private_deterministic_and_preserves_raw_and_packed(
    tmp_path, storage
):
    download, upload, downloads, uploads = _storage_functions(storage)
    commands = FakeCommands(tmp_path, source_probe=_probe_json())
    raw_before = storage[RAW_PATH]
    packed_before = storage[PACKED_PATH]

    result = await export.export_archive_browser_mp4(
        raw_video_path=RAW_PATH,
        application_id="archive_app_123",
        generation_run_id="run_123",
        bucket=BUCKET,
        download_fn=download,
        upload_fn=upload,
        bucket_privacy_verifier=_private_bucket_verifier,
        command_runner=commands,
    )

    assert result.storage_path == "archive-intake/archive_app_123/runs/run_123/browser.mp4"
    assert result.converted is False
    assert result.background_suitability == "not_evaluated"
    assert result.customer_delivery_ready is False
    assert uploads == [result.storage_path]
    assert storage[RAW_PATH] == raw_before
    assert storage[PACKED_PATH] == packed_before
    assert storage[result.storage_path] == raw_before
    assert downloads[:2] == [result.storage_path, RAW_PATH]
    assert any(call[0] == "ffprobe" for call in commands.calls)
    assert sum(call[0] == "ffmpeg" for call in commands.calls) == 2


@pytest.mark.anyio
async def test_nonfaststart_mp4_is_rejected_by_browser_validation(tmp_path):
    commands = FakeCommands(tmp_path, source_probe=_probe_json())

    with pytest.raises(export.ArchiveBrowserExportError) as exc:
        await export.validate_archive_browser_mp4_bytes(
            _mp4_bytes(faststart=False),
            bucket=BUCKET,
            storage_path=RAW_PATH,
            command_runner=commands,
        )

    assert exc.value.code == "BROWSER_VIDEO_NOT_FASTSTART"


@pytest.mark.anyio
async def test_nonfaststart_compatible_raw_uses_copy_only_faststart_remux(tmp_path):
    storage = {RAW_PATH: _mp4_bytes(faststart=False, marker=b"original")}
    download, upload, _, uploads = _storage_functions(storage)
    commands = FakeCommands(tmp_path, source_probe=_probe_json())

    result = await export.export_archive_browser_mp4(
        raw_video_path=RAW_PATH,
        application_id="archive_app_123",
        generation_run_id="run_remux",
        bucket=BUCKET,
        download_fn=download,
        upload_fn=upload,
        bucket_privacy_verifier=_private_bucket_verifier,
        command_runner=commands,
    )

    assert result.converted is True
    assert uploads == [result.storage_path]
    remux_calls = [call for call in commands.calls if call[0] == "ffmpeg" and "-c" in call]
    assert len(remux_calls) == 1
    assert remux_calls[0][remux_calls[0].index("-c") + 1] == "copy"
    assert "+faststart" in remux_calls[0]
    assert "libx264" not in remux_calls[0]
    assert export._has_faststart_mp4(storage[result.storage_path])


@pytest.mark.anyio
async def test_raw_mp4_repair_validates_before_same_path_replace_and_is_duplicate_safe(tmp_path):
    storage = {RAW_PATH: _mp4_bytes(faststart=False, marker=b"original")}
    download, _, _, _ = _storage_functions(storage)
    replacements: list[str] = []

    async def replace(path: str, data: bytes, content_type: str, bucket: str):
        assert content_type == "video/mp4"
        assert bucket == BUCKET
        replacements.append(path)
        storage[path] = data

    commands = FakeCommands(tmp_path, source_probe=_probe_json())
    original = storage[RAW_PATH]
    first = await export.repair_archive_raw_mp4(
        storage_path=RAW_PATH,
        bucket=BUCKET,
        download_fn=download,
        replace_fn=replace,
        bucket_privacy_verifier=_private_bucket_verifier,
        command_runner=commands,
    )

    assert first.remuxed is True
    assert first.deduplicated is False
    assert replacements == [RAW_PATH]
    assert storage[RAW_PATH] != original
    assert export._has_faststart_mp4(storage[RAW_PATH])

    second = await export.repair_archive_raw_mp4(
        storage_path=RAW_PATH,
        bucket=BUCKET,
        download_fn=download,
        replace_fn=replace,
        bucket_privacy_verifier=_private_bucket_verifier,
        command_runner=commands,
    )
    assert second.remuxed is False
    assert second.deduplicated is True
    assert replacements == [RAW_PATH]


@pytest.mark.anyio
async def test_raw_mp4_repair_keeps_original_when_remux_fails(tmp_path):
    storage = {RAW_PATH: _mp4_bytes(faststart=False, marker=b"original")}
    download, _, _, _ = _storage_functions(storage)
    replacements: list[str] = []

    async def replace(path: str, data: bytes, content_type: str, bucket: str):
        replacements.append(path)

    commands = FakeCommands(tmp_path, source_probe=_probe_json())
    commands.fail_ffmpeg = True
    original = storage[RAW_PATH]

    with pytest.raises(export.ArchiveBrowserExportError) as exc:
        await export.repair_archive_raw_mp4(
            storage_path=RAW_PATH,
            bucket=BUCKET,
            download_fn=download,
            replace_fn=replace,
            bucket_privacy_verifier=_private_bucket_verifier,
            command_runner=commands,
        )

    assert exc.value.code == "FFMPEG_FASTSTART_FAILED"
    assert replacements == []
    assert storage[RAW_PATH] == original


@pytest.mark.anyio
async def test_incompatible_raw_is_converted_to_h264_yuv420p_faststart(tmp_path, storage):
    download, upload, _, _ = _storage_functions(storage)
    commands = FakeCommands(
        tmp_path,
        source_probe=_probe_json(codec="vp9", pix_fmt="yuv444p", duration=2.0),
        output_probe=_probe_json(codec="h264", pix_fmt="yuv420p", duration=2.0),
    )

    result = await export.export_archive_browser_mp4(
        raw_video_path=RAW_PATH,
        application_id="archive_app_123",
        generation_run_id="run_convert",
        bucket=BUCKET,
        download_fn=download,
        upload_fn=upload,
        bucket_privacy_verifier=_private_bucket_verifier,
        command_runner=commands,
        background_validator=lambda data, probe: True,
    )

    assert result.converted is True
    assert result.probe.codec_name == "h264"
    assert result.probe.pixel_format == "yuv420p"
    assert result.background_suitability == "validated_by_caller"
    assert result.customer_delivery_ready is True
    ffmpeg_calls = [call for call in commands.calls if call[0] == "ffmpeg"]
    assert any("libx264" in call and "+faststart" in call for call in ffmpeg_calls)


@pytest.mark.anyio
async def test_existing_valid_export_is_idempotent_without_reencoding(tmp_path, storage):
    download, upload, downloads, uploads = _storage_functions(storage)
    target = export.deterministic_browser_object_path("archive_app_123", "run_existing")
    storage[target] = _mp4_bytes(marker=b"existing")
    commands = FakeCommands(tmp_path, source_probe=_probe_json())

    result = await export.export_archive_browser_mp4(
        raw_video_path=RAW_PATH,
        application_id="archive_app_123",
        generation_run_id="run_existing",
        bucket=BUCKET,
        download_fn=download,
        upload_fn=upload,
        bucket_privacy_verifier=_private_bucket_verifier,
        command_runner=commands,
    )

    assert result.deduplicated is True
    assert uploads == []
    assert downloads == [target]
    assert not any("libx264" in call for call in commands.calls)


@pytest.mark.anyio
async def test_unverified_bucket_fails_closed_before_storage_access(tmp_path, storage):
    download, upload, downloads, uploads = _storage_functions(storage)
    commands = FakeCommands(tmp_path, source_probe=_probe_json())

    async def unverified_bucket(bucket: str) -> bool:
        return False

    with pytest.raises(export.ArchiveBrowserExportError) as exc:
        await export.export_archive_browser_mp4(
            raw_video_path=RAW_PATH,
            application_id="archive_app_123",
            generation_run_id="run_unverified_bucket",
            bucket=BUCKET,
            download_fn=download,
            upload_fn=upload,
            bucket_privacy_verifier=unverified_bucket,
            command_runner=commands,
        )

    assert exc.value.code == "PRIVATE_BUCKET_UNVERIFIED"
    assert downloads == []
    assert uploads == []


@pytest.mark.anyio
async def test_missing_bucket_does_not_default_to_general_storage(tmp_path, storage):
    download, upload, downloads, uploads = _storage_functions(storage)
    commands = FakeCommands(tmp_path, source_probe=_probe_json())

    with pytest.raises(export.ArchiveBrowserExportError) as exc:
        await export.export_archive_browser_mp4(
            raw_video_path=RAW_PATH,
            application_id="archive_app_123",
            generation_run_id="run_missing_bucket",
            download_fn=download,
            upload_fn=upload,
            bucket_privacy_verifier=_private_bucket_verifier,
            command_runner=commands,
        )

    assert exc.value.code == "PRIVATE_BUCKET_REQUIRED"
    assert downloads == []
    assert uploads == []


@pytest.mark.anyio
async def test_storage_permission_error_is_not_treated_as_missing(tmp_path, storage):
    _, upload, downloads, uploads = _storage_functions(storage)
    commands = FakeCommands(tmp_path, source_probe=_probe_json())

    async def denied_download(path: str, bucket: str) -> bytes:
        downloads.append(path)
        raise PermissionError("storage permission denied")

    with pytest.raises(export.ArchiveBrowserExportError) as exc:
        await export.export_archive_browser_mp4(
            raw_video_path=RAW_PATH,
            application_id="archive_app_123",
            generation_run_id="run_permission_denied",
            bucket=BUCKET,
            download_fn=denied_download,
            upload_fn=upload,
            bucket_privacy_verifier=_private_bucket_verifier,
            command_runner=commands,
        )

    assert exc.value.code == "STORAGE_READ_FAILED"
    assert downloads == ["archive-intake/archive_app_123/runs/run_permission_denied/browser.mp4"]
    assert uploads == []


@pytest.mark.anyio
async def test_same_run_exports_are_serialized_with_one_conversion(tmp_path, storage):
    download, upload, _, _ = _storage_functions(storage)
    commands = FakeCommands(
        tmp_path,
        source_probe=_probe_json(codec="vp9", pix_fmt="yuv444p"),
        output_probe=_probe_json(),
    )

    async def run_once():
        return await export.export_archive_browser_mp4(
            raw_video_path=RAW_PATH,
            application_id="archive_app_123",
            generation_run_id="run_concurrent",
            bucket=BUCKET,
            download_fn=download,
            upload_fn=upload,
            bucket_privacy_verifier=_private_bucket_verifier,
            command_runner=commands,
        )

    first, second = await asyncio.gather(run_once(), run_once())

    assert {first.deduplicated, second.deduplicated} == {False, True}
    assert sum("libx264" in call for call in commands.calls) == 1


@pytest.mark.anyio
async def test_upload_race_does_not_overwrite_existing_winner(tmp_path, storage):
    download, _, _, _ = _storage_functions(storage)
    commands = FakeCommands(tmp_path, source_probe=_probe_json())
    target = export.deterministic_browser_object_path("archive_app_123", "run_race")
    winner = _mp4_bytes(marker=b"winner")

    async def racing_upload(path: str, data: bytes, content_type: str, bucket: str):
        storage[path] = winner
        raise FileExistsError(path)

    result = await export.export_archive_browser_mp4(
        raw_video_path=RAW_PATH,
        application_id="archive_app_123",
        generation_run_id="run_race",
        bucket=BUCKET,
        download_fn=download,
        upload_fn=racing_upload,
        bucket_privacy_verifier=_private_bucket_verifier,
        command_runner=commands,
    )

    assert result.deduplicated is True
    assert storage[target] == winner


@pytest.mark.anyio
async def test_source_input_limit_rejects_before_ffmpeg(tmp_path, storage):
    download, upload, _, uploads = _storage_functions(storage)
    commands = FakeCommands(tmp_path, source_probe=_probe_json())

    with pytest.raises(export.ArchiveBrowserExportError) as exc:
        await export.export_archive_browser_mp4(
            raw_video_path=RAW_PATH,
            application_id="archive_app_123",
            generation_run_id="run_input_limit",
            bucket=BUCKET,
            download_fn=download,
            upload_fn=upload,
            bucket_privacy_verifier=_private_bucket_verifier,
            command_runner=commands,
            max_source_bytes=4,
        )

    assert exc.value.code == "RAW_VIDEO_TOO_LARGE"
    assert not any(call[0] == "ffprobe" for call in commands.calls)
    assert uploads == []


@pytest.mark.anyio
async def test_private_bucket_verification_and_upload_are_mocked(monkeypatch):
    client = _FakeStorageClient(public=False)
    monkeypatch.setattr(supabase_assets, "_client", lambda: client)

    assert await supabase_assets.verify_private_storage_bucket(BUCKET) is True
    await supabase_assets.upload_private_asset_to_storage(
        "archive-intake/app/runs/run/browser.mp4",
        b"video",
        "video/mp4",
        bucket=BUCKET,
        required_prefix="archive-intake/",
    )

    assert client.bucket.uploads[0][2] == {"content-type": "video/mp4", "upsert": "false"}


@pytest.mark.anyio
async def test_public_bucket_verification_is_rejected(monkeypatch):
    client = _FakeStorageClient(public=True)
    monkeypatch.setattr(supabase_assets, "_client", lambda: client)

    assert await supabase_assets.verify_private_storage_bucket(BUCKET) is False


def test_missing_ffmpeg_command_is_reported_without_raising():
    result = export._run_command(["__missing_ffmpeg_for_test__"], timeout=1)
    assert result.returncode == 127


@pytest.mark.anyio
async def test_background_validator_can_reject_opaque_export(tmp_path, storage):
    download, upload, _, uploads = _storage_functions(storage)
    commands = FakeCommands(tmp_path, source_probe=_probe_json())

    with pytest.raises(export.ArchiveBrowserExportError) as exc:
        await export.export_archive_browser_mp4(
            raw_video_path=RAW_PATH,
            application_id="archive_app_123",
            generation_run_id="run_bad_background",
            bucket=BUCKET,
            download_fn=download,
            upload_fn=upload,
            bucket_privacy_verifier=_private_bucket_verifier,
            command_runner=commands,
            background_validator=lambda data, probe: False,
        )

    assert exc.value.code == "BACKGROUND_UNSUITABLE"
    assert uploads == []


@pytest.mark.anyio
async def test_ffprobe_failure_is_reported_and_does_not_upload(tmp_path, storage):
    download, upload, _, uploads = _storage_functions(storage)
    commands = FakeCommands(tmp_path, source_probe=_probe_json())
    commands.fail_ffprobe = True

    with pytest.raises(export.ArchiveBrowserExportError) as exc:
        await export.export_archive_browser_mp4(
            raw_video_path=RAW_PATH,
            application_id="archive_app_123",
            generation_run_id="run_probe_failure",
            bucket=BUCKET,
            download_fn=download,
            upload_fn=upload,
        bucket_privacy_verifier=_private_bucket_verifier,
            command_runner=commands,
        )

    assert exc.value.code == "FFPROBE_FAILED"
    assert uploads == []


@pytest.mark.anyio
async def test_ffmpeg_failure_is_reported_and_does_not_upload(tmp_path, storage):
    download, upload, _, uploads = _storage_functions(storage)
    commands = FakeCommands(
        tmp_path,
        source_probe=_probe_json(codec="vp9", pix_fmt="yuv444p"),
        output_probe=_probe_json(),
    )
    commands.fail_ffmpeg = True

    with pytest.raises(export.ArchiveBrowserExportError) as exc:
        await export.export_archive_browser_mp4(
            raw_video_path=RAW_PATH,
            application_id="archive_app_123",
            generation_run_id="run_ffmpeg_failure",
            bucket=BUCKET,
            download_fn=download,
            upload_fn=upload,
            bucket_privacy_verifier=_private_bucket_verifier,
            command_runner=commands,
        )

    assert exc.value.code == "FFMPEG_FAILED"
    assert uploads == []


@pytest.mark.anyio
async def test_oversized_export_is_rejected_and_existing_artifacts_remain_unchanged(tmp_path, storage):
    download, upload, _, uploads = _storage_functions(storage)
    commands = FakeCommands(tmp_path, source_probe=_probe_json())
    raw_before = storage[RAW_PATH]
    packed_before = storage[PACKED_PATH]

    with pytest.raises(export.ArchiveBrowserExportError) as exc:
        await export.export_archive_browser_mp4(
            raw_video_path=RAW_PATH,
            application_id="archive_app_123",
            generation_run_id="run_too_large",
            bucket=BUCKET,
            download_fn=download,
            upload_fn=upload,
            bucket_privacy_verifier=_private_bucket_verifier,
            command_runner=commands,
            max_bytes=8,
        )

    assert exc.value.code == "BROWSER_VIDEO_TOO_LARGE"
    assert uploads == []
    assert storage[RAW_PATH] == raw_before
    assert storage[PACKED_PATH] == packed_before


@pytest.mark.anyio
async def test_direct_browser_validation_rejects_unsupported_audio_codec(tmp_path, storage):
    download, upload, _, uploads = _storage_functions(storage)
    commands = FakeCommands(
        tmp_path,
        source_probe=_probe_json(audio_codec="opus"),
    )

    with pytest.raises(export.ArchiveBrowserExportError) as exc:
        await export.export_archive_browser_mp4(
            raw_video_path=RAW_PATH,
            application_id="archive_app_123",
            generation_run_id="run_bad_audio",
            bucket=BUCKET,
            download_fn=download,
            upload_fn=upload,
            bucket_privacy_verifier=_private_bucket_verifier,
            command_runner=commands,
        )

    assert exc.value.code == "BROWSER_AUDIO_INCOMPATIBLE"
    assert uploads == []


@pytest.mark.anyio
async def test_direct_browser_validation_rejects_excessive_duration(tmp_path, storage):
    download, upload, _, uploads = _storage_functions(storage)
    commands = FakeCommands(
        tmp_path,
        source_probe=_probe_json(duration=export.ARCHIVE_MAX_SOURCE_DURATION_SEC + 1),
    )

    with pytest.raises(export.ArchiveBrowserExportError) as exc:
        await export.export_archive_browser_mp4(
            raw_video_path=RAW_PATH,
            application_id="archive_app_123",
            generation_run_id="run_long",
            bucket=BUCKET,
            download_fn=download,
            upload_fn=upload,
            bucket_privacy_verifier=_private_bucket_verifier,
            command_runner=commands,
        )

    assert exc.value.code == "RAW_VIDEO_DURATION_TOO_LONG"
    assert uploads == []
