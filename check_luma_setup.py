# -*- coding: utf-8 -*-
"""
Four checks before Luma I2V:
  1) LUMA_API_KEY loaded and looks like a Luma key
  2) Supabase env vars for upload
  3) Luma API accepts the key (GET /generations?limit=1)
  4) Optional: image URL is reachable (pass URL as first arg)

  python check_luma_setup.py
  python check_luma_setup.py "https://....supabase.co/.../file.png"
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

try:
    from dotenv import load_dotenv

    for _p in (
        ROOT / "env.local",
        ROOT / ".env.local",
        ROOT / ".env",
        ROOT / "backend" / "env.local",
        ROOT / "backend" / ".env.local",
        ROOT / "backend" / ".env",
    ):
        if _p.is_file():
            try:
                load_dotenv(_p, override=True, encoding="utf-8-sig")
            except TypeError:
                load_dotenv(_p, override=True)
except ImportError:
    pass


def main() -> int:
    ok = True

    # --- 1) LUMA_API_KEY ---
    key = (os.getenv("LUMA_API_KEY") or "").strip()
    if not key:
        print("[1] FAIL: LUMA_API_KEY is empty (check backend/.env.local)")
        ok = False
    elif not key.startswith("luma-"):
        print("[1] WARN: key should usually start with luma-")
        print("    ", key[:20] + "...")
    else:
        print("[1] OK: LUMA_API_KEY is set (starts with luma-)")

    # --- 2) Supabase ---
    url = (os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL") or "").strip()
    skey = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not skey:
        print("[2] FAIL: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required for upload")
        ok = False
    else:
        print("[2] OK: Supabase URL and service role key are set")

    # --- 3) Luma API auth ---
    if key:
        try:
            import requests

            r = requests.get(
                "https://api.lumalabs.ai/dream-machine/v1/generations",
                params={"limit": 1},
                headers={
                    "Authorization": f"Bearer {key}",
                    "Accept": "application/json",
                },
                timeout=20,
            )
            if r.status_code == 200:
                print("[3] OK: Luma API accepts this key (GET /generations)")
            else:
                print(f"[3] FAIL: Luma returned HTTP {r.status_code}: {(r.text or '')[:200]}")
                ok = False
        except Exception as e:
            print(f"[3] FAIL: {e}")
            ok = False
    else:
        print("[3] SKIP: no key")

    # --- 4) Image URL reachable (optional) ---
    if len(sys.argv) >= 2:
        img_url = sys.argv[1].strip()
        if not img_url.startswith("http"):
            print(
                "[4] FAIL: paste the real URL from script output (dog_only_nobg_url), "
                "starting with https:// — not the example text."
            )
            ok = False
        else:
            try:
                import requests

                g = requests.get(img_url, timeout=15, stream=True)
                ct = g.headers.get("content-type", "")
                if g.status_code == 200 and "image" in ct.lower():
                    print(f"[4] OK: URL returns 200, content-type={ct[:60]}")
                elif g.status_code == 200:
                    print(f"[4] WARN: 200 but content-type={ct!r} (expect image/*)")
                else:
                    print(f"[4] FAIL: HTTP {g.status_code} — Luma cannot fetch this URL")
                    ok = False
                g.close()
            except Exception as e:
                print(f"[4] FAIL: cannot fetch URL: {e}")
                ok = False
    else:
        print("[4] SKIP: pass an image URL to test, e.g.:")
        print('    python check_luma_setup.py "https://xxx.supabase.co/.../file.png"')

    print()
    if ok:
        print("Summary: basic checks passed. If Luma still says moderation failed, try public bucket + another image.")
    else:
        print("Summary: fix FAIL items first.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
