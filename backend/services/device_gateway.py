"""Backend-owned WebSocket gateway for the new Eternal Beam hardware.

This module deliberately has no knowledge of the legacy Pi, UDP, Unity, or
device-renderer implementations.  It owns only command delivery and ACK
state.  Supabase is used for durable command/state records when configured;
the in-memory store keeps local development and unit tests deterministic.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

COMMANDS_TABLE = "device_commands"
STATE_TABLE = "device_connection_state"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


def configured_devices() -> dict[str, str]:
    """Read device_id -> secret credentials without logging the secrets.

    Preferred configuration is ETERNAL_BEAM_DEVICE_TOKENS='{"beam-001":"..."}'.
    The single-device pair is supported for simple deployments.
    """
    raw = (os.getenv("ETERNAL_BEAM_DEVICE_TOKENS") or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("ETERNAL_BEAM_DEVICE_TOKENS must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("ETERNAL_BEAM_DEVICE_TOKENS must be a JSON object")
        return {str(k).strip(): str(v) for k, v in parsed.items() if str(k).strip() and str(v)}

    device_id = (os.getenv("ETERNAL_BEAM_DEVICE_ID") or "").strip()
    secret = (os.getenv("ETERNAL_BEAM_DEVICE_TOKEN") or "").strip()
    return {device_id: secret} if device_id and secret else {}


def authenticate_device(device_id: str, token: str) -> bool:
    expected = configured_devices().get((device_id or "").strip())
    return bool(expected and hmac.compare_digest(expected, (token or "").strip()))


@dataclass
class DeviceState:
    device_id: str
    online: bool = False
    connected_at: str | None = None
    last_seen: str | None = None
    last_ack: str | None = None


_connections: dict[str, WebSocket] = {}
_states: dict[str, DeviceState] = {}
_pending: dict[str, OrderedDict[str, dict[str, Any]]] = {}
_acked: dict[str, set[str]] = {}
_lock = asyncio.Lock()


def __reset_for_tests() -> None:
    _connections.clear()
    _states.clear()
    _pending.clear()
    _acked.clear()


def _state(device_id: str) -> DeviceState:
    return _states.setdefault(device_id, DeviceState(device_id=device_id))


def _persist_state(state: DeviceState) -> None:
    if not _use_db():
        return
    client = _supabase()
    if not client:
        return
    client.table(STATE_TABLE).upsert(
        {
            "device_id": state.device_id,
            "online": state.online,
            "connected_at": state.connected_at,
            "last_seen": state.last_seen,
            "last_ack": state.last_ack,
            "updated_at": _now(),
        },
        on_conflict="device_id",
    ).execute()


def _persist_command(device_id: str, command: dict[str, Any]) -> None:
    if not _use_db():
        return
    client = _supabase()
    if not client:
        return
    client.table(COMMANDS_TABLE).insert(
        {
            "command_id": command["command_id"],
            "device_id": device_id,
            "event": command["event"],
            "payload": command,
            "status": "pending",
        }
    ).execute()


def _persist_ack(device_id: str, command_id: str) -> None:
    if not _use_db():
        return
    client = _supabase()
    if not client:
        return
    client.table(COMMANDS_TABLE).update(
        {"status": "acked", "acked_at": _now()}
    ).eq("device_id", device_id).eq("command_id", command_id).eq("status", "pending").execute()


def _load_pending(device_id: str) -> list[dict[str, Any]]:
    if not _use_db():
        return list(_pending.get(device_id, {}).values())
    client = _supabase()
    if not client:
        return list(_pending.get(device_id, {}).values())
    result = (
        client.table(COMMANDS_TABLE)
        .select("command_id,event,payload")
        .eq("device_id", device_id)
        .eq("status", "pending")
        .order("created_at")
        .execute()
    )
    rows = getattr(result, "data", None) or []
    return [row.get("payload") for row in rows if isinstance(row, dict) and isinstance(row.get("payload"), dict)]


async def register(device_id: str, websocket: WebSocket) -> list[dict[str, Any]]:
    async with _lock:
        previous = _connections.get(device_id)
        _connections[device_id] = websocket
        state = _state(device_id)
        now = _now()
        state.online = True
        state.connected_at = now
        state.last_seen = now
        _persist_state(state)
        pending = _load_pending(device_id)
        _pending.setdefault(device_id, OrderedDict()).update(
            (str(command["command_id"]), command) for command in pending if command.get("command_id")
        )
    if previous is not None and previous is not websocket:
        try:
            await previous.close(code=4001, reason="replaced by newer connection")
        except Exception:
            pass
    return list(_pending.get(device_id, {}).values())


async def unregister(device_id: str, websocket: WebSocket) -> None:
    async with _lock:
        if _connections.get(device_id) is not websocket:
            return
        _connections.pop(device_id, None)
        state = _state(device_id)
        state.online = False
        state.last_seen = _now()
        _persist_state(state)


async def touch(device_id: str) -> None:
    async with _lock:
        state = _state(device_id)
        state.last_seen = _now()
        _persist_state(state)


async def acknowledge(device_id: str, command_id: str) -> bool:
    cid = (command_id or "").strip()
    if not cid:
        return False
    async with _lock:
        known = cid in _pending.get(device_id, {}) or cid in _acked.get(device_id, set())
        _pending.setdefault(device_id, OrderedDict()).pop(cid, None)
        _acked.setdefault(device_id, set()).add(cid)
        state = _state(device_id)
        state.last_ack = cid
        state.last_seen = _now()
        _persist_ack(device_id, cid)
        _persist_state(state)
        return known


async def enqueue(device_id: str, command: dict[str, Any]) -> dict[str, Any]:
    command = dict(command)
    command.setdefault("command_id", str(uuid.uuid4()))
    command_id = str(command["command_id"])
    async with _lock:
        _pending.setdefault(device_id, OrderedDict())[command_id] = command
        _persist_command(device_id, command)
        websocket = _connections.get(device_id)
    if websocket is not None:
        try:
            await websocket.send_json(command)
        except Exception:
            await unregister(device_id, websocket)
    return command


async def pending(device_id: str) -> list[dict[str, Any]]:
    async with _lock:
        return _load_pending(device_id)


def state_snapshot(device_id: str) -> dict[str, Any]:
    state = _state(device_id)
    return {
        "device_id": state.device_id,
        "online": state.online,
        "connected_at": state.connected_at,
        "last_seen": state.last_seen,
        "last_ack": state.last_ack,
    }
