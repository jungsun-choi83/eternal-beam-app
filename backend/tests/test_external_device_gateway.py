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
