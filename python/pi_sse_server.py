"""Pi 센서 이벤트 → 폰 웹앱 (SSE / Server-Sent Events)."""

from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_subscribers: list[queue.Queue[str]] = []
_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None


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
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != "/events":
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


def start_sse_server(host: str = "0.0.0.0", port: int = 8787) -> ThreadingHTTPServer:
    global _server
    if _server is not None:
        return _server
    _server = ThreadingHTTPServer((host, port), _SseHandler)
    threading.Thread(target=_server.serve_forever, daemon=True).start()
    print(f"[SSE] 폰 웹앱 연결 대기 http://{host}:{port}/events", flush=True)
    return _server
