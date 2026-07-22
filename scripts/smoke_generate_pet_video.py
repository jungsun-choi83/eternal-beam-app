"""Smoke test: POST /api/generate-pet-video with local cutout PNG."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / "public" / "demo" / "goya-cutout.png"
API = "https://eternal-beam-video-api.onrender.com/api/generate-pet-video"


def multipart_body(image_bytes: bytes) -> tuple[bytes, str]:
    boundary = "----EternalBeamSmokeBoundary"
    chunks: list[bytes] = []

    def field(name: str, value: str) -> None:
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )

    chunks.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="cutout.png"\r\n'
        f"Content-Type: image/png\r\n\r\n".encode()
    )
    chunks.append(image_bytes)
    chunks.append(b"\r\n")
    field("user_id", "anonymous")
    field("skip_preprocessing", "true")
    field("idle_only", "true")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), boundary


def main() -> int:
    if not IMAGE.is_file():
        print(f"Missing test image: {IMAGE}", file=sys.stderr)
        return 1
    body, boundary = multipart_body(IMAGE.read_bytes())
    req = urllib.request.Request(API, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            payload = json.loads(resp.read().decode())
            print(json.dumps(payload, indent=2, ensure_ascii=False)[:2000])
            idle = payload.get("idle_video_url") or ""
            print("idle_video_url:", idle[:120] if idle else "(empty)")
            return 0 if idle else 2
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}", file=sys.stderr)
        print(e.read().decode()[:800], file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
