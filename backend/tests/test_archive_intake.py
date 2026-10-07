from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import ANY

import anyio
import httpx
import pytest
from fastapi import FastAPI

from backend.routers import archive_intake_v1
from backend.services import archive_intake_service
from backend.services import pet_reference_service
from backend.services import pet_registry

from .conftest import ASGITestClient, make_jpeg_bytes


SERVICE_TOKEN = "archive-test-secret"
ENDPOINT = "/api/internal/archive-intake"


@pytest.fixture(autouse=True)
def _mock_archive_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("ARCHIVE_SERVICE_TOKEN", SERVICE_TOKEN)
    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()
    archive_intake_service.__reset_for_tests()
    yield
    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()
    archive_intake_service.__reset_for_tests()


@pytest.fixture
def uploads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from backend.services import supabase_assets

    paths: list[str] = []

    async def fake_upload(path: str, data: bytes, content_type: str) -> str:
        paths.append(path)
        return f"mock://{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    return paths


@pytest.fixture
def client(uploads: list[str]) -> ASGITestClient:
    app = FastAPI()
    app.include_router(archive_intake_v1.router, prefix="/api")
    return ASGITestClient(app)


def _form(**overrides: str) -> dict[str, str]:
    values = {
        "application_id": "application-123",
        "customer_email": "test@example.com",
        "pet_name": "Goya",
        "pet_type": "dog",
        "breed": "Shiba Inu",
    }
    values.update(overrides)
    return values


def _photo(index: int) -> tuple[str, tuple[str, bytes, str]]:
    return (
        "photos",
        (
            f"pet-{index}.jpg",
            make_jpeg_bytes(width=80 + index, height=60 + index),
            "image/jpeg",
        ),
    )


def _photos(count: int, *, start: int = 0) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [_photo(start + index) for index in range(count)]


def _post(
    client: ASGITestClient,
    *,
    files=None,
    data: dict[str, str] | None = None,
    token: str | None = SERVICE_TOKEN,
):
    headers = {"X-Archive-Service-Token": token} if token is not None else {}
    kwargs = {"data": data or _form(), "headers": headers}
    if files is not None:
        kwargs["files"] = files
    return client.post(ENDPOINT, **kwargs)


def _assert_valid_intake(client: ASGITestClient, photo_count: int) -> None:
    response = _post(client, files=_photos(photo_count))

    assert response.status_code == 201
    body = response.json()
    assert body["application_id"] == "application-123"
    assert body["owner_id"] == "archive_application-123"
    assert body["content_id"] == "archive_application-123"
    assert body["pet_id"] == "pet_archive_application-123"
    assert body["reference_count"] == photo_count
    assert len(body["references"]) == photo_count
    assert all(item["reference_id"] for item in body["references"])
    assert all(item["deduplicated"] is False for item in body["references"])


def test_valid_one_photo_intake(client: ASGITestClient):
    _assert_valid_intake(client, 1)


def test_valid_two_photo_intake(client: ASGITestClient):
    _assert_valid_intake(client, 2)


def test_valid_three_photo_intake(client: ASGITestClient):
    _assert_valid_intake(client, 3)


def test_duplicate_retry_is_idempotent(
    client: ASGITestClient,
    uploads: list[str],
):
    files = _photos(2)
    first = _post(client, files=files)
    second = _post(client, files=files)

    assert first.status_code == second.status_code == 201
    assert second.json()["reference_count"] == 2
    assert [item["reference_id"] for item in second.json()["references"]] == [
        item["reference_id"] for item in first.json()["references"]
    ]
    assert all(item["deduplicated"] is True for item in second.json()["references"])
    assert len(uploads) == 2


def test_same_application_always_maps_to_same_pet(client: ASGITestClient):
    first = _post(client, files=_photos(1))
    second = _post(client, files=_photos(1, start=1))

    assert first.status_code == second.status_code == 201
    for key in ("owner_id", "content_id", "pet_id"):
        assert first.json()[key] == second.json()[key]
    assert second.json()["reference_count"] == 2


@pytest.mark.parametrize("token", [None, "wrong-token", f" {SERVICE_TOKEN} "])
def test_missing_or_invalid_service_token_is_rejected(
    client: ASGITestClient,
    token: str | None,
):
    response = _post(client, files=_photos(1), token=token)

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "ARCHIVE_INTAKE_FORBIDDEN"
    assert SERVICE_TOKEN not in response.text


def test_service_token_missing_from_server_configuration_fails_closed(
    client: ASGITestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("ARCHIVE_SERVICE_TOKEN")

    response = _post(client, files=_photos(1))

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ARCHIVE_INTAKE_NOT_CONFIGURED"
    assert SERVICE_TOKEN not in response.text


def test_invalid_token_uses_constant_time_comparison(
    client: ASGITestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    comparisons: list[tuple[str, str]] = []

    def fake_compare(left: str, right: str) -> bool:
        comparisons.append((left, right))
        return False

    monkeypatch.setattr(archive_intake_v1.hmac, "compare_digest", fake_compare)

    response = _post(client, files=_photos(1), token=None)

    assert response.status_code == 401
    assert comparisons == [("", SERVICE_TOKEN)]


def test_zero_photos_is_rejected(client: ASGITestClient):
    response = _post(client)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "ARCHIVE_PHOTO_COUNT_INVALID"


def test_more_than_three_photos_is_rejected(client: ASGITestClient):
    response = _post(client, files=_photos(4))

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "ARCHIVE_PHOTO_COUNT_INVALID"


def test_unsupported_image_type_is_rejected_without_partial_upload(
    client: ASGITestClient,
    uploads: list[str],
):
    files = [
        _photo(0),
        ("photos", ("pet.gif", b"GIF89a", "image/gif")),
    ]

    response = _post(client, files=files)

    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "ARCHIVE_PHOTO_TYPE_UNSUPPORTED"
    assert uploads == []


def test_spoofed_image_content_is_rejected_without_upload(
    client: ASGITestClient,
    uploads: list[str],
):
    response = _post(
        client,
        files=[("photos", ("not-an-image.jpg", b"not an image", "image/jpeg"))],
    )

    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "ARCHIVE_PHOTO_CONTENT_INVALID"
    assert uploads == []


@pytest.mark.parametrize(
    "application_id",
    ["bad/application", "contains space", ".", "x" * 129],
)
def test_invalid_application_id_is_rejected(
    client: ASGITestClient,
    application_id: str,
):
    response = _post(
        client,
        files=_photos(1),
        data=_form(application_id=application_id),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "ARCHIVE_APPLICATION_ID_INVALID"


@pytest.mark.parametrize(
    "email",
    [
        "missing-at.example.com",
        "two@@example.com",
        "space here@example.com",
        ".leading@example.com",
        "customer@invalid_domain",
    ],
)
def test_invalid_email_is_rejected(client: ASGITestClient, email: str):
    response = _post(
        client,
        files=_photos(1),
        data=_form(customer_email=email),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "ARCHIVE_EMAIL_INVALID"


def test_metadata_is_persisted_in_mock_mode(client: ASGITestClient):
    response = _post(
        client,
        files=_photos(2),
        data=_form(
            customer_email="  TEST@EXAMPLE.COM  ",
            pet_name="  Goya  ",
            pet_type="  dog  ",
            breed="  Shiba Inu  ",
        ),
    )

    assert response.status_code == 201
    assert archive_intake_service.mock_get("application-123") == {
        "archive_application_id": "application-123",
        "owner_id": "archive_application-123",
        "content_id": "archive_application-123",
        "pet_id": "pet_archive_application-123",
        "customer_email": "test@example.com",
        "pet_name": "Goya",
        "pet_type": "dog",
        "breed": "Shiba Inu",
        "reference_count": 2,
        "status": "REFERENCES_READY",
        "updated_at": ANY,
        "generation_run_id": None,
        "result_url": None,
        "last_error": None,
        "created_at": ANY,
    }


def test_reference_count_is_total_stored_not_current_request_length(
    client: ASGITestClient,
):
    first = _post(client, files=_photos(2))
    second = _post(client, files=_photos(1))

    assert first.status_code == second.status_code == 201
    assert len(second.json()["references"]) == 1
    assert second.json()["references"][0]["deduplicated"] is True
    assert second.json()["reference_count"] == 2
    assert archive_intake_service.mock_get("application-123")["reference_count"] == 2


def test_application_cannot_accumulate_more_than_three_references(
    client: ASGITestClient,
    uploads: list[str],
):
    assert _post(client, files=_photos(3)).status_code == 201

    response = _post(client, files=_photos(1, start=10))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ARCHIVE_REFERENCE_LIMIT_EXCEEDED"
    assert len(uploads) == 3


def test_concurrent_requests_cannot_race_past_three_references(
    client: ASGITestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    assert _post(client, files=_photos(2)).status_code == 201

    from backend.services import supabase_assets

    both_uploads_started = asyncio.Event()
    release_uploads = asyncio.Event()
    started = 0

    async def synchronized_upload(path: str, data: bytes, content_type: str) -> str:
        nonlocal started
        started += 1
        if started == 2:
            both_uploads_started.set()
        await both_uploads_started.wait()
        await release_uploads.wait()
        return f"mock://{path}"

    monkeypatch.setattr(
        supabase_assets,
        "upload_asset_to_storage",
        synchronized_upload,
    )

    async def scenario():
        transport = httpx.ASGITransport(
            app=client.app,
            raise_app_exceptions=False,
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as http:
            kwargs = {
                "data": _form(),
                "headers": {"X-Archive-Service-Token": SERVICE_TOKEN},
            }
            requests = [
                asyncio.create_task(
                    http.post(ENDPOINT, files=_photos(1, start=index), **kwargs)
                )
                for index in (10, 20)
            ]
            await both_uploads_started.wait()
            release_uploads.set()
            return await asyncio.gather(*requests)

    responses = anyio.run(scenario)

    assert sorted(response.status_code for response in responses) == [201, 409]
    refs = anyio.run(
        lambda: pet_reference_service.list_references(
            user_id="archive_application-123",
            pet_id="pet_archive_application-123",
        )
    )
    assert len(pet_reference_service.active_originals(refs)) == 3


def test_unrecorded_reference_is_not_reported_as_success(
    client: ASGITestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    async def failed_record(**kwargs):
        return pet_reference_service.PetReference(
            id=None,
            pet_id="pet_archive_application-123",
            content_id="archive_application-123",
            user_id="archive_application-123",
            role=pet_reference_service.ROLE_ORIGINAL,
            source=pet_reference_service.SOURCE_OPS,
            bucket="user-assets",
            object_path="unrecorded",
            version=1,
            recorded=False,
        )

    monkeypatch.setattr(pet_reference_service, "record_original", failed_record)

    response = _post(client, files=_photos(1))

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ARCHIVE_REFERENCE_PERSIST_FAILED"


def test_archive_service_refuses_anon_only_supabase_configuration(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "1")
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "browser-key")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    with pytest.raises(RuntimeError, match="server credentials"):
        anyio.run(
            lambda: archive_intake_service.save_intake(
                archive_application_id="application-123",
                owner_id="archive_application-123",
                content_id="archive_application-123",
                pet_id="pet_archive_application-123",
                customer_email="test@example.com",
                pet_name="Goya",
                pet_type="dog",
                breed="Shiba Inu",
                reference_count=1,
            )
        )


def test_archive_intakes_migration_is_server_only_and_constrained():
    migration = (
        Path(__file__).parents[2]
        / "supabase"
        / "migrations"
        / "20261105000000_archive_intakes.sql"
    ).read_text(encoding="utf-8").lower()

    assert "enable row level security" in migration
    assert "create policy" not in migration
    assert "from public, anon, authenticated" in migration
    assert "to service_role" in migration
    assert "pet_name text not null" in migration
    assert "reference_count >= 0 and reference_count <= 3" in migration
