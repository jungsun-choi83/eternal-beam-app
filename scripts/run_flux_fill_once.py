"""
FLUX.1 [pro] Fill 단발 실행 — 테스트 전용 일회성 스크립트.

프로덕션 엔드포인트를 추가하지 않는다(요청대로). canvas/mask 를 Supabase 에 올려
공개 URL 을 만든 뒤 fal 큐에 **한 번만** 제출하고 결과를 내려받는다.

재시도 없음: 실패하면 그대로 예외로 끝난다(중복 과금 방지).

사용:
    python scripts/run_flux_fill_once.py \
        --canvas outputs/fill_test/canvas.png \
        --mask   outputs/fill_test/mask.png \
        --prompt-file <path> \
        --out    outputs/fill_test/flux_fill_raw.png
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import dotenv_values, load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
for k, v in dotenv_values(ROOT / ".env.local").items():
    if v and str(v).strip():
        os.environ[k] = str(v).strip().strip('"').strip("'")

import requests  # noqa: E402

from backend.services import supabase_assets  # noqa: E402

MODEL = "fal-ai/flux-pro/v1/fill"
QUEUE = "https://queue.fal.run"


def _headers() -> dict:
    key = (os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or "").strip()
    if not key:
        raise SystemExit("FAL_KEY 가 없습니다.")
    return {"Authorization": f"Key {key}", "Content-Type": "application/json", "Accept": "application/json"}


async def _upload(path: str, key: str) -> str:
    data = Path(path).read_bytes()
    return await supabase_assets.upload_asset_to_storage(key, data, "image/png")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--canvas", required=True)
    ap.add_argument("--mask", required=True)
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int)
    args = ap.parse_args()

    prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    cid = f"fill_{int(time.time())}"

    print("uploading canvas + mask to Supabase for public URLs…")
    image_url = asyncio.run(_upload(args.canvas, f"filltest/{cid}/canvas.png"))
    mask_url = asyncio.run(_upload(args.mask, f"filltest/{cid}/mask.png"))
    print(f"  image_url ok  ({len(image_url)} chars)")
    print(f"  mask_url  ok  ({len(mask_url)} chars)")

    payload = {
        "prompt": prompt,
        "image_url": image_url,
        "mask_url": mask_url,
        "num_images": 1,
        "output_format": "png",
        "safety_tolerance": "2",
    }
    if args.seed is not None:
        payload["seed"] = args.seed

    print(f"\nsubmitting ONE request to {MODEL} …")
    t0 = time.time()
    r = requests.post(f"{QUEUE}/{MODEL}", headers=_headers(), json=payload, timeout=60)
    if not r.ok:
        raise SystemExit(f"submit failed HTTP {r.status_code}: {(r.text or '')[:800]}")
    sub = r.json()
    req_id = sub.get("request_id", "")
    status_url = sub.get("status_url") or f"{QUEUE}/{MODEL}/requests/{req_id}/status"
    response_url = sub.get("response_url") or f"{QUEUE}/{MODEL}/requests/{req_id}"
    print(f"  request_id: {req_id}")

    # 폴링 — 재제출 없음
    while True:
        if time.time() - t0 > 600:
            raise SystemExit("timeout (no retry)")
        s = requests.get(status_url, headers=_headers(), timeout=30)
        s.raise_for_status()
        st = str(s.json().get("status") or "").upper()
        if st == "COMPLETED":
            break
        if st in ("FAILED", "ERROR", "CANCELLED"):
            raise SystemExit(f"generation failed: {s.text[:600]}")
        time.sleep(2)

    res = requests.get(response_url, headers=_headers(), timeout=60)
    res.raise_for_status()
    body = res.json()
    elapsed = time.time() - t0

    img = (body.get("images") or [{}])[0]
    url = img.get("url")
    if not url:
        raise SystemExit(f"no image url in response: {str(body)[:600]}")

    Path(args.out).write_bytes(requests.get(url, timeout=180).content)

    print(f"\n  elapsed        : {elapsed:.1f}s")
    print(f"  seed           : {body.get('seed')}")
    print(f"  output         : {img.get('width')}x{img.get('height')}  {img.get('content_type')}")
    print(f"  url            : {url}")
    print(f"  saved          : {args.out}")
    print(f"  est. cost      : $0.05 (0.79 MP -> billed as 1 MP)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
