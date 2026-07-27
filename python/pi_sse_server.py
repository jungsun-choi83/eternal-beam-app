"""Pi 센서 이벤트 → 폰 웹앱 (SSE) + 포레스트 데모 트리거 (HTTP POST)."""

from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

_subscribers: list[queue.Queue[str]] = []
_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None
_udp_forward: Callable[[dict[str, Any]], None] | None = None


def register_udp_forward(fn: Callable[[dict[str, Any]], None]) -> None:
    global _udp_forward
    _udp_forward = fn


def broadcast_event(payload: dict[str, Any]) -> None:
    data = json.dumps(payload, separators=(",", ":"))
    with _lock:
        dead: list[queue.Queue[str]] = []
        for q in _subscribers:
            try:
                q.put_nowait(data)
            except Exception:
                dead.append(q)
        for q in dead:
            if q in _subscribers:
                _subscribers.remove(q)


class _SseHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        # HTTPS 웹앱 → 로컬 Pi (Private Network Access)
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/health":
            body = json.dumps({"ok": True, "service": "pi_sse"}, separators=(",", ":"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors()
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            return

        if path != "/events":
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._cors()
        self.end_headers()

        q: queue.Queue[str] = queue.Queue()
        with _lock:
            _subscribers.append(q)

        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                data = q.get()
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _lock:
                if q in _subscribers:
                    _subscribers.remove(q)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/demo/forest":
            self._handle_demo_play({"theme_id": "fresh_forest"})
            return
        if path == "/demo/play":
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                body = {}
            theme_id = str(body.get("theme_id") or "fresh_forest").strip()
            content_id = body.get("content_id")
            payload: dict[str, Any] = {"theme_id": theme_id}
            if content_id:
                payload["content_id"] = str(content_id)
            self._handle_demo_play(payload)
            return
        if path == "/pet/wake-names":
            self._handle_pet_wake_names()
            return
        self.send_error(404)

    def _handle_pet_wake_names(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            body = {}
        names_raw = body.get("names") or body.get("wake_names") or []
        names: list[str] = []
        if isinstance(names_raw, list):
            names = [str(n).strip() for n in names_raw if str(n).strip()]
        elif isinstance(names_raw, str):
            names = [p.strip() for p in names_raw.replace("，", ",").split(",") if p.strip()]
        pet_name = str(body.get("pet_name") or (names[0] if names else "")).strip()
        try:
            from pet_wake_store import save_wake_names

            saved = save_wake_names(names, pet_name=pet_name or None)
        except Exception as e:  # noqa: BLE001
            self.send_error(500, explain=str(e))
            return
        broadcast_event(
            {
                "event": "pet_wake_updated",
                "pet_name": pet_name,
                "wake_names": saved,
                "source": "app_signup",
            }
        )
        body_out = json.dumps(
            {"ok": True, "pet_name": pet_name, "wake_names": saved},
            separators=(",", ":"),
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.end_headers()
        self.wfile.write(body_out.encode("utf-8"))

    def _handle_demo_play(self, payload: dict[str, Any]) -> None:
        theme_id = str(payload.get("theme_id") or "fresh_forest")
        content_id = payload.get("content_id")
        event_payload: dict[str, Any] = {
            "event": "theme_play",
            "theme_id": theme_id,
            "source": "app_broadcast",
        }
        if content_id:
            event_payload["content_id"] = str(content_id)
        broadcast_event(event_payload)
        if _udp_forward is not None:
            udp_cmds: list[dict[str, Any]] = [
                {
                    "event": "nfc_match",
                    "source": "app_broadcast",
                    "theme_id": theme_id,
                },
                {"event": "theme_play", "theme_id": theme_id, "source": "app_broadcast"},
                {"event": "idle", "source": "app_broadcast", "theme_id": theme_id},
                {
                    "event": "nfc_tagged",
                    "theme_id": theme_id,
                    "source": "app_broadcast",
                },
            ]
            if content_id:
                for cmd in udp_cmds:
                    cmd["content_id"] = str(content_id)
            for cmd in udp_cmds:
                try:
                    _udp_forward(cmd)
                except Exception as e:  # noqa: BLE001
                    print(f"[HTTP /demo/play] UDP {cmd.get('event')} 실패: {e}", flush=True)

        body = json.dumps(
            {"ok": True, "event": "theme_play", "theme_id": theme_id},
            separators=(",", ":"),
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))


def start_sse_server(host: str = "0.0.0.0", port: int = 8787) -> ThreadingHTTPServer:
    global _server
    if _server is not None:
        return _server
    _server = ThreadingHTTPServer((host, port), _SseHandler)
    threading.Thread(target=_server.serve_forever, daemon=True).start()
    print(
        f"[HTTP/SSE] 웹앱 대기 http://{host}:{port}/events  POST /demo/play /demo/forest /pet/wake-names",
        flush=True,
    )
    return _server
