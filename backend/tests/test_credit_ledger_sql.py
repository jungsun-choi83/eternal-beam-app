"""
크레딧 원장의 **SQL 계약** — 실제 Postgres 에서 검증한다.

왜 필요한가: test_credit_ledger.py 는 인메모리 경로만 확인한다. 목업과 SQL 이
갈라지면 그 차이는 **프로덕션에서만** 드러난다 — 이 저장소가 Phase 8 에서 이미
겪은 일이다(0크레딧 갱신이 Python 목업에서는 통과하고 SQL 에서는 P0001 로 실패).

여기서 고정하는 계약:
    지갑 변경과 원장 기록은 **함께** 일어난다 (하나만 남을 수 없다)
    같은 idempotency_key 는 두 번 적용되지 않는다
    실패한 차감은 지갑도 원장도 건드리지 않는다
    사유와 부호가 어긋나면 거절한다
    기존 지갑은 legacy_migration 개시 행을 받는다
    백필 후 credit_ledger_drift() 는 비어 있다

로컬 Postgres 가 없으면 skip 한다 — CI 에 DB 를 강제하지 않는다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO / "supabase" / "migrations"

#: 적용 순서 = 마이그레이션 순서. 순서를 바꾸면 실제 배포와 다른 것을 검증하게 된다.
STACK = [
    "20260930000000_authoritative_wallet_rpcs.sql",
    "20260930000100_payment_history.sql",
    "20261001000000_credit_ledger.sql",
    "20261001000100_credit_ledger_wire_rpcs.sql",
    # 이 파일은 적용 시점에 백필을 돌리고, 끝에서 drift 를 확인해 어긋나면
    # **마이그레이션 자체를 실패시킨다.** 여기서 함께 적용하므로, 이 픽스처가
    # 만들어졌다는 사실 자체가 그 자기 검증이 통과했다는 뜻이다.
    "20261001000200_credit_ledger_backfill.sql",
]

#: 원장 도입 **이전부터** 있던 지갑들. 백필 대상이다.
PRE_EXISTING = """
create table public.user_wallets (
  user_id text primary key, current_credits int not null default 0 check (current_credits >= 0),
  updated_at timestamptz default now());
create table public.user_subscriptions (
  user_id text primary key, plan_id text, status text, next_billing_date timestamptz,
  store_type text, original_transaction_id text, latest_transaction_id text,
  created_at timestamptz default now(), updated_at timestamptz default now());
create table public.subscription_webhook_events (
  id bigserial primary key, user_id text, plan_id text, store_type text,
  event_type text, event_fingerprint text unique, transaction_id text,
  credits_granted int, amount_krw int, raw_payload jsonb,
  created_at timestamptz default now());
insert into public.user_wallets (user_id, current_credits)
values ('legacy_17', 17), ('legacy_0', 0), ('legacy_3', 3);
"""


def _psql() -> str | None:
    for c in ("psql", "/opt/homebrew/opt/postgresql@16/bin/psql"):
        if shutil.which(c) or os.path.exists(c):
            return shutil.which(c) or c
    return None


PSQL = _psql()


def _run(db: str, sql: str, *, want_ok: bool = True) -> str:
    r = subprocess.run(
        [PSQL, "-d", db, "-qtA", "-c", sql], capture_output=True, text=True, timeout=60
    )
    out = (r.stdout + r.stderr).strip()
    if want_ok and r.returncode != 0:
        raise AssertionError(f"psql 실패:\n{sql}\n{out}")
    return out


@pytest.fixture(scope="module")
def db():
    if not PSQL:
        pytest.skip("psql 이 없다 — SQL 계약 테스트를 건너뛴다")
    try:
        subprocess.run(
            [PSQL, "-d", "postgres", "-qtA", "-c", "select 1"],
            capture_output=True, timeout=10, check=True,
        )
    except Exception:
        pytest.skip("로컬 Postgres 가 떠 있지 않다 — SQL 계약 테스트를 건너뛴다")

    name = f"eb_ledger_{uuid.uuid4().hex[:10]}"
    subprocess.run(
        [PSQL, "-d", "postgres", "-qc", f"create database {name}"],
        capture_output=True, check=True, timeout=30,
    )
    try:
        _run(name, PRE_EXISTING)
        for f in STACK:
            r = subprocess.run(
                [PSQL, "-d", name, "-q", "-v", "ON_ERROR_STOP=1", "-f", str(MIGRATIONS / f)],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode != 0:
                raise AssertionError(f"{f} 적용 실패:\n{r.stdout}{r.stderr}")
        yield name
    finally:
        subprocess.run(
            [PSQL, "-d", "postgres", "-qc", f"drop database {name}"],
            capture_output=True, timeout=30,
        )


def _apply(db, user, delta, reason, key, **kw) -> str:
    args = [f"'{user}'", str(delta), f"'{reason}'", f"'{key}'"]
    args.append(f"'{kw['product_key']}'" if kw.get("product_key") else "null")
    args.append(str(kw["unit_price"]) if kw.get("unit_price") is not None else "null")
    args.append(f"'{kw.get('state', 'COMMITTED')}'")
    return _run(db, f"select public.wallet_apply({','.join(args)});", want_ok=False)


def _balance(db, user) -> int | None:
    out = _run(db, f"select current_credits from public.user_wallets where user_id='{user}';")
    return int(out) if out else None


def _ledger_sum(db, user) -> int:
    out = _run(db, f"select coalesce(sum(delta),0) from public.credit_ledger where user_id='{user}';")
    return int(out or 0)


def _rows(db, user) -> int:
    return int(_run(db, f"select count(*) from public.credit_ledger where user_id='{user}';") or 0)


# ── 개시 잔액 백필 ───────────────────────────────────────────────────────────


def test_backfill_gives_existing_wallets_an_opening_row(db):
    """
    17 크레딧을 가진 사용자가 갑자기 '원장 합계 0' 이 되면 안 된다.
    """
    _run(db, "select public.credit_ledger_backfill_opening();")

    assert _ledger_sum(db, "legacy_17") == 17
    assert _balance(db, "legacy_17") == 17
    reason = _run(db, "select reason from public.credit_ledger where user_id='legacy_17';")
    assert reason == "legacy_migration"


def test_backfill_covers_zero_balance_wallets_too(db):
    """잔액 0 인 지갑도 원장에 자리를 갖는다 — 그래야 '아직 안 한 것'과 구분된다."""
    _run(db, "select public.credit_ledger_backfill_opening();")
    assert _rows(db, "legacy_0") == 1
    assert _ledger_sum(db, "legacy_0") == 0


def test_backfill_is_idempotent(db):
    _run(db, "select public.credit_ledger_backfill_opening();")
    before = _rows(db, "legacy_3")
    _run(db, "select public.credit_ledger_backfill_opening();")
    assert _rows(db, "legacy_3") == before == 1
    assert _balance(db, "legacy_3") == 3, "백필이 잔액을 바꿔서는 안 된다"


def test_no_drift_after_backfill(db):
    _run(db, "select public.credit_ledger_backfill_opening();")
    assert _run(db, "select count(*) from public.credit_ledger_drift();") == "0"


# ── 지갑 변경 + 원장 기록의 원자성 ──────────────────────────────────────────


def test_apply_moves_the_wallet_and_records_together(db):
    _apply(db, "u_atomic", 5, "credit_pack_topup", "at1", product_key="credit_pack_4", unit_price=4900)
    assert _balance(db, "u_atomic") == 5
    assert _ledger_sum(db, "u_atomic") == 5
    row = _run(
        db,
        "select reason||'|'||product_key||'|'||unit_price||'|'||balance_after "
        "from public.credit_ledger where idempotency_key='at1';",
    )
    assert row == "credit_pack_topup|credit_pack_4|4900|5"


def test_a_failed_deduction_leaves_no_trace(db):
    """
    **부분 적용이 남으면 그게 곧 불변식 위반이다.**
    잔액이 부족하면 지갑도 원장도 그대로여야 한다.
    """
    _apply(db, "u_fail", 2, "credit_pack_topup", "f1")
    out = _apply(db, "u_fail", -99, "idle_generation", "f2")

    assert "insufficient_credits" in out
    assert _balance(db, "u_fail") == 2
    assert _rows(db, "u_fail") == 1
    assert _ledger_sum(db, "u_fail") == 2


def test_replay_applies_nothing_and_reports_it(db):
    first = _apply(db, "u_dup", 5, "credit_pack_topup", "dup1")
    second = _apply(db, "u_dup", 5, "credit_pack_topup", "dup1")

    assert json.loads(first)["replayed"] is False
    assert json.loads(second)["replayed"] is True
    assert _balance(db, "u_dup") == 5, "같은 키로 두 번 충전됐다"
    assert _rows(db, "u_dup") == 1


# ── 사유·부호 ────────────────────────────────────────────────────────────────


def test_a_spend_reason_cannot_carry_a_positive_delta(db):
    out = _apply(db, "u_dir", 5, "theme_purchase", "dir1")
    assert "credit_ledger_direction_check" in out
    assert _rows(db, "u_dir") == 0


def test_an_unknown_reason_is_rejected(db):
    """
    오타 난 사유는 조용히 저장되면 안 된다. 저장되면 집계에서 사라지고,
    원장이 있는데도 설명하지 못하는 금액이 생긴다.

    (방향 제약이 먼저 걸리는지 사유 제약이 먼저 걸리는지는 Postgres 가 정한다 —
     둘 다 이 행을 거부하므로 어느 쪽이든 계약은 지켜진다.)
    """
    out = _apply(db, "u_typo", 5, "idle_generatoin", "typo1")
    assert "credit_ledger_" in out and "_check" in out
    assert _rows(db, "u_typo") == 0


def test_zero_delta_is_rejected(db):
    out = _apply(db, "u_zero", 0, "admin_adjustment", "z1")
    assert "invalid_amount" in out


def test_missing_idempotency_key_is_rejected(db):
    out = _run(
        db,
        "select public.wallet_apply('u_nokey', 5, 'credit_pack_topup', '');",
        want_ok=False,
    )
    assert "idempotency_key_required" in out


# ── 가입 보너스 ──────────────────────────────────────────────────────────────


def test_wallet_ensure_grants_the_starter_bonus_once(db):
    assert _run(db, "select public.wallet_ensure('u_starter', 4);") == "4"
    assert _run(db, "select public.wallet_ensure('u_starter', 4);") == "4"
    assert _rows(db, "u_starter") == 1
    assert _ledger_sum(db, "u_starter") == 4


def test_wallet_ensure_with_no_bonus_records_nothing(db):
    assert _run(db, "select public.wallet_ensure('u_nobonus', 0);") == "0"
    assert _rows(db, "u_nobonus") == 0
    assert _balance(db, "u_nobonus") == 0


# ── 기존 RPC 가 원장을 남기는가 ─────────────────────────────────────────────


def test_iap_charge_records_a_topup(db):
    _run(
        db,
        "select public.process_iap_charge('u_iap','credit_pack_4','mock','fp1','tx1',4900,4,null);",
    )
    row = _run(
        db,
        "select reason||'|'||delta||'|'||coalesce(ref_type,'-') "
        "from public.credit_ledger where user_id='u_iap';",
    )
    assert row == "credit_pack_topup|4|payment_history"
    assert _ledger_sum(db, "u_iap") == _balance(db, "u_iap") == 4


def test_membership_renewal_records_a_grant(db):
    _run(
        db,
        "select public.process_subscription_renewal("
        "'u_mem','standard_subscription','toss','RENEWAL','fpm1','txm1','txm1',"
        "12,9900,now()+interval '30 days');",
    )
    row = _run(
        db,
        "select reason||'|'||delta||'|'||coalesce(product_key,'-') "
        "from public.credit_ledger where user_id='u_mem';",
    )
    assert row == "membership_grant|12|standard_subscription"


def test_zero_credit_membership_records_nothing(db):
    """웹 멤버십(0 크레딧)은 지갑도 원장도 건드리지 않는다 — 예전 계약 그대로."""
    _run(
        db,
        "select public.process_subscription_renewal("
        "'u_web','web_membership','toss','RENEWAL','fpw1','txw1','txw1',"
        "0,9900,now()+interval '30 days');",
    )
    assert _rows(db, "u_web") == 0
    assert _balance(db, "u_web") is None, "0 크레딧 플랜은 지갑 행을 만들지 않는다"


def test_legacy_two_argument_calls_still_work(db):
    """
    기존 2-인자 호출이 그대로 동작한다 (새 인자는 전부 기본값).
    동작하지 않으면 배포 중 구버전 코드가 지갑을 못 건드린다.
    """
    assert _run(db, "select public.add_wallet_credits('u_legacy', 7);") == "7"
    assert _run(db, "select public.deduct_wallet_credits('u_legacy', 3);") == "4"
    # 사유를 안 넘겼어도 **기록은 남는다** — 누락보다 낫다.
    assert _rows(db, "u_legacy") == 2
    assert _ledger_sum(db, "u_legacy") == 4


# ── 최종 불변식 ──────────────────────────────────────────────────────────────


def test_no_drift_anywhere_after_every_test(db):
    """
    이 파일의 모든 조작이 끝난 뒤에도 어긋난 지갑이 없어야 한다.
    (모듈 스코프 DB 라 위 테스트들의 결과가 전부 누적돼 있다.)
    """
    _run(db, "select public.credit_ledger_backfill_opening();")
    drift = _run(db, "select coalesce(string_agg(user_id, ','), '') from public.credit_ledger_drift();")
    assert drift == "", f"원장과 지갑이 어긋난 사용자: {drift}"
