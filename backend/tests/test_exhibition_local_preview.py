"""Safety checks for the opt-in, in-memory Exhibition Staff preview."""

from __future__ import annotations

import os
import io
import subprocess
import sys

import anyio
import pytest
from fastapi import FastAPI
from PIL import Image

from backend.auth import AuthedUser, require_user
from backend.routers import exhibition_passport_v1, exhibition_prep_v1, exhibition_queue_v1
from backend.services import exhibition_prep_store as st
from backend.services import exhibition_passport
from backend.services import identity_service
from backend.services.shaker_ops import require_ops
from backend.tests.conftest import ASGITestClient


@pytest.mark.parametrize(
    ("environment_name", "environment_value"),
    [("APP_ENV", "production"), ("ENVIRONMENT", "staging")],
)
def test_preview_launcher_rejects_production_environment(monkeypatch, environment_name, environment_value):
    from backend import exhibition_preview

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv(environment_name, environment_value)
    monkeypatch.delenv("ENABLE_EXHIBITION_LOCAL_PREVIEW", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_TEST_AUTH", raising=False)

    with pytest.raises(RuntimeError, match="production-like environment"):
        exhibition_preview.configure_local_preview_environment()

    assert os.getenv("ENABLE_EXHIBITION_LOCAL_PREVIEW") is None
    assert os.getenv("ALLOW_INSECURE_TEST_AUTH") is None


def test_preview_launcher_rejects_conflicting_environment_settings(monkeypatch):
    from backend import exhibition_preview

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ENABLE_EXHIBITION_LOCAL_PREVIEW", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_TEST_AUTH", raising=False)

    with pytest.raises(RuntimeError, match="ENVIRONMENT=production"):
        exhibition_preview.configure_local_preview_environment()

    assert os.getenv("ENABLE_EXHIBITION_LOCAL_PREVIEW") is None
    assert os.getenv("ALLOW_INSECURE_TEST_AUTH") is None


def test_preview_launcher_configures_only_the_isolated_local_process(monkeypatch):
    from backend import exhibition_preview

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    exhibition_preview.configure_local_preview_environment()

    assert os.getenv("APP_ENV") == "local"
    assert os.getenv("ENABLE_EXHIBITION_LOCAL_PREVIEW") == "1"
    assert os.getenv("ALLOW_INSECURE_TEST_AUTH") == "1"


def _preview_app() -> ASGITestClient:
    app = FastAPI()
    app.include_router(exhibition_queue_v1.router, prefix="/api")
    app.include_router(exhibition_passport_v1.router, prefix="/api")
    app.dependency_overrides[require_ops] = lambda: AuthedUser(user_id="local-ops")
    return ASGITestClient(app)


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (216, 180, 106)).save(buf, format="JPEG")
    return buf.getvalue()


def test_preview_requires_local_environment(monkeypatch):
    monkeypatch.setenv("ENABLE_EXHIBITION_LOCAL_PREVIEW", "1")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert st.local_preview_enabled() is False

    monkeypatch.setenv("APP_ENV", "local")
    assert st.local_preview_enabled() is True

    monkeypatch.setenv("APP_ENV", "production")
    assert st.local_preview_enabled() is False
    try:
        st.default_stores()
    except RuntimeError as exc:
        assert "Supabase Exhibition store is disabled" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("a preview flag must fail closed even in production")


def test_preview_stores_are_in_memory_and_supabase_store_fails_closed(monkeypatch):
    monkeypatch.setenv("ENABLE_EXHIBITION_LOCAL_PREVIEW", "1")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setattr(st, "_LOCAL_PREVIEW_STORES", None)

    stores = exhibition_prep_v1.get_stores()
    assert isinstance(stores[0], st.InMemoryRunStore)
    assert isinstance(stores[1], st.InMemoryArtifactStore)

    try:
        st.default_stores()
    except RuntimeError as exc:
        assert "Supabase Exhibition store is disabled" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("local preview must never construct Supabase stores")

    try:
        exhibition_passport.default_store()
    except RuntimeError as exc:
        assert "Supabase Passport store is disabled" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("local preview must never construct the Passport Supabase store")


def test_preview_identity_linking_is_memory_only(monkeypatch):
    monkeypatch.setenv("ENABLE_EXHIBITION_LOCAL_PREVIEW", "1")
    monkeypatch.setenv("APP_ENV", "local")
    identity_service.__reset_for_tests()

    def fail_if_supabase_is_used():
        raise AssertionError("local preview attempted to use Supabase identity persistence")

    monkeypatch.setattr(identity_service, "_supabase", fail_if_supabase_is_used)
    resolved = anyio.run(
        lambda: identity_service.resolve_identity(
            subject="local-staff-subject",
            email="Staff@Example.com",
            email_verified=True,
        )
    )
    assert resolved.user_id == "staff@example.com"
    assert identity_service._MOCK_LINKS["local-staff-subject"]["eb_user_id"] == "staff@example.com"


def test_preview_keeps_unauthorized_accounts_blocked(monkeypatch):
    monkeypatch.setenv("ENABLE_EXHIBITION_LOCAL_PREVIEW", "1")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SHAKER_OPS_USER_IDS", "authorized@example.com")

    app = FastAPI()
    app.include_router(exhibition_queue_v1.router, prefix="/api")
    app.dependency_overrides[require_user] = lambda: AuthedUser(user_id="unauthorized@example.com")
    client = ASGITestClient(app)

    response = client.get("/api/exhibition/staff/queue?exhibition_id=local-preview")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "OPS_FORBIDDEN"


def test_staff_queue_and_show_next_use_memory_only(monkeypatch):
    monkeypatch.setenv("ENABLE_EXHIBITION_LOCAL_PREVIEW", "1")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setattr(st, "_LOCAL_PREVIEW_STORES", None)

    client = _preview_app()
    queue = client.get("/api/exhibition/staff/queue?exhibition_id=local-preview")
    assert queue.status_code == 200
    assert queue.json()["preparing"] == []

    empty = client.post(
        "/api/exhibition/staff/queue/show-next",
        json={"exhibition_id": "local-preview"},
    )
    assert empty.status_code == 409
    assert empty.json()["detail"]["code"] == "QUEUE_EMPTY"

    public = client.get("/api/exhibition/queue?exhibition_id=local-preview")
    assert public.status_code == 200
    assert public.json() == {"now_showing": None, "up_next": []}


def test_passport_number_cannot_fall_back_to_staff_allocator(monkeypatch):
    monkeypatch.setenv("ENABLE_EXHIBITION_LOCAL_PREVIEW", "1")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setattr(st, "_LOCAL_PREVIEW_STORES", None)

    client = _preview_app()
    response = client.post(
        "/api/exhibition/staff/submissions",
        files={"file": ("photo.jpg", b"not-an-image", "image/jpeg")},
        data={"exhibition_id": "local-preview", "queue_number": "7"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PASSPORT_NOT_FOUND"
    assert st.local_preview_stores()[0].rows == {}


def test_local_preview_completes_passport_registration_photo_association_and_retrieval(monkeypatch):
    monkeypatch.setenv("ENABLE_EXHIBITION_LOCAL_PREVIEW", "1")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SUPABASE_URL", "https://production.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "must-not-be-used")
    monkeypatch.setattr(st, "_LOCAL_PREVIEW_STORES", None)
    monkeypatch.setattr(st, "_LOCAL_PREVIEW_PASSPORT_STORE", None)

    def fail_if_supabase_is_used():
        raise AssertionError("local Passport preview attempted a Supabase client")

    monkeypatch.setattr("backend.services.supabase_assets.get_client", fail_if_supabase_is_used)
    client = _preview_app()
    token = "P" * 43

    registered = client.post(
        "/api/exhibition/passport/registrations",
        json={
            "exhibition_id": "local-preview",
            "pet_name": "Nabi",
            "email": "visitor@example.com",
            "privacy_consent": True,
            "idempotency_key": token,
        },
    )
    assert registered.status_code == 200, registered.text
    assert registered.json()["queue_number"] == 1
    assert "email" not in registered.text

    staff_submission = client.post(
        "/api/exhibition/staff/submissions",
        files={"file": ("nabi.jpg", _jpeg_bytes(), "image/jpeg")},
        data={"exhibition_id": "local-preview", "queue_number": "1"},
    )
    assert staff_submission.status_code == 200, staff_submission.text
    assert staff_submission.json()["queue_number"] == 1

    retrieved = client.post(
        "/api/exhibition/passport/retrieve",
        json={"passport_token": token},
    )
    assert retrieved.status_code == 200, retrieved.text
    body = retrieved.json()
    assert body["photo_status"] == "PHOTO_RECEIVED"
    assert body["photo_url"].startswith("data:image/png;base64,")
    assert "email" not in retrieved.text
    assert st.local_preview_stores()[0].rows


def test_main_mounts_passport_routes_only_for_verified_local_preview():
    code = """
import os
import sys
sys.modules['rembg'] = None
os.environ['ENABLE_EXHIBITION_LOCAL_PREVIEW'] = '1'
os.environ['APP_ENV'] = 'local'
import backend.main as main
paths = {route.path for route in main.app.routes}
print('/api/exhibition/passport/registrations' in paths)
"""
    env = os.environ.copy()
    env["ENABLE_EXHIBITION_LOCAL_PREVIEW"] = "1"
    env["APP_ENV"] = "local"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-1] == "True"


def test_main_mounts_only_queue_router_in_local_preview():
    code = """
import os
import sys
sys.modules['rembg'] = None
os.environ['ENABLE_EXHIBITION_LOCAL_PREVIEW'] = '1'
os.environ['APP_ENV'] = 'local'
os.environ.pop('ENABLE_EXHIBITION_PREP', None)
import backend.main as main
paths = {route.path for route in main.app.routes}
print('/api/exhibition/staff/queue' in paths)
print('/api/exhibition/prep-runs' in paths)
"""
    env = os.environ.copy()
    env["ENABLE_EXHIBITION_LOCAL_PREVIEW"] = "1"
    env["APP_ENV"] = "local"
    env.pop("ENABLE_EXHIBITION_PREP", None)
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-2:] == ["True", "False"]


def test_main_does_not_mount_preview_in_production_environment():
    code = """
import os
import sys
sys.modules['rembg'] = None
os.environ['ENABLE_EXHIBITION_LOCAL_PREVIEW'] = '1'
os.environ['APP_ENV'] = 'production'
os.environ['ENABLE_EXHIBITION_PREP'] = '1'
import backend.main as main
print(any(route.path == '/api/exhibition/staff/queue' for route in main.app.routes))
print(any(route.path == '/api/exhibition/passport/registrations' for route in main.app.routes))
"""
    env = os.environ.copy()
    env["ENABLE_EXHIBITION_LOCAL_PREVIEW"] = "1"
    env["APP_ENV"] = "production"
    env["ENABLE_EXHIBITION_PREP"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-2:] == ["False", "False"]
