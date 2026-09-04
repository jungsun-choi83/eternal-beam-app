"""
Durable Pet Identity Intake (Phase 1) 계약 테스트.

- 원본은 스토리지에 영구 저장되고 대장(pet_reference_images)에 version 1 로 남는다
- 같은 바이트의 재시도는 멱등하다 (새 버전·새 객체를 만들지 않는다)
- 한 펫에 여러 원본 레퍼런스가 쌓인다 (버전 증가)
- 소유권 격리: 남의 펫에는 기록도 조회도 안 된다 (TOFU + pets 레지스트리)
- 원본 vs 파생: 파생 기록은 업로드하지 않는다 — 원본 객체를 덮어쓸 방법이 없다
- 기존 단일 사진 온보딩(누끼 응답 계약)은 레퍼런스 기록이 실패해도 그대로다
"""

from __future__ import annotations

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import assets as assets_router
from backend.routers import matting as matting_router
from backend.routers import pet_references_v1 as references_router
from backend.services import pet_reference_service as refs
from backend.services import pet_registry

from .conftest import ASGITestClient, make_jpeg_bytes, make_rgba_png_bytes


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    """DB 를 쓰지 않는 인메모리 경로 + 스토리지 목업."""
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    refs.__reset_for_tests()
    pet_registry.__reset_for_tests()
    yield
    refs.__reset_for_tests()
    pet_registry.__reset_for_tests()


@pytest.fixture
def uploads(monkeypatch) -> list[str]:
    """upload_asset_to_storage 호출 경로 기록."""
    from backend.services import supabase_assets

    paths: list[str] = []

    async def fake_upload(path, data, content_type):
        paths.append(path)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    return paths


def _run(coro):
    return anyio.run(lambda: coro)


# --------------------------------------------------------------------------
# 원본 보존
# --------------------------------------------------------------------------


def test_record_original_persists_bytes_and_row(uploads):
    data = make_jpeg_bytes()
    ref = _run(
        refs.record_original(
            user_id="alice@test",
            content_id="cid1",
            data=data,
            mime_type="image/jpeg",
            original_filename="dog.jpg",
            diagnostics={"quality_score": 0.9},
        )
    )

    assert ref.recorded is True
    assert ref.deduplicated is False
    assert ref.role == refs.ROLE_ORIGINAL
    assert ref.pet_id == "pet_cid1"
    assert ref.version == 1
    assert ref.content_hash and len(ref.content_hash) == 64
    assert ref.width == 128 and ref.height == 96
    assert ref.bytes_size == len(data)
    assert ref.original_filename == "dog.jpg"
    # 해시가 경로에 들어간다 — 다른 바이트가 같은 객체를 덮어쓸 수 없다.
    assert ref.object_path == f"alice@test/cid1/references/original_{ref.content_hash[:16]}.jpg"
    assert uploads == [ref.object_path]

    listed = _run(refs.list_references(user_id="alice@test", pet_id="pet_cid1"))
    assert len(listed) == 1
    assert listed[0].object_path == ref.object_path


def test_same_bytes_intake_is_idempotent(uploads):
    data = make_jpeg_bytes()
    first = _run(refs.record_original(user_id="alice@test", content_id="cid1", data=data, mime_type="image/jpeg"))
    second = _run(refs.record_original(user_id="alice@test", content_id="cid1", data=data, mime_type="image/jpeg"))

    assert second.deduplicated is True
    assert second.id == first.id
    assert second.version == first.version == 1
    # 재시도는 스토리지에 다시 올리지도 않는다.
    assert len(uploads) == 1

    listed = _run(refs.list_references(user_id="alice@test", pet_id="pet_cid1"))
    assert len(listed) == 1


def test_multiple_originals_get_increasing_versions(uploads):
    a = _run(refs.record_original(user_id="alice@test", content_id="cid1", data=make_jpeg_bytes(64, 64), mime_type="image/jpeg"))
    b = _run(refs.record_original(user_id="alice@test", content_id="cid1", data=make_jpeg_bytes(200, 150), mime_type="image/jpeg"))

    assert (a.version, b.version) == (1, 2)
    assert a.object_path != b.object_path
    assert len(uploads) == 2

    listed = _run(refs.list_references(user_id="alice@test", pet_id="pet_cid1"))
    assert [r.version for r in listed if r.role == refs.ROLE_ORIGINAL] == [1, 2]


# --------------------------------------------------------------------------
# 소유권 격리
# --------------------------------------------------------------------------


def test_second_user_cannot_add_reference_to_owned_pet(uploads):
    _run(refs.record_original(user_id="alice@test", content_id="cid1", data=make_jpeg_bytes(), mime_type="image/jpeg"))

    with pytest.raises(refs.PetReferenceError) as e:
        _run(refs.record_original(user_id="mallory@test", content_id="cid1", data=make_jpeg_bytes(64, 64), mime_type="image/jpeg"))
    assert e.value.code == "PET_NOT_OWNED"
    assert e.value.status == 403


def test_registry_owner_blocks_other_users_even_without_rows(uploads):
    # 레지스트리에 등록된 펫(레퍼런스 행 없음)에 남이 기록하려는 경우.
    _run(
        pet_registry.register(
            user_id="alice@test",
            pet_id="pet_cid9",
            content_id="cid9",
            breathing_object_path="alice@test/cid9/idle_loop.mp4",
            verify=False,
        )
    )
    with pytest.raises(refs.PetReferenceError) as e:
        _run(refs.record_original(user_id="mallory@test", content_id="cid9", data=make_jpeg_bytes(), mime_type="image/jpeg"))
    assert e.value.code == "PET_NOT_OWNED"


def test_list_is_ownership_isolated(uploads):
    _run(refs.record_original(user_id="alice@test", content_id="cid1", data=make_jpeg_bytes(), mime_type="image/jpeg"))

    with pytest.raises(refs.PetReferenceError) as e:
        _run(refs.list_references(user_id="mallory@test", pet_id="pet_cid1"))
    assert e.value.code == "PET_NOT_OWNED"


# --------------------------------------------------------------------------
# 원본 vs 파생 (provenance)
# --------------------------------------------------------------------------


def test_derived_record_never_uploads_or_touches_original(uploads):
    original = _run(refs.record_original(user_id="alice@test", content_id="cid1", data=make_jpeg_bytes(), mime_type="image/jpeg"))
    uploads_after_original = list(uploads)

    derived = _run(
        refs.record_derived(
            user_id="alice@test",
            content_id="cid1",
            object_path="alice@test/cid1/cutout_vitmatte.png",
            derived_kind="cutout_vitmatte",
            diagnostics={"refined": True},
        )
    )

    assert derived.role == refs.ROLE_DERIVED
    assert derived.derived_kind == "cutout_vitmatte"
    # 파생 기록은 스토리지에 아무것도 올리지 않는다 — 원본 객체는 건드릴 수 없다.
    assert uploads == uploads_after_original
    assert derived.object_path != original.object_path

    listed = _run(refs.list_references(user_id="alice@test", pet_id="pet_cid1"))
    roles = {r.object_path: r.role for r in listed}
    assert roles[original.object_path] == refs.ROLE_ORIGINAL
    assert roles[derived.object_path] == refs.ROLE_DERIVED


def test_derived_same_object_is_idempotent(uploads):
    kw = dict(
        user_id="alice@test",
        content_id="cid1",
        object_path="alice@test/cid1/cutout_vitmatte.png",
        derived_kind="cutout_vitmatte",
    )
    first = _run(refs.record_derived(**kw))
    second = _run(refs.record_derived(**kw))
    assert second.deduplicated is True
    assert second.id == first.id

    listed = _run(refs.list_references(user_id="alice@test", pet_id="pet_cid1"))
    assert len(listed) == 1


def test_derived_requires_kind(uploads):
    with pytest.raises(refs.PetReferenceError):
        _run(
            refs.record_derived(
                user_id="alice@test",
                content_id="cid1",
                object_path="alice@test/cid1/cutout.png",
                derived_kind="",
            )
        )


# --------------------------------------------------------------------------
# POST /api/assets/original (라우터 계약)
# --------------------------------------------------------------------------


@pytest.fixture
def assets_client(uploads) -> ASGITestClient:
    app = FastAPI()
    app.include_router(assets_router.router, prefix="/api")
    return ASGITestClient(app)


def test_post_original_persists_and_reports(assets_client, uploads):
    res = assets_client.post(
        "/api/assets/original",
        files={"file": ("dog.jpg", make_jpeg_bytes(), "image/jpeg")},
        data={"user_id": "alice@test", "content_id": "cid1", "diagnostics_json": '{"quality_score": 0.8}'},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["pet_id"] == "pet_cid1"
    assert body["reference_recorded"] is True
    assert body["deduplicated"] is False
    assert body["version"] == 1
    assert body["object_path"].startswith("alice@test/cid1/references/original_")
    assert uploads == [body["object_path"]]

    listed = _run(refs.list_references(user_id="alice@test", pet_id="pet_cid1"))
    assert len(listed) == 1
    assert listed[0].diagnostics == {"quality_score": 0.8}


def test_post_original_duplicate_is_idempotent(assets_client, uploads):
    data = make_jpeg_bytes()
    files = {"file": ("dog.jpg", data, "image/jpeg")}
    form = {"user_id": "alice@test", "content_id": "cid1"}
    first = assets_client.post("/api/assets/original", files=files, data=form).json()
    second = assets_client.post("/api/assets/original", files={"file": ("dog.jpg", data, "image/jpeg")}, data=form).json()

    assert second["deduplicated"] is True
    assert second["reference_id"] == first["reference_id"]
    assert len(uploads) == 1


def test_post_original_rejects_empty_and_missing_fields(assets_client):
    res = assets_client.post(
        "/api/assets/original",
        files={"file": ("dog.jpg", b"", "image/jpeg")},
        data={"user_id": "alice@test", "content_id": "cid1"},
    )
    assert res.status_code == 400

    res = assets_client.post(
        "/api/assets/original",
        files={"file": ("dog.jpg", make_jpeg_bytes(), "image/jpeg")},
        data={"user_id": " ", "content_id": "cid1"},
    )
    assert res.status_code == 400


def test_post_original_storage_failure_is_502(assets_client, monkeypatch):
    from backend.services import supabase_assets

    async def boom(path, data, content_type):
        raise RuntimeError("storage down")

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", boom)
    res = assets_client.post(
        "/api/assets/original",
        files={"file": ("dog.jpg", make_jpeg_bytes(), "image/jpeg")},
        data={"user_id": "alice@test", "content_id": "cid1"},
    )
    assert res.status_code == 502
    # durable 하지 않으면 대장에도 남지 않는다.
    assert _run(refs.list_references(user_id="alice@test", pet_id="pet_cid1")) == []


def test_post_original_ownership_isolated(assets_client):
    files = {"file": ("dog.jpg", make_jpeg_bytes(), "image/jpeg")}
    assets_client.post("/api/assets/original", files=files, data={"user_id": "alice@test", "content_id": "cid1"})
    res = assets_client.post(
        "/api/assets/original",
        files={"file": ("other.jpg", make_jpeg_bytes(64, 64), "image/jpeg")},
        data={"user_id": "mallory@test", "content_id": "cid1"},
    )
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "PET_NOT_OWNED"


# --------------------------------------------------------------------------
# Phase 7B authenticated stable intake contract
# --------------------------------------------------------------------------


def _phase7b_form(user="alice@test", content="stable"):
    return {"user_id": user, "content_id": content, "phase1_intake": "true"}


def _phase7b_auth(user="alice@test"):
    return {"Authorization": f"Bearer test:{user}"}


def test_phase7b_original_then_cutout_is_ready_and_idempotent(
    assets_client, uploads, monkeypatch
):
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    original = make_jpeg_bytes()
    cutout = make_rgba_png_bytes(0.5)

    first = assets_client.post(
        "/api/assets/original",
        files={"file": ("dog.jpg", original, "image/jpeg")},
        data=_phase7b_form(),
        headers=_phase7b_auth(),
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["pet_id"] == "pet_stable"
    assert first_body["intake_ready"] is False

    files = {
        "file": ("dog.jpg", original, "image/jpeg"),
        "cutout_file": ("cutout.png", cutout, "image/png"),
    }
    second = assets_client.post(
        "/api/assets/original",
        files=files,
        data=_phase7b_form(),
        headers=_phase7b_auth(),
    )
    assert second.status_code == 200
    body = second.json()
    assert body["user_id"] == "alice@test"
    assert body["content_id"] == "stable"
    assert body["pet_id"] == "pet_stable"
    assert body["reference_id"] == first_body["reference_id"]
    assert body["cutout_reference_id"]
    assert body["intake_ready"] is True

    duplicate = assets_client.post(
        "/api/assets/original",
        files={
            "file": ("dog.jpg", original, "image/jpeg"),
            "cutout_file": ("cutout.png", cutout, "image/png"),
        },
        data=_phase7b_form(),
        headers=_phase7b_auth(),
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["reference_id"] == body["reference_id"]
    assert duplicate.json()["cutout_reference_id"] == body["cutout_reference_id"]
    # original and cutout are each uploaded exactly once.
    assert len(uploads) == 2

    ledger = _run(refs.list_references(user_id="alice@test", pet_id="pet_stable"))
    assert len(ledger) == 2
    ready, authoritative, derived = refs.intake_readiness(ledger)
    assert ready is True
    assert authoritative.role == refs.ROLE_ORIGINAL
    assert derived.role == refs.ROLE_DERIVED
    assert derived.parent_reference_id == authoritative.id


def test_phase7b_rejects_claimed_user_mismatch(assets_client, monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    res = assets_client.post(
        "/api/assets/original",
        files={"file": ("dog.jpg", make_jpeg_bytes(), "image/jpeg")},
        data=_phase7b_form(user="mallory@test"),
        headers=_phase7b_auth(user="alice@test"),
    )
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "INTAKE_IDENTITY_MISMATCH"


def test_phase7b_rejects_different_original_for_same_upload(assets_client, monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    assets_client.post(
        "/api/assets/original",
        files={"file": ("dog.jpg", make_jpeg_bytes(), "image/jpeg")},
        data=_phase7b_form(),
        headers=_phase7b_auth(),
    )
    res = assets_client.post(
        "/api/assets/original",
        files={"file": ("other.jpg", make_jpeg_bytes(64, 64), "image/jpeg")},
        data=_phase7b_form(),
        headers=_phase7b_auth(),
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "PHASE1_ORIGINAL_CONFLICT"


def test_phase7b_cutout_failure_preserves_original_and_retry_continues(
    assets_client, uploads, monkeypatch
):
    from backend.services import supabase_assets

    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    original = make_jpeg_bytes()
    cutout = make_rgba_png_bytes(0.5)
    original_response = assets_client.post(
        "/api/assets/original",
        files={"file": ("dog.jpg", original, "image/jpeg")},
        data=_phase7b_form(),
        headers=_phase7b_auth(),
    )
    assert original_response.status_code == 200
    real_upload = supabase_assets.upload_asset_to_storage

    async def fail_cutout(path, data, content_type):
        if "/cutout_" in path:
            raise RuntimeError("cutout storage down")
        return await real_upload(path, data, content_type)

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fail_cutout)
    failed = assets_client.post(
        "/api/assets/original",
        files={
            "file": ("dog.jpg", original, "image/jpeg"),
            "cutout_file": ("cutout.png", cutout, "image/png"),
        },
        data=_phase7b_form(),
        headers=_phase7b_auth(),
    )
    assert failed.status_code == 502
    ledger = _run(refs.list_references(user_id="alice@test", pet_id="pet_stable"))
    assert [item.role for item in ledger] == [refs.ROLE_ORIGINAL]

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", real_upload)
    retried = assets_client.post(
        "/api/assets/original",
        files={
            "file": ("dog.jpg", original, "image/jpeg"),
            "cutout_file": ("cutout.png", cutout, "image/png"),
        },
        data=_phase7b_form(),
        headers=_phase7b_auth(),
    )
    assert retried.status_code == 200
    assert retried.json()["intake_ready"] is True


def test_derived_parent_must_be_same_pet_original(uploads):
    with pytest.raises(refs.PetReferenceError) as error:
        _run(
            refs.record_derived(
                user_id="alice@test",
                content_id="stable",
                object_path="alice@test/stable/references/cutout.png",
                derived_kind="cutout_reference",
                parent_reference_id="missing-original",
            )
        )
    assert error.value.code == "PET_REFERENCE_PARENT_INVALID"


def test_phase1_get_reports_intake_ready(assets_client, monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    original = make_jpeg_bytes()
    created = assets_client.post(
        "/api/assets/original",
        files={
            "file": ("dog.jpg", original, "image/jpeg"),
            "cutout_file": ("cutout.png", make_rgba_png_bytes(0.5), "image/png"),
        },
        data=_phase7b_form(),
        headers=_phase7b_auth(),
    )
    assert created.status_code == 200

    app = FastAPI()
    app.include_router(references_router.router, prefix="/api")
    response = ASGITestClient(app).get(
        "/api/v1/pet/references/pet_stable", headers=_phase7b_auth()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["intake_ready"] is True
    assert body["original_reference_id"] == created.json()["reference_id"]
    assert body["cutout_reference_id"] == created.json()["cutout_reference_id"]
    derived = next(item for item in body["references"] if item["role"] == "derived")
    assert derived["parent_reference_id"] == body["original_reference_id"]


def test_phase7b_stops_at_phase1_even_when_legacy_autobuild_flag_is_on(
    assets_client, monkeypatch
):
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    monkeypatch.setenv("IDENTITY_PROFILE_AUTOBUILD", "1")
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(assets_router, "_autobuild_identity_profile", forbidden)
    response = assets_client.post(
        "/api/assets/original",
        files={
            "file": ("dog.jpg", make_jpeg_bytes(), "image/jpeg"),
            "cutout_file": ("cutout.png", make_rgba_png_bytes(0.5), "image/png"),
        },
        data=_phase7b_form(),
        headers=_phase7b_auth(),
    )
    assert response.status_code == 200
    assert response.json()["intake_ready"] is True
    assert calls == []


# --------------------------------------------------------------------------
# 기존 단일 사진 온보딩 보존 (누끼 훅은 fail-open)
# --------------------------------------------------------------------------


def _fake_matte(monkeypatch):
    png = make_rgba_png_bytes(0.5)

    def fake(raw, **kwargs):
        return png, {"subject_detected": True}

    monkeypatch.setattr(matting_router, "matte_foreground_with_meta", fake)


@pytest.fixture
def matting_client(uploads, monkeypatch) -> ASGITestClient:
    from backend.services import supabase_assets

    app = FastAPI()
    app.include_router(matting_router.router, prefix="/api")
    client = ASGITestClient(app)

    async def fake_row(*args, **kwargs):
        return None

    monkeypatch.setattr(supabase_assets, "ensure_user_asset_row", fake_row)
    return client


def test_matting_save_records_derived_reference(matting_client, monkeypatch):
    _fake_matte(monkeypatch)
    res = matting_client.post(
        "/api/matting/cutout",
        files={"file": ("dog.jpg", make_jpeg_bytes(), "image/jpeg")},
        data={"user_id": "alice@test", "content_id": "cid1", "save_to_storage": "true"},
    )
    assert res.status_code == 200
    listed = _run(refs.list_references(user_id="alice@test", pet_id="pet_cid1"))
    assert len(listed) == 1
    assert listed[0].role == refs.ROLE_DERIVED
    assert listed[0].derived_kind == "cutout_vitmatte"
    assert listed[0].object_path == "alice@test/cid1/cutout_vitmatte.png"
    # 진단 메타가 그 시점 그대로 남는다.
    assert listed[0].diagnostics and listed[0].diagnostics.get("refinement_type") == "vitmatte"


def test_matting_without_save_records_nothing(matting_client, monkeypatch):
    _fake_matte(monkeypatch)
    res = matting_client.post(
        "/api/matting/cutout",
        files={"file": ("dog.jpg", make_jpeg_bytes(), "image/jpeg")},
        data={"user_id": "alice@test", "content_id": "cid1", "save_to_storage": "false"},
    )
    assert res.status_code == 200
    assert _run(refs.list_references(user_id="alice@test", pet_id="pet_cid1")) == []


def test_matting_contract_unchanged_when_reference_recording_fails(matting_client, monkeypatch):
    """레퍼런스 기록이 죽어도 누끼 응답 계약(단일 사진 온보딩)은 그대로다."""
    _fake_matte(monkeypatch)

    async def boom(**kwargs):
        raise RuntimeError("reference ledger down")

    monkeypatch.setattr(refs, "record_derived", boom)

    res = matting_client.post(
        "/api/matting/cutout",
        files={"file": ("dog.jpg", make_jpeg_bytes(), "image/jpeg")},
        data={"user_id": "alice@test", "content_id": "cid1", "save_to_storage": "true"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["content_id"] == "cid1"
    assert body["cutout_url"]
    assert body["error"] is None
    assert "cutout_quality" in body
