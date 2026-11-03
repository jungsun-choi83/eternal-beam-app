from __future__ import annotations

import logging

import numpy as np
import pytest

from backend.services import archive_preparation_diagnostics as diagnostics
from backend.services import pet_cutout_service
from backend.services import pet_reference_service
from backend.services import supabase_assets
from backend.services import vitmatte_service as vs
from backend.services.cutout_errors import SubjectNotDetectedError
from .conftest import make_jpeg_bytes


@pytest.fixture(autouse=True)
def _mock_archive_storage(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    pet_reference_service.__reset_for_tests()
    yield
    pet_reference_service.__reset_for_tests()


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _seed_original(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str, dict[str, bytes]]:
    objects: dict[str, bytes] = {}

    async def fake_upload(path: str, data: bytes, content_type: str, **kwargs) -> str:
        objects[path] = bytes(data)
        return f"mock://{path}"

    async def fake_download(path: str, *, bucket: str | None = None) -> bytes:
        return objects[path]

    monkeypatch.setattr(supabase_assets, "upload_asset_for_scope", fake_upload)
    monkeypatch.setattr(supabase_assets, "download_asset_from_storage", fake_download)

    content_id = "archive_diagnostic_app"
    user_id = "archive_diagnostic_app"
    original = await pet_reference_service.record_original(
        user_id=user_id,
        content_id=content_id,
        data=make_jpeg_bytes(64, 64),
        mime_type="image/jpeg",
        bucket="user-assets",
        require_private=True,
    )
    return user_id, original.pet_id, objects


@pytest.mark.anyio
async def test_archive_preparation_logs_all_safe_stage_fields(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setenv("ETERNAL_BEAM_PIPELINE_TIMING", "1")
    user_id, pet_id, _objects = await _seed_original(monkeypatch)
    mask = np.zeros((64, 64), dtype=np.uint8)
    yy, xx = np.ogrid[:64, :64]
    mask[((xx - 32) / 15) ** 2 + ((yy - 32) / 20) ** 2 <= 1] = 255

    monkeypatch.setattr(
        vs,
        "_detect_subject",
        lambda *args, **kwargs: vs.SubjectDetection(
            bbox=(18, 12, 46, 52), class_id=16, class_name="dog", confidence=0.9
        ),
    )
    monkeypatch.setattr(
        vs,
        "_segment_foreground",
        lambda *args, **kwargs: vs.SegmentationOutcome(
            fg_binary=mask,
            trimap=vs._binary_mask_to_trimap(mask),
            segmenter_used="sam2",
            sam2_score=0.9,
        ),
    )
    monkeypatch.setattr(
        vs,
        "_run_vitmatte_roi",
        lambda rgb, trimap, model_name, device, *, crop_bbox: (
            (trimap == 255).astype(np.float32),
            {"enabled": False},
        ),
    )

    observer = diagnostics.observer("archive-diagnostic-app")
    with caplog.at_level(logging.INFO, logger=diagnostics.logger.name):
        prepared = await pet_cutout_service.prepare_strict_cutouts(
            user_id=user_id, pet_id=pet_id, stage_observer=observer
        )

    assert len(prepared.pairs) == 1
    records = [
        record
        for record in caplog.records
        if record.name == diagnostics.logger.name
    ]
    assert {record.stage for record in records} == diagnostics.STAGES
    for record in records:
        assert record.application_id == "archive-diagnostic-app"
        assert record.duration_ms >= 0
        assert record.error_code == "OK"
        assert record.traceback is None


@pytest.mark.anyio
async def test_archive_preparation_preserves_typed_errors_and_redacts_traceback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setenv("ETERNAL_BEAM_PIPELINE_TIMING", "1")
    user_id, pet_id, _objects = await _seed_original(monkeypatch)
    secret = "signed-url=customer-secret"

    async def fail_generate(data: bytes, **kwargs):
        raise SubjectNotDetectedError(secret)

    monkeypatch.setattr(pet_cutout_service, "generate_vitmatte_cutout", fail_generate)
    observer = diagnostics.observer("archive-diagnostic-failure")

    with caplog.at_level(logging.WARNING, logger=diagnostics.logger.name):
        with pytest.raises(SubjectNotDetectedError) as caught:
            await pet_cutout_service.prepare_strict_cutouts(
                user_id=user_id, pet_id=pet_id, stage_observer=observer
            )

    assert caught.value.code == "SUBJECT_NOT_DETECTED"
    failure = next(
        record
        for record in caplog.records
        if record.name == diagnostics.logger.name
        and record.stage == "VITMATTE"
        and record.error_code == "SUBJECT_NOT_DETECTED"
    )
    assert secret not in str(failure.traceback)
    assert "customer-secret" not in caplog.text


def test_initial_queue_wait_log_is_opt_in_and_uses_claim_timestamps(monkeypatch, caplog):
    monkeypatch.setenv("ETERNAL_BEAM_PIPELINE_TIMING", "1")
    with caplog.at_level(logging.INFO, logger=diagnostics.logger.name):
        diagnostics.report_initial_queue_wait({
            "archive_application_id": "app-queue",
            "preparation_attempt": 1,
            "created_at": "2026-11-02T12:51:00+00:00",
            "updated_at": "2026-11-02T15:09:00+00:00",
        })
    event = next(record for record in caplog.records if getattr(record, "event", None) == "eternal_beam_pipeline_timing")
    assert event.application_id == "app-queue"
    assert event.generation_run_id is None
    assert event.stage == "archive_preparation_queue_wait"
    assert event.duration_ms == 138 * 60 * 1000
