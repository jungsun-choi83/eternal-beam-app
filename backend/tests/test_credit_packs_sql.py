"""
크레딧 팩 확인의 **SQL 원자성 계약** — 실제 Postgres.

    주문 paid + 지갑 충전 + 원장을 **한 트랜잭션으로**

나누면 두 가지 부분 실패가 생긴다:
    주문만 paid  → 고객은 돈을 냈는데 크레딧이 없다. 주문은 성공이라고 말한다.
    충전만 성공  → 같은 주문으로 다시 확인해 무한 충전이 가능하다.

그리고 Phase 5 의 종료 조건인 전체 고리를 여기서 한 번 통과시킨다:
    KRW → Beam Credits → Theme → 영구 소유
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

STACK = [
    "20260930000000_authoritative_wallet_rpcs.sql",
    "20261001000000_credit_ledger.sql",
    "20261001000100_credit_ledger_wire_rpcs.sql",
    "20261002000000_digital_products.sql",
    "20261003000000_theme_purchase_with_credits.sql",
    "20261004000000_credit_packs.sql",
]

BASE = """
create table public.user_wallets (
  user_id text primary key, current_credits int not null default 0 check (current_credits >= 0),
  updated_at timestamptz default now());
create table public.user_theme_entitlements (
  user_id text not null, theme_key text not null, status text not null default 'owned',
  provider text, order_id text, payment_key text, amount integer, currency text default 'KRW',
  purchased_at timestamptz not null default now(), expires_at timestamptz,
  primary key (user_id, theme_key));
create unique index utei on public.user_theme_entitlements (order_id) where order_id is not null;
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


@pytest.fixture()
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

    name = f"eb_packs_{uuid.uuid4().hex[:10]}"
    subprocess.run(
        [PSQL, "-d", "postgres", "-qc", f"create database {name}"],
        capture_output=True, check=True, timeout=30,
    )
    try:
        _run(name, BASE)
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


def _order(db, order_id, user, pack="pack_5"):
    _run(
        db,
        "insert into credit_pack_orders (order_id, user_id, pack_key, amount, credits) "
        f"select '{order_id}','{user}',pack_key,price_krw,credits from credit_packs "
        f"where pack_key='{pack}';",
    )


def _confirm(db, order_id, user, *, want_ok=False) -> str:
    return _run(
        db,
        f"select public.confirm_credit_pack_order('{order_id}','{user}','pk_1');",
        want_ok=want_ok,
    )


def _state(db, user) -> tuple[int, int, int]:
    out = _run(
        db,
        "select coalesce((select current_credits from user_wallets where user_id='{u}'), -1)"
        " || '|' || (select count(*) from credit_ledger where user_id='{u}')"
        " || '|' || (select count(*) from credit_pack_orders where user_id='{u}' and status='paid');".format(u=user),
    )
    a, b, c = out.split("|")
    return int(a), int(b), int(c)


# ── 시드 ─────────────────────────────────────────────────────────────────────


def test_seeded_packs(db):
    rows = _run(
        db,
        "select string_agg(pack_key||':'||credits||':'||price_krw, ',' order by sort_order) "
        "from credit_packs where active;",
    )
    assert rows == "pack_5:5:4900,pack_12:12:9900,pack_30:30:19900"


# ── 원자성 ───────────────────────────────────────────────────────────────────


def test_confirm_marks_the_order_and_credits_the_wallet_together(db):
    _order(db, "o1", "alice")
    out = json.loads(_confirm(db, "o1", "alice", want_ok=True))

    assert out["credits_added"] == 5
    assert out["credits_remaining"] == 5
    assert _state(db, "alice") == (5, 1, 1)

    row = _run(
        db,
        "select reason||'|'||delta||'|'||product_key||'|'||unit_price||'|'||ref_type "
        "from credit_ledger where user_id='alice';",
    )
    assert row == "credit_pack_topup|5|pack_5|4900|credit_pack_orders"


def test_replay_adds_nothing(db):
    _order(db, "o2", "bob")
    first = json.loads(_confirm(db, "o2", "bob", want_ok=True))
    second = json.loads(_confirm(db, "o2", "bob", want_ok=True))

    assert first["credits_added"] == 5
    assert second["credits_added"] == 0
    assert second["replayed"] is True
    assert _state(db, "bob") == (5, 1, 1)


def test_another_users_order_is_not_found(db):
    """order_id 는 리다이렉트 URL 에 있다 — 남의 결제로 지갑을 채울 수 없다."""
    _order(db, "o3", "carol")
    out = _confirm(db, "o3", "mallory")

    assert "order_not_found" in out
    assert _state(db, "mallory")[0] == -1  # 지갑조차 만들어지지 않았다
    assert _state(db, "carol") == (-1, 0, 0)


def test_a_missing_order_is_rejected(db):
    out = _confirm(db, "nope", "dave")
    assert "order_not_found" in out


def test_a_failed_order_cannot_be_confirmed(db):
    _order(db, "o4", "erin")
    _run(db, "update credit_pack_orders set status='failed' where order_id='o4';")
    out = _confirm(db, "o4", "erin")

    assert "order_not_pending" in out
    assert _state(db, "erin") == (-1, 0, 0)


def test_a_broken_ledger_write_rolls_back_the_order(db):
    """
    **주문만 paid 인 상태가 만들어질 수 없다.**

    원장 쓰기를 강제로 실패시킨다. 트랜잭션이라면 주문 상태와 지갑도 함께
    되돌아가야 한다 — Python 계층에서는 흉내 낼 수 없는 성질이다.
    """
    _order(db, "o5", "frank")
    _run(db, "alter table public.credit_ledger add constraint tmp_block check (false);")
    try:
        out = _confirm(db, "o5", "frank")
        assert "tmp_block" in out or "violates check constraint" in out
        status = _run(db, "select status from credit_pack_orders where order_id='o5';")
        assert status == "pending", "원장 실패인데 주문이 paid 로 남았다"
        assert _state(db, "frank")[0] == -1
    finally:
        _run(db, "alter table public.credit_ledger drop constraint tmp_block;")

    out = json.loads(_confirm(db, "o5", "frank", want_ok=True))
    assert out["credits_added"] == 5


def test_no_drift_after_topups(db):
    _order(db, "o6", "gina", pack="pack_30")
    _confirm(db, "o6", "gina", want_ok=True)
    assert _run(db, "select count(*) from public.credit_ledger_drift();") == "0"


# ── Phase 5 종료 조건: 전체 고리 ────────────────────────────────────────────


def test_the_full_loop_krw_to_credits_to_theme_to_permanent_ownership(db):
    """
        잔액 2 → Aurora(5) 시도 → 부족 → 팩 구매(₩4,900) → 잔액 7
              → Aurora 구매 → 잔액 2 → **영구 소유**
    """
    _run(db, "select public.wallet_apply('hana', 2, 'admin_adjustment', 'seed');")

    # 부족해서 살 수 없다.
    blocked = _run(
        db,
        "select public.purchase_theme_with_credits('hana','aurora','theme:hana:aurora');",
        want_ok=False,
    )
    assert "insufficient_credits" in blocked

    # KRW → 크레딧
    _order(db, "o7", "hana", pack="pack_5")
    topup = json.loads(_confirm(db, "o7", "hana", want_ok=True))
    assert topup["credits_added"] == 5
    assert topup["credits_remaining"] == 7

    # 크레딧 → 테마
    bought = json.loads(
        _run(
            db,
            "select public.purchase_theme_with_credits('hana','aurora','theme:hana:aurora');",
        )
    )
    assert bought["charged"] == 5
    assert bought["credits_remaining"] == 2

    # 영구 소유
    row = _run(
        db,
        "select status||'|'||provider||'|'||coalesce(expires_at::text,'FOREVER') "
        "from user_theme_entitlements where user_id='hana' and theme_key='aurora';",
    )
    assert row == "owned|credits|FOREVER"

    # 원장이 전 과정을 설명하고, 잔액과 어긋나지 않는다.
    reasons = _run(
        db,
        "select string_agg(reason, ',' order by created_at) from credit_ledger where user_id='hana';",
    )
    assert reasons == "admin_adjustment,credit_pack_topup,theme_purchase"
    assert _run(db, "select count(*) from public.credit_ledger_drift();") == "0"
