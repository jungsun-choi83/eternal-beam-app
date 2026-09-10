#!/usr/bin/env python3
"""
재무 레코드 감사 — **읽기 전용**. 아무것도 쓰지 않는다.

    python -m backend.scripts.audit_financial_records
    python -m backend.scripts.audit_financial_records --json

── 왜 필요한가 ───────────────────────────────────────────────────────────────
잔액·소유권을 옮기기 전에 **무엇이 실제 매출이고 무엇이 목업인지** 알아야 한다.
현재 배포는 다음 플래그로 돌고 있다(render.yaml):

    PAYMENT_MOCK=1        영수증 검증 없이 크레딧이 충전된다
    SUBSCRIPTION_MOCK=1   사용자가 자기 구독을 임의로 활성화할 수 있다
    GENERATION_MOCK=1     프로바이더에 실제로 제출하지 않는다
    LUMA_MOCK=1

그래서 지금 DB 에 있는 잔액·구매 원장·구독 이벤트 중 상당수는 실제 결제의
결과가 아닐 수 있다. 그것을 구분하지 않고 새 시스템으로 옮기면, 옮긴 뒤에는
영영 구분할 수 없다 — 목업 크레딧과 실구매 크레딧이 같은 정수 하나로 합쳐지기
때문이다.

이 스크립트는 판단을 내리지 않는다. **무엇이 있는지 보여 주기만** 한다.
옮길지 말지는 사람이 정한다.

⚠️ 필요한 환경변수: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _supabase():
    from backend.models.content import _supabase_client

    return _supabase_client()


def _rows(sb, table: str, select: str = "*") -> Optional[list[dict[str, Any]]]:
    """표를 통째로 읽는다. 표가 없으면 None (오류와 '비어 있음'을 구분한다)."""
    try:
        r = sb.table(table).select(select).execute()
    except Exception as e:
        print(f"  ⚠️  {table}: 읽지 못했다 ({type(e).__name__}: {e})")
        return None
    return list(getattr(r, "data", None) or [])


def _seed_users() -> set[str]:
    from backend.data.dummy_business_seed import DUMMY_WALLETS

    return {str(r.get("user_id") or "").strip() for r in DUMMY_WALLETS}


def _tally(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        v = str(r.get(key))
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def audit() -> dict[str, Any]:
    sb = _supabase()
    if not sb:
        raise SystemExit(
            "Supabase 가 설정되지 않았다. SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 를 넣고 다시 실행할 것."
        )

    seed = _seed_users()
    report: dict[str, Any] = {"mock_flags": {}, "tables": {}}

    # ── 지금 어떤 목업 플래그가 켜져 있는가 ──────────────────────────────────
    for flag in (
        "PAYMENT_MOCK", "SUBSCRIPTION_MOCK", "GENERATION_MOCK", "LUMA_MOCK",
        "TOSS_MOCK", "PREMIUM_REQUIRES_SUBSCRIPTION", "HYBRID_USE_SUPABASE",
        "ALLOW_INSECURE_TEST_AUTH", "PET_HYBRID_SEED", "STARTER_CREDITS",
    ):
        report["mock_flags"][flag] = os.getenv(flag)

    print("── 목업/안전 플래그 (이 프로세스 기준) ──────────────────────────────")
    for k, v in report["mock_flags"].items():
        print(f"  {k:32} = {v!r}")
    print()

    # ── 지갑 ────────────────────────────────────────────────────────────────
    wallets = _rows(sb, "user_wallets")
    if wallets is not None:
        total = sum(int(w.get("current_credits") or 0) for w in wallets)
        seeded = [w for w in wallets if str(w.get("user_id") or "").strip() in seed]
        nonzero = [w for w in wallets if int(w.get("current_credits") or 0) > 0]
        report["tables"]["user_wallets"] = {
            "rows": len(wallets),
            "total_credits": total,
            "nonzero_wallets": len(nonzero),
            "seed_user_wallets": len(seeded),
            "seed_user_credits": sum(int(w.get("current_credits") or 0) for w in seeded),
        }
        print("── user_wallets ────────────────────────────────────────────────────")
        print(f"  지갑 수                 {len(wallets)}")
        print(f"  잔액 합계               {total}")
        print(f"  잔액 > 0 인 지갑        {len(nonzero)}")
        print(f"  더미 시드 사용자 지갑   {len(seeded)}  (demo-user 등)")
        print()

    # ── 크레딧 원장 + 대조 ──────────────────────────────────────────────────
    # 이 절이 이 스크립트에서 가장 중요하다. 원장은 만들어 두고 아무도 대조하지
    # 않으면 그냥 로그다 — drift 가 0 인지 확인하는 것이 원장을 갖는 실질적 이유다.
    entries = _rows(sb, "credit_ledger")
    print("── credit_ledger ───────────────────────────────────────────────────")
    if entries is None:
        report["tables"]["credit_ledger"] = {"present": False}
        print("  ❌ 표가 없거나 읽지 못했다.")
        print("     → supabase/migrations/20261001000000_credit_ledger.sql 부터 적용할 것.")
    else:
        by_reason = _tally(entries, "reason")
        report["tables"]["credit_ledger"] = {
            "present": True,
            "rows": len(entries),
            "by_reason": by_reason,
            "by_state": _tally(entries, "state"),
        }
        print(f"  행 수                   {len(entries)}")
        print(f"  사유 별                 {by_reason}")
        print(f"  상태 별                 {_tally(entries, 'state')}")

        try:
            d = sb.rpc("credit_ledger_drift", {}).execute()
            drift = list(getattr(d, "data", None) or [])
        except Exception as e:
            drift = None
            print(f"  ⚠️ 대조 실패 ({type(e).__name__}) — credit_ledger_drift() 를 확인할 것")
        if drift is not None:
            report["tables"]["credit_ledger"]["drift_rows"] = len(drift)
            if drift:
                print(f"  ❌ 지갑과 원장이 어긋난 사용자 {len(drift)} 명 — 조사 필요")
                for row in drift[:10]:
                    print(f"       {row.get('user_id')}: 지갑 {row.get('wallet_balance')} "
                          f"vs 원장 {row.get('ledger_sum')} (차이 {row.get('difference')})")
            else:
                print("  ✔ 대조 통과 — 모든 잔액이 원장으로 설명된다")
    print()

    # ── IAP 결제 이력 ───────────────────────────────────────────────────────
    # 이 표가 **없으면** 재플레이 방어가 없다는 뜻이다 (20260930000100 참고).
    payments = _rows(sb, "payment_history")
    if payments is None:
        report["tables"]["payment_history"] = {"present": False}
        print("── payment_history ─────────────────────────────────────────────────")
        print("  ❌ 표가 없거나 읽지 못했다.")
        print("     → supabase/migrations/20260930000100_payment_history.sql 을 적용할 것.")
        print("     이 표가 없으면 같은 IAP 영수증으로 크레딧이 **반복 충전**된다.")
        print()
    else:
        by_store = _tally(payments, "store_type")
        mock_rows = [p for p in payments if str(p.get("store_type")) == "mock"]
        mock_credits = sum(int(p.get("credits_added") or 0) for p in mock_rows)
        real_credits = sum(
            int(p.get("credits_added") or 0)
            for p in payments
            if str(p.get("store_type")) in ("apple", "google")
            and str(p.get("status")) == "success"
        )
        report["tables"]["payment_history"] = {
            "present": True,
            "rows": len(payments),
            "by_store_type": by_store,
            "by_status": _tally(payments, "status"),
            "mock_credits_granted": mock_credits,
            "real_credits_granted": real_credits,
        }
        print("── payment_history ─────────────────────────────────────────────────")
        print(f"  행 수                   {len(payments)}")
        print(f"  store_type 별           {by_store}")
        print(f"  status 별               {_tally(payments, 'status')}")
        print(f"  ⚠️ 목업으로 충전된 크레딧 {mock_credits}")
        print(f"  실 스토어 충전 크레딧    {real_credits}")
        print()

    # ── 프리미엄 구매 원장 ──────────────────────────────────────────────────
    purchases = _rows(sb, "premium_purchases")
    if purchases is not None:
        active = [p for p in purchases if not p.get("refunded_at")]
        refunded = [p for p in purchases if p.get("refunded_at")]
        report["tables"]["premium_purchases"] = {
            "rows": len(purchases),
            "active": len(active),
            "refunded": len(refunded),
            "active_credits": sum(int(p.get("credits_charged") or 0) for p in active),
            "by_kind": _tally(purchases, "kind"),
        }
        print("── premium_purchases ───────────────────────────────────────────────")
        print(f"  행 수                   {len(purchases)}  (활성 {len(active)} / 환불 {len(refunded)})")
        print(f"  활성 구매 크레딧 합계   {sum(int(p.get('credits_charged') or 0) for p in active)}")
        print(f"  kind 별                 {_tally(purchases, 'kind')}")
        print()

    # ── 레거시 4코인 세션 ───────────────────────────────────────────────────
    sessions = _rows(sb, "credit_generation_sessions")
    if sessions is not None:
        # 환불 표시는 있는데 종료되지 않은 세션 = Phase 1 이 고친 결함의 흔적일 수 있다.
        refunded = [s for s in sessions if s.get("refunded_at")]
        report["tables"]["credit_generation_sessions"] = {
            "rows": len(sessions),
            "refunded": len(refunded),
            "by_status": _tally(sessions, "status"),
        }
        print("── credit_generation_sessions ──────────────────────────────────────")
        print(f"  행 수                   {len(sessions)}  (환불 표시 {len(refunded)})")
        print(f"  status 별               {_tally(sessions, 'status')}")
        print()

    # ── 구독 / 정기결제 ─────────────────────────────────────────────────────
    events = _rows(sb, "subscription_webhook_events")
    if events is not None:
        granted = sum(int(e.get("credits_granted") or 0) for e in events)
        report["tables"]["subscription_webhook_events"] = {
            "rows": len(events),
            "credits_granted": granted,
            "by_store_type": _tally(events, "store_type"),
        }
        print("── subscription_webhook_events ─────────────────────────────────────")
        print(f"  행 수                   {len(events)}")
        print(f"  구독으로 지급된 크레딧  {granted}")
        print(f"  store_type 별           {_tally(events, 'store_type')}")
        print()

    bill = _rows(sb, "billing_payments")
    if bill is not None:
        paid = [b for b in bill if str(b.get("status")) == "paid"]
        report["tables"]["billing_payments"] = {
            "rows": len(bill),
            "paid": len(paid),
            "paid_amount_krw": sum(int(b.get("amount") or 0) for b in paid),
            "by_kind": _tally(bill, "kind"),
        }
        print("── billing_payments (Toss 정기결제) ────────────────────────────────")
        print(f"  행 수                   {len(bill)}  (paid {len(paid)})")
        print(f"  결제 합계 (KRW)         {sum(int(b.get('amount') or 0) for b in paid)}")
        print()

    # ── 테마 소유권: 두 저장소 비교 ─────────────────────────────────────────
    ents = _rows(sb, "user_theme_entitlements")
    slots = _rows(sb, "purchased_slots")
    if ents is not None:
        report["tables"]["user_theme_entitlements"] = {
            "rows": len(ents),
            "by_provider": _tally(ents, "provider"),
            "by_status": _tally(ents, "status"),
        }
        print("── user_theme_entitlements (카탈로그가 읽는 유일한 곳) ─────────────")
        print(f"  행 수                   {len(ents)}")
        print(f"  provider 별             {_tally(ents, 'provider')}")
        print()
    if slots is not None:
        with_capture = [s for s in slots if str(s.get("payment_id") or "").strip()]
        report["tables"]["purchased_slots"] = {
            "classification": "LEGACY_DEV_ONLY",
            "excluded_from_migration": True,
            "rows": len(slots),
            "rows_with_capture_id": len(with_capture),
        }
        print("── purchased_slots (레거시 PayPal — LEGACY/DEV-ONLY) ───────────────")
        print(f"  행 수                   {len(slots)}")
        print(f"  capture id 가 있는 행   {len(with_capture)}")
        print("  분류: legacy/dev-only — 크레딧·소유권 마이그레이션에서 **제외**한다.")
        print("        (docs/PAYPAL_LEGACY.md — 라우터가 마운트된 적이 없어 실 결제 불가)")
        if with_capture:
            print("  ❌ capture id 가 붙은 행이 있다 — 실 PayPal 승인 흔적일 수 있다.")
            print("     분류를 재검토할 것: python -m backend.scripts.verify_paypal_dev_only")
        print()

    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="재무 레코드 감사 (읽기 전용)")
    ap.add_argument("--json", action="store_true", help="사람이 읽는 표 대신 JSON 만 출력")
    args = ap.parse_args()

    report = audit()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print("── 요약 ────────────────────────────────────────────────────────────")
        print("  이 스크립트는 아무것도 바꾸지 않았다.")
        print("  잔액을 옮기기 전에 위의 '목업으로 충전된 크레딧' 숫자를 먼저 판단할 것.")


if __name__ == "__main__":
    main()
