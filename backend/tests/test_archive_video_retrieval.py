from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from backend.routers import archive_intake_v1
from backend.services import archive_browser_export
from backend.services import archive_intake_service
from backend.services import archive_raw_video
from backend.services import motion_video_service
from backend.services import pet_reference_service
from backend.services import pet_generation_run_service
from backend.services import supabase_assets
from .conftest import ASGITestClient


SERVICE_TOKEN = "archive-video-test-secret"
BUCKET = "archive-private-test"
APPLICATION_ID = "application-123"
RUN_ID = "run-123"
RAW_BUCKET = "archive-raw-private-test"
MOTION_VERSION_ID = "motion-version-123"
CANDIDATE_ID = "candidate-123"
OBJECT_PATH = (
    f"archive_{APPLICATION_ID}/archive_{APPLICATION_ID}/motions/breathing/v1/wan_a1_raw.mp4"
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _mock_archive_video_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("ARCHIVE_SERVICE_TOKEN", SERVICE_TOKEN)
    archive_intake_service.__reset_for_tests()
    pet_generation_run_service.__reset_for_tests()
    motion_video_service.__reset_for_tests()

    async def private_bucket(bucket: str) -> bool:
        return bucket == RAW_BUCKET

    monkeypatch.setattr(supabase_assets, "verify_private_storage_bucket", private_bucket)
    yield
    archive_intake_service.__reset_for_tests()
    pet_generation_run_service.__reset_for_tests()
    motion_video_service.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(archive_intake_v1.router, prefix="/api")
    return ASGITestClient(app)


def _box(kind: str, payload: bytes = b"") -> bytes:
    size = 8 + len(payload)
    return size.to_bytes(4, "big") + kind.encode("ascii") + payload


def _mp4_bytes() -> bytes:
    return _box("ftyp", b"isom0000") + _box("moov", b"metadata") + _box("mdat", b"payload")


def _nonfast_mp4_bytes() -> bytes:
    return _box("ftyp", b"isom0000") + _box("mdat", b"payload") + _box("moov", b"metadata")


def _probe_json(*, codec: str = "h264", pixel_format: str = "yuv420p") -> bytes:
    return json.dumps(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": codec,
                    "pix_fmt": pixel_format,
                    "width": 4,
                    "height": 4,
                    "duration": "2.0",
                    "r_frame_rate": "24/1",
                }
            ],
            "format": {
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                "duration": "2.0",
            },
        }
    ).encode()


class _FakeCommands:
    def __init__(self, *, codec: str = "h264", pixel_format: str = "yuv420p"):
        self.calls: list[list[str]] = []
        self.probe = _probe_json(codec=codec, pixel_format=pixel_format)

    def __call__(self, args, *, timeout):
        args = list(args)
        self.calls.append(args)
        if args[0] == "ffprobe":
            return subprocess.CompletedProcess(args, 0, self.probe, b"")
        if args[-1] != "-":
            Path(args[-1]).write_bytes(_mp4_bytes())
        return subprocess.CompletedProcess(args, 0, b"\x80" * (4 * 4 * 3), b"")


async def _seed_intake(*, status: str = archive_intake_service.STATUS_COMPLETED, run_id: str = RUN_ID):
    content_id = f"archive_{APPLICATION_ID}"
    await archive_intake_service.save_intake(
        archive_application_id=APPLICATION_ID,
        owner_id=f"archive_{APPLICATION_ID}",
        content_id=content_id,
        pet_id=pet_reference_service.pet_id_for_content(content_id),
        customer_email="customer@example.com",
        pet_name="Goya",
        pet_type="dog",
        breed="Shiba Inu",
        reference_count=1,
    )
    await archive_intake_service.claim_preparation(APPLICATION_ID)
    await archive_intake_service.mark_generation_queued(APPLICATION_ID, run_id)
    owner_id = f"archive_{APPLICATION_ID}"
    pet_id = pet_reference_service.pet_id_for_content(content_id)
    pet_generation_run_service._MOCK_RUNS.append(
        {
            "id": run_id,
            "user_id": owner_id,
            "pet_id": pet_id,
            "content_id": content_id,
            "motion_id": "BREATHING",
            "request_kind": "FREE_HOME",
            "idempotency_key": f"free-home:{content_id}",
            "status": (
                pet_generation_run_service.STATUS_PUBLISHED
                if status == archive_intake_service.STATUS_COMPLETED
                else pet_generation_run_service.STATUS_QUEUED
            ),
            "current_stage": pet_generation_run_service.STAGE_PUBLISHED,
            "motion_version_id": MOTION_VERSION_ID,
            "selected_candidate_id": CANDIDATE_ID,
            "publication_id": "publication-123",
            "provider_state": {
                "_business_qa": {
                    "terminal_state": pet_generation_run_service.BUSINESS_DELIVERED_GENERATED,
                }
            },
        }
    )
    motion_video_service._MOCK_VERSIONS.append(
        {
            "id": MOTION_VERSION_ID,
            "pet_id": pet_id,
            "user_id": owner_id,
            "motion_id": "BREATHING",
            "version": 1,
            "status": motion_video_service.STATUS_COMPLETE,
            "selected_candidate_id": CANDIDATE_ID,
        }
    )
    motion_video_service._MOCK_CANDIDATES.append(
        {
            "id": CANDIDATE_ID,
            "motion_version_id": MOTION_VERSION_ID,
            "pet_id": pet_id,
            "user_id": owner_id,
            "motion_id": "BREATHING",
            "provider": "wan",
            "attempt": 1,
            "raw_bucket": RAW_BUCKET,
            "raw_video_path": OBJECT_PATH,
            "decision": "PASS",
            "selected": True,
            "qa_result": {"decision": "PASS"},
        }
    )
    if status == archive_intake_service.STATUS_COMPLETED:
        await archive_intake_service.mark_generation_completed(run_id, "internal-result")


def _install_storage_mocks(monkeypatch: pytest.MonkeyPatch, *, data: bytes, info: dict):
    calls: list[tuple[str, str]] = []

    async def get_info(path: str, *, bucket: str) -> dict:
        calls.append(("info", path))
        return info

    async def download(path: str, *, bucket: str) -> bytes:
        calls.append(("download", path))
        return data

    monkeypatch.setattr(supabase_assets, "get_private_asset_info_from_storage", get_info)
    monkeypatch.setattr(supabase_assets, "download_private_asset_from_storage", download)
    return calls


async def _get(
    client: ASGITestClient,
    *,
    token: str | None = SERVICE_TOKEN,
    run_id: str | None = RUN_ID,
    application_id: str = APPLICATION_ID,
):
    headers = {}
    if token is not None:
        headers["X-Archive-Service-Token"] = token
    if run_id is not None:
        headers["X-Archive-Generation-Run-ID"] = run_id
    transport = httpx.ASGITransport(app=client.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        return await http.get(
            f"/api/internal/archive-intake/{application_id}/video",
            headers=headers,
        )


@pytest.mark.anyio
async def test_video_requires_archive_service_token(client):
    response = await _get(client, token=None)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "ARCHIVE_INTAKE_FORBIDDEN"


@pytest.mark.anyio
async def test_video_requires_generation_run_header(client):
    response = await _get(client, run_id=None)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "ARCHIVE_GENERATION_RUN_ID_REQUIRED"


@pytest.mark.anyio
async def test_video_rejects_invalid_application_and_missing_intake(client):
    invalid = await _get(client, application_id="bad.id")
    assert invalid.status_code == 400

    response = await _get(client)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ARCHIVE_INTAKE_NOT_FOUND"


@pytest.mark.anyio
async def test_video_requires_completed_generation(client):
    await _seed_intake(status=archive_intake_service.STATUS_GENERATION_QUEUED)

    response = await _get(client)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ARCHIVE_VIDEO_NOT_READY"


@pytest.mark.anyio
async def test_video_rejects_generation_run_mismatch(client):
    await _seed_intake()

    response = await _get(client, run_id="run-other")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ARCHIVE_GENERATION_RUN_MISMATCH"


@pytest.mark.anyio
async def test_video_rejects_generation_run_owned_by_another_application(client, monkeypatch):
    await _seed_intake()

    async def other_run(run_id: str):
        return {
            "archive_application_id": "other-application",
            "generation_run_id": run_id,
            "status": archive_intake_service.STATUS_COMPLETED,
            "owner_id": "archive_other-application",
            "content_id": "archive_other-application",
            "pet_id": "pet_archive_other-application",
        }

    monkeypatch.setattr(archive_intake_service, "get_intake_by_generation_run_id", other_run)
    response = await _get(client)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ARCHIVE_GENERATION_RUN_MISMATCH"


@pytest.mark.anyio
async def test_video_rejects_identity_mismatch(client):
    await _seed_intake()
    archive_intake_service.mock_get(APPLICATION_ID)["owner_id"] = "wrong-owner"

    response = await _get(client)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ARCHIVE_IDENTITY_MISMATCH"


@pytest.mark.anyio
async def test_video_returns_not_available_when_artifact_is_missing(client, monkeypatch):
    await _seed_intake()

    async def missing(path: str, *, bucket: str) -> dict:
        raise FileNotFoundError(path)

    monkeypatch.setattr(supabase_assets, "get_private_asset_info_from_storage", missing)
    response = await _get(client)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ARCHIVE_RAW_VIDEO_NOT_AVAILABLE"


@pytest.mark.anyio
async def test_video_enforces_metadata_size_and_content_type(client, monkeypatch):
    await _seed_intake()
    data = _mp4_bytes()
    calls = _install_storage_mocks(
        monkeypatch,
        data=data,
        info={"content_type": "video/mp4", "size": 40 * 1024 * 1024 + 1},
    )

    response = await _get(client)
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "ARCHIVE_VIDEO_TOO_LARGE"
    assert calls == [("info", OBJECT_PATH)]

    _install_storage_mocks(
        monkeypatch,
        data=data,
        info={"content_type": "application/octet-stream", "size": len(data)},
    )
    response = await _get(client)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ARCHIVE_RAW_VIDEO_INCOMPATIBLE"


@pytest.mark.anyio
async def test_video_rejects_invalid_mp4_format(client, monkeypatch):
    await _seed_intake()
    data = _mp4_bytes()
    _install_storage_mocks(
        monkeypatch,
        data=data,
        info={"content_type": "video/mp4", "size": len(data)},
    )
    commands = _FakeCommands(codec="vp9", pixel_format="yuv444p")
    monkeypatch.setattr(archive_browser_export, "_run_command", commands)

    response = await _get(client)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ARCHIVE_RAW_VIDEO_INCOMPATIBLE"


@pytest.mark.anyio
async def test_video_remuxes_nonfaststart_raw_without_storage_write(client, monkeypatch):
    await _seed_intake()
    data = _nonfast_mp4_bytes()
    _install_storage_mocks(
        monkeypatch,
        data=data,
        info={"content_type": "video/mp4", "size": len(data)},
    )
    commands = _FakeCommands()
    monkeypatch.setattr(archive_browser_export, "_run_command", commands)

    response = await _get(client)

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert archive_browser_export._has_faststart_mp4(response.content)
    assert response.content != data


@pytest.mark.anyio
async def test_video_storage_failure_is_safe(client, monkeypatch):
    await _seed_intake()

    async def unavailable(path: str, *, bucket: str) -> dict:
        raise PermissionError("private storage unavailable")

    monkeypatch.setattr(supabase_assets, "get_private_asset_info_from_storage", unavailable)
    response = await _get(client)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ARCHIVE_VIDEO_STORAGE_UNAVAILABLE"
    assert "private storage unavailable" not in response.text


@pytest.mark.anyio
async def test_video_requires_explicit_storage_configuration(client, monkeypatch):
    await _seed_intake()
    async def unverified(bucket: str) -> bool:
        return False

    monkeypatch.setattr(supabase_assets, "verify_private_storage_bucket", unverified)

    response = await _get(client)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ARCHIVE_RAW_BUCKET_UNVERIFIED"


@pytest.mark.anyio
async def test_video_success_is_authenticated_preview_only_and_never_exports(
    client, monkeypatch
):
    await _seed_intake()
    data = _mp4_bytes()
    calls = _install_storage_mocks(
        monkeypatch,
        data=data,
        info={"content_type": "video/mp4", "size": len(data)},
    )
    commands = _FakeCommands()
    monkeypatch.setattr(archive_browser_export, "_run_command", commands)

    async def must_not_export(**kwargs):  # pragma: no cover - called only on regression
        raise AssertionError("video retrieval must not trigger export")

    monkeypatch.setattr(archive_browser_export, "export_archive_browser_mp4", must_not_export)
    response = await _get(client, run_id=RUN_ID)

    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["x-archive-video-readiness"] == "staff_preview_only"
    assert response.headers["x-archive-customer-delivery-ready"] == "false"
    assert calls == [("info", OBJECT_PATH), ("download", OBJECT_PATH)]


@pytest.mark.anyio
async def test_video_rejects_download_size_mismatch(client, monkeypatch):
    await _seed_intake()
    data = _mp4_bytes()
    _install_storage_mocks(
        monkeypatch,
        data=data,
        info={"content_type": "video/mp4", "size": len(data) + 1},
    )

    response = await _get(client)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ARCHIVE_RAW_VIDEO_INCOMPATIBLE"


@pytest.mark.anyio
async def test_video_rejects_wrong_selected_candidate(client):
    await _seed_intake()
    pet_generation_run_service._MOCK_RUNS[0]["selected_candidate_id"] = "other-candidate"

    response = await _get(client)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] in {
        "ARCHIVE_RAW_LINEAGE_INVALID",
        "ARCHIVE_RAW_CANDIDATE_MISMATCH",
    }


@pytest.mark.anyio
async def test_video_rejects_fallback_run(client):
    await _seed_intake()
    pet_generation_run_service._MOCK_RUNS[0]["provider_state"] = {
        "_business_qa": {
            "terminal_state": pet_generation_run_service.BUSINESS_DELIVERED_FALLBACK,
        }
    }

    response = await _get(client)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ARCHIVE_RAW_GENERATION_NOT_READY"


@pytest.mark.anyio
async def test_video_rejects_missing_raw_video(client):
    await _seed_intake()
    motion_video_service._MOCK_CANDIDATES[0]["raw_video_path"] = None

    response = await _get(client)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ARCHIVE_RAW_ARTIFACT_INVALID"


@pytest.mark.anyio
async def test_video_resolves_selected_raw_object_not_browser_export(client, monkeypatch):
    await _seed_intake()
    data = _mp4_bytes()
    calls = _install_storage_mocks(
        monkeypatch,
        data=data,
        info={"content_type": "video/mp4", "size": len(data)},
    )
    commands = _FakeCommands()
    monkeypatch.setattr(archive_browser_export, "_run_command", commands)

    response = await _get(client)

    assert response.status_code == 200
    assert response.content == data
    assert calls == [("info", OBJECT_PATH), ("download", OBJECT_PATH)]
    assert all("browser.mp4" not in call[1] for call in calls)
