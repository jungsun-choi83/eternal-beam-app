"""
테마 크레딧 구매의 **SQL 원자성 계약** — 실제 Postgres 에서 검증한다.

인메모리 테스트(test_theme_credit_purchase.py)는 보상 로직으로 같은 관찰 가능한
결과를 흉내 낼 뿐이다. **진짜 원자성은 트랜잭션이 있어야 성립**하고, 그것은
여기서만 확인할 수 있다.

핵심 검증: 함수 중간에 실패하면 지갑도 원장도 소유권도 **하나도 바뀌지 않는다.**
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
create unique index user_theme_entitlements_order_idx
  on public.user_theme_entitlements (order_id) where order_id is not null;
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
    """
    테스트마다 새 DB 를 만든다 (모듈 스코프가 아니다).

    이 파일은 **원자성**을 검증하므로 각 시나리오가 깨끗한 상태에서 시작해야 한다.
    앞 테스트의 잔액·소유권이 남아 있으면 "아무것도 바뀌지 않았다"를 확인할 기준선이
    흐려진다.
    """
    if not PSQL:
        pytest.skip("psql 이 없다 — SQL 계약 테스트를 건너뛴다")
    try:
        subprocess.run(
            [PSQL, "-d", "postgres", "-qtA", "-c", "select 1"],
            capture_output=True, timeout=10, check=True,
        )
    except Exception:
        pytest.skip("로컬 Postgres 가 떠 있지 않다 — SQL 계약 테스트를 건너뛴다")

    name = f"eb_theme_{uuid.uuid4().hex[:10]}"
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


def _fund(db, user, amount, key="fund1"):
    _run(db, f"select public.wallet_apply('{user}', {amount}, 'credit_pack_topup', '{key}');")


def _buy(db, user, theme, key=None, *, want_ok=False) -> str:
    k = key or f"theme:{user}:{theme}"
    return _run(
        db,
        f"select public.purchase_theme_with_credits('{user}','{theme}','{k}');",
        want_ok=want_ok,
    )


def _state(db, user) -> tuple[int, int, int]:
    """(잔액, 소유권 수, 원장 행 수) — 원자성 판정의 세 축."""
    out = _run(
        db,
        "select coalesce((select current_credits from user_wallets where user_id='{u}'), -1)"
        " || '|' || (select count(*) from user_theme_entitlements where user_id='{u}')"
        " || '|' || (select count(*) from credit_ledger where user_id='{u}');".format(u=user),
    )
    a, b, c = out.split("|")
    return int(a), int(b), int(c)


# ── 요구된 흐름 ──────────────────────────────────────────────────────────────


def test_the_documented_flow(db):
    """잔액 12 / 가격 5 → 잠금 해제 → 잔액 7 · Aurora OWNED (영구)."""
    _fund(db, "alice", 12)
    out = json.loads(_buy(db, "alice", "aurora", want_ok=True))

    assert out["charged"] == 5
    assert out["already_owned"] is False
    assert out["credits_remaining"] == 7

    row = _run(
        db,
        "select status||'|'||provider||'|'||amount||'|'||currency||'|'"
        "||coalesce(expires_at::text,'PERMANENT') "
        "from user_theme_entitlements where user_id='alice' and theme_key='aurora';",
    )
    assert row == "owned|credits|5|CREDIT|PERMANENT"


def test_the_ledger_records_the_spend(db):
    _fund(db, "alice", 12)
    _buy(db, "alice", "aurora", want_ok=True)
    row = _run(
        db,
        "select reason||'|'||delta||'|'||balance_after||'|'||product_key||'|'||unit_price "
        "from credit_ledger where reason='theme_purchase' and user_id='alice';",
    )
    assert row == "theme_purchase|-5|7|theme:aurora|5"


def test_no_drift_after_purchase(db):
    _fund(db, "alice", 12)
    _buy(db, "alice", "aurora", want_ok=True)
    assert _run(db, "select count(*) from public.credit_ledger_drift();") == "0"


# ── 원자성: 실패는 아무것도 남기지 않는다 ───────────────────────────────────


def test_insufficient_credits_rolls_back_everything(db):
    """
    **이 파일의 이유.** 잔액 부족으로 실패하면 세 축 모두 그대로여야 한다.
    """
    _fund(db, "bob", 3)
    before = _state(db, "bob")

    out = _buy(db, "bob", "aurora")
    assert "insufficient_credits" in out

    assert _state(db, "bob") == before, "실패한 구매가 상태를 바꿨다"


def test_an_unsold_theme_rolls_back_everything(db):
    _fund(db, "carol", 20)
    before = _state(db, "carol")

    _run(db, "delete from digital_products where product_key='theme:ocean_deep';")
    out = _buy(db, "carol", "ocean_deep")

    assert "product_not_sold" in out
    assert _state(db, "carol") == before


def test_a_free_theme_is_rejected_without_side_effects(db):
    _fund(db, "dave", 20)
    before = _state(db, "dave")

    out = _buy(db, "dave", "fresh_forest")
    assert "theme_is_free" in out
    assert _state(db, "dave") == before


def test_an_inactive_product_is_not_sold(db):
    _fund(db, "erin", 20)
    _run(db, "update digital_products set active=false where product_key='theme:aurora';")
    before = _state(db, "erin")

    out = _buy(db, "erin", "aurora")
    assert "product_not_sold" in out
    assert _state(db, "erin") == before


def test_a_broken_entitlement_write_rolls_back_the_charge(db):
    """
    **차감만 성공하는 상태가 만들어질 수 없다.**

    소유권 쓰기를 강제로 실패시킨다(제약 위반). 트랜잭션이라면 차감과 원장도
    함께 사라져야 한다 — 이것이 Python 계층에서는 흉내 낼 수 없는 성질이다.
    """
    _fund(db, "frank", 20)
    before = _state(db, "frank")

    # 소유권 테이블에 절대 통과할 수 없는 제약을 건다.
    _run(db, "alter table public.user_theme_entitlements add constraint tmp_block check (false);")
    try:
        out = _buy(db, "frank", "aurora")
        assert "tmp_block" in out or "violates check constraint" in out
        assert _state(db, "frank") == before, "소유권 실패인데 차감이 남았다"
    finally:
        _run(db, "alter table public.user_theme_entitlements drop constraint tmp_block;")

    # 제약을 풀면 정상적으로 살 수 있다 — 앞선 실패가 아무 자국도 남기지 않았다.
    out = json.loads(_buy(db, "frank", "aurora", want_ok=True))
    assert out["charged"] == 5


# ── 멱등성 ───────────────────────────────────────────────────────────────────


def test_double_tap_charges_once(db):
    _fund(db, "gina", 12)
    first = json.loads(_buy(db, "gina", "aurora", want_ok=True))
    second = json.loads(_buy(db, "gina", "aurora", want_ok=True))

    assert first["charged"] == 5
    assert second["charged"] == 0
    assert second["already_owned"] is True
    assert _state(db, "gina")[0] == 7


def test_an_existing_toss_entitlement_is_never_overwritten(db):
    """KRW 결제 기록이 크레딧 기록으로 조용히 바뀌면 안 된다."""
    _fund(db, "hana", 12)
    _run(
        db,
        "insert into user_theme_entitlements (user_id, theme_key, status, provider, order_id, "
        "amount, currency) values ('hana','aurora','owned','toss','toss_1',4900,'KRW');",
    )
    out = json.loads(_buy(db, "hana", "aurora", want_ok=True))

    assert out["charged"] == 0
    assert out["already_owned"] is True
    row = _run(
        db,
        "select provider||'|'||amount||'|'||currency from user_theme_entitlements "
        "where user_id='hana' and theme_key='aurora';",
    )
    assert row == "toss|4900|KRW"
    assert _state(db, "hana")[0] == 12


def test_repurchase_is_possible_after_the_entitlement_expires(db):
    """만료된 소유권은 다시 살 수 있다 (덮어쓰기가 정당한 유일한 경우)."""
    _fund(db, "iris", 20)
    _run(
        db,
        "insert into user_theme_entitlements (user_id, theme_key, status, provider, order_id, "
        "expires_at) values ('iris','aurora','owned','toss','toss_x', now() - interval '1 day');",
    )
    out = json.loads(_buy(db, "iris", "aurora", want_ok=True))
    assert out["charged"] == 5
    row = _run(
        db,
        "select provider||'|'||coalesce(expires_at::text,'PERMANENT') "
        "from user_theme_entitlements where user_id='iris' and theme_key='aurora';",
    )
    assert row == "credits|PERMANENT"


# ── 가격은 카탈로그가 정한다 ────────────────────────────────────────────────


def test_price_comes_from_the_catalog(db):
    _fund(db, "jack", 20)
    _run(db, "update digital_products set credit_price=9 where product_key='theme:aurora';")
    out = json.loads(_buy(db, "jack", "aurora", want_ok=True))
    assert out["charged"] == 9
    assert out["credits_remaining"] == 11


def test_seeded_theme_prices(db):
    """Aurora 5 · Sunset 4 — 같은 카테고리, 다른 값."""
    rows = _run(
        db,
        "select string_agg(product_key||'='||credit_price, ',' order by product_key) "
        "from digital_products where product_key in "
        "('theme:aurora','theme:sunset','theme:custom_photo_bg');",
    )
    assert rows == "theme:aurora=5,theme:custom_photo_bg=8,theme:sunset=4"
