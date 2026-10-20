"""Safety checks for the opt-in, in-memory Exhibition Staff preview."""

from __future__ import annotations

import os
import subprocess
import sys

import anyio
from fastapi import FastAPI

from backend.auth import AuthedUser, require_user
from backend.routers import exhibition_prep_v1, exhibition_queue_v1
from backend.services import exhibition_prep_store as st
from backend.services import exhibition_passport
from backend.services import identity_service
from backend.services.shaker_ops import require_ops
from backend.tests.conftest import ASGITestClient


def _preview_app() -> ASGITestClient:
    app = FastAPI()
    app.include_router(exhibition_queue_v1.router, prefix="/api")
    app.dependency_overrides[require_ops] = lambda: AuthedUser(user_id="local-ops")
    return ASGITestClient(app)


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
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "EXHIBITION_SUBMISSION_FAILED"
    assert st.local_preview_stores()[0].rows == {}


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
    assert result.stdout.strip().splitlines()[-1] == "False"
