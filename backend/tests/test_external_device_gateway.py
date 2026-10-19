from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI

from backend.auth import AuthedUser, require_user
from backend.routers import device_ws_v1
from backend.services import device_gateway
from backend.services.motion_publication_service import PublishedBreathing
from .conftest import ASGITestClient


class FakeSocket:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, value):
        self.sent.append(value)

    async def close(self, **kwargs):
        self.closed = True


class _FakeTable:
    """Minimal Supabase query-builder double — just enough for device_gateway.py."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self._op: tuple[str, dict] | None = None
        self._on_conflict: str | None = None
        self._filters: dict = {}

    def insert(self, row):
        self._op = ("insert", dict(row))
        return self

    def update(self, fields):
        self._op = ("update", dict(fields))
        return self

    def upsert(self, row, on_conflict=None):
        self._op = ("upsert", dict(row))
        self._on_conflict = on_conflict
        return self

    def select(self, *_a):
        self._op = ("select", {})
        return self

    def eq(self, key, value):
        self._filters[key] = value
        return self

    def order(self, *_a, **_k):
        return self

    def execute(self):
        kind, payload = self._op
        matches = lambda row: all(row.get(k) == v for k, v in self._filters.items())  # noqa: E731
        if kind == "insert":
            self.rows.append(payload)
        elif kind == "update":
            for row in self.rows:
                if matches(row):
                    row.update(payload)
        elif kind == "upsert":
            keys = (self._on_conflict or "").split(",")
            for row in self.rows:
                if all(row.get(k) == payload.get(k) for k in keys):
                    row.update(payload)
                    break
            else:
                self.rows.append(payload)
        elif kind == "select":
            return type("Result", (), {"data": [dict(r) for r in self.rows if matches(r)]})()
        return type("Result", (), {"data": None})()


class _FakeDeviceSupabase:
    """Simulates the *shared* Supabase tables device_gateway.py persists to —
    used to prove cross-process delivery: a row inserted through one
    "process" (a bare _FakeTable call, standing in for the Exhibition worker)
    is visible to sync_pending() running against this same fake client
    (standing in for the API/WebSocket process)."""

    def __init__(self):
        self._tables: dict[str, list[dict]] = {}

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self._tables.setdefault(name, []))


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("ETERNAL_BEAM_DEVICE_TOKENS", '{"beam-test":"secret"}')
    device_gateway.__reset_for_tests()
    yield
    device_gateway.__reset_for_tests()


def test_device_auth_rejects_unknown_and_wrong_secret():
    assert not device_gateway.authenticate_device("unknown", "secret")
    assert not device_gateway.authenticate_device("beam-test", "wrong")
    assert device_gateway.authenticate_device("beam-test", "secret")


def test_offline_command_is_pending_and_reconnect_replays_once():
    async def run():
        command = await device_gateway.enqueue(
            "beam-test", {"event": "theme_play", "theme_id": "fresh_forest"}
        )
        assert (await device_gateway.pending("beam-test"))[0]["command_id"] == command["command_id"]

        socket = FakeSocket()
        replay = await device_gateway.register("beam-test", socket)
        assert [item["command_id"] for item in replay] == [command["command_id"]]
        await device_gateway.acknowledge("beam-test", command["command_id"])
        assert await device_gateway.pending("beam-test") == []

        await device_gateway.unregister("beam-test", socket)
        second = FakeSocket()
        assert await device_gateway.register("beam-test", second) == []

    asyncio.run(run())


def test_live_command_is_sent_and_duplicate_ack_is_safe():
    async def run():
        socket = FakeSocket()
        await device_gateway.register("beam-test", socket)
        command = await device_gateway.enqueue(
            "beam-test", {"event": "theme_play", "theme_id": "beach"}
        )
        assert socket.sent[-1]["event"] == "theme_play"
        assert await device_gateway.acknowledge("beam-test", command["command_id"])
        assert await device_gateway.acknowledge("beam-test", command["command_id"])
        assert await device_gateway.pending("beam-test") == []

    asyncio.run(run())


# ── cross-process delivery (sync_pending) ───────────────────────────────────
#
# device_gateway's _connections/_pending/_delivered are plain module-level
# dicts, private to whichever OS process holds them. enqueue() only pushes
# live when *this* process also holds the socket. A sibling process (the
# Exhibition prep worker, or a sibling API worker) can only persist the
# device_commands row — sync_pending() is what lets the process that *does*
# hold the socket notice that row and push it, without a reconnect.
#
# These tests use a fake Supabase client so "another process persisted a
# command" is simulated faithfully: inserting a row straight into the shared
# fake table, bypassing device_gateway.enqueue()/_connections entirely — the
# only thing the two processes actually share in production.


@pytest.fixture
def fake_db(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "1")
    client = _FakeDeviceSupabase()
    monkeypatch.setattr(device_gateway, "_supabase", lambda: client)
    return client


def test_sync_pending_is_a_noop_without_a_live_socket_in_this_process(fake_db):
    assert asyncio.run(device_gateway.sync_pending("beam-test")) == []


def test_sync_pending_delivers_a_command_persisted_by_another_process(fake_db):
    async def run():
        socket = FakeSocket()
        await device_gateway.register("beam-test", socket)
        assert socket.sent == []  # nothing pending yet

        # Simulate the Exhibition worker (a separate process): it only ever
        # inserts into device_commands directly — it never touches this
        # process's _connections, so it cannot call send_json itself.
        fake_db.table(device_gateway.COMMANDS_TABLE).insert(
            {
                "command_id": "cmd-from-worker",
                "device_id": "beam-test",
                "event": "pet_asset",
                "payload": {"command_id": "cmd-from-worker", "event": "pet_asset", "run_id": "r1"},
                "status": "pending",
            }
        ).execute()
        assert socket.sent == []  # not yet — only the periodic check notices it

        sent = await device_gateway.sync_pending("beam-test")
        assert [c["command_id"] for c in sent] == ["cmd-from-worker"]
        assert socket.sent[-1]["command_id"] == "cmd-from-worker"
        assert [c["command_id"] for c in await device_gateway.pending("beam-test")] == ["cmd-from-worker"]

        assert await device_gateway.acknowledge("beam-test", "cmd-from-worker")
        assert await device_gateway.pending("beam-test") == []

    asyncio.run(run())


def test_sync_pending_does_not_resend_while_awaiting_ack(fake_db):
    async def run():
        socket = FakeSocket()
        await device_gateway.register("beam-test", socket)
        fake_db.table(device_gateway.COMMANDS_TABLE).insert(
            {
                "command_id": "cmd-1",
                "device_id": "beam-test",
                "event": "pet_asset",
                "payload": {"command_id": "cmd-1", "event": "pet_asset", "run_id": "r1"},
                "status": "pending",
            }
        ).execute()

        await device_gateway.sync_pending("beam-test")
        await device_gateway.sync_pending("beam-test")
        await device_gateway.sync_pending("beam-test")
        assert len(socket.sent) == 1  # one send, not one per poll tick

        # A second, genuinely new command still gets through on the next tick.
        fake_db.table(device_gateway.COMMANDS_TABLE).insert(
            {
                "command_id": "cmd-2",
                "device_id": "beam-test",
                "event": "pet_asset",
                "payload": {"command_id": "cmd-2", "event": "pet_asset", "run_id": "r2"},
                "status": "pending",
            }
        ).execute()
        await device_gateway.sync_pending("beam-test")
        assert len(socket.sent) == 2
        assert socket.sent[-1]["command_id"] == "cmd-2"

    asyncio.run(run())


def test_sync_pending_skips_a_command_enqueue_already_pushed_live(fake_db):
    """When enqueue() itself delivers live (this process holds the socket —
    the normal, non-cross-process case), the next periodic sync_pending()
    tick must not re-send it."""

    async def run():
        socket = FakeSocket()
        await device_gateway.register("beam-test", socket)
        command = await device_gateway.enqueue("beam-test", {"event": "theme_play", "theme_id": "beach"})
        assert len(socket.sent) == 1

        await device_gateway.sync_pending("beam-test")
        assert len(socket.sent) == 1  # not resent

        assert await device_gateway.acknowledge("beam-test", command["command_id"])

    asyncio.run(run())


def _client(monkeypatch, published: PublishedBreathing | None = None):
    app = FastAPI()
    app.include_router(device_ws_v1.router, prefix="/api")
    app.dependency_overrides[require_user] = lambda: AuthedUser(user_id="user-1")
    if published is not None:
        async def fake_published(**kwargs):
            return published
        monkeypatch.setattr(
            device_ws_v1.motion_publication_service,
            "get_published_breathing",
            fake_published,
        )
    return ASGITestClient(app)


def _published_pet() -> PublishedBreathing:
    return PublishedBreathing(
        pet_id="pet-1",
        motion_id="BREATHING",
        breathing_bucket="user-assets",
        breathing_object_path="published/pet-1/breathing_packed.mp4",
        url="https://signed.example/published.mp4?token=fresh",
        background_baked=False,
        motion_version_id="version-1",
        delivery_format="packed_alpha",
        publication_id="publication-1",
        content_id="content-1",
    )


def test_theme_command_shape():
    response = _client(None).post(
        "/api/v1/device/commands",
        json={"device_id": "beam-test", "event": "theme_play", "theme_id": "fresh_forest"},
    )
    assert response.status_code == 200, response.text
    command = asyncio.run(device_gateway.pending("beam-test"))[0]
    assert command == {
        "command_id": command["command_id"],
        "event": "theme_play",
        "theme_id": "fresh_forest",
    }


def test_unknown_device_command_is_rejected():
    response = _client(None).post(
        "/api/v1/device/commands",
        json={"device_id": "not-provisioned", "event": "theme_play", "theme_id": "beach"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "DEVICE_UNKNOWN"


def test_pet_command_uses_published_signed_asset(monkeypatch):
    published = _published_pet()
    response = _client(monkeypatch, published).post(
        "/api/v1/device/commands",
        json={
            "device_id": "beam-test",
            "event": "pet_asset",
            "pet_id": "pet-1",
            "motion_id": "BREATHING",
        },
    )
    assert response.status_code == 200, response.text
    command = asyncio.run(device_gateway.pending("beam-test"))[0]
    assert command["event"] == "pet_asset"
    assert command["video_url"] == published.url
    assert command["packed_url"] == published.url
    assert command["delivery_format"] == "packed_alpha"
    assert "spawn_vfx" not in command


def test_pet_command_forwards_spawn_vfx_selector(monkeypatch):
    published = _published_pet()
    response = _client(monkeypatch, published).post(
        "/api/v1/device/commands",
        json={
            "device_id": "beam-test",
            "event": "pet_asset",
            "pet_id": "pet-1",
            "motion_id": "BREATHING",
            "spawn_vfx": "heart",
        },
    )
    assert response.status_code == 200, response.text
    command = asyncio.run(device_gateway.pending("beam-test"))[0]
    assert command == {
        "command_id": command["command_id"],
        "event": "pet_asset",
        "pet_id": "pet-1",
        "motion_id": "BREATHING",
        "spawn_vfx": "heart",
        "video_url": published.url,
        "delivery_format": "packed_alpha",
        "version": 1,
        "packed_url": published.url,
    }


def test_pet_command_replay_preserves_spawn_vfx_and_ack_flow(monkeypatch):
    response = _client(monkeypatch, _published_pet()).post(
        "/api/v1/device/commands",
        json={
            "device_id": "beam-test",
            "event": "pet_asset",
            "pet_id": "pet-1",
            "motion_id": "BREATHING",
            "spawn_vfx": "heart",
        },
    )
    assert response.status_code == 200, response.text
    command_id = response.json()["command_id"]

    async def run():
        socket = FakeSocket()
        replay = await device_gateway.register("beam-test", socket)
        assert len(replay) == 1
        assert replay[0]["command_id"] == command_id
        assert replay[0]["spawn_vfx"] == "heart"

        # ACK semantics stay idempotent and remove the complete queued command.
        assert await device_gateway.acknowledge("beam-test", command_id)
        assert await device_gateway.acknowledge("beam-test", command_id)
        assert await device_gateway.pending("beam-test") == []

    asyncio.run(run())


def test_unknown_but_well_formed_vfx_key_is_forwarded_for_device_resolution(monkeypatch):
    response = _client(monkeypatch, _published_pet()).post(
        "/api/v1/device/commands",
        json={
            "device_id": "beam-test",
            "event": "pet_asset",
            "pet_id": "pet-1",
            "motion_id": "BREATHING",
            "spawn_vfx": "future_sparkle-2",
        },
    )
    assert response.status_code == 200, response.text
    command = asyncio.run(device_gateway.pending("beam-test"))[0]
    assert command["spawn_vfx"] == "future_sparkle-2"


@pytest.mark.parametrize(
    "invalid_vfx",
    ["", "Heart", "heart burst", "../heart", "a" * 65, 123],
)
def test_invalid_vfx_key_is_rejected(monkeypatch, invalid_vfx):
    response = _client(monkeypatch, _published_pet()).post(
        "/api/v1/device/commands",
        json={
            "device_id": "beam-test",
            "event": "pet_asset",
            "pet_id": "pet-1",
            "motion_id": "BREATHING",
            "spawn_vfx": invalid_vfx,
        },
    )
    assert response.status_code == 422
    assert asyncio.run(device_gateway.pending("beam-test")) == []
