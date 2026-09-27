"""
갱신 RPC 의 **SQL 계약** — 실제 Postgres 에서 검증한다.

왜 이 파일이 필요한가: 다른 모든 구독 테스트는 HYBRID_USE_SUPABASE=0 으로 돌아
Python 목업 경로(process_renewal_mock)만 탄다. 그래서 Phase 8 에서 0 크레딧 플랜을
목업 쪽만 고치고 **SQL RPC 는 고치지 않은 채** 전부 초록이었다. 실제 배포에서는
process_subscription_renewal 이 P0001 invalid_amount 로 실패했다.

여기서 고정하는 계약:
    p_credits > 0  → 지갑에 충전 (레거시 12크레딧 플랜 불변)
    p_credits = 0  → 지갑을 **건드리지 않는다** (웹 멤버십). 행도 만들지 않는다.
    p_credits < 0  → invalid_amount (음수는 언제나 버그)
    p_credits null → invalid_amount
    중복 지문      → duplicate_subscription_event

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
DOCS = REPO / "docs"

#: add_wallet_credits 의 **유일한 권위 정의**.
#:
#: 예전에는 docs/supabase_payment_iap.sql 을 읽었다. 그 파일이 배포된 정의였기
#: 때문인데, 그건 "마이그레이션과 docs 에 서로 다른 정의가 있고 나중에 붙여넣은
#: 쪽이 이긴다"는 상태를 테스트가 그대로 흉내 내고 있었다는 뜻이다.
#:
#: Phase 1 에서 정의를 마이그레이션으로 승격했으므로 여기도 그쪽을 읽는다.
#: 이제 이 테스트는 **실제로 배포될 SQL** 을 검증한다.
STRICT_WALLET_FN = MIGRATIONS / "20260930000000_authoritative_wallet_rpcs.sql"
RENEWAL_FIX = MIGRATIONS / "20260819000400_renewal_allows_zero_credits.sql"

SCHEMA = """
create table public.user_wallets (
  user_id text primary key, current_credits int not null default 0,
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
"""


def _psql() -> str | None:
    for c in ("psql", "/opt/homebrew/opt/postgresql@16/bin/psql"):
        if shutil.which(c) or os.path.exists(c):
            return shutil.which(c) or c
    return None


PSQL = _psql()


def _run(db: str, sql: str, *, want_ok: bool = True) -> str:
    r = subprocess.run(
        [PSQL, "-d", db, "-qtA", "-c", sql],
        capture_output=True, text=True, timeout=60,
    )
    out = (r.stdout + r.stderr).strip()
    if want_ok and r.returncode != 0:
        raise AssertionError(f"psql 실패:\n{sql}\n{out}")
    return out


def _extract_fn(path: Path, name: str) -> str:
    """파일에서 `create or replace function <name> ... $$;` 블록만 뽑는다."""
    text = path.read_text()
    start = text.index(f"create or replace function public.{name}")
    end = text.index("$$;", start) + len("$$;")
    return text[start:end]


@pytest.fixture(scope="module")
def db():
    if not PSQL:
        pytest.skip("psql 이 없다 — SQL 계약 테스트를 건너뛴다")
    try:
        subprocess.run([PSQL, "-d", "postgres", "-qtA", "-c", "select 1"],
                       capture_output=True, timeout=10, check=True)
    except Exception:
        pytest.skip("로컬 Postgres 가 떠 있지 않다 — SQL 계약 테스트를 건너뛴다")

    name = f"eb_rpc_{uuid.uuid4().hex[:10]}"
    subprocess.run([PSQL, "-d", "postgres", "-qc", f"create database {name}"],
                   capture_output=True, check=True, timeout=30)
    try:
        _run(name, SCHEMA)
        _run(name, _extract_fn(STRICT_WALLET_FN, "add_wallet_credits"))
        _run(name, RENEWAL_FIX.read_text())
        yield name
    finally:
        subprocess.run([PSQL, "-d", "postgres", "-qc", f"drop database {name}"],
                       capture_output=True, timeout=30)


def _renew(db, user, plan, credits, fp, store="toss") -> str:
    return _run(
        db,
        "select public.process_subscription_renewal("
        f"'{user}','{plan}','{store}','RENEWAL','{fp}','tx_{fp}','tx_{fp}',"
        f"{'null' if credits is None else credits},9900,now()+interval '30 days');",
        want_ok=False,
    )


def _balance(db, user) -> int | None:
    out = _run(db, f"select current_credits from public.user_wallets where user_id='{user}';")
    return int(out) if out else None


# ── 0 크레딧 (웹 멤버십) ─────────────────────────────────────────────────────


def test_zero_credit_renewal_succeeds(db):
    """**이 버그의 재발 방지선.** 예전에는 P0001 invalid_amount 로 실패했다."""
    out = _renew(db, "toss@e.com", "web_membership", 0, "z1")
    assert "invalid_amount" not in out, "0 크레딧 갱신이 여전히 실패한다"
    assert json.loads(out)["status"] == "active"


def test_zero_credit_renewal_does_not_create_a_wallet(db):
    _renew(db, "nowallet@e.com", "web_membership", 0, "z2")
    assert _balance(db, "nowallet@e.com") is None, "웹 멤버십 가입자에게 지갑이 생겼다"


def test_zero_credit_renewal_reports_existing_balance(db):
    _run(db, "insert into public.user_wallets(user_id,current_credits) values('mixed@e.com',7);")
    out = _renew(db, "mixed@e.com", "web_membership", 0, "z3")
    assert json.loads(out)["credits_remaining"] == 7
    assert _balance(db, "mixed@e.com") == 7, "0 크레딧 갱신이 잔액을 바꿨다"


def test_zero_credit_renewal_still_activates_subscription(db):
    _renew(db, "act@e.com", "web_membership", 0, "z4")
    out = _run(db, "select plan_id||'/'||status from public.user_subscriptions where user_id='act@e.com';")
    assert out == "web_membership/active"


# ── 양수 크레딧 (레거시 불변) ────────────────────────────────────────────────


def test_legacy_twelve_credit_renewal_grants_credits(db):
    out = _renew(db, "legacy@e.com", "standard_subscription", 12, "L1", store="apple")
    assert json.loads(out)["credits_remaining"] == 12
    assert _balance(db, "legacy@e.com") == 12


def test_legacy_renewal_accumulates_across_periods(db):
    _renew(db, "acc@e.com", "standard_subscription", 12, "L2", store="apple")
    out = _renew(db, "acc@e.com", "standard_subscription", 12, "L3", store="apple")
    assert json.loads(out)["credits_remaining"] == 24, "레거시 누적 충전이 깨졌다"
    assert _balance(db, "acc@e.com") == 24


def test_legacy_renewal_creates_wallet_when_absent(db):
    _renew(db, "fresh@e.com", "standard_subscription", 12, "L4", store="apple")
    assert _balance(db, "fresh@e.com") == 12


# ── 음수·null 은 여전히 거부 ─────────────────────────────────────────────────


@pytest.mark.parametrize("credits", [-1, -12, None])
def test_invalid_credit_amounts_are_rejected(db, credits):
    out = _renew(db, f"bad{credits}@e.com", "web_membership", credits, f"b{credits}")
    assert "invalid_amount" in out, f"{credits} 가 통과했다"


def test_rejected_renewal_writes_nothing(db):
    _renew(db, "rollback@e.com", "web_membership", -5, "r1")
    assert _balance(db, "rollback@e.com") is None
    assert _run(db, "select count(*) from public.user_subscriptions where user_id='rollback@e.com';") == "0"


# ── 기존 계약 불변 ───────────────────────────────────────────────────────────


def test_duplicate_fingerprint_still_raises(db):
    _renew(db, "dup@e.com", "web_membership", 0, "d1")
    out = _renew(db, "dup@e.com", "web_membership", 0, "d1")
    assert "duplicate_subscription_event" in out, "멱등 계약이 깨졌다"


def test_add_wallet_credits_still_rejects_zero(db):
    """
    IAP 충전 경로의 가드는 **그대로 둔다** — 0원짜리 크레딧 충전은 언제나 버그다.
    고친 것은 호출부(갱신 RPC)이지 이 함수가 아니다.
    """
    out = _run(db, "select public.add_wallet_credits('x@e.com', 0);", want_ok=False)
    assert "invalid_amount" in out, "IAP 가드가 느슨해졌다"
