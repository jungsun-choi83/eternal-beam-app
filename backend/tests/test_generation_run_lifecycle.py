"""Stale-run resurrection guard (migration 20261032).

The worker must only ever pick up QUEUED / due WAITING_PROVIDER / stale-lease
RUNNING rows, and stale-lease recovery is bounded. FAILED / CANCELLED /
RECOVERY_REQUIRED / PUBLISHED are left alone until a *user* retries. Every
provider boundary is mocked (PipelineHarness).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI

from backend.routers import generation_runs_v1
from backend.services import (
    motion_video_service,
    pet_generation_run_service as runs,
    pet_reference_service,
    pet_registry,
)

from .conftest import ASGITestClient, make_jpeg_bytes
from .test_phase7c_generation_runs import (
    CID,
    PET,
    USER,
    PipelineHarness,
    _run,
    seed_intake,
    start,
    work,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("SEEDANCE_TRANSPORT", "runway")
    runs.__reset_for_tests()
    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()
    yield
    runs.__reset_for_tests()
    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()


@pytest.fixture
def storage(monkeypatch):
    from backend.services import supabase_assets

    uploads = []

    async def upload(path, data, content_type):
        uploads.append(path)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", upload)
    return uploads


def row(run_id: str) -> dict:
    return next(r for r in runs._MOCK_RUNS if r["id"] == run_id)


def crash_worker_on(run_id: str) -> None:
    """Simulate an OOM/SIGKILL: the lease was taken, no _fail() ever ran, and
    the lease has since expired."""
    r = row(run_id)
    assert r["status"] == runs.STATUS_RUNNING
    r["lease_expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()


def claim(worker_id="crashy-worker"):
    return _run(runs._claim_next(worker_id))


def harness_for_any_pet(harness: PipelineHarness) -> PipelineHarness:
    """PipelineHarness hard-asserts pet_id == PET in _capture; the fresh-pet
    tests drive a second pet through the same mocked pipeline."""
    harness._capture = lambda stage, kwargs: harness.calls.append(stage)  # type: ignore[method-assign]
    return harness


def seed_second_pet():
    other_cid = f"{CID}-fresh-pet"
    other_pet = f"pet_{other_cid}"
    original = _run(
        pet_reference_service.record_original(
            user_id=USER, content_id=other_cid, data=make_jpeg_bytes(), mime_type="image/jpeg",
        )
    )
    _run(
        pet_reference_service.record_derived(
            user_id=USER,
            content_id=other_cid,
            object_path=f"{USER}/{other_cid}/references/cutout.png",
            derived_kind="cutout_reference",
            parent_reference_id=original.id,
            mime_type="image/png",
        )
    )
    return other_pet


# ── 1. run fails and UI requires retry → never reclaimed ──────────────────────


def test_failed_run_is_never_reclaimed_by_the_worker(storage, monkeypatch):
    seed_intake()
    PipelineHarness(monkeypatch, motion_status=motion_video_service.STATUS_FAILED)
    start(key="fails")
    failed = work()
    assert failed.status == runs.STATUS_FAILED
    assert failed.execution_token is None and failed.lease_expires_at is None
    assert failed.completed_at is not None

    for _ in range(3):
        assert work() is None
    assert row(failed.id)["status"] == runs.STATUS_FAILED


# ── 2. explicit cancel → never reclaimed ───────────────────────────────────────


def test_cancelled_run_is_never_reclaimed_by_the_worker(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    queued = start(key="cancel-queued")

    cancelled = _run(runs.cancel_generation_run(user_id=USER, run_id=queued.id, reason="user_cancelled"))
    assert cancelled.status == runs.STATUS_CANCELLED
    assert cancelled.execution_token is None
    assert cancelled.lease_expires_at is None
    assert cancelled.worker_id is None
    assert cancelled.last_error["code"] == runs.ERROR_RUN_CANCELLED
    assert cancelled.last_error["reason"] == "user_cancelled"

    assert work() is None
    assert harness.counts["identity_build"] == 0
    # idempotent — cancelling again does not change anything
    again = _run(runs.cancel_generation_run(user_id=USER, run_id=queued.id))
    assert again.status == runs.STATUS_CANCELLED


def test_cancel_while_running_fences_the_worker_at_its_next_write(storage, monkeypatch):
    """The worker is mid-run (holds a lease). Cancel clears the token; the
    worker's next fenced write loses the lease and it stops without
    overwriting CANCELLED with FAILED."""
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    queued = start(key="cancel-running")
    running = claim("worker-a")
    assert running.status == runs.STATUS_RUNNING and running.execution_token

    cancelled = _run(runs.cancel_generation_run(user_id=USER, run_id=queued.id, reason="poll_timeout"))
    assert cancelled.status == runs.STATUS_CANCELLED

    # worker-a continues with its stale token
    result = _run(runs._execute(running))
    assert result.status == runs.STATUS_CANCELLED
    assert row(queued.id)["status"] == runs.STATUS_CANCELLED
    assert row(queued.id)["last_error"]["reason"] == "poll_timeout"
    assert harness.counts["publication"] == 0
    assert work() is None


def test_cancel_of_a_recovery_required_run_stops_it(storage, monkeypatch):
    seed_intake()
    PipelineHarness(monkeypatch)
    queued = start(key="cancel-recovery")
    r = row(queued.id)
    r["status"] = runs.STATUS_RECOVERY_REQUIRED
    cancelled = _run(runs.cancel_generation_run(user_id=USER, run_id=queued.id))
    assert cancelled.status == runs.STATUS_CANCELLED
    assert work() is None


def test_cancel_by_another_user_is_rejected(storage, monkeypatch):
    seed_intake()
    PipelineHarness(monkeypatch)
    queued = start(key="cancel-mallory")
    with pytest.raises(runs.PetGenerationRunError) as error:
        _run(runs.cancel_generation_run(user_id="mallory@test", run_id=queued.id))
    assert error.value.code == "PET_NOT_OWNED"
    assert row(queued.id)["status"] == runs.STATUS_QUEUED


# ── 3. worker crash during a recoverable RUNNING run → bounded recovery ───────


def test_worker_crash_is_recovered_once_the_lease_expires(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    queued = start(key="crash-once")

    first = claim("worker-a")  # claimed, then the process dies
    assert first.lease_recoveries == 0
    assert work() is None  # lease still valid → nobody else may take it
    crash_worker_on(queued.id)

    recovered = work()
    assert recovered.id == queued.id
    assert recovered.status == runs.STATUS_PUBLISHED
    assert recovered.lease_recoveries == 1
    assert harness.counts["publication"] == 1


def test_recovery_budget_exhausted_fails_the_run_instead_of_resurrecting_forever(
    storage, monkeypatch
):
    monkeypatch.setenv("GENERATION_RUN_MAX_LEASE_RECOVERIES", "2")
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    queued = start(key="crash-loop")

    claim()  # original claim
    crash_worker_on(queued.id)
    assert claim().lease_recoveries == 1  # recovery #1
    crash_worker_on(queued.id)
    assert claim().lease_recoveries == 2  # recovery #2 (last allowed)
    crash_worker_on(queued.id)

    # The (max+1)-th hand-out is turned into FAILED without doing any work.
    stopped = work()
    assert stopped.id == queued.id
    assert stopped.status == runs.STATUS_FAILED
    assert stopped.last_error["code"] == runs.ERROR_WORKER_RECOVERY_EXHAUSTED
    assert stopped.last_error["lease_recoveries"] == 2
    assert stopped.execution_token is None and stopped.lease_expires_at is None
    assert harness.counts["identity_build"] == 0

    for _ in range(3):
        assert work() is None
    assert row(queued.id)["status"] == runs.STATUS_FAILED


def test_self_heal_closes_a_run_whose_exhausted_hand_out_also_died(storage, monkeypatch):
    monkeypatch.setenv("GENERATION_RUN_MAX_LEASE_RECOVERIES", "1")
    seed_intake()
    PipelineHarness(monkeypatch)
    queued = start(key="self-heal")
    claim()
    r = row(queued.id)
    r["lease_recoveries"] = 5  # beyond the cap, lease dead: nothing may claim it
    crash_worker_on(queued.id)

    assert claim() is None
    assert r["status"] == runs.STATUS_FAILED
    assert r["last_error"]["code"] == runs.ERROR_WORKER_RECOVERY_EXHAUSTED
    assert r["execution_token"] is None


# ── 4. fresh pet while an older failed / stale run exists ─────────────────────


def test_fresh_pet_is_processed_while_old_failed_run_stays_failed(storage, monkeypatch):
    seed_intake()
    other_pet = seed_second_pet()
    PipelineHarness(monkeypatch, motion_status=motion_video_service.STATUS_FAILED)
    old = start(key="old-pet")
    assert work().status == runs.STATUS_FAILED

    harness = harness_for_any_pet(PipelineHarness(monkeypatch))  # healthy pipeline for the new pet
    fresh = _run(
        runs.start_generation_run(
            user_id=USER, pet_id=other_pet, motion_id="BREATHING",
            request_kind="FREE_HOME", idempotency_key="fresh-pet",
        )
    )
    processed = work()
    assert processed.id == fresh.id
    assert processed.status == runs.STATUS_PUBLISHED
    assert row(old.id)["status"] == runs.STATUS_FAILED
    assert harness.counts["publication"] == 1
    assert work() is None


def test_fresh_queued_run_is_claimed_before_a_stale_lease_recovery(storage, monkeypatch):
    """Ordering: QUEUED (fresh intent) beats a crashed RUNNING row even though
    the crashed row is older — a crash-looping pet can no longer starve a
    new upload."""
    seed_intake()
    other_pet = seed_second_pet()
    harness_for_any_pet(PipelineHarness(monkeypatch))
    old = start(key="old-pet")
    claim("worker-a")
    crash_worker_on(old.id)

    fresh = _run(
        runs.start_generation_run(
            user_id=USER, pet_id=other_pet, motion_id="BREATHING",
            request_kind="FREE_HOME", idempotency_key="fresh-pet",
        )
    )
    assert row(old.id)["updated_at"] <= row(fresh.id)["updated_at"]

    first = work()
    assert first.id == fresh.id and first.status == runs.STATUS_PUBLISHED
    second = work()  # then the bounded recovery of the old one
    assert second.id == old.id and second.lease_recoveries == 1
    assert work() is None


# ── 5. retry ──────────────────────────────────────────────────────────────────


def test_retry_requeues_only_that_run_and_resets_the_recovery_budget(storage, monkeypatch):
    monkeypatch.setenv("GENERATION_RUN_MAX_LEASE_RECOVERIES", "0")
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    queued = start(key="retry-me")
    claim()
    crash_worker_on(queued.id)
    exhausted = work()
    assert exhausted.status == runs.STATUS_FAILED
    assert exhausted.lease_recoveries == 1
    assert work() is None

    retried = _run(runs.retry_generation_run(user_id=USER, run_id=queued.id))
    assert retried.id == queued.id
    assert retried.status == runs.STATUS_QUEUED
    assert retried.lease_recoveries == 0
    assert retried.retry_count == 1
    assert retried.last_error is None
    assert retried.completed_at is None
    assert len(runs._MOCK_RUNS) == 1  # no second row

    done = work()
    assert done.id == queued.id and done.status == runs.STATUS_PUBLISHED
    assert harness.counts["publication"] == 1
    assert work() is None


def test_retry_of_a_cancelled_run_requeues_it(storage, monkeypatch):
    seed_intake()
    PipelineHarness(monkeypatch)
    queued = start(key="retry-cancelled")
    _run(runs.cancel_generation_run(user_id=USER, run_id=queued.id, reason="poll_timeout"))
    retried = _run(runs.retry_generation_run(user_id=USER, run_id=queued.id))
    assert retried.status == runs.STATUS_QUEUED
    assert work().status == runs.STATUS_PUBLISHED


def test_retry_of_an_active_run_does_not_reset_it(storage, monkeypatch):
    seed_intake()
    PipelineHarness(monkeypatch)
    queued = start(key="retry-active")
    running = claim("worker-a")
    same = _run(runs.retry_generation_run(user_id=USER, run_id=queued.id))
    assert same.status == runs.STATUS_RUNNING
    assert same.execution_token == running.execution_token


# ── 6. completed runs stay terminal ──────────────────────────────────────────


def test_published_run_is_terminal_for_worker_cancel_and_retry(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    queued = start(key="done")
    assert work().status == runs.STATUS_PUBLISHED
    assert work() is None

    assert _run(runs.cancel_generation_run(user_id=USER, run_id=queued.id)).status == runs.STATUS_PUBLISHED
    assert _run(runs.retry_generation_run(user_id=USER, run_id=queued.id)).status == runs.STATUS_PUBLISHED
    assert harness.counts["publication"] == 1
    assert work() is None


# ── 7. claim exclusions ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status",
    [runs.STATUS_CANCELLED, runs.STATUS_FAILED, runs.STATUS_RECOVERY_REQUIRED, runs.STATUS_PUBLISHED],
)
def test_claim_never_picks_non_claimable_statuses(storage, monkeypatch, status):
    seed_intake()
    PipelineHarness(monkeypatch)
    queued = start(key=f"excluded-{status}")
    r = row(queued.id)
    r["status"] = status
    r["execution_token"] = None
    r["lease_expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert claim() is None
    assert r["status"] == status


def test_claim_migration_excludes_dead_states_and_caps_recovery():
    root = Path(__file__).resolve().parents[2]
    sql = (root / "supabase/migrations/20261032000000_generation_run_stale_resurrection.sql").read_text()
    assert "add column if not exists lease_recoveries int not null default 0" in sql
    assert "drop function if exists public.claim_next_pet_generation_run(text, int);" in sql
    assert "p_max_lease_recoveries int default 2" in sql
    assert "and lease_recoveries <= v_max" in sql
    assert "and lease_recoveries > v_max" in sql  # self-heal
    assert "'WORKER_RECOVERY_EXHAUSTED'" in sql
    # claim predicate: only these three ways in
    assert "status = 'QUEUED'" in sql
    assert "(status = 'WAITING_PROVIDER' and coalesce(next_attempt_at, now()) <= now())" in sql
    assert "status = 'RUNNING'\n       and lease_expires_at <= now()" in sql
    for dead in ("'FAILED'", "'CANCELLED'", "'RECOVERY_REQUIRED'", "'PUBLISHED'"):
        # dead states appear only in comments / the self-heal target, never as a
        # claimable predicate branch
        assert f"status = {dead}\n" not in sql.split("select * into v_run")[1].split("for update")[0]
    assert "when 'WAITING_PROVIDER' then 0" in sql
    assert "when 'QUEUED' then 1" in sql
    assert "else 2" in sql
    assert "to service_role" in sql


# ── premium reservation release on cancel ─────────────────────────────────────


def test_cancel_reconciles_premium_reservation(storage, monkeypatch):
    seed_intake()
    PipelineHarness(monkeypatch)
    from backend.services import premium_run_fulfillment

    reconciled = []

    async def fake_reconcile(**kwargs):
        reconciled.append(kwargs)
        return True

    monkeypatch.setattr(premium_run_fulfillment, "reconcile_failed_run", fake_reconcile)
    premium = _run(
        runs.start_generation_run(
            user_id=USER,
            pet_id=PET,
            motion_id=runs.premium_motion_finalization.PREMIUM_MOTIONS[0],
            request_kind=runs.REQUEST_PREMIUM_PRODUCT,
            idempotency_key="premium-cancel",
            reservation_ledger_id="ledger-1",
            credits_reserved=3,
        )
    )
    cancelled = _run(runs.cancel_generation_run(user_id=USER, run_id=premium.id))
    assert cancelled.status == runs.STATUS_CANCELLED
    assert reconciled and reconciled[0]["reservation_ledger_id"] == "ledger-1"


# ── API surface ───────────────────────────────────────────────────────────────


def test_cancel_and_retry_endpoints(storage, monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    seed_intake()
    PipelineHarness(monkeypatch)
    app = FastAPI()
    app.include_router(generation_runs_v1.router, prefix="/api")
    client = ASGITestClient(app)
    auth = {"Authorization": f"Bearer test:{USER}"}

    created = client.post(
        "/api/v1/pet/generation-runs", headers=auth,
        json={"pet_id": PET, "idempotency_key": "api-cancel"},
    )
    run_id = created.json()["run_id"]
    assert created.json()["lease_recoveries"] == 0

    cancelled = client.post(
        f"/api/v1/pet/generation-runs/{run_id}/cancel", headers=auth,
        json={"reason": "poll_timeout"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["last_error"]["reason"] == "poll_timeout"
    assert "execution_token" not in cancelled.json()
    assert work() is None

    retried = client.post(f"/api/v1/pet/generation-runs/{run_id}/retry", headers=auth)
    assert retried.status_code == 200
    assert retried.json()["status"] == "QUEUED"
    assert retried.json()["run_id"] == run_id
    assert work().status == runs.STATUS_PUBLISHED

    # body is optional
    no_body = client.post(f"/api/v1/pet/generation-runs/{run_id}/cancel", headers=auth)
    assert no_body.status_code == 200
    assert no_body.json()["status"] == "PUBLISHED"  # terminal stays terminal
