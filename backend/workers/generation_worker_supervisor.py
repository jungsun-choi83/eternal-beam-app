"""Run both durable workers in the Render generation-worker service."""

from __future__ import annotations

import logging
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence


logger = logging.getLogger("generation_worker_supervisor")

WORKER_MODULES = (
    "backend.workers.archive_intake_preparation_worker",
    "backend.workers.pet_generation_worker",
)
POLL_INTERVAL_SECONDS = 0.25
SHUTDOWN_TIMEOUT_SECONDS = 25.0


def _stop_children(
    children: Sequence[tuple[str, subprocess.Popen]],
    *,
    timeout: float,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Ask children to stop, then kill any child that misses the grace period."""

    live = []
    for name, child in children:
        try:
            if child.poll() is None:
                child.terminate()
                live.append((name, child))
        except OSError:
            logger.exception("Could not send SIGTERM to %s worker", name)

    deadline = monotonic() + max(0.0, timeout)
    while live and monotonic() < deadline:
        live = [(name, child) for name, child in live if child.poll() is None]
        if live:
            sleep(min(POLL_INTERVAL_SECONDS, max(0.0, deadline - monotonic())))

    for name, child in live:
        if child.poll() is None:
            logger.error("%s worker did not stop in time; sending SIGKILL", name)
            try:
                child.kill()
            except OSError:
                logger.exception("Could not send SIGKILL to %s worker", name)

    for name, child in children:
        try:
            child.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            logger.error("Could not reap %s worker after shutdown", name)
        except OSError:
            logger.exception("Could not reap %s worker", name)


def run_supervisor(
    *,
    shutdown_event: threading.Event,
    popen_factory: Callable[..., subprocess.Popen] | None = None,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    shutdown_timeout: float = SHUTDOWN_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> int:
    """Start and monitor both workers; any unexpected exit fails the service."""

    spawn = popen_factory or subprocess.Popen
    children: list[tuple[str, subprocess.Popen]] = []

    try:
        for module in WORKER_MODULES:
            command = [sys.executable, "-m", module]
            child = spawn(command)
            children.append((module, child))
            logger.info("Started worker module=%s pid=%s", module, child.pid)
    except Exception:
        logger.exception("Could not start both worker processes")
        _stop_children(
            children,
            timeout=shutdown_timeout,
            sleep=sleep,
            monotonic=monotonic,
        )
        return 1

    try:
        while not shutdown_event.is_set():
            for module, child in children:
                exit_code = child.poll()
                if exit_code is not None:
                    logger.error(
                        "Worker exited unexpectedly module=%s exit_code=%s",
                        module,
                        exit_code,
                    )
                    _stop_children(
                        children,
                        timeout=shutdown_timeout,
                        sleep=sleep,
                        monotonic=monotonic,
                    )
                    return 1
            sleep(poll_interval)
    except Exception:
        logger.exception("Worker supervisor failed while monitoring children")
        _stop_children(
            children,
            timeout=shutdown_timeout,
            sleep=sleep,
            monotonic=monotonic,
        )
        return 1

    logger.info("Shutdown requested; stopping both worker processes")
    _stop_children(
        children,
        timeout=shutdown_timeout,
        sleep=sleep,
        monotonic=monotonic,
    )
    return 0


def main() -> int:
    shutdown_event = threading.Event()

    def request_shutdown(signum: int, _frame: object) -> None:
        logger.info("Received signal=%s; requesting worker shutdown", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    return run_supervisor(shutdown_event=shutdown_event)


if __name__ == "__main__":
    raise SystemExit(main())
