"""New external-device WebSocket contract.

This router is intentionally independent from the legacy device_v1 router and
all Pi/Unity/device-renderer code.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, field_validator

from ..auth import AuthedUser, require_user
from ..services import device_gateway
from ..services import motion_publication_service
from ..services import theme_catalog

router = APIRouter(prefix="/v1/device", tags=["external-device-v1"])


class DeviceCommandRequest(BaseModel):
    device_id: str = Field(min_length=1)
    event: str
    theme_id: str | None = None
    pet_id: str | None = None
    motion_id: str | None = None
    spawn_vfx: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]*$",
        strict=True,
    )

    @field_validator("event")
    @classmethod
    def valid_event(cls, value: str) -> str:
        event = value.strip()
        if event not in {"theme_play", "pet_asset"}:
            raise ValueError("event must be theme_play or pet_asset")
        return event


class DeviceCommandResponse(BaseModel):
    command_id: str
    event: str
    device_id: str
    delivery: str


def _command_error(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _require_device(device_id: str) -> None:
    if device_id not in device_gateway.configured_devices():
        raise _command_error("DEVICE_UNKNOWN", "Unknown device_id", 404)


@router.post("/commands", response_model=DeviceCommandResponse)
async def send_device_command(
    body: DeviceCommandRequest,
    user: AuthedUser = Depends(require_user),
):
    """Queue a background-theme or published-pet command for a device."""
    _require_device(body.device_id)

    if body.event == "theme_play":
        try:
            theme_id = theme_catalog.normalize_theme_key(body.theme_id)
        except theme_catalog.ThemeCatalogError as exc:
            raise _command_error(exc.code, exc.message, exc.status) from exc
        command: dict[str, Any] = {
            "event": "theme_play",
            "theme_id": theme_id,
        }
    else:
        pet_id = (body.pet_id or "").strip()
        motion_id = (body.motion_id or "").strip().upper()
        if not pet_id or motion_id != "BREATHING":
            raise _command_error(
                "PUBLISHED_ASSET_REQUIRED",
                "The first device contract supports published BREATHING only.",
                409,
            )
        try:
            published = await motion_publication_service.get_published_breathing(
                user_id=user.user_id,
                pet_id=pet_id,
            )
        except motion_publication_service.MotionPublicationError as exc:
            raise _command_error(exc.code, exc.message, exc.status) from exc

        # The URL is resolved from the published pointer at send time.  No raw
        # candidate URL is accepted from the caller.
        command = {
            "event": "pet_asset",
            "pet_id": published.pet_id,
            "motion_id": published.motion_id,
            "video_url": published.url,
            "delivery_format": published.delivery_format or "raw",
            "version": 1,
        }
        if body.spawn_vfx is not None:
            # Selector only: the device owns and resolves the local VFX asset.
            command["spawn_vfx"] = body.spawn_vfx
        if published.delivery_format == "packed_alpha":
            command["packed_url"] = published.url

    queued = await device_gateway.enqueue(body.device_id, command)
    return DeviceCommandResponse(
        command_id=queued["command_id"],
        event=queued["event"],
        device_id=body.device_id,
        delivery="sent" if device_gateway.state_snapshot(body.device_id)["online"] else "pending",
    )


@router.get("/devices/{device_id}")
async def get_device_state(device_id: str, user: AuthedUser = Depends(require_user)):
    del user
    _require_device(device_id)
    return device_gateway.state_snapshot(device_id)


@router.websocket("/ws")
async def device_websocket(
    websocket: WebSocket,
    device_id: str = Query(...),
    token: str = Query(...),
):
    """External client contract: ws(s)://host/api/v1/device/ws?device_id=...&token=..."""
    if not device_gateway.authenticate_device(device_id, token):
        await websocket.close(code=4401, reason="invalid device credentials")
        return

    await websocket.accept()
    pending_commands = await device_gateway.register(device_id, websocket)
    try:
        await websocket.send_json({"event": "connected", "device_id": device_id})
        for command in pending_commands:
            await websocket.send_json(command)

        heartbeat_seconds = max(5, int(__import__("os").getenv("DEVICE_WS_HEARTBEAT_SECONDS", "25")))
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=heartbeat_seconds)
            except asyncio.TimeoutError:
                await websocket.send_json({"event": "heartbeat", "ts": time.time()})
                continue

            if not isinstance(message, dict):
                continue
            await device_gateway.touch(device_id)
            event = str(message.get("event") or "").strip().lower()
            if event == "ack":
                command_id = str(message.get("command_id") or "")
                known = await device_gateway.acknowledge(device_id, command_id)
                await websocket.send_json(
                    {"event": "ack", "command_id": command_id, "status": "ok" if known else "unknown"}
                )
            elif event in {"heartbeat", "pong"}:
                await websocket.send_json({"event": "heartbeat_ack", "ts": time.time()})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await device_gateway.unregister(device_id, websocket)
