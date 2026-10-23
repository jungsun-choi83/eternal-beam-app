"""Exercise companion scoping through the real durable run insertion path."""

import asyncio

import pytest

from backend.services import pet_generation_run_service as runs
from backend.services import premium_run_fulfillment as fulfillment
from .test_phase7c_generation_runs import (
    PET, USER, _clean, _run, seed_intake, storage,  # noqa: F401
)


def companion(source):
    return _run(fulfillment.ensure_system_companion(
        user_id=USER, pet_id=PET, action_id="COME_CLOSER", source_run_id=source,
        start_image_override={
            "bucket": "assets", "object_path": f"derived/{source}.png",
            "reference_id": f"ref-{source}",
        },
    ))


@pytest.mark.parametrize("old_status", [
    runs.STATUS_QUEUED, runs.STATUS_PUBLISHED, runs.STATUS_FAILED,
])
def test_distinct_sources_keep_distinct_runs_and_close_positions(storage, old_status):
    seed_intake()
    first = companion("come-closer-A")
    _run(runs._update(first, {"status": old_status}))
    second = companion("come-closer-B")
    assert second != first
    # Same-source retries still reuse exactly one companion, in any status.
    assert companion("come-closer-A") == first
    assert companion("come-closer-B") == second
    for source, rid in [("come-closer-A", first), ("come-closer-B", second)]:
        row = _run(runs._row_by_id(rid))
        assert row["idempotency_key"] == f"system-companion:RETURN_HOME:{PET}:{source}"
        assert row["provider_state"]["close_position_override"] == {
            "bucket": "assets", "object_path": f"derived/{source}.png",
            "reference_id": f"ref-{source}",
        }


@pytest.mark.parametrize("old_status", [runs.STATUS_PUBLISHED, runs.STATUS_FAILED])
def test_legacy_pet_only_companion_does_not_block_new_source(storage, old_status):
    seed_intake()
    old = _run(runs.start_generation_run(
        user_id=USER, pet_id=PET, motion_id="RETURN_HOME",
        request_kind=runs.REQUEST_FREE_HOME,
        idempotency_key=f"system-companion:RETURN_HOME:{PET}",
    ))
    _run(runs._update(old.id, {"status": old_status}))
    assert companion("new-come-closer") != old.id


def test_missing_source_cannot_fall_back_to_pet_only_key():
    with pytest.raises(ValueError, match="source generation run"):
        companion("")


def test_failed_missing_start_is_restored_and_requeued_atomically(storage, monkeypatch):
    seed_intake()
    rid = companion("source")
    _run(runs._update(rid, {
        "status": runs.STATUS_FAILED, "current_stage": runs.STAGE_CANONICAL,
        "provider_state": {"_operator": {"keep": True}},
        "last_error": {"code": "DERIVED_START_MISSING"}, "completed_at": "old",
    }))
    transitions = []
    transition = runs._transition

    async def record_transition(run_id, fields, **kwargs):
        transitions.append((fields, kwargs))
        return await transition(run_id, fields, **kwargs)

    monkeypatch.setattr(runs, "_transition", record_transition)
    assert companion("source") == rid
    row = _run(runs._row_by_id(rid))
    assert row["status"] == runs.STATUS_QUEUED
    assert row["retry_count"] == 1
    assert row["last_error"] is None and row["completed_at"] is None
    assert row["provider_state"]["close_position_override"]["object_path"] == "derived/source.png"
    assert row["provider_state"]["_operator"] == {"keep": True}
    assert transitions[0][0]["status"] == runs.STATUS_QUEUED
    assert transitions[0][0]["provider_state"] == row["provider_state"]
    assert transitions[0][1]["from_statuses"] == (runs.STATUS_FAILED,)
    assert len(runs._MOCK_RUNS) == 1


def test_retry_keeps_saved_full_override_when_one_is_already_present(storage):
    seed_intake()
    rid = companion("source")
    saved = {"bucket": "original", "object_path": "literal/final.png", "reference_id": "ref",
             "lineage": {"come_closer_run_id": "source", "extra": [1, 2]}}
    _run(runs._update(rid, {
        "status": runs.STATUS_FAILED, "last_error": {"code": "DERIVED_START_MISSING"},
        "provider_state": {"close_position_override": saved},
    }))
    assert companion("source") == rid
    assert _run(runs._row_by_id(rid))["provider_state"]["close_position_override"] == saved


@pytest.mark.parametrize("status", [
    runs.STATUS_QUEUED, runs.STATUS_RUNNING, runs.STATUS_WAITING_PROVIDER,
    runs.STATUS_RECOVERY_REQUIRED, runs.STATUS_PUBLISHED, runs.STATUS_CANCELLED,
])
def test_active_successful_and_cancelled_companions_are_never_revived(storage, status):
    seed_intake()
    rid = companion("source")
    _run(runs._update(rid, {"status": status, "last_error": {"code": "DERIVED_START_MISSING"}}))
    before = dict(_run(runs._row_by_id(rid)))
    assert companion("source") == rid
    assert _run(runs._row_by_id(rid)) == before
    assert len(runs._MOCK_RUNS) == 1


@pytest.mark.parametrize("code", ["MOTION_QA_FAILED", "PROVIDER_FAILED", "CANONICAL_NOT_COMPLETE"])
def test_other_failures_are_not_automatically_retried(storage, code):
    seed_intake()
    rid = companion("source")
    _run(runs._update(rid, {"status": runs.STATUS_FAILED, "last_error": {"code": code}}))
    assert companion("source") == rid
    row = _run(runs._row_by_id(rid))
    assert row["status"] == runs.STATUS_FAILED and row["retry_count"] == 0


def test_concurrent_revival_keeps_one_row_and_one_retry(storage, monkeypatch):
    seed_intake()
    rid = companion("source")
    _run(runs._update(rid, {
        "status": runs.STATUS_FAILED, "provider_state": {},
        "last_error": {"code": "DERIVED_START_MISSING"},
    }))
    transition = runs._transition

    async def interleave(*args, **kwargs):
        await asyncio.sleep(0)
        return await transition(*args, **kwargs)

    monkeypatch.setattr(runs, "_transition", interleave)

    async def concurrently():
        return await asyncio.gather(*[
            fulfillment.ensure_system_companion(
                user_id=USER, pet_id=PET, action_id="COME_CLOSER", source_run_id="source",
                start_image_override={"bucket": "b", "object_path": "literal/source.png", "reference_id": "ref"},
            ) for _ in range(5)
        ])

    assert _run(concurrently()) == [rid] * 5
    assert len(runs._MOCK_RUNS) == 1
    row = _run(runs._row_by_id(rid))
    assert row["status"] == runs.STATUS_QUEUED and row["retry_count"] == 1
