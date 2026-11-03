"""Backend-owned WebSocket gateway for the new Eternal Beam hardware.

This module deliberately has no knowledge of the legacy Pi, UDP, Unity, or
device-renderer implementations.  It owns only command delivery and ACK
state.  Supabase is used for durable command/state records when configured;
the in-memory store keeps local development and unit tests deterministic.

── cross-process delivery ────────────────────────────────────────────────────
_connections (the live WebSocket) only exists in whichever OS process accepted
that socket (the uvicorn/API process). A sibling process that calls enqueue()
— e.g. the Exhibition prep worker, or another uvicorn worker if the API is
ever run with multiple processes — has its own empty _connections and can
therefore only persist the device_commands row; it can never push to a
device it did not itself register. sync_pending() is how the process holding
the live socket catches up: called periodically from the WS loop, it re-reads
the durable pending rows (the one thing every process shares) and sends
anything this process hasn't already pushed down *this* socket. _delivered
tracks that per-device "already pushed, awaiting ACK" set so the periodic
check does not resend on every tick.
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
#: command_ids already pushed down the *current* live socket for a device —
#: lets sync_pending() poll the durable store without resending while a
#: command is still awaiting ACK. Reset wholesale on every register().
_delivered: dict[str, set[str]] = {}
_lock = asyncio.Lock()


def __reset_for_tests() -> None:
    _connections.clear()
    _states.clear()
    _pending.clear()
    _acked.clear()
    _delivered.clear()


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
        replay = list(_pending.get(device_id, {}).values())
        # Fresh socket, fresh delivery set — the caller is about to send every
        # item in `replay` immediately, so sync_pending() must not repeat it.
        _delivered[device_id] = {str(c["command_id"]) for c in replay if c.get("command_id")}
    if previous is not None and previous is not websocket:
        try:
            await previous.close(code=4001, reason="replaced by newer connection")
        except Exception:
            pass
    return replay


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
        _delivered.get(device_id, set()).discard(cid)
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
            async with _lock:
                _delivered.setdefault(device_id, set()).add(command_id)
        except Exception:
            await unregister(device_id, websocket)
    return command


async def pending(device_id: str) -> list[dict[str, Any]]:
    async with _lock:
        return _load_pending(device_id)


async def sync_pending(device_id: str) -> list[dict[str, Any]]:
    """Push any durably-pending commands this process hasn't sent yet.

    No-op if this process doesn't hold the live socket for device_id (the
    normal case for every process except the one that accepted the
    connection). Call this periodically from the WS loop while connected —
    it is what lets a command persisted by *another* process (the Exhibition
    worker, or a sibling API worker) reach an already-connected device
    without waiting for a reconnect.
    """
    async with _lock:
        websocket = _connections.get(device_id)
        if websocket is None:
            return []
        delivered = _delivered.setdefault(device_id, set())
        rows = _load_pending(device_id)
        to_send = [row for row in rows if row.get("command_id") and str(row["command_id"]) not in delivered]
        for row in to_send:
            cid = str(row["command_id"])
            _pending.setdefault(device_id, OrderedDict())[cid] = row
            delivered.add(cid)
    sent: list[dict[str, Any]] = []
    for row in to_send:
        try:
            await websocket.send_json(row)
            sent.append(row)
        except Exception:
            await unregister(device_id, websocket)
            break
    return sent


def state_snapshot(device_id: str) -> dict[str, Any]:
    state = _state(device_id)
    return {
        "device_id": state.device_id,
        "online": state.online,
        "connected_at": state.connected_at,
        "last_seen": state.last_seen,
        "last_ack": state.last_ack,
    }
