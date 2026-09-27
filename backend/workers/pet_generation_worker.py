"""Durable Phase 7D worker for BREATHING / FREE_HOME generation runs.

Run separately from FastAPI:
    python -m backend.workers.pet_generation_worker
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket

from dotenv import load_dotenv

logging.basicConfig(
    level=os.getenv("PET_GENERATION_WORKER_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pet_generation_worker")

_STOP = False


def _load_environment() -> None:
    # Deployment/process variables are authoritative.  Local dotenv files are
    # only defaults: allowing a checked-out .env.local to overwrite an
    # injected allowlist (or mock flag) can either block an approved recovery
    # or, worse, enable a provider unexpectedly.  Preserve the existing
    # process environment while still allowing the repository's dotenv files
    # to cascade over one another for local development.
    inherited = dict(os.environ)
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    load_dotenv(os.path.join(root, ".env"))
    for path in (
        os.path.join(root, "env.local"),
        os.path.join(root, ".env.local"),
        os.path.join(root, "backend", "env.local"),
        os.path.join(root, "backend", ".env.local"),
    ):
        if os.path.isfile(path):
            load_dotenv(path, override=True)
    os.environ.update(inherited)


def _stop(signum, frame) -> None:  # noqa: ARG001
    global _STOP
    # Only flip the flag here. The loop below finishes whatever tick is
    # already in flight (its lease is fenced by execution_token and kept
    # alive by _LeaseHeartbeater) and then simply stops claiming new work —
    # no DB write happens from the signal handler itself, so a slow or
    # re-entrant signal can never corrupt a lease.
    logger.info("shutdown signal received (signum=%s); stopping after current tick", signum)
    _STOP = True


async def run_once(worker_id: str):
    from ..services import pet_generation_run_service

    return await pet_generation_run_service.process_next_generation_run(worker_id=worker_id)


async def _run() -> None:
    global _STOP
    worker_id = os.getenv("PET_GENERATION_WORKER_ID") or f"generation-{socket.gethostname()}-{os.getpid()}"
    poll_seconds = max(1.0, float(os.getenv("PET_GENERATION_WORKER_POLL_SEC", "10")))
    enabled = os.getenv("PET_GENERATION_WORKER_ENABLED", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not enabled:
        raise RuntimeError("PET_GENERATION_WORKER_ENABLED=1 is required")

    heartbeat_every = max(1, int(os.getenv("PET_GENERATION_WORKER_HEARTBEAT_TICKS", "30")))
    logger.info(
        "Phase 7D worker started: worker_id=%s pid=%s poll_sec=%s lease_sec=%s "
        "generation_mock=%s luma_mock=%s",
        worker_id,
        os.getpid(),
        poll_seconds,
        os.getenv("GENERATION_RUN_LEASE_SECONDS", "300"),
        os.getenv("GENERATION_MOCK", "0"),
        os.getenv("LUMA_MOCK", "0"),
    )
    idle_ticks = 0
    while not _STOP:
        try:
            result = await run_once(worker_id)
            if result:
                idle_ticks = 0
                # A run was claimed and advanced this tick (including one that
                # was released back to WAITING_PROVIDER). Its lease/worker_id
                # are already cleared by _progress(), so another ready run
                # (QUEUED, or a different WAITING_PROVIDER run whose
                # next_attempt_at is due) may already be claimable. Loop
                # immediately instead of blocking this worker on a fixed
                # sleep; claim_next_pet_generation_run's next_attempt_at
                # check is what actually gates re-polling the same run, so
                # no timer is needed here to enforce that.
                logger.info(
                    "run advanced: run_id=%s stage=%s status=%s",
                    result.id,
                    result.current_stage,
                    result.status,
                )
                continue
        except Exception:
            logger.exception("generation worker tick failed")
        # Nothing was claimable (or the tick errored): back off before
        # re-polling so an idle/erroring worker doesn't busy-loop the claim
        # RPC.
        idle_ticks += 1
        if idle_ticks % heartbeat_every == 0:
            # Liveness signal for the deploy logs: on an idle queue this is
            # the only line that proves the process is still up and polling,
            # as opposed to having exited or hung silently.
            logger.info("worker alive: worker_id=%s idle_ticks=%s", worker_id, idle_ticks)
        await asyncio.sleep(poll_seconds)
    logger.info("Phase 7D worker stopped: worker_id=%s", worker_id)


def main() -> None:
    _load_environment()
    logger.info("pet_generation_worker booting: pid=%s", os.getpid())
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
