"""
`credit_sessions_paid_has_reservation` 과 그 **유일한 예외**.

Phase 7 이 세운 규칙: **유료 생성에는 예약이 있어야 한다.** 예약 없이 과금된
세션은 스키마가 거부한다 — 애플리케이션이 실수해도 기록될 수 없다.

예외가 하나 있다. 4크레딧 기기 생성 팩(credit_generation_service)은 기기
호환성이 이전되지 않아 은퇴하지 못했고(docs/LEGACY_RETIREMENT.md §5), 여전히
예약 없이 차감한다. 그래서 `legacy_charge` 플래그가 있다.

── 이 파일이 막는 두 가지 ──────────────────────────────────────────────────

1. **예외가 번지는 것.** legacy_charge 를 쓰는 곳이 하나뿐이어야 한다. 늘어나면
   "예약 없이 과금해도 된다"가 되살아나고, Phase 7 종료 조건이 무너진다.

2. **제약이 과거를 부정하는 것.** credits_charged 는 `default 4` 인 기존 컬럼이라
   예약 이전의 모든 세션이 4를 들고 있다. 그 과금은 잘못된 것이 아니라 차감-후-환불
   방식으로 정상 처리된 것이다. 제약을 그대로 걸면 마이그레이션이 기존 행에서
   실패한다(실측: check constraint ... is violated by some row).
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
MIGRATION = (
    REPO / "supabase" / "migrations" / "20261006000000_credit_reservations.sql"
)


# ── 1. 예외가 번지지 않는다 ─────────────────────────────────────────────────


def _production_py():
    for p in BACKEND.rglob("*.py"):
        if ".venv" in p.parts or "__pycache__" in p.parts or "tests" in p.parts:
            continue
        yield p, str(p.relative_to(REPO)).replace("\\", "/")


def test_only_the_unretired_device_pack_charges_without_a_reservation():
    """
    **호출부는 하나뿐이다.**

    늘어난다면 그것은 새 기능이 예약을 건너뛰었다는 뜻이다. 예약이 없으면 실패한
    생성이 크레딧을 영구히 먹고(해제할 대상이 없다), 재시도가 두 번 청구한다 —
    Phase 7 이 정확히 그것을 막으려고 만들어졌다.
    """
    callers = [
        rel for p, rel in _production_py()
        if "legacy_charge=True" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert callers == ["backend/services/credit_generation_service.py"], (
        f"예약 없는 과금을 하는 곳이 예상과 다르다: {callers}\n"
        "docs/LEGACY_RETIREMENT.md §5 참고 — 이 예외는 4크레딧 기기 팩 전용이다."
    )


def test_the_reservation_era_paths_do_not_use_the_exemption():
    """
    아이들·액션은 예약을 거쳐 제출된다 (Phase 7·8). 그 경로가 예외를 쓰기
    시작하면 두 방식이 동시에 살아 있게 된다.
    """
    for name in ("premium_generation.py", "generation_credits.py"):
        src = (BACKEND / "services" / name).read_text(encoding="utf-8")
        assert "legacy_charge=True" not in src, f"{name} 이 예약을 건너뛴다"


def test_the_exemption_defaults_to_off():
    """
    기본값이 True 였다면 예약을 잊은 새 경로가 **조용히** 통과한다.
    스키마의 방어는 잊었을 때 걸리라고 있는 것이다.
    """
    tree = ast.parse(
        (BACKEND / "services" / "generated_motions_service.py").read_text(encoding="utf-8")
    )
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "create_credit_session"
    )
    names = [a.arg for a in fn.args.kwonlyargs]
    assert "legacy_charge" in names, "예외가 키워드 전용 인자가 아니다"
    default = fn.args.kw_defaults[names.index("legacy_charge")]
    assert isinstance(default, ast.Constant) and default.value is False


def test_the_retirement_is_documented_where_the_exemption_lives():
    """이 예외가 왜 있는지 모르면 다음 사람은 그냥 지우거나 그냥 퍼뜨린다."""
    src = (BACKEND / "services" / "credit_generation_service.py").read_text(encoding="utf-8")
    assert "LEGACY_RETIREMENT.md" in src
    doc = (REPO / "docs" / "LEGACY_RETIREMENT.md").read_text(encoding="utf-8")
    assert "generate_with_credit" in doc


# ── 2. 제약이 과거를 부정하지 않는다 (실제 Postgres) ────────────────────────


def _psql() -> str | None:
    for c in ("psql", "/opt/homebrew/opt/postgresql@16/bin/psql"):
        if shutil.which(c) or os.path.exists(c):
            return shutil.which(c) or c
    return None


PSQL = _psql()

#: 예약이 생기기 **전** 상태. credits_charged 가 `default 4` 인 것이 핵심이다 —
#: 그 기본값 때문에 기존 행 전부가 유료로 보이고, 예약 컬럼은 아직 없다.
PRE_RESERVATION = """
create table public.credit_ledger (
  ledger_id uuid primary key default gen_random_uuid(),
  user_id text not null, delta int not null, state text not null default 'SETTLED');
create table public.user_wallets (
  user_id text primary key, current_credits int not null default 0);
create table public.credit_generation_sessions (
  session_id uuid primary key,
  user_id text not null, pet_id text not null,
  place_key text not null, place_id text not null, pet_image_url text not null,
  credits_charged int not null default 4,
  status text not null default 'processing',
  created_at timestamptz not null default now());
create table public.scene_generation_jobs (
  job_id uuid primary key default gen_random_uuid(), user_id text not null,
  status text not null default 'queued');
"""

#: 배포 시점에 이미 존재하던 세션들. 하나는 아직 processing 이다 — 웹훅이
#: 나중에 도착해 이 행을 UPDATE 한다.
LEGACY_ROWS = """
insert into public.credit_generation_sessions
  (session_id, user_id, pet_id, place_key, place_id, pet_image_url, credits_charged, status)
values
  (gen_random_uuid(), 'u_old', 'p1', 'snow_forest', 'sf', 'https://x/1.png', 4, 'completed'),
  (gen_random_uuid(), 'u_old', 'p2', 'beach', 'be', 'https://x/2.png', 4, 'processing'),
  (gen_random_uuid(), 'u_free', 'p3', 'beach', 'be', 'https://x/3.png', 0, 'completed');
"""


def _run(db: str, sql: str, *, want_ok: bool = True) -> str:
    r = subprocess.run(
        [PSQL, "-d", db, "-qtA", "-c", sql], capture_output=True, text=True, timeout=60
    )
    out = (r.stdout + r.stderr).strip()
    if want_ok and r.returncode != 0:
        raise AssertionError(f"psql 실패:\n{sql}\n{out}")
    if not want_ok and r.returncode == 0:
        raise AssertionError(f"실패했어야 하는 SQL 이 성공했다:\n{sql}")
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

    name = f"eb_legacy_{uuid.uuid4().hex[:10]}"
    subprocess.run(
        [PSQL, "-d", "postgres", "-qc", f"create database {name}"],
        capture_output=True, check=True, timeout=30,
    )
    try:
        _run(name, PRE_RESERVATION)
        _run(name, LEGACY_ROWS)
        yield name
    finally:
        subprocess.run(
            [PSQL, "-d", "postgres", "-qc", f"drop database {name}"],
            capture_output=True, timeout=30,
        )


def _apply_sessions_part(db: str) -> subprocess.CompletedProcess:
    """
    마이그레이션에서 **세션 표 부분만** 떼어 적용한다.

    앞부분은 wallet_apply 등 다른 마이그레이션의 함수에 의존한다. 여기서 보려는
    것은 "기존 행이 있는 표에 이 제약을 걸 수 있는가" 하나다.
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    part = sql[sql.index("-- ── 생성 세션 ↔ 예약"):]
    return subprocess.run(
        [PSQL, "-d", db, "-q", "-v", "ON_ERROR_STOP=1", "-c", part],
        capture_output=True, text=True, timeout=120,
    )


def test_the_migration_applies_to_a_database_that_already_has_paid_sessions(db):
    """
    **실측된 실패의 회귀 테스트.**

        ERROR: check constraint "credit_sessions_paid_has_reservation"
               is violated by some row

    예약 이전의 과금을 무효로 선언하는 제약은 걸리지 않는다 — 걸려서도 안 된다.
    """
    r = _apply_sessions_part(db)
    assert r.returncode == 0, f"기존 유료 세션이 있는 DB 에서 실패했다:\n{r.stdout}{r.stderr}"

    marked = _run(db, "select count(*) from public.credit_generation_sessions where legacy_charge;")
    assert marked == "2", "예약 이전 유료 세션이 표시되지 않았다"
    free = _run(
        db,
        "select legacy_charge from public.credit_generation_sessions where credits_charged = 0;",
    )
    assert free == "f", "무료 세션까지 레거시로 표시했다 — 예외를 넓히고 있다"


def test_an_in_flight_legacy_session_can_still_be_finalised(db):
    """
    배포 시점에 processing 이던 세션의 웹훅은 **나중에** 도착한다.

    ⚠️ NOT VALID 로 우회했다면 여기서 막혔을 것이다 — NOT VALID 제약도 기존 행을
    UPDATE 할 때는 검사한다. 그러면 고객은 4크레딧을 냈는데 결과를 받지 못한다.
    """
    assert _apply_sessions_part(db).returncode == 0
    _run(
        db,
        "update public.credit_generation_sessions set status = 'completed' "
        "where status = 'processing';",
    )
    assert _run(
        db, "select count(*) from public.credit_generation_sessions where status = 'completed';"
    ) == "3"


def test_a_new_paid_session_without_a_reservation_is_still_rejected(db):
    """
    **예외는 과거와 그 한 경로에만 열려 있다.** 규칙 자체는 살아 있다.

    legacy_charge 를 명시하지 않은 유료 세션은 예약이 없으면 거부된다 —
    예약을 잊은 새 코드가 조용히 통과하지 못한다.
    """
    assert _apply_sessions_part(db).returncode == 0
    _run(
        db,
        "insert into public.credit_generation_sessions "
        "(session_id, user_id, pet_id, place_key, place_id, pet_image_url, credits_charged) "
        "values (gen_random_uuid(), 'u_new', 'p9', 'beach', 'be', 'https://x/9.png', 3);",
        want_ok=False,
    )

    # 예약이 있으면 통과한다.
    _run(
        db,
        "insert into public.credit_generation_sessions "
        "(session_id, user_id, pet_id, place_key, place_id, pet_image_url, credits_charged, "
        " reservation_ledger_id) "
        "values (gen_random_uuid(), 'u_new', 'p9', 'beach', 'be', 'https://x/9.png', 3, "
        " gen_random_uuid());",
    )
