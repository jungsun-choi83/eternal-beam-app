"""publish_phase6_breathing after 20261104 — QA authority in the database (real SQL).

Applies the three publication migrations in order to a throwaway local Postgres.
Skipped when no Postgres binaries are installed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

from .test_breathing_publication_business_gate import _BASE, _GATE, _MIGRATIONS, _Pg, _pg_bin

_RETIRE = _MIGRATIONS / "20261104000000_breathing_publication_legacy_authority_retirement.sql"
_BEFORE, _AFTER = "2026-09-03T00:00:00+00:00", "2026-10-03T00:00:00+00:00"

_STUB_SCHEMA = """
create role anon; create role authenticated; create role service_role;
create table public.pet_motion_versions (
  id uuid primary key, user_id text, pet_id text, motion_id text,
  status text, version int, selected_candidate_id uuid);
create table public.pet_motion_candidates (
  id uuid primary key, motion_version_id uuid, user_id text, pet_id text,
  motion_id text, selected boolean, decision text,
  qa_result jsonb not null default '{}'::jsonb, created_at timestamptz not null default now());
create table public.pets (
  pet_id text primary key, user_id text, content_id text, breathing_bucket text,
  breathing_object_path text, source text, background_baked boolean,
  created_at timestamptz, updated_at timestamptz);
create table public.pet_generation_runs (
  id uuid primary key, user_id text, motion_version_id uuid,
  provider_state jsonb not null default '{}'::jsonb, created_at timestamptz not null default now());
"""


@pytest.fixture(scope="module")
def pg():
    initdb, pg_ctl, psql = _pg_bin("initdb"), _pg_bin("pg_ctl"), _pg_bin("psql")
    if not (initdb and pg_ctl and psql):
        pytest.skip("local Postgres binaries not available")
    root = tempfile.mkdtemp(prefix="ebpg")
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    env.pop("LC_CTYPE", None)
    data, port = os.path.join(root, "d"), 55000 + os.getpid() % 1000
    try:
        subprocess.run(
            [initdb, "-D", data, "-U", "postgres", "-A", "trust", "--locale=C", "-E", "UTF8"],
            check=True, capture_output=True, env=env,
        )
        subprocess.run(
            [pg_ctl, "-D", data, "-w", "-l", os.path.join(root, "log"),
             "-o", f"-c listen_addresses='' -c unix_socket_directories='{root}' -p {port}",
             "start"],
            check=True, capture_output=True, env=env,
        )
        db = _Pg(psql, root, port)
        db.ok(_STUB_SCHEMA)
        for migration in (_BASE, _GATE, _RETIRE):  # applied in filename order
            db.file(migration)
        yield db
    finally:
        subprocess.run([pg_ctl, "-D", data, "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(root, ignore_errors=True)


def _receipt(delivery: str, integrity: str = "PASS") -> dict:
    return {"version": "business-v1", "delivery_action": delivery, "integrity_status": integrity}


def _publish(
    pg: _Pg,
    *,
    decision: str,
    receipt: dict | None,
    created_at: str = _AFTER,
    run: dict | None = None,          # provider_state._business_qa_cutover, or None for no run row
    authority: str | None = None,     # p_qa_authority; None = six-argument call
):
    version, candidate = str(uuid.uuid4()), str(uuid.uuid4())
    user, pet = f"user-{version[:8]}", f"pet_{version}"
    qa = {"decision": decision, **({"business_qa": receipt} if receipt is not None else {})}
    pg.ok(
        f"insert into pet_motion_versions values ('{version}','{user}','{pet}','BREATHING',"
        f"'complete',1,'{candidate}');"
        f"insert into pet_motion_candidates values ('{candidate}','{version}','{user}','{pet}',"
        f"'BREATHING',true,'{decision}','{json.dumps(qa)}'::jsonb,'{created_at}');"
    )
    if run is not None:
        state = json.dumps({"_business_qa_cutover": run})
        pg.ok(f"insert into pet_generation_runs (id,user_id,motion_version_id,provider_state) values "
              f"('{uuid.uuid4()}','{user}','{version}','{state}'::jsonb);")
    extra = f", p_qa_authority => '{authority}'" if authority is not None else ""
    result = pg.run(
        f"select publish_phase6_breathing(p_user_id => '{user}', p_pet_id => '{pet}', "
        f"p_motion_version_id => '{version}', p_selected_candidate_id => '{candidate}', "
        f"p_bucket => 'user-assets', p_object_path => '{user}/b.mp4'{extra});"
    )
    result.version = version  # type: ignore[attr-defined]
    return result


def _published(pg: _Pg, result) -> bool:
    count = pg.ok(
        f"select count(*) from pet_motion_publications where motion_version_id='{result.version}'"
    )
    if result.returncode == 0:
        assert count == "1" and json.loads(result.stdout.strip())["publication_id"]
        return True
    assert count == "0" and "CANDIDATE_NOT_PASS" in result.stderr, result.stderr
    return False


BUSINESS_RUN = {"enrolled": False, "breathing_qa_authority": "business"}
LEGACY_RUN_ENROLLED = {"enrolled": True, "breathing_qa_authority": "legacy"}


# ── business authority: the receipt alone decides ────────────────────────


@pytest.mark.parametrize("legacy", ["PASS", "REVIEW", "FAIL"])
@pytest.mark.parametrize("delivery", ["DELIVER", "DELIVER_WITH_ADVISORY"])
def test_business_delivering_receipt_publishes_whatever_legacy_says(pg, legacy, delivery):
    result = _publish(pg, decision=legacy, receipt=_receipt(delivery), run=BUSINESS_RUN,
                      authority="business")
    assert _published(pg, result) is True


def test_business_legacy_pass_with_blocking_receipt_is_refused(pg):
    # The hole closed by this migration: legacy PASS no longer publishes unchecked.
    assert _published(pg, _publish(
        pg, decision="PASS", receipt=_receipt("BLOCK", "FAIL"), run=BUSINESS_RUN, authority="business"
    )) is False
    assert _published(pg, _publish(
        pg, decision="PASS", receipt=_receipt("DELIVER", "FAIL"), run=BUSINESS_RUN, authority="business"
    )) is False


def test_business_missing_receipt_is_refused_inside_a_business_run_even_pre_cutover(pg):
    for created_at in (_AFTER, _BEFORE):
        result = _publish(pg, decision="PASS", receipt=None, created_at=created_at,
                          run=BUSINESS_RUN, authority="business")
        assert _published(pg, result) is False


def test_run_stamp_alone_enforces_business_authority_for_six_argument_callers(pg):
    assert _published(pg, _publish(pg, decision="PASS", receipt=None, run=BUSINESS_RUN)) is False
    assert _published(pg, _publish(
        pg, decision="FAIL", receipt=_receipt("DELIVER_WITH_ADVISORY"), run=BUSINESS_RUN
    )) is True


def test_business_receipt_governs_regardless_of_enrollment(pg):
    result = _publish(pg, decision="FAIL", receipt=_receipt("DELIVER"),
                      run={"enrolled": False, "breathing_qa_authority": "business"}, authority="business")
    assert _published(pg, result) is True


# ── direct route grandfathering ──────────────────────────────────────────


def test_direct_route_grandfathers_pre_cutover_legacy_pass_only(pg):
    assert _published(pg, _publish(
        pg, decision="PASS", receipt=None, created_at=_BEFORE, authority="business"
    )) is True
    assert _published(pg, _publish(
        pg, decision="PASS", receipt=None, created_at=_AFTER, authority="business"
    )) is False
    assert _published(pg, _publish(
        pg, decision="REVIEW", receipt=None, created_at=_BEFORE, authority="business"
    )) is False
    # A pre-cutover PASS that does carry a receipt is judged by the receipt.
    assert _published(pg, _publish(
        pg, decision="PASS", receipt=_receipt("BLOCK", "FAIL"), created_at=_BEFORE, authority="business"
    )) is False


# ── flag off / unstamped: legacy behavior of 20261103 restored ───────────


def test_legacy_mode_keeps_previous_behavior(pg):
    # Six-argument call, no stamp: legacy PASS publishes without a receipt check.
    assert _published(pg, _publish(pg, decision="PASS", receipt=None)) is True
    assert _published(pg, _publish(pg, decision="PASS", receipt=_receipt("BLOCK", "FAIL"))) is True
    # Non-PASS needs an enrolled run plus a delivering receipt.
    assert _published(pg, _publish(
        pg, decision="FAIL", receipt=_receipt("DELIVER_WITH_ADVISORY"), run=LEGACY_RUN_ENROLLED
    )) is True
    assert _published(pg, _publish(
        pg, decision="FAIL", receipt=_receipt("DELIVER_WITH_ADVISORY"),
        run={"enrolled": False, "breathing_qa_authority": "legacy"},
    )) is False
    assert _published(pg, _publish(pg, decision="FAIL", receipt=None, run=LEGACY_RUN_ENROLLED)) is False
    # Explicit legacy argument behaves the same.
    assert _published(pg, _publish(pg, decision="PASS", receipt=None, authority="legacy")) is True


def test_migration_replaces_the_six_argument_function_and_stacks_after_the_gate(pg):
    assert pg.ok(
        "select count(*) from pg_proc where proname = 'publish_phase6_breathing'"
    ) == "1"
    assert pg.ok(
        "select pronargs from pg_proc where proname = 'publish_phase6_breathing'"
    ) == "7"
    names = sorted(p.name for p in _MIGRATIONS.glob("2026*.sql"))
    assert names.index(_RETIRE.name) == names.index(_GATE.name) + 1 == len(names) - 1
    sql = _RETIRE.read_text()
    assert "drop function if exists public.publish_phase6_breathing(text, text, uuid, uuid, text, text);" in sql
    assert "timestamptz '2026-10-02T15:52:00+00:00'" in sql
