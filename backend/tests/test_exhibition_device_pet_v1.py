"""
GET /api/exhibition/device/pet/{visitor_number} — Exhibition 전용 PULL 엔드포인트.

워커의 자동 handoff 는 제거됐다 (test_exhibition_prep_run.py 의 격리 테스트가 이
파일을 계속 지키고, 자동-전송 제거는 exhibition_prep_worker.py 소스에서 직접
확인한다). 여기서는 새 엔드포인트의 상태 전이와 인증만 본다.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from backend.routers import exhibition_device_v1 as dev_v1
from backend.services import exhibition_prep_store as st
from backend.services.cutout_errors import SubjectNotDetectedError
from backend.tests.test_exhibition_breathing_maps import frontal_dog, round_blob
from backend.tests.test_exhibition_handoff import PAYLOAD_KEYS
from backend.tests.test_exhibition_prep_run import _jpeg, _run, fake_matte
from .conftest import ASGITestClient

DEVICE_ID = "beam-001"
DEVICE_SECRET = "s3cret"


@pytest.fixture
def stores():
    return st.InMemoryRunStore(), st.InMemoryArtifactStore()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setenv("ETERNAL_BEAM_DEVICE_TOKENS", f'{{"{DEVICE_ID}":"{DEVICE_SECRET}"}}')
    monkeypatch.delenv("EXHIBITION_DEFAULT_ID", raising=False)
    for k in ("EXHIBITION_HANDOFF_URL_TTL_SEC",):
        monkeypatch.delenv(k, raising=False)


def _app(stores):
    app = FastAPI()
    app.include_router(dev_v1.router, prefix="/api")
    app.dependency_overrides[dev_v1.get_stores] = lambda: stores
    return ASGITestClient(app)


def _auth(**extra):
    return {"device_id": DEVICE_ID, "token": DEVICE_SECRET, **extra}


def test_wrong_or_missing_device_token_is_unauthorized(stores):
    client = _app(stores)
    res = client.get("/api/exhibition/device/pet/0001", params=_auth(token="nope"))
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "DEVICE_UNAUTHORIZED"

    res = client.get("/api/exhibition/device/pet/0001", params={"device_id": "unknown-device", "token": "x"})
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "DEVICE_UNAUTHORIZED"


def test_unknown_visitor_number_is_not_found(stores):
    client = _app(stores)
    res = client.get("/api/exhibition/device/pet/0007", params=_auth(exhibition_id="expo-1"))
    assert res.status_code == 404
    assert res.json()["detail"] == {"package_id": "visitor_0007", "status": "NOT_FOUND"}


def test_non_integer_visitor_number_is_rejected(stores):
    client = _app(stores)
    res = client.get("/api/exhibition/device/pet/not-a-number", params=_auth())
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "INVALID_VISITOR_NUMBER"


def test_queued_run_reports_waiting(stores):
    store, artifacts = stores
    row = svc_create_queued(stores, exhibition_id="expo-1", queue_number=12)
    assert row["status"] == st.STATUS_QUEUED  # still in line, not yet claimed by the worker

    client = _app(stores)
    res = client.get("/api/exhibition/device/pet/0012", params=_auth(exhibition_id="expo-1"))
    assert res.status_code == 200
    assert res.json() == {"package_id": "visitor_0012", "status": "WAITING"}


def test_ready_run_mints_a_fresh_pet_asset_payload_each_call(stores):
    store, artifacts = stores
    final = _run(
        stores, _jpeg(800, 800), fake_matte(frontal_dog),
        exhibition_id="expo-1", allocate_queue_number=lambda: 40,
    )
    assert final["status"] == st.STATUS_READY

    seen_ttls = []
    real_signed_url = artifacts.signed_url
    artifacts.signed_url = lambda path, ttl=3600: seen_ttls.append(ttl) or real_signed_url(path, ttl)

    client = _app(stores)
    for _ in range(2):  # not cached — minted fresh on every request
        res = client.get("/api/exhibition/device/pet/0040", params=_auth(exhibition_id="expo-1"))
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "READY"
        payload = body["pet_asset"]
        assert set(payload) == PAYLOAD_KEYS
        assert payload["event"] == "pet_asset"
        assert payload["run_id"] == final["id"]
        assert payload["package_id"] == "visitor_0040"
        assert payload["schema_version"] == "EXHIBITION_RIG_INPUT_V1"
        assert payload["motion"] == "BREATHING"
        assert payload["packed_url"] == f"memory://exhibition/{final['id']}/visitor_0040.zip"
        assert not any("preview_overlay" in str(v) for v in payload.values())

    assert len(seen_ttls) == 2  # signed twice, not reused from a cache


def test_needs_review_run_reports_failed_with_reasons(stores):
    final = _run(
        stores, _jpeg(800, 800), fake_matte(round_blob),
        exhibition_id="expo-1", allocate_queue_number=lambda: 41,
    )
    assert final["status"] == st.STATUS_NEEDS_REVIEW

    client = _app(stores)
    res = client.get("/api/exhibition/device/pet/0041", params=_auth(exhibition_id="expo-1"))
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "FAILED"
    assert body["package_id"] == "visitor_0041"
    assert "head_low_confidence" in body["review_reasons"]


def test_failed_run_reports_failed_with_error_code(stores):
    def _reject(_b):
        raise SubjectNotDetectedError("No supported pet was detected in the image.")

    final = _run(
        stores, _jpeg(400, 400), _reject,
        exhibition_id="expo-1", allocate_queue_number=lambda: 42,
    )
    assert final["status"] == st.STATUS_FAILED

    client = _app(stores)
    res = client.get("/api/exhibition/device/pet/0042", params=_auth(exhibition_id="expo-1"))
    assert res.status_code == 200
    body = res.json()
    assert body == {
        "package_id": "visitor_0042", "status": "FAILED", "error_code": "SUBJECT_NOT_DETECTED", "review_reasons": [],
    }


def test_default_exhibition_id_is_used_when_omitted(stores):
    final = _run(
        stores, _jpeg(800, 800), fake_matte(frontal_dog),
        exhibition_id="default", allocate_queue_number=lambda: 5,
    )
    assert final["status"] == st.STATUS_READY

    client = _app(stores)
    res = client.get("/api/exhibition/device/pet/0005", params=_auth())  # no exhibition_id query param
    assert res.status_code == 200
    assert res.json()["status"] == "READY"


def test_a_run_in_a_different_exhibition_is_not_found(stores):
    _run(
        stores, _jpeg(800, 800), fake_matte(frontal_dog),
        exhibition_id="expo-other", allocate_queue_number=lambda: 40,
    )
    client = _app(stores)
    res = client.get("/api/exhibition/device/pet/0040", params=_auth(exhibition_id="expo-1"))
    assert res.status_code == 404


def svc_create_queued(stores, *, exhibition_id: str, queue_number: int):
    from backend.services import exhibition_prep_service as svc

    store, artifacts = stores
    return svc.create_run(
        _jpeg(800, 800), created_by="ops-1", store=store, artifacts=artifacts,
        exhibition_id=exhibition_id, allocate_queue_number=lambda: queue_number,
    )
