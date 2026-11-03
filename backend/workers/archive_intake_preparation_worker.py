"""Durable worker for Archive strict-cutout preparation.

Run separately from FastAPI:
    python -m backend.workers.archive_intake_preparation_worker
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket

from ..services import archive_intake_preparation_service, archive_intake_service, archive_preparation_diagnostics


logger = logging.getLogger("archive_intake_preparation_worker")
_STOP = False


def _single_application_id() -> str | None:
    raw = os.getenv("ARCHIVE_PREPARATION_TEST_APPLICATION_ID")
    if raw is None:
        return None
    application_id = raw.strip()
    if not application_id:
        raise RuntimeError(
            "ARCHIVE_PREPARATION_TEST_APPLICATION_ID must be non-empty when set"
        )
    return application_id


async def _heartbeat(application_id: str, lease_id: str, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        updated = await archive_intake_service.heartbeat_preparation(
            application_id, lease_id
        )
        if updated is None:
            return


async def run_once(worker_id: str):
    allowed_application_id = _single_application_id()
    claimed = await archive_intake_service.claim_next_preparation(
        worker_id,
        application_id=allowed_application_id,
    )
    if (
        allowed_application_id is not None
        and claimed is not None
        and claimed.get("archive_application_id") != allowed_application_id
    ):
        raise RuntimeError(
            "Archive single-application restriction was not enforced; refusing to process."
        )
    if not claimed:
        return None

    archive_preparation_diagnostics.report_initial_queue_wait(claimed)
    application_id = str(claimed.get("archive_application_id") or "")
    lease_id = str(claimed.get("preparation_lease_id") or "")
    heartbeat_task = None
    if application_id and lease_id:
        lease_seconds = archive_intake_service._preparation_lease_seconds()
        heartbeat_task = asyncio.create_task(
            _heartbeat(application_id, lease_id, max(1.0, lease_seconds / 3.0))
        )
    try:
        return await archive_intake_preparation_service.process_claimed(claimed)
    except archive_intake_preparation_service.ArchivePreparationError as exc:
        logger.warning(
            "Archive preparation failed application=%s code=%s",
            application_id,
            exc.code,
        )
        return await archive_intake_service.get_intake(application_id)
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)


async def _run() -> None:
    global _STOP
    enabled = os.getenv("ARCHIVE_PREPARATION_WORKER_ENABLED", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not enabled:
        raise RuntimeError("ARCHIVE_PREPARATION_WORKER_ENABLED=1 is required")

    worker_id = os.getenv("ARCHIVE_PREPARATION_WORKER_ID") or (
        f"archive-preparation-{socket.gethostname()}-{os.getpid()}"
    )
    poll_seconds = max(1.0, float(os.getenv("ARCHIVE_PREPARATION_WORKER_POLL_SEC", "5")))
    while not _STOP:
        result = await run_once(worker_id)
        if result is None:
            await asyncio.sleep(poll_seconds)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("ARCHIVE_PREPARATION_WORKER_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(_run())


if __name__ == "__main__":
    main()
