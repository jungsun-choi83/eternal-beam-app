"""Temporary local test client — simulates one hardware device over the existing
Device Gateway WebSocket contract (backend/routers/device_ws_v1.py).

Not the real Pi/Unity device and not wired into any app startup path — manual
local testing only, e.g. for an Exhibition device_gateway handoff test against
beam-001. Delete once you're done, or keep it around for the next manual test.

Usage:
    DEVICE_ID=beam-001 BACKEND_HOST=127.0.0.1:8001 DEVICE_SECRET=<token from
    ETERNAL_BEAM_DEVICE_TOKENS> backend/.venv/bin/python -m backend.scripts.fake_beam_device_ws_client

Prints every message received and auto-ACKs anything with a command_id
(theme_play, pet_asset, ...) immediately — good enough to drive
device_commands.status from pending to acked for a manual check.
"""

from __future__ import annotations

import asyncio
import json
import os

import websockets


async def main() -> None:
    device_id = os.environ["DEVICE_ID"]
    host = os.environ.get("BACKEND_HOST", "127.0.0.1:8001")
    secret = os.environ["DEVICE_SECRET"]
    scheme = "wss" if os.environ.get("DEVICE_WS_TLS", "0").strip() == "1" else "ws"
    url = f"{scheme}://{host}/api/v1/device/ws?device_id={device_id}&token={secret}"
    print(f"connecting to {url}")
    async with websockets.connect(url) as ws:
        async for raw in ws:
            message = json.loads(raw)
            print("recv:", message)
            event = message.get("event")
            if event in ("connected", "heartbeat"):
                continue
            command_id = message.get("command_id")
            if command_id:
                ack = {"event": "ack", "command_id": command_id}
                await ws.send(json.dumps(ack))
                print("sent:", ack)


if __name__ == "__main__":
    asyncio.run(main())
