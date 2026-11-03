from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services import archive_storage
from backend.services import asset_url_refresh
from backend.services import durable_provider_jobs
from backend.services import pet_generation_run_service
from backend.services import supabase_assets


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeBucket:
    def __init__(self, name: str):
        self.name = name
        self.uploads: list[tuple[str, bytes, dict]] = []
        self.public_url_calls = 0
        self.signed_url_calls = 0
        self.signed_response: object = {"data": {"signedUrl": f"https://signed/{name}"}}

    def upload(self, path: str, data: bytes, options: dict):
        self.uploads.append((path, bytes(data), dict(options)))

    def create_signed_url(self, path: str, ttl: int):
        self.signed_url_calls += 1
        return self.signed_response

    def get_public_url(self, path: str):
        self.public_url_calls += 1
        return f"https://public/{self.name}/{path}"


class _FakeStorage:
    def __init__(self):
        self.buckets: dict[str, _FakeBucket] = {}
        self.private_buckets = {"user-assets"}

    def from_(self, name: str) -> _FakeBucket:
        return self.buckets.setdefault(name, _FakeBucket(name))

    def get_bucket(self, name: str):
        return SimpleNamespace(data={"public": name not in self.private_buckets})


class _FakeSupabase:
    def __init__(self):
        self.storage = _FakeStorage()


@pytest.fixture
def fake_supabase(monkeypatch: pytest.MonkeyPatch) -> _FakeSupabase:
    client = _FakeSupabase()
    monkeypatch.setattr(supabase_assets, "_client", lambda: client)
    return client


def test_archive_scope_is_private_and_does_not_change_global_default(
    monkeypatch: pytest.MonkeyPatch,
):
    assert supabase_assets.BUCKET == "user-assets"
    assert supabase_assets.active_bucket() == "user-assets"

    with archive_storage.archive_scope():
        assert supabase_assets.active_bucket() == "user-assets"
        assert supabase_assets.active_requires_private() is True
        assert asset_url_refresh.default_bucket() == "user-assets"

    assert supabase_assets.active_bucket() == "user-assets"
    assert supabase_assets.active_requires_private() is False


def test_archive_bucket_is_fixed_to_user_assets():
    assert archive_storage.archive_storage_bucket() == "user-assets"


@pytest.mark.anyio
async def test_private_archive_upload_uses_bucket_and_signed_url_only(
    fake_supabase: _FakeSupabase,
):
    url = await supabase_assets.upload_asset_to_storage(
        "archive-app/input.png",
        b"png",
        "image/png",
        bucket="user-assets",
        require_private=True,
    )

    stored = fake_supabase.storage.buckets["user-assets"]
    assert url == "https://signed/user-assets"
    assert len(stored.uploads) == 1
    assert stored.signed_url_calls == 1
    assert stored.public_url_calls == 0


@pytest.mark.anyio
async def test_private_upload_fails_closed_when_signed_url_is_unavailable(
    fake_supabase: _FakeSupabase,
):
    stored = fake_supabase.storage.from_("user-assets")
    stored.signed_response = {"data": {}}

    with pytest.raises(RuntimeError, match="signed URL"):
        await supabase_assets.upload_asset_to_storage(
            "archive-app/input.png",
            b"png",
            "image/png",
            bucket="user-assets",
            require_private=True,
        )
    assert stored.public_url_calls == 0


def test_run_scope_is_durable_and_legacy_runs_are_unchanged():
    archive_run = SimpleNamespace(
        user_id="archive_legacy",
        provider_state={"_archive_storage": {"bucket": "user-assets", "private": True}},
    )
    with archive_storage.scope_for_run(archive_run):
        assert supabase_assets.active_bucket() == "user-assets"
        assert supabase_assets.active_requires_private() is True

    old_phase_5c2_run = SimpleNamespace(
        user_id="archive_old",
        provider_state={"_archive_storage": {"bucket": "user-media", "private": True}},
    )
    with archive_storage.scope_for_run(old_phase_5c2_run):
        assert supabase_assets.active_bucket() == "user-media"
        assert supabase_assets.active_requires_private() is True

    legacy_run = SimpleNamespace(user_id="archive_legacy", provider_state={})
    with archive_storage.scope_for_run(legacy_run):
        assert supabase_assets.active_bucket() == "user-assets"
        assert supabase_assets.active_requires_private() is True


def test_provider_receipt_refresh_preserves_archive_scope(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(durable_provider_jobs, "summary_for_run", lambda run_id: {})
    run = SimpleNamespace(
        id="run-archive",
        provider_state={
            "_archive_storage": {"bucket": "user-assets", "private": True},
            "_business_qa_cutover": {"enrolled": False},
        },
    )
    state = pet_generation_run_service._provider_state(run)
    assert state["_archive_storage"] == {"bucket": "user-assets", "private": True}


@pytest.mark.anyio
async def test_archive_rejects_public_user_assets(fake_supabase: _FakeSupabase):
    fake_supabase.storage.private_buckets.clear()
    with pytest.raises(RuntimeError, match="not verified private"):
        await supabase_assets.upload_asset_to_storage(
            "archive-app/input.png",
            b"png",
            "image/png",
            bucket="user-assets",
            require_private=True,
        )

