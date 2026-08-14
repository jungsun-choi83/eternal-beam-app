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


def build_pet_ready_base(body: dict[str, Any], content_id: str) -> dict[str, Any]:
    """
    /demo/pet-ready 본문 → SSE·UDP 로 실어 보낼 공통 필드 (순수 함수).

    화이트리스트 방식이라 모르는 키는 버린다. packed_url 은 **추가** 필드다 —
    idle_url/video_url 을 절대 덮어쓰지 않는다. packed 를 모르는 기존 S23 빌드는
    지금까지처럼 video_url 을 읽어 휘도 키 모드로 재생하고, packed 를 아는 빌드만
    packed_url 을 우선 선택한다(VideoLayer.IsPackedAlphaUrl).
    """
    base: dict[str, Any] = {
        "content_id": content_id,
        "source": "app_idle_ready",
    }
    idle_url = body.get("idle_url") or body.get("video_url")
    if idle_url:
        url = str(idle_url).strip()
        base["idle_url"] = url
        base["video_url"] = url
    cutout_url = body.get("cutout_url")
    if cutout_url:
        base["cutout_url"] = str(cutout_url).strip()
    # 공백만 있는 값도 "없음"으로 본다 — strip 후 판정.
    packed_url = str(body.get("packed_url") or "").strip()
    if packed_url:
        base["packed_url"] = packed_url
    return base


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
        if path == "/demo/pet-ready":
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                body = {}
            self._handle_pet_ready(body)
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
        self.wfile.write(body.encode("utf-8"))

    def _handle_pet_ready(self, body: dict[str, Any]) -> None:
        """idle 완료 → Unity(S23)만 — 배경(Pi)은 건드리지 않음."""
        content_id = str(body.get("content_id") or "").strip()
        if not content_id:
            self.send_error(400, explain="content_id required")
            return

        base = build_pet_ready_base(body, content_id)

        broadcast_event({**base, "event": "pet_ready"})
        if _udp_forward is not None:
            for event in ("nfc_match", "idle"):
                cmd = {**base, "event": event}
                try:
                    _udp_forward(cmd)
                except Exception as e:  # noqa: BLE001
                    print(f"[HTTP /demo/pet-ready] UDP {event} 실패: {e}", flush=True)

        body_out = json.dumps(
            {"ok": True, "event": "pet_ready", "content_id": content_id},
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
        f"[HTTP/SSE] 웹앱 대기 http://{host}:{port}/events  "
        f"POST /demo/play /demo/pet-ready /demo/forest /pet/wake-names",
        flush=True,
    )
    return _server
