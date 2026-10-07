from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import ANY

import anyio
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

from .conftest import ASGITestClient, make_jpeg_bytes, make_rgba_png_bytes


SERVICE_TOKEN = "archive-test-secret"
ENDPOINT = "/api/internal/archive-intake"


@pytest.fixture(autouse=True)
def _mock_archive_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("ARCHIVE_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("BUSINESS_QA_USER_TEST_MODE", "off")
    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()
    archive_intake_service.__reset_for_tests()
    archive_intake_preparation_service.__reset_for_tests()
    pet_generation_run_service.__reset_for_tests()
    yield
    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()
    archive_intake_service.__reset_for_tests()
    archive_intake_preparation_service.__reset_for_tests()
    pet_generation_run_service.__reset_for_tests()


@pytest.fixture
def uploads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from backend.services import supabase_assets

    paths: list[str] = []
    objects: dict[str, bytes] = {}

    async def fake_upload(path: str, data: bytes, content_type: str) -> str:
        paths.append(path)
        objects[path] = data
        return f"mock://{path}"

    async def fake_download(path: str, *, bucket: str | None = None) -> bytes:
        return objects[path]

    async def fake_generate(data: bytes, **kwargs) -> pet_cutout_service.GeneratedCutout:
        return pet_cutout_service.GeneratedCutout(
            png=make_rgba_png_bytes(0.5),
            diagnostics={"quality_score": 0.9, "subject_detected": True},
        )

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    monkeypatch.setattr(supabase_assets, "download_asset_from_storage", fake_download)
    monkeypatch.setattr(pet_cutout_service, "generate_vitmatte_cutout", fake_generate)
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


def _assert_strict_pairs(
    refs: list[pet_reference_service.PetReference], expected: int
) -> tuple[
    list[pet_reference_service.PetReference],
    list[pet_reference_service.PetReference],
]:
    originals = pet_reference_service.active_originals(refs)
    cutouts = pet_reference_service.active_cutouts(refs)
    assert len(originals) == expected
    assert len(cutouts) == expected
    originals_by_id = {original.id: original for original in originals}
    assert None not in originals_by_id
    assert {cutout.parent_reference_id for cutout in cutouts} == set(
        originals_by_id
    )
    for cutout in cutouts:
        original = originals_by_id[cutout.parent_reference_id]
        assert original.recorded is True
        assert original.acceptance_state == pet_reference_service.STATE_ACCEPTED
        assert cutout.recorded is True
        assert cutout.acceptance_state == pet_reference_service.STATE_ACCEPTED
        assert cutout.derived_kind == "cutout_reference"
        assert cutout.parent_reference_id == original.id
        assert cutout.user_id == original.user_id
        assert cutout.pet_id == original.pet_id
        assert cutout.content_id == original.content_id
    return originals, cutouts


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
    assert body["status"] == "GENERATION_QUEUED"
    assert body["generation_run_id"]

    refs = anyio.run(
        lambda: pet_reference_service.list_references(
            user_id=body["owner_id"], pet_id=body["pet_id"]
        )
    )
    originals, cutouts = _assert_strict_pairs(refs, photo_count)
    ready, original, cutout = pet_reference_service.intake_readiness(refs)
    assert ready is True
    assert len(originals) == photo_count
    assert cutout.parent_reference_id == original.id
    assert len(cutouts) == photo_count
    assert len(pet_generation_run_service._MOCK_RUNS) == 1
    assert (
        pet_generation_run_service._MOCK_RUNS[0]["idempotency_key"]
        == "free-home:archive_application-123"
    )


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
    assert len(uploads) == 4  # two originals plus their two linked cutouts
    assert first.json()["generation_run_id"] == second.json()["generation_run_id"]
    assert len(pet_generation_run_service._MOCK_RUNS) == 1
    refs = anyio.run(
        lambda: pet_reference_service.list_references(
            user_id=first.json()["owner_id"], pet_id=first.json()["pet_id"]
        )
    )
    _assert_strict_pairs(refs, 2)


def test_same_application_always_maps_to_same_pet(client: ASGITestClient):
    first = _post(client, files=_photos(1))
    second = _post(client, files=_photos(1))

    assert first.status_code == second.status_code == 201
    for key in ("owner_id", "content_id", "pet_id"):
        assert first.json()[key] == second.json()[key]
    assert second.json()["reference_count"] == 1


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
        "status": "GENERATION_QUEUED",
        "updated_at": ANY,
        "generation_run_id": ANY,
        "result_url": None,
        "last_error": None,
        "created_at": ANY,
    }


def test_metadata_resend_preserves_success_and_future_lifecycle_fields(
    client: ASGITestClient,
):
    first = _post(client, files=_photos(1))
    intake = archive_intake_service.mock_get("application-123")
    intake.update(
        {
            "status": "COMPLETED",
            "result_url": "mock://result.mp4",
            "future_delivery_receipt": "receipt-1",
        }
    )
    created_at = intake["created_at"]
    run_id = intake["generation_run_id"]

    resent = _post(client, files=_photos(1))

    assert first.status_code == resent.status_code == 201
    saved = archive_intake_service.mock_get("application-123")
    assert saved["status"] == "COMPLETED"
    assert saved["generation_run_id"] == run_id
    assert saved["result_url"] == "mock://result.mp4"
    assert saved["created_at"] == created_at
    assert saved["future_delivery_receipt"] == "receipt-1"


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


def test_existing_linked_cutout_skips_matting(
    client: ASGITestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    response = _post(client, files=_photos(3))
    assert response.status_code == 201

    async def should_not_run(*args, **kwargs):
        raise AssertionError("matting should not run for an existing strict cutout")

    monkeypatch.setattr(
        pet_cutout_service, "generate_vitmatte_cutout", should_not_run
    )
    prepared = anyio.run(
        lambda: pet_cutout_service.prepare_strict_cutouts(
            user_id=response.json()["owner_id"], pet_id=response.json()["pet_id"]
        )
    )

    assert len(prepared.pairs) == 3
    assert all(pair.generated is False for pair in prepared.pairs)
    assert all(
        pair.cutout.parent_reference_id == pair.original.id
        for pair in prepared.pairs
    )


def test_partial_cutout_success_queues_with_all_originals_and_two_pairs(
    client: ASGITestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from backend.services.cutout_errors import SubjectNotDetectedError

    failed_bytes = _photo(1)[1][1]
    real_generate = pet_cutout_service.generate_vitmatte_cutout

    async def fail_middle(data: bytes, **kwargs):
        if data == failed_bytes:
            raise SubjectNotDetectedError("unsafe provider detail")
        return await real_generate(data, **kwargs)

    monkeypatch.setattr(pet_cutout_service, "generate_vitmatte_cutout", fail_middle)
    response = _post(client, files=_photos(3))

    assert response.status_code == 201
    assert response.json()["status"] == "GENERATION_QUEUED"
    assert response.json()["generation_run_id"]
    refs = anyio.run(
        lambda: pet_reference_service.list_references(
            user_id=response.json()["owner_id"], pet_id=response.json()["pet_id"]
        )
    )
    assert len(pet_reference_service.active_originals(refs)) == 3
    cutouts = pet_reference_service.active_cutouts(refs)
    assert len(cutouts) == 2
    assert len({cutout.parent_reference_id for cutout in cutouts}) == 2
    assert len(pet_generation_run_service._MOCK_RUNS) == 1


def test_cutout_failure_preserves_original_and_records_safe_failure(
    client: ASGITestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from backend.services.cutout_errors import SubjectNotDetectedError

    real_generate = pet_cutout_service.generate_vitmatte_cutout

    async def fail_cutout(*args, **kwargs):
        raise SubjectNotDetectedError("provider secret must not be persisted")

    monkeypatch.setattr(
        pet_cutout_service, "generate_vitmatte_cutout", fail_cutout
    )
    response = _post(client, files=_photos(1))

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "SUBJECT_NOT_DETECTED",
        "message": archive_intake_v1.WORKFLOW_RETRY_MESSAGE,
    }
    intake = archive_intake_service.mock_get("application-123")
    assert intake["status"] == "GENERATION_FAILED"
    assert intake["generation_run_id"] is None
    assert intake["last_error"] == "SUBJECT_NOT_DETECTED"
    assert "secret" not in intake["last_error"]
    refs = anyio.run(
        lambda: pet_reference_service.list_references(
            user_id="archive_application-123",
            pet_id="pet_archive_application-123",
        )
    )
    assert len(pet_reference_service.active_originals(refs)) == 1
    assert pet_reference_service.active_cutouts(refs) == []
    assert pet_generation_run_service._MOCK_RUNS == []

    monkeypatch.setattr(
        pet_cutout_service, "generate_vitmatte_cutout", real_generate
    )
    retried = _post(client, files=_photos(1))

    assert retried.status_code == 201
    assert retried.json()["status"] == "GENERATION_QUEUED"
    retried_refs = anyio.run(
        lambda: pet_reference_service.list_references(
            user_id="archive_application-123",
            pet_id="pet_archive_application-123",
        )
    )
    assert len(pet_reference_service.active_originals(retried_refs)) == 1
    assert len(pet_reference_service.active_cutouts(retried_refs)) == 1


def test_generation_start_failure_keeps_cutout_and_retry_resumes(
    client: ASGITestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    real_start = pet_generation_run_service.start_generation_run
    generate_calls = 0
    real_generate = pet_cutout_service.generate_vitmatte_cutout

    async def counting_generate(*args, **kwargs):
        nonlocal generate_calls
        generate_calls += 1
        return await real_generate(*args, **kwargs)

    async def fail_start(**kwargs):
        raise pet_generation_run_service.PetGenerationRunError(
            "GENERATION_RUNS_UNAVAILABLE",
            "raw provider failure must not be persisted",
            status=503,
        )

    monkeypatch.setattr(
        pet_cutout_service, "generate_vitmatte_cutout", counting_generate
    )
    monkeypatch.setattr(pet_generation_run_service, "start_generation_run", fail_start)
    failed = _post(client, files=_photos(1))

    assert failed.status_code == 503
    assert failed.json()["detail"]["code"] == "GENERATION_RUNS_UNAVAILABLE"
    assert "raw provider failure" not in failed.text
    assert (
        archive_intake_service.mock_get("application-123")["status"]
        == "GENERATION_FAILED"
    )
    failed_refs = anyio.run(
        lambda: pet_reference_service.list_references(
            user_id="archive_application-123",
            pet_id="pet_archive_application-123",
        )
    )
    _, original, cutout = pet_reference_service.intake_readiness(failed_refs)
    assert cutout.parent_reference_id == original.id
    cutout_id = cutout.id

    monkeypatch.setattr(
        pet_generation_run_service, "start_generation_run", real_start
    )
    retried = _post(client, files=_photos(1))

    assert retried.status_code == 201
    assert retried.json()["status"] == "GENERATION_QUEUED"
    assert retried.json()["generation_run_id"]
    retried_refs = anyio.run(
        lambda: pet_reference_service.list_references(
            user_id=retried.json()["owner_id"], pet_id=retried.json()["pet_id"]
        )
    )
    assert pet_reference_service.active_cutouts(retried_refs)[0].id == cutout_id
    assert generate_calls == 1
    assert len(pet_generation_run_service._MOCK_RUNS) == 1


def test_retry_reuses_successful_pairs_and_retries_only_missing_cutout(
    client: ASGITestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from backend.services.cutout_errors import SubjectNotDetectedError

    failed_bytes = _photo(1)[1][1]
    real_generate = pet_cutout_service.generate_vitmatte_cutout
    real_start = pet_generation_run_service.start_generation_run
    first_attempts: list[bytes] = []

    async def partial_generate(data: bytes, **kwargs):
        first_attempts.append(data)
        if data == failed_bytes:
            raise SubjectNotDetectedError("unsafe provider detail")
        return await real_generate(data, **kwargs)

    async def fail_start(**kwargs):
        raise pet_generation_run_service.PetGenerationRunError(
            "GENERATION_RUNS_UNAVAILABLE",
            "unsafe provider detail",
            status=503,
        )

    monkeypatch.setattr(
        pet_cutout_service, "generate_vitmatte_cutout", partial_generate
    )
    monkeypatch.setattr(pet_generation_run_service, "start_generation_run", fail_start)
    failed = _post(client, files=_photos(3))

    assert failed.status_code == 503
    assert len(first_attempts) == 3
    refs_after_failure = anyio.run(
        lambda: pet_reference_service.list_references(
            user_id="archive_application-123",
            pet_id="pet_archive_application-123",
        )
    )
    assert len(pet_reference_service.active_originals(refs_after_failure)) == 3
    existing_cutout_ids = {
        cutout.id for cutout in pet_reference_service.active_cutouts(refs_after_failure)
    }
    assert len(existing_cutout_ids) == 2

    retry_attempts: list[bytes] = []

    async def successful_generate(data: bytes, **kwargs):
        retry_attempts.append(data)
        return await real_generate(data, **kwargs)

    monkeypatch.setattr(
        pet_cutout_service, "generate_vitmatte_cutout", successful_generate
    )
    monkeypatch.setattr(
        pet_generation_run_service, "start_generation_run", real_start
    )
    retried = _post(client, files=_photos(3))

    assert retried.status_code == 201
    assert retried.json()["status"] == "GENERATION_QUEUED"
    assert retry_attempts == [failed_bytes]
    refs_after_retry = anyio.run(
        lambda: pet_reference_service.list_references(
            user_id="archive_application-123",
            pet_id="pet_archive_application-123",
        )
    )
    _, final_cutouts = _assert_strict_pairs(refs_after_retry, 3)
    assert existing_cutout_ids.issubset({cutout.id for cutout in final_cutouts})
    assert len(pet_generation_run_service._MOCK_RUNS) == 1


def test_retry_after_queue_receipt_write_failure_reuses_generation_run(
    client: ASGITestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    real_mark_queued = archive_intake_service.mark_generation_queued
    mark_calls = 0

    async def fail_first_mark(application_id: str, run_id: str):
        nonlocal mark_calls
        mark_calls += 1
        if mark_calls == 1:
            raise RuntimeError("simulated Archive metadata write failure")
        return await real_mark_queued(application_id, run_id)

    monkeypatch.setattr(
        archive_intake_service, "mark_generation_queued", fail_first_mark
    )
    failed = _post(client, files=_photos(1))
    assert failed.status_code == 503
    assert (
        failed.json()["detail"]["code"]
        == "ARCHIVE_GENERATION_PREPARATION_FAILED"
    )
    assert "simulated Archive metadata write failure" not in failed.text
    assert (
        archive_intake_service.mock_get("application-123")["status"]
        == "GENERATION_FAILED"
    )
    assert len(pet_generation_run_service._MOCK_RUNS) == 1
    original_run_id = pet_generation_run_service._MOCK_RUNS[0]["id"]
    failed_refs = anyio.run(
        lambda: pet_reference_service.list_references(
            user_id="archive_application-123",
            pet_id="pet_archive_application-123",
        )
    )
    _, original, cutout = pet_reference_service.intake_readiness(failed_refs)
    assert cutout.parent_reference_id == original.id
    cutout_id = cutout.id

    retried = _post(client, files=_photos(1))

    assert retried.status_code == 201
    assert retried.json()["status"] == "GENERATION_QUEUED"
    assert retried.json()["generation_run_id"] == original_run_id
    assert len(pet_generation_run_service._MOCK_RUNS) == 1
    retried_refs = anyio.run(
        lambda: pet_reference_service.list_references(
            user_id="archive_application-123",
            pet_id="pet_archive_application-123",
        )
    )
    assert pet_reference_service.active_cutouts(retried_refs)[0].id == cutout_id


def test_business_qa_rejection_is_not_bypassed(
    client: ASGITestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("BUSINESS_QA_USER_TEST_MODE", "allowlist")
    monkeypatch.setenv("BUSINESS_QA_USER_TEST_PET_IDS", "another-pet")

    response = _post(client, files=_photos(1))

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "BUSINESS_QA_USER_TEST_NOT_ENROLLED"
    intake = archive_intake_service.mock_get("application-123")
    assert intake["status"] == "GENERATION_FAILED"
    assert intake["last_error"] == "BUSINESS_QA_USER_TEST_NOT_ENROLLED"
    assert pet_generation_run_service._MOCK_RUNS == []


def test_preparing_without_generation_receipt_is_not_reported_as_success(
    client: ASGITestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    async def leave_preparing(application_id: str):
        row = archive_intake_service.mock_get(application_id)
        row.update(
            {
                "status": "PREPARING",
                "generation_run_id": None,
                "last_error": None,
            }
        )
        return archive_intake_preparation_service.ArchivePreparationResult(
            intake=dict(row)
        )

    monkeypatch.setattr(
        archive_intake_preparation_service, "prepare_and_queue", leave_preparing
    )

    response = _post(client, files=_photos(1))

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "ARCHIVE_GENERATION_PREPARING",
        "message": archive_intake_v1.WORKFLOW_RETRY_MESSAGE,
    }
    intake = archive_intake_service.mock_get("application-123")
    assert intake["status"] == "PREPARING"
    assert intake["generation_run_id"] is None


def test_concurrent_duplicate_processing_reuses_cutout_and_run(
    client: ASGITestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    real_generate = pet_cutout_service.generate_vitmatte_cutout
    generate_calls = 0

    async def counting_generate(*args, **kwargs):
        nonlocal generate_calls
        generate_calls += 1
        return await real_generate(*args, **kwargs)

    monkeypatch.setattr(
        pet_cutout_service, "generate_vitmatte_cutout", counting_generate
    )

    async def scenario():
        transport = httpx.ASGITransport(app=client.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as http:
            kwargs = {
                "data": _form(),
                "files": _photos(3),
                "headers": {"X-Archive-Service-Token": SERVICE_TOKEN},
            }
            return await asyncio.gather(
                http.post(ENDPOINT, **kwargs),
                http.post(ENDPOINT, **kwargs),
            )

    first, second = anyio.run(scenario)

    assert first.status_code == second.status_code == 201
    assert first.json()["generation_run_id"] == second.json()["generation_run_id"]
    assert len(pet_generation_run_service._MOCK_RUNS) == 1
    refs = anyio.run(
        lambda: pet_reference_service.list_references(
            user_id=first.json()["owner_id"], pet_id=first.json()["pet_id"]
        )
    )
    _assert_strict_pairs(refs, 3)
    assert generate_calls == 3


def test_application_cannot_accumulate_more_than_three_references(
    client: ASGITestClient,
    uploads: list[str],
    monkeypatch: pytest.MonkeyPatch,
):
    async def references_only(application_id: str):
        row = await archive_intake_service.get_intake(application_id)
        return archive_intake_preparation_service.ArchivePreparationResult(intake=row)

    monkeypatch.setattr(
        archive_intake_preparation_service, "prepare_and_queue", references_only
    )
    assert _post(client, files=_photos(3)).status_code == 503

    response = _post(client, files=_photos(1, start=10))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ARCHIVE_REFERENCE_LIMIT_EXCEEDED"
    assert len(uploads) == 3


def test_concurrent_requests_cannot_race_past_three_references(
    client: ASGITestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    async def references_only(application_id: str):
        row = await archive_intake_service.get_intake(application_id)
        return archive_intake_preparation_service.ArchivePreparationResult(intake=row)

    monkeypatch.setattr(
        archive_intake_preparation_service, "prepare_and_queue", references_only
    )
    assert _post(client, files=_photos(2)).status_code == 503

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

    assert sorted(response.status_code for response in responses) == [409, 503]
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


def test_archive_preparation_migration_has_atomic_claim_and_safe_upsert():
    migration = (
        Path(__file__).parents[2]
        / "supabase"
        / "migrations"
        / "20261107000000_archive_intake_preparation.sql"
    ).read_text(encoding="utf-8").lower()

    assert "'preparing'" in migration
    assert "'generation_failed'" in migration
    assert "upsert_archive_intake_metadata" in migration
    assert "claim_archive_intake_preparation" in migration
    assert "pg_advisory_xact_lock" in migration
    assert "generation_run_id is null" in migration
    assert "interval '15 minutes'" in migration
    assert "from public, anon, authenticated" in migration
    assert "to service_role" in migration


def test_archive_generation_idempotency_key_is_stable_and_valid_length():
    key = archive_intake_preparation_service.generation_idempotency_key(
        "archive_application-123"
    )

    assert key == "free-home:archive_application-123"
    assert len(key) <= 200


def test_archive_failure_codes_are_machine_safe():
    assert (
        archive_intake_service.safe_error_code("provider-token=secret\ntrace")
        == "ARCHIVE_GENERATION_PREPARATION_FAILED"
    )
    assert (
        archive_intake_service.safe_error_code("GENERATION_RUNS_UNAVAILABLE")
        == "GENERATION_RUNS_UNAVAILABLE"
    )


def test_archive_cutout_reuse_keeps_inference_on_calling_thread():
    caller_thread = threading.get_ident()
    inference_threads: list[int] = []
    png = make_rgba_png_bytes(0.5)

    def fake_matte(data: bytes, **kwargs):
        inference_threads.append(threading.get_ident())
        return png, {"method": "vitmatte"}

    generated = anyio.run(
        lambda: pet_cutout_service.generate_vitmatte_cutout(
            b"image-bytes",
            matte_function=fake_matte,
            quality_function=lambda value: {"quality_score": 0.9},
        )
    )

    assert inference_threads == [caller_thread]
    assert generated.png == png
    assert generated.diagnostics["refinement_type"] == "vitmatte"


def test_normal_matting_route_stays_same_thread_and_uses_existing_model_functions():
    source = (
        Path(__file__).parents[1] / "routers" / "matting.py"
    ).read_text(encoding="utf-8")

    assert "generate_vitmatte_cutout_sync(" in source
    assert "matte_function=matte_foreground_with_meta" in source
    assert "quality_function=analyze_alpha_fur_edge" in source
    assert "asyncio.to_thread" not in source
