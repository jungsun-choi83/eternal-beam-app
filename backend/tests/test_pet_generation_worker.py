"""Worker-loop throughput tests: a WAITING_PROVIDER tick must release the
worker immediately instead of blocking it on a fixed sleep, while an idle
worker (nothing claimable) must still back off rather than busy-loop.
"""

from __future__ import annotations

from types import SimpleNamespace

import anyio
import pytest

from backend.workers import pet_generation_worker as worker


def _run(awaitable):
    return anyio.run(lambda: awaitable)


def _fake_run(status: str, run_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=run_id, current_stage="MOTION_GENERATION", status=status)


@pytest.fixture(autouse=True)
def _reset_stop_flag():
    worker._STOP = False
    yield
    worker._STOP = False


def test_waiting_run_releases_worker_for_another_ready_run(monkeypatch):
    """A WAITING_PROVIDER tick must not sleep before the loop tries to claim
    the next ready run; the queue only goes idle (and sleeps) once nothing
    is left to claim.
    """
    monkeypatch.setenv("PET_GENERATION_WORKER_ENABLED", "1")
    monkeypatch.setenv("PET_GENERATION_WORKER_ID", "test-worker")
    monkeypatch.setenv("PET_GENERATION_WORKER_POLL_SEC", "10")

    ticks = []
    # Tick 1: worker picks up a run that is still waiting on its provider and
    # is released. Tick 2: a different, ready run is claimed and finishes.
    # Tick 3: nothing left to claim -> idle.
    results = [
        _fake_run("WAITING_PROVIDER", "waiting-run"),
        _fake_run("PUBLISHED", "ready-run"),
    ]

    async def fake_run_once(worker_id):
        ticks.append(worker_id)
        if results:
            return results.pop(0)
        worker._STOP = True
        return None

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(worker, "run_once", fake_run_once)
    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)

    _run(worker._run())

    assert ticks == ["test-worker", "test-worker", "test-worker"]
    # Exactly one sleep: the idle tick after the queue drained. Nothing
    # slept between the WAITING_PROVIDER release and the next ready run.
    assert sleep_calls == [10.0]


def test_idle_worker_still_backs_off_instead_of_busy_looping(monkeypatch):
    """Removing the WAITING_PROVIDER sleep must not turn an empty queue into
    a busy-loop against the claim RPC.
    """
    monkeypatch.setenv("PET_GENERATION_WORKER_ENABLED", "1")
    monkeypatch.setenv("PET_GENERATION_WORKER_POLL_SEC", "5")

    ticks = {"n": 0}

    async def fake_run_once(worker_id):
        ticks["n"] += 1
        if ticks["n"] >= 2:
            worker._STOP = True
        return None

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(worker, "run_once", fake_run_once)
    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)

    _run(worker._run())

    assert ticks["n"] == 2
    assert sleep_calls == [5.0, 5.0]
