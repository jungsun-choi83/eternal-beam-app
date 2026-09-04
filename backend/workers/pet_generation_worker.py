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

    logger.info("Phase 7D worker started: worker_id=%s", worker_id)
    while not _STOP:
        try:
            result = await run_once(worker_id)
            if result:
                logger.info(
                    "run advanced: run_id=%s stage=%s status=%s",
                    result.id,
                    result.current_stage,
                    result.status,
                )
                if result.status == "WAITING_PROVIDER":
                    await asyncio.sleep(poll_seconds)
                continue
        except Exception:
            logger.exception("generation worker tick failed")
        await asyncio.sleep(poll_seconds)
    logger.info("Phase 7D worker stopped")


def main() -> None:
    _load_environment()
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
