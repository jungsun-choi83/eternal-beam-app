"""publish_phase6_breathing — Business QA publish gate (real SQL, throwaway Postgres).

The gate lives in the database function, so these tests run the actual
migrations against a temporary local cluster. Skipped when no Postgres
binaries are installed.
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

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATIONS = _ROOT / "supabase" / "migrations"
_BASE = _MIGRATIONS / "20261017000000_phase7a_breathing_publication.sql"
_GATE = _MIGRATIONS / "20261103000000_breathing_publication_business_qa_gate.sql"

_STUB_SCHEMA = """
create role anon; create role authenticated; create role service_role;
create table public.pet_motion_versions (
  id uuid primary key, user_id text, pet_id text, motion_id text,
  status text, version int, selected_candidate_id uuid);
create table public.pet_motion_candidates (
  id uuid primary key, motion_version_id uuid, user_id text, pet_id text,
  motion_id text, selected boolean, decision text,
  qa_result jsonb not null default '{}'::jsonb);
create table public.pets (
  pet_id text primary key, user_id text, content_id text, breathing_bucket text,
  breathing_object_path text, source text, background_baked boolean,
  created_at timestamptz, updated_at timestamptz);
create table public.pet_generation_runs (
  id uuid primary key, user_id text, motion_version_id uuid,
  provider_state jsonb not null default '{}'::jsonb);
"""


def _pg_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for base in ("/opt/homebrew/opt/postgresql@16/bin", "/usr/local/opt/postgresql@16/bin"):
        candidate = os.path.join(base, name)
        if os.path.exists(candidate):
            return candidate
    return None


class _Pg:
    def __init__(self, psql: str, sock: str, port: int) -> None:
        self._base = [psql, "-h", sock, "-p", str(port), "-U", "postgres", "-d", "postgres",
                      "-X", "-q",
                      "-v", "ON_ERROR_STOP=1"]

    def run(self, sql: str) -> subprocess.CompletedProcess:
        return subprocess.run([*self._base, "-At", "-c", sql], capture_output=True, text=True)

    def ok(self, sql: str) -> str:
        result = self.run(sql)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def file(self, path: Path) -> None:
        result = subprocess.run([*self._base, "-f", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


@pytest.fixture(scope="module")
def pg():
    initdb, pg_ctl, psql = _pg_bin("initdb"), _pg_bin("pg_ctl"), _pg_bin("psql")
    if not (initdb and pg_ctl and psql):
        pytest.skip("local Postgres binaries not available")
    root = tempfile.mkdtemp(prefix="ebpg")
    # An invalid inherited locale (e.g. LC_CTYPE=UTF-8 on macOS) aborts startup.
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    env.pop("LC_CTYPE", None)
    data, port = os.path.join(root, "d"), 54000 + os.getpid() % 1000
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
        db.file(_BASE)
        db.file(_GATE)
        yield db
    finally:
        subprocess.run([pg_ctl, "-D", data, "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(root, ignore_errors=True)


def _receipt(delivery: str, integrity: str) -> dict:
    return {
        "version": "business-v1",
        "authority_profile": "breathing-v2",
        "delivery_action": delivery,
        "integrity_status": integrity,
        "retry_action": "STOP" if delivery != "BLOCK" else "REGENERATE",
    }


def _publish(
    pg: _Pg,
    *,
    decision: str,
    receipt: dict | None,
    enrolled: bool | None,
    motion_id: str = "BREATHING",
) -> subprocess.CompletedProcess:
    """Seed one version/candidate(/run) and call the publish function."""

    version, candidate = str(uuid.uuid4()), str(uuid.uuid4())
    user, pet = f"user-{version[:8]}", f"pet_{version}"
    qa = {"decision": decision}
    if receipt is not None:
        qa["business_qa"] = receipt
    qa_json = json.dumps(qa).replace("'", "''")
    pg.ok(
        f"insert into pet_motion_versions values ('{version}','{user}','{pet}','{motion_id}',"
        f"'complete',1,'{candidate}');"
        f"insert into pet_motion_candidates values ('{candidate}','{version}','{user}','{pet}',"
        f"'{motion_id}',true,'{decision}','{qa_json}'::jsonb);"
    )
    if enrolled is not None:
        state = json.dumps({"_business_qa_cutover": {"enrolled": enrolled}})
        pg.ok(
            f"insert into pet_generation_runs values ('{uuid.uuid4()}','{user}','{version}',"
            f"'{state}'::jsonb);"
        )
    result = pg.run(
        f"select publish_phase6_breathing('{user}','{pet}','{version}','{candidate}',"
        f"'user-assets','{user}/breathing.mp4');"
    )
    result.version, result.pet = version, pet  # type: ignore[attr-defined]
    return result


def _assert_published(pg: _Pg, result) -> None:
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["publication_id"] and payload["deduplicated"] is False
    assert pg.ok(
        f"select count(*) from pet_motion_publications where motion_version_id='{result.version}'"
    ) == "1"
    assert pg.ok(
        f"select breathing_motion_version_id from pets where pet_id='{result.pet}'"
    ) == result.version


def _assert_refused(pg: _Pg, result, decision: str = "FAIL") -> None:
    assert result.returncode != 0
    assert f"CANDIDATE_NOT_PASS:{decision}" in result.stderr
    assert pg.ok(
        f"select count(*) from pet_motion_publications where motion_version_id='{result.version}'"
    ) == "0"


@pytest.mark.parametrize("delivery", ["DELIVER_WITH_ADVISORY", "DELIVER"])
def test_enrolled_deliverable_receipt_publishes_despite_legacy_fail(pg, delivery):
    result = _publish(pg, decision="FAIL", receipt=_receipt(delivery, "PASS"), enrolled=True)
    _assert_published(pg, result)


def test_enrolled_integrity_review_receipt_publishes(pg):
    result = _publish(
        pg, decision="REVIEW", receipt=_receipt("DELIVER_WITH_ADVISORY", "REVIEW"), enrolled=True
    )
    _assert_published(pg, result)


def test_enrolled_block_with_integrity_fail_is_refused(pg):
    result = _publish(pg, decision="FAIL", receipt=_receipt("BLOCK", "FAIL"), enrolled=True)
    _assert_refused(pg, result)


def test_enrolled_deliver_action_with_integrity_fail_is_refused(pg):
    # Inconsistent receipt: integrity FAIL always wins.
    result = _publish(pg, decision="FAIL", receipt=_receipt("DELIVER", "FAIL"), enrolled=True)
    _assert_refused(pg, result)


def test_enrolled_missing_receipt_is_refused(pg):
    result = _publish(pg, decision="FAIL", receipt=None, enrolled=True)
    _assert_refused(pg, result)


def test_enrolled_receipt_of_unknown_version_is_refused(pg):
    receipt = {**_receipt("DELIVER", "PASS"), "version": "business-v0"}
    result = _publish(pg, decision="FAIL", receipt=receipt, enrolled=True)
    _assert_refused(pg, result)


@pytest.mark.parametrize("enrolled", [False, None])
def test_not_enrolled_legacy_fail_is_refused(pg, enrolled):
    # enrolled=None: no generation run row at all (direct publication).
    result = _publish(
        pg, decision="FAIL", receipt=_receipt("DELIVER_WITH_ADVISORY", "PASS"), enrolled=enrolled
    )
    _assert_refused(pg, result)


@pytest.mark.parametrize("enrolled", [False, None, True])
def test_legacy_pass_still_publishes(pg, enrolled):
    result = _publish(pg, decision="PASS", receipt=None, enrolled=enrolled)
    _assert_published(pg, result)


def test_other_motions_are_still_rejected_by_this_function(pg):
    result = _publish(
        pg, decision="PASS", receipt=_receipt("DELIVER", "PASS"), enrolled=True,
        motion_id="TAIL_WAGGING",
    )
    assert result.returncode != 0
    assert "BREATHING_REQUIRED" in result.stderr


def test_gate_migration_changes_only_the_decision_condition():
    def body(path: Path) -> list[str]:
        text = path.read_text()
        start = text.index("create or replace function public.publish_phase6_breathing")
        return [line.strip() for line in text[start:].splitlines()]

    base, gate = body(_BASE), body(_GATE)
    removed = [line for line in base if line not in gate]
    assert removed == []
    added = "\n".join(line for line in gate if line not in base)
    assert "_business_qa_cutover" in added and "'business-v1'" in added
    assert "'DELIVER', 'DELIVER_WITH_ADVISORY'" in added
    assert "revoke all on function public.publish_phase6_breathing" in _GATE.read_text()
