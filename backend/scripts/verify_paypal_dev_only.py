#!/usr/bin/env python3
"""
PayPal 이 **개발 전용이었는지 확인한다.** 읽기만 하고, 옮기지 않는다.

    python -m backend.scripts.verify_paypal_dev_only
    python -m backend.scripts.verify_paypal_dev_only --json

── 이 스크립트가 하지 않는 것 ────────────────────────────────────────────────
**소유권을 이관하지 않는다.** 이관 코드가 여기 없다 — theme_entitlement.grant 를
import 조차 하지 않는다. purchased_slots 는 legacy/dev-only 로 분류됐고, 그 데이터는
크레딧·소유권 마이그레이션에서 **제외**된다(PAYPAL_LEGACY.md 참고).

경로가 없는 것이 가장 확실한 보장이다. 나중에 누군가 이관을 검토하더라도, 그건
새로 작성하는 결정이지 이 파일에 플래그를 하나 켜는 일이 되어서는 안 된다.

── 무엇을 확인하는가 ─────────────────────────────────────────────────────────
"개발 전용이었다"는 가정을 **반증할 증거**를 찾는다. 확인이 아니라 반증을 찾는
이유는, 확인만 하면 보고 싶은 것만 보게 되기 때문이다. 반증이 하나라도 나오면
크게 출력하고 exit code 1 로 끝난다.

  A. purchased_slots 에 행이 있는가 — 있다면 누구 것인가
  B. 그 행에 PayPal capture id 가 붙어 있는가 (= 실제 승인 흔적)
  C. user_theme_entitlements 에 provider='paypal' 행이 있는가
  D. 코드 배치상 실 결제가 가능했는가 (라우터 마운트 여부)

⚠️ 필요한 환경변수: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
   (없으면 D 만 확인하고 DB 확인은 UNKNOWN 으로 남긴다 — 없는 것을 없다고
    단정하지 않는다.)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

REPO = Path(_ROOT)


def _supabase():
    try:
        from backend.models.content import _supabase_client

        return _supabase_client()
    except Exception:
        return None


def _rows(sb, table: str) -> Optional[list[dict[str, Any]]]:
    """표를 읽는다. 못 읽으면 None — '비어 있음'과 구분한다."""
    try:
        r = sb.table(table).select("*").execute()
    except Exception:
        return None
    return list(getattr(r, "data", None) or [])


def check_router_never_mounted() -> dict[str, Any]:
    """
    D. 배포된 API 가 PayPal 결제를 **받을 수 있었는가.**

    main.py 가 paypal 라우터를 include 하지 않으면 /api/paypal/* 는 404 다.
    그러면 capture-order 가 호출될 수 없고, purchased_slots 에 쓰는 유일한 함수
    (supabase_assets.record_theme_purchase)는 도달 불가능하다.
    """
    main_py = (REPO / "backend" / "main.py").read_text(encoding="utf-8")
    mounted = bool(re.search(r"include_router\(\s*paypal", main_py))

    # purchased_slots 에 쓰는 곳이 정말 그 라우터 하나뿐인지도 함께 본다.
    writers: list[str] = []
    for path in (REPO / "backend").rglob("*.py"):
        if ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        if path.name in ("verify_paypal_dev_only.py",):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "record_theme_purchase(" in text and "async def record_theme_purchase" not in text:
            writers.append(str(path.relative_to(REPO)))

    return {
        "paypal_router_mounted_in_main": mounted,
        "purchased_slots_writers": writers,
        "reachable_in_deployment": mounted,
    }


def verify() -> dict[str, Any]:
    out: dict[str, Any] = {"contradictions": [], "checks": {}}

    # ── D. 코드 배치 ────────────────────────────────────────────────────────
    code = check_router_never_mounted()
    out["checks"]["code_reachability"] = code

    print("── D. 배포에서 PayPal 결제가 가능했는가 ────────────────────────────")
    print(f"  main.py 가 paypal 라우터를 마운트?   {code['paypal_router_mounted_in_main']}")
    print(f"  purchased_slots 에 쓰는 코드         {code['purchased_slots_writers'] or '없음'}")
    if code["reachable_in_deployment"]:
        out["contradictions"].append(
            "main.py 가 paypal 라우터를 마운트하고 있다 — 실 결제가 가능한 배치다."
        )
        print("  ❌ 라우터가 마운트돼 있다 — 실 결제가 가능했을 수 있다.")
    else:
        print("  ✔ 마운트되지 않음 → /api/paypal/* 는 404, capture 경로 도달 불가")
    print()

    sb = _supabase()
    if not sb:
        out["checks"]["database"] = {"status": "UNKNOWN", "reason": "Supabase 미설정"}
        print("── DB 확인 (A/B/C) ─────────────────────────────────────────────────")
        print("  ⚠️ UNKNOWN — SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 가 없다.")
        print("     DB 를 보지 못했으므로 '행이 없다'고 단정하지 않는다.")
        print("     프로덕션 자격증명으로 다시 실행할 것.")
        print()
        return out

    # ── A/B. purchased_slots ────────────────────────────────────────────────
    slots = _rows(sb, "purchased_slots")
    print("── A/B. purchased_slots ────────────────────────────────────────────")
    if slots is None:
        out["checks"]["purchased_slots"] = {"status": "ABSENT_OR_UNREADABLE"}
        print("  ✔ 표가 없거나 읽지 못했다 — 이관할 데이터가 존재하지 않는다.")
    else:
        # PayPal capture id 는 대문자·숫자 조합의 승인 식별자다. 그것이 붙어 있으면
        # 실제로 PayPal 승인이 일어났다는 뜻이므로 **가장 강한 반증**이다.
        with_capture = [s for s in slots if str(s.get("payment_id") or "").strip()]
        users = sorted({str(s.get("user_id") or "") for s in slots})
        out["checks"]["purchased_slots"] = {
            "status": "PRESENT",
            "rows": len(slots),
            "rows_with_capture_id": len(with_capture),
            "distinct_users": len(users),
            "users": users[:50],
        }
        print(f"  행 수                    {len(slots)}")
        print(f"  capture id 가 붙은 행    {len(with_capture)}")
        print(f"  사용자 수                {len(users)}")
        if len(slots) == 0:
            print("  ✔ 비어 있다 — 개발 전용 가정과 일치한다.")
        elif with_capture:
            out["contradictions"].append(
                f"purchased_slots 에 PayPal capture id 가 붙은 행이 {len(with_capture)} 건 있다 "
                "— 실제 PayPal 승인이 일어났다는 뜻이다."
            )
            print("  ❌ capture id 가 있는 행이 있다 — 실 승인 흔적일 수 있다.")
            for s in with_capture[:10]:
                print(f"       user={s.get('user_id')} theme={s.get('theme_id')} "
                      f"payment_id={s.get('payment_id')} at={s.get('purchased_at')}")
        else:
            print("  ⚠️ 행은 있지만 capture id 가 없다 — 수동 삽입/로컬 실행의 흔적으로 보인다.")
            print("     실 결제 증빙이 아니므로 이관 대상이 아니다.")
            for s in slots[:10]:
                print(f"       user={s.get('user_id')} theme={s.get('theme_id')} "
                      f"at={s.get('purchased_at')}")
    print()

    # ── C. 이미 이관된 흔적이 있는가 ────────────────────────────────────────
    ents = _rows(sb, "user_theme_entitlements")
    print("── C. user_theme_entitlements 에 PayPal 출처 행 ────────────────────")
    if ents is None:
        out["checks"]["paypal_entitlements"] = {"status": "UNREADABLE"}
        print("  ⚠️ 읽지 못했다.")
    else:
        paypal_ents = [
            e for e in ents
            if str(e.get("provider") or "").lower() == "paypal"
            or str(e.get("order_id") or "").startswith("paypal:")
        ]
        out["checks"]["paypal_entitlements"] = {
            "status": "PRESENT" if paypal_ents else "NONE",
            "rows": len(paypal_ents),
        }
        if paypal_ents:
            out["contradictions"].append(
                f"user_theme_entitlements 에 PayPal 출처 소유권이 {len(paypal_ents)} 건 있다 "
                "— 이미 이관됐거나 실 구매가 존재한다."
            )
            print(f"  ❌ {len(paypal_ents)} 건 — 예상과 다르다. 조사가 필요하다.")
            for e in paypal_ents[:10]:
                print(f"       user={e.get('user_id')} theme={e.get('theme_key')} "
                      f"order={e.get('order_id')}")
        else:
            print("  ✔ 없음 — 이관된 적이 없고, 이관할 계획도 없다.")
    print()

    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="PayPal 개발 전용 가정 검증 (읽기 전용 — 이관 기능 없음)"
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = verify()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    print("── 결론 ────────────────────────────────────────────────────────────")
    if report["contradictions"]:
        print("  ❌ 가정과 어긋나는 증거가 있다. **진행하기 전에 확인할 것.**")
        for c in report["contradictions"]:
            print(f"     · {c}")
        raise SystemExit(1)

    print("  ✔ '개발 전용' 가정을 반증하는 증거가 없다.")
    print("    purchased_slots 는 legacy/dev-only 로 분류되며, 크레딧·소유권")
    print("    마이그레이션에서 제외된다 (docs/PAYPAL_LEGACY.md).")
    print("    이 스크립트는 아무것도 바꾸지 않았고, 이관 기능도 갖고 있지 않다.")


if __name__ == "__main__":
    main()
