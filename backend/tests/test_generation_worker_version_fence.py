from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest

from backend.services import generation_worker_contract
from backend.services import pet_generation_run_service as runs
from backend.workers import pet_generation_worker as worker


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = REPO_ROOT / "supabase/migrations/20261110000000_generation_worker_version_fence.sql"


def _run(awaitable):
    return anyio.run(lambda: awaitable)


def test_claim_rpc_requires_current_worker_version_and_drops_legacy_signatures():
    sql = MIGRATION.read_text()
    assert "v_expected_worker_version constant text := 'pet-generation-worker-v2'" in sql
    assert generation_worker_contract.WORKER_CONTRACT_VERSION == "pet-generation-worker-v2"
    assert "GENERATION_WORKER_VERSION_MISMATCH" in sql
    for signature in (
        "text, int",
        "text, int, int",
        "text, int, int, text[]",
    ):
        assert f"drop function if exists public.claim_next_pet_generation_run({signature});" in sql
    assert "p_worker_version text" in sql
    assert "p_worker_hostname text" in sql
    assert "p_worker_pid integer" in sql


def test_claim_rpc_records_identity_atomically_with_the_claim():
    sql = MIGRATION.read_text()
    assert "create table if not exists public.pet_generation_run_claim_events" in sql
    assert "execution_token uuid not null" in sql
    assert "insert into public.pet_generation_run_claim_events" in sql
    assert "worker_id" in sql
    assert "worker_hostname" in sql
    assert "worker_pid" in sql
    assert "worker_version" in sql
    assert "previous_status" in sql


def test_old_worker_version_is_rejected_before_queue_access(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    with pytest.raises(runs.PetGenerationRunError) as caught:
        _run(
            runs._claim_next(
                "old-worker",
                worker_hostname="old-host",
                worker_pid=123,
                worker_version="pet-generation-worker-v1",
            )
        )
    assert caught.value.code == "WORKER_VERSION_MISMATCH"


def test_current_worker_sends_identity_and_version_to_claim_rpc(monkeypatch):
    recorded = {}

    class Query:
        def execute(self):
            return SimpleNamespace(data={"claimed": False})

    class Client:
        def rpc(self, name, params):
            recorded["name"] = name
            recorded["params"] = params
            return Query()

    monkeypatch.setattr(runs, "_use_db", lambda: True)
    monkeypatch.setattr(runs, "_supabase", lambda: Client())
    monkeypatch.setattr(runs.business_qa_user_test, "claim_pet_allowlist", lambda: None)

    assert _run(
        runs._claim_next(
            "current-worker",
            worker_hostname="current-host",
            worker_pid=456,
            worker_version=generation_worker_contract.WORKER_CONTRACT_VERSION,
        )
    ) is None
    assert recorded == {
        "name": "claim_next_pet_generation_run",
        "params": {
            "p_worker_id": "current-worker",
            "p_worker_hostname": "current-host",
            "p_worker_pid": 456,
            "p_worker_version": generation_worker_contract.WORKER_CONTRACT_VERSION,
            "p_lease_seconds": 300,
            "p_max_lease_recoveries": 2,
            "p_pet_allowlist": None,
        },
    }


def test_worker_run_once_supplies_process_identity(monkeypatch):
    recorded = {}

    async def fake_process(**kwargs):
        recorded.update(kwargs)
        return None

    monkeypatch.setattr(runs, "process_next_generation_run", fake_process)
    monkeypatch.setattr(worker.socket, "gethostname", lambda: "local-host")
    monkeypatch.setattr(worker.os, "getpid", lambda: 789)

    assert _run(worker.run_once("local-worker")) is None
    assert recorded == {
        "worker_id": "local-worker",
        "worker_hostname": "local-host",
        "worker_pid": 789,
        "worker_version": generation_worker_contract.WORKER_CONTRACT_VERSION,
    }

