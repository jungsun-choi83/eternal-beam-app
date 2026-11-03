"""Safe, stage-level diagnostics for Archive strict-cutout preparation."""

from __future__ import annotations

import logging
import os
import re
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from typing import Callable, Iterator


logger = logging.getLogger(__name__)

STAGES = frozenset(
    {
        "REFERENCE_LIST",
        "PRIVATE_DOWNLOAD",
        "YOLO",
        "SAM2",
        "VITMATTE",
        "PNG_VALIDATE",
        "PRIVATE_UPLOAD",
        "DERIVED_REFERENCE_PERSIST",
    }
)
_SAFE_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,199}$")
_FALLBACK_ERROR_CODE = "ARCHIVE_GENERATION_PREPARATION_FAILED"

StageObserver = Callable[[str, float, Exception | None], None]


def safe_error_code(exc: Exception | None) -> str:
    if exc is None:
        return "OK"
    raw = str(getattr(exc, "code", "") or "").strip() if exc else ""
    if _SAFE_ERROR_CODE_RE.fullmatch(raw):
        return raw
    return _FALLBACK_ERROR_CODE


def redacted_traceback(exc: Exception) -> str:
    """Return traceback locations and type, never exception text or source lines."""
    frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    locations = "\n".join(
        f'  File "<redacted>/{os.path.basename(frame.filename)}", '
        f"line {frame.lineno}, in {frame.name}"
        for frame in frames
    )
    return f"Traceback (most recent call last):\n{locations}\n{type(exc).__name__}"


def observer(application_id: str) -> StageObserver:
    app_id = (application_id or "").strip() or "unknown"

    def report(stage: str, duration_s: float, exc: Exception | None) -> None:
        if stage not in STAGES or os.getenv("ETERNAL_BEAM_PIPELINE_TIMING", "0").strip() != "1":
            return
        fields = {
            "event": "eternal_beam_pipeline_timing",
            "application_id": app_id,
            "generation_run_id": None,
            "stage": stage,
            "duration_ms": round(max(0.0, duration_s) * 1000.0, 2),
            "error_code": safe_error_code(exc),
            "traceback": redacted_traceback(exc) if exc else None,
        }
        logger.warning("eternal_beam_pipeline_timing", extra=fields) if exc else logger.info(
            "eternal_beam_pipeline_timing", extra=fields
        )

    return report


def report_initial_queue_wait(claimed: dict) -> None:
    """Log first-attempt intake age at claim; later retries lack a queued_at field."""
    if os.getenv("ETERNAL_BEAM_PIPELINE_TIMING", "0").strip() != "1":
        return
    try:
        if int(claimed.get("preparation_attempt") or 0) != 1:
            return
        queued_at = datetime.fromisoformat(str(claimed.get("created_at") or "").replace("Z", "+00:00"))
        claimed_at = datetime.fromisoformat(str(claimed.get("updated_at") or "").replace("Z", "+00:00"))
        duration_ms = max(0.0, (claimed_at - queued_at).total_seconds() * 1000.0)
    except (TypeError, ValueError):
        return
    logger.info(
        "eternal_beam_pipeline_timing",
        extra={
            "event": "eternal_beam_pipeline_timing",
            "application_id": str(claimed.get("archive_application_id") or "unknown"),
            "generation_run_id": None,
            "stage": "archive_preparation_queue_wait",
            "duration_ms": round(duration_ms, 2),
            "stage_started_at": queued_at.isoformat(),
            "stage_completed_at": claimed_at.isoformat(),
        },
    )


@contextmanager
def observe_stage(
    stage_observer: StageObserver | None, stage: str
) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    except Exception as exc:
        if stage_observer:
            try:
                stage_observer(stage, time.perf_counter() - started, exc)
            except Exception:
                pass
        raise
    else:
        if stage_observer:
            try:
                stage_observer(stage, time.perf_counter() - started, None)
            except Exception:
                pass
