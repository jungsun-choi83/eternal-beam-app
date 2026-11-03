from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from backend.routers import archive_intake_v1
from backend.services import archive_intake_preparation_service
from backend.services import archive_intake_service
from backend.services import pet_cutout_service
from backend.services import pet_generation_run_service
from backend.services import pet_reference_service
from backend.services import pet_registry
from backend.workers import archive_intake_preparation_worker
from .conftest import make_jpeg_bytes, make_rgba_png_bytes


SERVICE_TOKEN = "archive-async-test-secret"
ENDPOINT = "/api/internal/archive-intake"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def async_archive_app(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("ARCHIVE_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("BUSINESS_QA_USER_TEST_MODE", "off")
    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()
    archive_intake_service.__reset_for_tests()
    archive_intake_preparation_service.__reset_for_tests()
    pet_generation_run_service.__reset_for_tests()

    objects: dict[str, bytes] = {}
    generation_calls: list[bytes] = []

    async def fake_upload(
        path: str, data: bytes, content_type: str, **kwargs
    ) -> str:
        objects[path] = data
        return f"mock://{path}"

    async def fake_download(path: str, *, bucket: str | None = None) -> bytes:
        return objects[path]

    async def fake_generate(data: bytes, **kwargs) -> pet_cutout_service.GeneratedCutout:
        generation_calls.append(data)
        return pet_cutout_service.GeneratedCutout(
            png=make_rgba_png_bytes(0.5),
            diagnostics={"quality_score": 0.9, "subject_detected": True},
        )

    monkeypatch.setattr(
        "backend.services.supabase_assets.upload_asset_to_storage", fake_upload
    )
    monkeypatch.setattr(
        "backend.services.supabase_assets.download_asset_from_storage", fake_download
    )
    monkeypatch.setattr(
        pet_cutout_service, "generate_vitmatte_cutout", fake_generate
    )

    app = FastAPI()
    app.include_router(archive_intake_v1.router, prefix="/api")
    yield app, generation_calls

    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()
    archive_intake_service.__reset_for_tests()
    archive_intake_preparation_service.__reset_for_tests()
    pet_generation_run_service.__reset_for_tests()


async def _post(app: FastAPI, application_id: str = "async-application"):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            ENDPOINT,
            headers={"X-Archive-Service-Token": SERVICE_TOKEN},
            data={
                "application_id": application_id,
                "customer_email": "test@example.com",
                "pet_name": "Goya",
                "pet_type": "dog",
                "breed": "Shiba Inu",
            },
            files=[
                (
                    "photos",
                    (
                        "pet.jpg",
                        make_jpeg_bytes(width=80, height=60),
                        "image/jpeg",
                    ),
                )
            ],
        )


async def _status(app: FastAPI, application_id: str):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(
            f"{ENDPOINT}/{application_id}/status",
            headers={"X-Archive-Service-Token": SERVICE_TOKEN},
        )


@pytest.mark.anyio
async def test_intake_acknowledges_preparation_before_cutout(async_archive_app):
    app, generation_calls = async_archive_app

    response = await _post(app)

    assert response.status_code == 201
    assert response.json()["status"] == "PREPARING"
    assert response.json()["generation_run_id"] is None
    assert generation_calls == []

    status = await _status(app, "async-application")
    assert status.status_code == 200
    assert status.json()["preparation_stage"] == "QUEUED"
    assert status.json()["preparation_progress"] == 0
    assert status.json()["preparation_error_code"] is None


@pytest.mark.anyio
async def test_worker_reuses_cutout_and_creates_one_idempotent_run(async_archive_app):
    app, generation_calls = async_archive_app
    response = await _post(app, "queued-application")
    assert response.status_code == 201

    result = await archive_intake_preparation_worker.run_once("worker-a")

    assert result is not None
    assert archive_intake_service.mock_get("queued-application")["status"] == (
        archive_intake_service.STATUS_GENERATION_QUEUED
    )
    assert len(generation_calls) == 1
    assert len(pet_generation_run_service._MOCK_RUNS) == 1
    assert pet_generation_run_service._MOCK_RUNS[0]["idempotency_key"] == (
        "free-home:archive_queued-application"
    )
    status = await _status(app, "queued-application")
    assert status.json()["preparation_stage"] == "COMPLETE"
    assert status.json()["preparation_progress"] == 100


@pytest.mark.anyio
async def test_long_running_preparation_has_one_claimed_worker(async_archive_app, monkeypatch):
    app, generation_calls = async_archive_app
    assert (await _post(app, "slow-application")).status_code == 201
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_generate(data: bytes, **kwargs) -> pet_cutout_service.GeneratedCutout:
        generation_calls.append(data)
        started.set()
        await release.wait()
        return pet_cutout_service.GeneratedCutout(
            png=make_rgba_png_bytes(0.5), diagnostics={"subject_detected": True}
        )

    monkeypatch.setattr(pet_cutout_service, "generate_vitmatte_cutout", slow_generate)
    first = asyncio.create_task(
        archive_intake_preparation_worker.run_once("worker-a")
    )
    await started.wait()
    second = await archive_intake_preparation_worker.run_once("worker-b")

    assert second is None
    assert len(generation_calls) == 1
    release.set()
    await first
    assert len(pet_generation_run_service._MOCK_RUNS) == 1


@pytest.mark.anyio
async def test_preparation_failure_is_safe_and_retryable(async_archive_app, monkeypatch):
    app, generation_calls = async_archive_app
    application_id = "failed-application"
    assert (await _post(app, application_id)).status_code == 201

    from backend.services.cutout_errors import SubjectNotDetectedError

    async def fail_generate(data: bytes, **kwargs):
        raise SubjectNotDetectedError("provider-secret-must-not-be-persisted")

    monkeypatch.setattr(pet_cutout_service, "generate_vitmatte_cutout", fail_generate)
    await archive_intake_preparation_worker.run_once("worker-a")

    failed = archive_intake_service.mock_get(application_id)
    assert failed["status"] == archive_intake_service.STATUS_GENERATION_FAILED
    assert failed["last_error"] == "SUBJECT_NOT_DETECTED"
    assert "provider-secret" not in repr(failed)
    status = await _status(app, application_id)
    assert status.json()["preparation_error_code"] == "SUBJECT_NOT_DETECTED"
    assert pet_generation_run_service._MOCK_RUNS == []

    async def succeed_generate(data: bytes, **kwargs):
        generation_calls.append(data)
        return pet_cutout_service.GeneratedCutout(
            png=make_rgba_png_bytes(0.5), diagnostics={"subject_detected": True}
        )

    monkeypatch.setattr(pet_cutout_service, "generate_vitmatte_cutout", succeed_generate)
    recovery = await archive_intake_service.request_preparation_recovery(application_id)
    assert recovery.outcome == archive_intake_service.RECOVERY_QUEUED
    await archive_intake_preparation_worker.run_once("worker-retry")
    assert archive_intake_service.mock_get(application_id)["status"] == (
        archive_intake_service.STATUS_GENERATION_QUEUED
    )
    assert len(pet_generation_run_service._MOCK_RUNS) == 1


@pytest.mark.anyio
async def test_expired_lease_is_reclaimed_after_worker_restart(async_archive_app):
    app, generation_calls = async_archive_app
    application_id = "restart-application"
    assert (await _post(app, application_id)).status_code == 201

    claimed = await archive_intake_service.claim_next_preparation("dead-worker")
    assert claimed is not None
    assert await archive_intake_preparation_worker.run_once("new-worker") is None

    row = archive_intake_service.mock_get(application_id)
    row["preparation_lease_expires_at"] = "2000-01-01T00:00:00+00:00"
    await archive_intake_preparation_worker.run_once("new-worker")

    final = archive_intake_service.mock_get(application_id)
    assert final["status"] == archive_intake_service.STATUS_GENERATION_QUEUED
    assert final["preparation_attempt"] == 2
    assert len(pet_generation_run_service._MOCK_RUNS) == 1
    assert len(generation_calls) == 1


@pytest.mark.anyio
async def test_single_application_worker_cannot_claim_unrelated_queue_entries(
    async_archive_app, monkeypatch: pytest.MonkeyPatch
):
    app, generation_calls = async_archive_app
    allowed = "single-allowed-application"
    unrelated = "single-unrelated-application"
    assert (await _post(app, allowed)).status_code == 201
    assert (await _post(app, unrelated)).status_code == 201
    monkeypatch.setenv("ARCHIVE_PREPARATION_TEST_APPLICATION_ID", allowed)

    result = await archive_intake_preparation_worker.run_once("single-worker")

    assert result is not None
    assert archive_intake_service.mock_get(allowed)["status"] == (
        archive_intake_service.STATUS_GENERATION_QUEUED
    )
    untouched = archive_intake_service.mock_get(unrelated)
    assert untouched["status"] == archive_intake_service.STATUS_PREPARING
    assert untouched["preparation_stage"] == archive_intake_service.PREPARATION_STAGE_QUEUED
    assert untouched["preparation_lease_id"] is None
    assert len(generation_calls) == 1


@pytest.mark.anyio
async def test_single_application_worker_fails_closed_on_unrestricted_claim(
    async_archive_app, monkeypatch: pytest.MonkeyPatch
):
    app, _generation_calls = async_archive_app
    allowed = "single-fail-closed-allowed"
    assert (await _post(app, allowed)).status_code == 201
    monkeypatch.setenv("ARCHIVE_PREPARATION_TEST_APPLICATION_ID", allowed)

    async def unfiltered_claim(worker_id: str, *, application_id: str | None = None):
        return {"archive_application_id": "unrelated-application"}

    monkeypatch.setattr(
        archive_intake_service, "claim_next_preparation", unfiltered_claim
    )
    with pytest.raises(RuntimeError, match="single-application restriction"):
        await archive_intake_preparation_worker.run_once("single-worker")
