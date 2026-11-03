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


SERVICE_TOKEN = "archive-recovery-service-secret"
OPERATOR_TOKEN = "archive-recovery-operator-secret"
ENDPOINT = "/api/internal/archive-intake"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def recovery_app(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("ARCHIVE_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("ARCHIVE_OPERATOR_TOKEN", OPERATOR_TOKEN)
    monkeypatch.setenv("BUSINESS_QA_USER_TEST_MODE", "off")
    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()
    archive_intake_service.__reset_for_tests()
    archive_intake_preparation_service.__reset_for_tests()
    pet_generation_run_service.__reset_for_tests()

    objects: dict[str, bytes] = {}
    generation_calls: list[bytes] = []
    should_fail = False

    async def fake_upload(
        path: str, data: bytes, content_type: str, **kwargs
    ) -> str:
        objects[path] = bytes(data)
        return f"mock://{path}"

    async def fake_download(path: str, *, bucket: str | None = None) -> bytes:
        return objects[path]

    async def fake_generate(data: bytes, **kwargs) -> pet_cutout_service.GeneratedCutout:
        generation_calls.append(data)
        if should_fail:
            from backend.services.cutout_errors import SubjectNotDetectedError

            raise SubjectNotDetectedError("sensitive-provider-detail")
        return pet_cutout_service.GeneratedCutout(
            png=make_rgba_png_bytes(0.5),
            diagnostics={"subject_detected": True},
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

    def set_failure(value: bool) -> None:
        nonlocal should_fail
        should_fail = value

    app = FastAPI()
    app.include_router(archive_intake_v1.router, prefix="/api")
    yield app, generation_calls, set_failure

    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()
    archive_intake_service.__reset_for_tests()
    archive_intake_preparation_service.__reset_for_tests()
    pet_generation_run_service.__reset_for_tests()


async def _intake(app: FastAPI, application_id: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            ENDPOINT,
            headers={"X-Archive-Service-Token": SERVICE_TOKEN},
            data={
                "application_id": application_id,
                "customer_email": "operator-test@example.com",
                "pet_name": "Goya",
                "pet_type": "dog",
                "breed": "Shiba Inu",
            },
            files=[
                (
                    "photos",
                    ("pet.jpg", make_jpeg_bytes(80, 60), "image/jpeg"),
                )
            ],
        )


async def _recover(
    app: FastAPI,
    application_id: str,
    *,
    token: str | None = OPERATOR_TOKEN,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    headers = {"X-Archive-Operator-Token": token} if token is not None else {}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            f"{ENDPOINT}/{application_id}/preparation-recovery",
            headers=headers,
        )


async def _status(app: FastAPI, application_id: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(
            f"{ENDPOINT}/{application_id}/status",
            headers={"X-Archive-Service-Token": SERVICE_TOKEN},
        )


@pytest.mark.anyio
async def test_recovery_is_operator_only(recovery_app):
    app, _calls, _set_failure = recovery_app
    assert (await _intake(app, "operator-auth-app")).status_code == 201

    missing = await _recover(app, "operator-auth-app", token=None)
    wrong = await _recover(app, "operator-auth-app", token="wrong")

    assert missing.status_code == 403
    assert missing.json()["detail"]["code"] == "ARCHIVE_RECOVERY_FORBIDDEN"
    assert wrong.status_code == 403
    assert wrong.json()["detail"]["code"] == "ARCHIVE_RECOVERY_FORBIDDEN"


@pytest.mark.anyio
async def test_duplicate_recovery_requests_queue_once(recovery_app):
    app, calls, set_failure = recovery_app
    application_id = "duplicate-recovery-app"
    assert (await _intake(app, application_id)).status_code == 201
    set_failure(True)
    await archive_intake_preparation_worker.run_once("failure-worker")
    assert archive_intake_service.mock_get(application_id)["status"] == (
        archive_intake_service.STATUS_GENERATION_FAILED
    )

    first, second = await asyncio.gather(
        _recover(app, application_id), _recover(app, application_id)
    )
    assert sorted(
        response.json()["recovery_outcome"] for response in (first, second)
    ) == ["ALREADY_QUEUED", "QUEUED"]
    assert calls == [calls[0]]
    assert len(pet_generation_run_service._MOCK_RUNS) == 0

    set_failure(False)
    await archive_intake_preparation_worker.run_once("recovery-worker")
    assert len(pet_generation_run_service._MOCK_RUNS) == 1


@pytest.mark.anyio
async def test_recovery_survives_worker_restart_and_does_not_requeue_active_lease(
    recovery_app,
):
    app, _calls, set_failure = recovery_app
    application_id = "restart-recovery-app"
    assert (await _intake(app, application_id)).status_code == 201
    set_failure(True)
    await archive_intake_preparation_worker.run_once("failure-worker")
    set_failure(False)
    queued = await _recover(app, application_id)
    assert queued.json()["recovery_outcome"] == "QUEUED"

    claimed = await archive_intake_service.claim_next_preparation("dead-worker")
    assert claimed is not None
    active = await _recover(app, application_id)
    assert active.json()["recovery_outcome"] == "ALREADY_IN_PROGRESS"

    row = archive_intake_service.mock_get(application_id)
    row["preparation_lease_expires_at"] = "2000-01-01T00:00:00+00:00"
    await archive_intake_preparation_worker.run_once("new-worker")
    assert archive_intake_service.mock_get(application_id)["status"] == (
        archive_intake_service.STATUS_GENERATION_QUEUED
    )
    assert len(pet_generation_run_service._MOCK_RUNS) == 1


@pytest.mark.anyio
async def test_recovery_failure_is_safe_and_reconciliation_uses_authoritative_status(
    recovery_app,
):
    app, calls, set_failure = recovery_app
    application_id = "reconcile-recovery-app"
    assert (await _intake(app, application_id)).status_code == 201
    set_failure(True)
    await archive_intake_preparation_worker.run_once("failure-worker")

    failed_status = await _status(app, application_id)
    assert failed_status.json()["status"] == "GENERATION_FAILED"
    assert failed_status.json()["preparation_error_code"] == "SUBJECT_NOT_DETECTED"
    assert "sensitive-provider" not in repr(archive_intake_service.mock_get(application_id))

    set_failure(False)
    assert (await _recover(app, application_id)).json()["recovery_outcome"] == "QUEUED"
    await archive_intake_preparation_worker.run_once("success-worker")

    authoritative = await _status(app, application_id)
    body = authoritative.json()
    assert body["status"] == "GENERATION_QUEUED"
    assert body["generation_run_id"]
    assert body["preparation_progress"] == 100
    assert len(pet_generation_run_service._MOCK_RUNS) == 1
    assert pet_generation_run_service._MOCK_RUNS[0]["idempotency_key"] == (
        "free-home:archive_reconcile-recovery-app"
    )

    already_done = await _recover(app, application_id)
    assert already_done.json()["recovery_outcome"] == "GENERATION_ALREADY_EXISTS"
    assert len(pet_generation_run_service._MOCK_RUNS) == 1
    assert len(calls) == 2
