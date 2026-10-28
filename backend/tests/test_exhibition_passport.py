from __future__ import annotations

import concurrent.futures

import pytest
from fastapi import FastAPI

from backend.auth import AuthedUser
from backend.routers import exhibition_queue_v1
from backend.routers import exhibition_passport_v1 as router
from backend.services import exhibition_passport as passport
from backend.services import exhibition_prep_store as prep_store
from backend.services import exhibition_queue_service as queue
from backend.services.shaker_ops import require_ops
from backend.tests.conftest import ASGITestClient
from backend.tests.test_exhibition_prep_run import _jpeg


EXPO = "expo-passport"


def _token(n: int = 0) -> str:
    return ("A" * 42) + str(n).zfill(2)


def _register(store, *, token: str, pet_name: str = "Coco", email: str = "Alice@example.com"):
    return passport.register(
        exhibition_id=EXPO,
        pet_name=pet_name,
        email=email,
        privacy_consent=True,
        idempotency_key=token,
        store=store,
    )


def test_registration_is_idempotent_and_token_hash_is_the_only_token_storage():
    store = passport.InMemoryPassportStore()
    first = _register(store, token=_token(1))
    replay = _register(store, token=_token(1))

    assert first["queue_number"] == replay["queue_number"] == 1
    assert first["replayed"] is False and replay["replayed"] is True
    assert first["photo_status"] == "WAITING_FOR_PHOTO"
    assert _token(1) not in repr(store.rows)
    assert passport.hash_token(_token(1)) in repr(store.rows)


def test_id_reservation_is_idempotent_and_contains_no_personal_data():
    store = passport.InMemoryPassportStore()
    first = passport.reserve_id(exhibition_id=EXPO, idempotency_key=_token(17), store=store)
    replay = passport.reserve_id(exhibition_id=EXPO, idempotency_key=_token(17), store=store)

    assert first["queue_number"] == replay["queue_number"] == 1
    assert first["replayed"] is False and replay["replayed"] is True
    assert first["pet_name"] is None
    assert "email" not in first
    assert _token(17) not in repr(store.rows)
    assert passport.hash_token(_token(17)) in repr(store.rows)


def test_reusing_a_token_for_different_details_is_rejected():
    store = passport.InMemoryPassportStore()
    _register(store, token=_token(2), pet_name="Coco")

    with pytest.raises(passport.PassportConflict) as exc:
        _register(store, token=_token(2), pet_name="Bori")
    assert exc.value.code == "IDEMPOTENCY_KEY_REUSED"


def test_passport_and_staff_use_the_same_counter_without_conflicting_numbers():
    runs = prep_store.InMemoryRunStore()
    passports = passport.InMemoryPassportStore(runs)
    registration = _register(passports, token=_token(3))
    staff = queue.submit(
        _jpeg(800, 800),
        created_by="staff-1",
        store=runs,
        artifacts=prep_store.InMemoryArtifactStore(),
        exhibition_id=EXPO,
        pet_name="Bori",
    )

    assert registration["queue_number"] == 1
    assert staff["queue_number"] == 2
    assert {registration["queue_number"], staff["queue_number"]} == {1, 2}


def test_concurrent_passport_registrations_get_distinct_numbers():
    runs = prep_store.InMemoryRunStore()
    store = passport.InMemoryPassportStore(runs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda i: _register(store, token=_token(i)), range(16)))

    assert sorted(row["queue_number"] for row in rows) == list(range(1, 17))


def test_retrieval_is_token_scoped_and_never_returns_email():
    store = passport.InMemoryPassportStore()
    created = _register(store, token=_token(4))
    retrieved = passport.retrieve(token=_token(4), store=store)

    assert retrieved["queue_number"] == created["queue_number"]
    assert "email" not in retrieved
    assert passport.retrieve(token=_token(5), store=store) is None


def test_optional_email_is_saved_with_separate_consent_flags_and_never_returned():
    store = passport.InMemoryPassportStore()
    reserved = passport.reserve_id(exhibition_id=EXPO, idempotency_key=_token(46), store=store)

    saved = passport.save_email(
        token=_token(46),
        email="Visitor@example.com",
        ai_video_consent=True,
        marketing_consent=False,
        store=store,
    )

    assert saved["email_saved"] is True
    assert saved["ai_video_consent"] is True
    assert saved["marketing_consent"] is False
    assert "email" not in saved
    retrieved = passport.retrieve(token=_token(46), store=store)
    assert retrieved["email_saved"] is True
    assert retrieved["ai_video_consent"] is True
    assert retrieved["marketing_consent"] is False
    assert "email" not in retrieved
    assert store.rows[f"{EXPO}:{passport.hash_token(_token(46))}"]["email"] == "visitor@example.com"
    assert reserved["queue_number"] == retrieved["queue_number"]


def test_skipping_optional_email_keeps_id_and_stores_no_email():
    store = passport.InMemoryPassportStore()
    reserved = passport.reserve_id(exhibition_id=EXPO, idempotency_key=_token(47), store=store)

    retrieved = passport.retrieve(token=_token(47), store=store)

    assert retrieved["email_saved"] is False
    assert retrieved["queue_number"] == reserved["queue_number"]
    assert store.rows[f"{EXPO}:{passport.hash_token(_token(47))}"]["email"] is None


def test_retrieval_returns_short_lived_source_photo_only_for_the_matching_token():
    runs = prep_store.InMemoryRunStore()
    artifacts = prep_store.InMemoryArtifactStore()
    store = passport.InMemoryPassportStore(runs)
    registration = _register(store, token=_token(40), pet_name="Momo")
    attached = passport.submit_photo(
        _jpeg(800, 800), created_by="ops-1", exhibition_id=EXPO,
        queue_number=registration["queue_number"], run_store=runs,
        artifacts=artifacts, store=store,
    )

    retrieved = passport.retrieve_with_photo(
        token=_token(40), store=store, run_store=runs, artifacts=artifacts,
    )
    assert retrieved["photo_url"] == f"memory://{runs.get(attached['run_id'])['source_path']}"
    assert retrieved["photo_access_error"] is None
    assert "email" not in retrieved
    assert "photo_run_id" not in retrieved
    assert passport.retrieve_with_photo(
        token=_token(41), store=store, run_store=runs, artifacts=artifacts,
    ) is None


def test_ready_passport_reports_missing_private_artifact_without_exposing_storage_details():
    runs = prep_store.InMemoryRunStore()
    store = passport.InMemoryPassportStore(runs)
    registration = _register(store, token=_token(42))
    # Simulate a previously linked run whose object is no longer available.
    run = runs.insert({
        "exhibition_id": EXPO, "status": "QUEUED", "source_path": "exhibition/missing/source.png",
    })
    store.rows[f"{EXPO}:{passport.hash_token(_token(42))}"].update(
        photo_status="PHOTO_RECEIVED", photo_run_id=run["id"],
    )

    retrieved = passport.retrieve_with_photo(
        token=_token(42), store=store, run_store=runs,
        artifacts=prep_store.InMemoryArtifactStore(),
    )
    assert retrieved["photo_url"] is None
    assert retrieved["photo_access_error"] == "PHOTO_UNAVAILABLE"
    assert "source_path" not in repr(retrieved)


def test_received_passport_reports_unavailable_if_its_linked_run_disappears():
    runs = prep_store.InMemoryRunStore()
    artifacts = prep_store.InMemoryArtifactStore()
    store = passport.InMemoryPassportStore(runs)
    registration = _register(store, token=_token(44))
    attached = passport.submit_photo(
        _jpeg(640, 480), created_by="ops-1", exhibition_id=EXPO,
        queue_number=registration["queue_number"], run_store=runs,
        artifacts=artifacts, store=store,
    )

    del runs.rows[attached["run_id"]]
    retrieved = passport.retrieve_with_photo(
        token=_token(44), store=store, run_store=runs, artifacts=artifacts,
    )

    assert retrieved["photo_status"] == "PHOTO_RECEIVED"
    assert retrieved["photo_url"] is None
    assert retrieved["photo_access_error"] == "PHOTO_UNAVAILABLE"


def test_photo_association_keeps_passport_status_and_run_reference_consistent():
    runs = prep_store.InMemoryRunStore()
    artifacts = prep_store.InMemoryArtifactStore()
    store = passport.InMemoryPassportStore(runs)
    registration = _register(store, token=_token(45))

    attached = passport.submit_photo(
        _jpeg(640, 480), created_by="ops-1", exhibition_id=EXPO,
        queue_number=registration["queue_number"], run_store=runs,
        artifacts=artifacts, store=store,
    )
    linked = store.get_by_queue_number(EXPO, registration["queue_number"])

    assert linked["photo_status"] == "PHOTO_RECEIVED"
    assert linked["photo_run_id"] == attached["run_id"]
    assert linked["photo_received_at"]


def test_public_api_has_registration_and_non_enumerable_retrieval_only():
    app = FastAPI()
    app.include_router(router.router, prefix="/api")
    store = passport.InMemoryPassportStore()
    app.dependency_overrides[router.get_store] = lambda: store
    client = ASGITestClient(app)

    created = client.post(
        "/api/exhibition/passport/registrations",
        json={
            "exhibition_id": EXPO,
            "pet_name": "  Coco  ",
            "email": "Alice@example.com",
            "privacy_consent": True,
            "idempotency_key": _token(6),
        },
    )
    assert created.status_code == 200, created.text
    assert "Alice@example.com" not in created.text
    assert created.json()["passport_token"] == _token(6)

    retrieved = client.post(
        "/api/exhibition/passport/retrieve",
        json={"passport_token": _token(6)},
    )
    assert retrieved.status_code == 200, retrieved.text
    assert "Alice@example.com" not in retrieved.text
    assert retrieved.headers["cache-control"] == "no-store"

    unknown = client.post(
        "/api/exhibition/passport/retrieve",
        json={"passport_token": _token(7)},
    )
    assert unknown.status_code == 404


def test_public_email_save_is_token_scoped_and_does_not_return_email():
    app = FastAPI()
    app.include_router(router.router, prefix="/api")
    store = passport.InMemoryPassportStore()
    app.dependency_overrides[router.get_store] = lambda: store
    client = ASGITestClient(app)

    reserved = client.post(
        "/api/exhibition/passport/id-reservations",
        json={"exhibition_id": EXPO, "idempotency_key": _token(48)},
    )
    assert reserved.status_code == 200, reserved.text

    saved = client.post(
        "/api/exhibition/passport/email",
        json={
            "passport_token": _token(48),
            "email": "Visitor@example.com",
            "ai_video_consent": True,
            "marketing_consent": False,
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["email_saved"] is True
    assert "Visitor@example.com" not in saved.text

    unknown = client.post(
        "/api/exhibition/passport/email",
        json={
            "passport_token": _token(49),
            "email": "other@example.com",
        },
    )
    assert unknown.status_code == 404
    assert client.get(f"/api/exhibition/passport/retrieve?passport_token={_token(6)}").status_code == 405


def test_public_api_reserves_a_pet_id_without_name_or_email():
    app = FastAPI()
    app.include_router(router.router, prefix="/api")
    store = passport.InMemoryPassportStore()
    app.dependency_overrides[router.get_store] = lambda: store
    client = ASGITestClient(app)

    created = client.post(
        "/api/exhibition/passport/id-reservations",
        json={"exhibition_id": EXPO, "idempotency_key": _token(18)},
    )
    assert created.status_code == 200, created.text
    assert created.json()["queue_number"] == 1
    assert created.json()["pet_name"] is None
    assert "email_saved" in created.text

    replay = client.post(
        "/api/exhibition/passport/id-reservations",
        json={"exhibition_id": EXPO, "idempotency_key": _token(18)},
    )
    assert replay.status_code == 200
    assert replay.json()["queue_number"] == 1
    assert replay.json()["replayed"] is True


def test_public_api_returns_signed_photo_only_for_a_token_scoped_ready_passport():
    runs = prep_store.InMemoryRunStore()
    artifacts = prep_store.InMemoryArtifactStore()
    store = passport.InMemoryPassportStore(runs)
    registration = _register(store, token=_token(43), pet_name="Momo")
    passport.submit_photo(
        _jpeg(640, 480), created_by="ops-1", exhibition_id=EXPO,
        queue_number=registration["queue_number"], run_store=runs,
        artifacts=artifacts, store=store,
    )

    app = FastAPI()
    app.include_router(router.router, prefix="/api")
    app.dependency_overrides[router.get_store] = lambda: store
    app.dependency_overrides[router.get_photo_access_stores] = lambda: (runs, artifacts)
    client = ASGITestClient(app)

    response = client.post(
        "/api/exhibition/passport/retrieve", json={"passport_token": _token(43)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["photo_url"].startswith("memory://")
    assert "email" not in body
    assert "photo_run_id" not in body


def test_staff_photo_reuses_passport_number_and_preserves_source_photo():
    runs = prep_store.InMemoryRunStore()
    artifacts = prep_store.InMemoryArtifactStore()
    store = passport.InMemoryPassportStore(runs)
    registration = _register(store, token=_token(10), pet_name="Coco")

    attached = passport.submit_photo(
        _jpeg(800, 800),
        created_by="ops-1",
        exhibition_id=EXPO,
        queue_number=registration["queue_number"],
        run_store=runs,
        artifacts=artifacts,
        store=store,
    )

    assert attached["queue_number"] == registration["queue_number"] == 1
    assert attached["run_id"] == store.get_by_queue_number(EXPO, 1)["photo_run_id"]
    run = runs.get(attached["run_id"])
    assert run["display_status"] == "WAITING"
    assert run["pet_name"] == "Coco"
    assert run["source_path"] in artifacts.objects
    assert artifacts.objects[run["source_path"]][0]
    assert store.get_by_queue_number(EXPO, 1)["photo_status"] == "PHOTO_RECEIVED"


def test_invalid_or_wrong_exhibition_passport_number_does_not_create_a_run():
    runs = prep_store.InMemoryRunStore()
    artifacts = prep_store.InMemoryArtifactStore()
    store = passport.InMemoryPassportStore(runs)
    _register(store, token=_token(11))

    for exhibition_id, number in ((EXPO, 999), ("other-exhibition", 1)):
        with pytest.raises(passport.PassportError) as exc:
            passport.submit_photo(
                _jpeg(800, 800),
                created_by="ops-1",
                exhibition_id=exhibition_id,
                queue_number=number,
                run_store=runs,
                artifacts=artifacts,
                store=store,
            )
        assert exc.value.code == "PASSPORT_NOT_FOUND"
    assert runs.rows == {}
    assert artifacts.objects == {}


def test_duplicate_passport_photo_is_rejected_without_a_second_queued_run():
    runs = prep_store.InMemoryRunStore()
    artifacts = prep_store.InMemoryArtifactStore()
    store = passport.InMemoryPassportStore(runs)
    registration = _register(store, token=_token(12))
    first = passport.submit_photo(
        _jpeg(800, 800), created_by="ops-1", exhibition_id=EXPO,
        queue_number=registration["queue_number"], run_store=runs, artifacts=artifacts, store=store,
    )

    with pytest.raises(passport.PassportConflict) as exc:
        passport.submit_photo(
            _jpeg(800, 800), created_by="ops-1", exhibition_id=EXPO,
            queue_number=registration["queue_number"], run_store=runs, artifacts=artifacts, store=store,
        )
    assert exc.value.code == "PASSPORT_PHOTO_ALREADY_ASSOCIATED"
    assert [r for r in runs.rows.values() if r.get("queue_number") == first["queue_number"]] == [runs.get(first["run_id"])]


def test_concurrent_passport_photo_submissions_have_one_association():
    runs = prep_store.InMemoryRunStore()
    artifacts = prep_store.InMemoryArtifactStore()
    store = passport.InMemoryPassportStore(runs)
    registration = _register(store, token=_token(13))

    def submit_one():
        try:
            return passport.submit_photo(
                _jpeg(800, 800), created_by="ops-1", exhibition_id=EXPO,
                queue_number=registration["queue_number"], run_store=runs, artifacts=artifacts, store=store,
            )
        except passport.PassportError as exc:
            return exc.code

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _i: submit_one(), range(6)))

    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(result == "PASSPORT_PHOTO_ALREADY_ASSOCIATED" for result in results) == 5
    assert len([r for r in runs.rows.values() if r.get("queue_number") is not None]) == 1


def test_storage_failure_can_be_retried_without_consuming_another_number():
    class FailingArtifacts(prep_store.InMemoryArtifactStore):
        def put(self, path, data, content_type):
            raise OSError("storage unavailable")

    runs = prep_store.InMemoryRunStore()
    store = passport.InMemoryPassportStore(runs)
    registration = _register(store, token=_token(14))
    with pytest.raises(OSError):
        passport.submit_photo(
            _jpeg(800, 800), created_by="ops-1", exhibition_id=EXPO,
            queue_number=registration["queue_number"], run_store=runs,
            artifacts=FailingArtifacts(), store=store,
        )
    assert store.get_by_queue_number(EXPO, 1)["photo_status"] == "WAITING_FOR_PHOTO"

    attached = passport.submit_photo(
        _jpeg(800, 800), created_by="ops-1", exhibition_id=EXPO,
        queue_number=registration["queue_number"], run_store=runs,
        artifacts=prep_store.InMemoryArtifactStore(), store=store,
    )
    assert attached["queue_number"] == 1


def test_authenticated_staff_endpoint_associates_passport_without_allocating_again(monkeypatch):
    runs = prep_store.InMemoryRunStore()
    artifacts = prep_store.InMemoryArtifactStore()
    passports = passport.InMemoryPassportStore(runs)
    registration = _register(passports, token=_token(15), pet_name="Nabi")

    app = FastAPI()
    app.include_router(exhibition_queue_v1.router, prefix="/api")
    app.dependency_overrides[require_ops] = lambda: AuthedUser(user_id="ops-1")
    app.dependency_overrides[exhibition_queue_v1.get_stores] = lambda: (runs, artifacts)
    monkeypatch.setattr(passport, "default_store", lambda: passports)
    client = ASGITestClient(app)

    response = client.post(
        "/api/exhibition/staff/submissions",
        files={"file": ("nabi.jpg", _jpeg(640, 480), "image/jpeg")},
        data={"exhibition_id": EXPO, "queue_number": str(registration["queue_number"])},
    )
    assert response.status_code == 200, response.text
    assert response.json()["queue_number"] == registration["queue_number"] == 1
    assert len(runs.rows) == 1
    assert passports.get_by_queue_number(EXPO, 1)["photo_status"] == "PHOTO_RECEIVED"


def test_authenticated_staff_endpoint_rejects_wrong_passport_number_before_storage(monkeypatch):
    runs = prep_store.InMemoryRunStore()
    artifacts = prep_store.InMemoryArtifactStore()
    passports = passport.InMemoryPassportStore(runs)
    _register(passports, token=_token(16))

    app = FastAPI()
    app.include_router(exhibition_queue_v1.router, prefix="/api")
    app.dependency_overrides[require_ops] = lambda: AuthedUser(user_id="ops-1")
    app.dependency_overrides[exhibition_queue_v1.get_stores] = lambda: (runs, artifacts)
    monkeypatch.setattr(passport, "default_store", lambda: passports)
    client = ASGITestClient(app)

    response = client.post(
        "/api/exhibition/staff/submissions",
        files={"file": ("nabi.jpg", _jpeg(640, 480), "image/jpeg")},
        data={"exhibition_id": EXPO, "queue_number": "999"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PASSPORT_NOT_FOUND"
    assert runs.rows == {}
    assert artifacts.objects == {}
