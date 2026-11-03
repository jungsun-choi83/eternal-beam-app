"""Prepare strict Archive intake lineage and queue the existing generation run."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from . import (
    archive_intake_service,
    archive_preparation_diagnostics,
    archive_storage,
    business_qa_user_test,
    pet_cutout_service,
    pet_generation_run_service,
    pet_reference_service,
)
from .cutout_errors import CutoutError


logger = logging.getLogger(__name__)
_LOCKS: dict[str, asyncio.Lock] = {}


@dataclass(frozen=True)
class ArchivePreparationResult:
    intake: dict[str, Any]
    original: pet_reference_service.PetReference | None = None
    cutout: pet_reference_service.PetReference | None = None
    cutout_generated: bool = False
    prepared_pairs: tuple[pet_cutout_service.PreparedCutout, ...] = ()
    failed_cutout_count: int = 0


class ArchivePreparationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _log_persistence_failure(
    application_id: str, stage: str, exc: Exception
) -> None:
    """Keep preparation logs structured and free of provider exception text."""
    logger.warning(
        "archive_preparation_stage",
        extra={
            "application_id": application_id,
            "stage": stage,
            "duration_ms": 0.0,
            "error_code": archive_preparation_diagnostics.safe_error_code(exc),
            "traceback": archive_preparation_diagnostics.redacted_traceback(exc),
        },
    )


def __reset_for_tests() -> None:
    _LOCKS.clear()


def generation_idempotency_key(content_id: str) -> str:
    cid = (content_id or "").strip()
    key = f"free-home:{cid}"
    if not cid or len(key) > 200:
        raise ArchivePreparationError(
            "ARCHIVE_GENERATION_KEY_INVALID",
            "The Archive generation idempotency key is invalid.",
        )
    return key


def _safe_failure(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, ArchivePreparationError):
        return exc.code, exc.message
    if isinstance(exc, CutoutError):
        return exc.code, "Archive cutout preparation failed."
    if isinstance(exc, business_qa_user_test.UserTestError):
        return exc.code, exc.message
    if isinstance(exc, pet_generation_run_service.PetGenerationRunError):
        return exc.code, "Archive generation could not be queued."
    if isinstance(exc, pet_reference_service.PetReferenceError):
        return exc.code, exc.message
    return (
        "ARCHIVE_GENERATION_PREPARATION_FAILED",
        "Archive generation preparation failed.",
    )


async def prepare_and_queue(
    archive_application_id: str,
) -> ArchivePreparationResult:
    """Legacy synchronous entry point used by controlled callers and tests."""
    app_id = (archive_application_id or "").strip()
    if not app_id:
        raise ArchivePreparationError(
            "ARCHIVE_APPLICATION_ID_INVALID", "Archive application ID is required."
        )

    # This avoids duplicate model work inside one process. The database claim
    # below is the cross-process authority.
    lock = _LOCKS.setdefault(app_id, asyncio.Lock())
    async with lock:
        current = await archive_intake_service.get_intake(app_id)
        if not current:
            raise ArchivePreparationError(
                "ARCHIVE_INTAKE_NOT_FOUND", "Archive intake metadata was not found."
            )
        if current.get("generation_run_id"):
            return ArchivePreparationResult(intake=current)

        claimed = await archive_intake_service.claim_preparation(app_id)
        if not claimed:
            current = await archive_intake_service.get_intake(app_id)
            if not current:
                raise ArchivePreparationError(
                    "ARCHIVE_INTAKE_NOT_FOUND", "Archive intake metadata was not found."
                )
            return ArchivePreparationResult(intake=current)

        return await _prepare_claimed(app_id, claimed)


async def process_claimed(claimed: dict[str, Any]) -> ArchivePreparationResult:
    """Process one durable preparation row already claimed by a worker."""
    app_id = str(claimed.get("archive_application_id") or "").strip()
    if not app_id:
        raise ArchivePreparationError(
            "ARCHIVE_APPLICATION_ID_INVALID", "Archive application ID is required."
        )
    lock = _LOCKS.setdefault(app_id, asyncio.Lock())
    async with lock:
        current = await archive_intake_service.get_intake(app_id)
        if not current:
            raise ArchivePreparationError(
                "ARCHIVE_INTAKE_NOT_FOUND", "Archive intake metadata was not found."
            )
        if current.get("generation_run_id"):
            return ArchivePreparationResult(intake=current)
        return await _prepare_claimed(app_id, claimed)


async def _prepare_claimed(
    app_id: str,
    claimed: dict[str, Any],
) -> ArchivePreparationResult:
    owner_id = str(claimed.get("owner_id") or "")
    content_id = str(claimed.get("content_id") or "")
    pet_id = str(claimed.get("pet_id") or "")
    lease_id = str(claimed.get("preparation_lease_id") or "") or None
    progress_stage = archive_intake_service.PREPARATION_STAGE_CLAIMED

    async def _progress(stage: str, progress: int) -> None:
        nonlocal progress_stage
        progress_stage = stage
        if lease_id:
            updated = await archive_intake_service.update_preparation_progress(
                app_id, lease_id, stage=stage, progress=progress
            )
            if updated is None:
                raise ArchivePreparationError(
                    "ARCHIVE_PREPARATION_LEASE_LOST",
                    "Archive preparation was claimed by another worker.",
                )

    if (
        owner_id != f"archive_{app_id}"
        or content_id != f"archive_{app_id}"
        or pet_id != pet_reference_service.pet_id_for_content(content_id)
    ):
        exc = ArchivePreparationError(
            "ARCHIVE_IDENTITY_MISMATCH",
            "Archive intake identity mapping is invalid.",
        )
        try:
            await archive_intake_service.mark_generation_failed(
                app_id,
                exc.code,
                lease_id=lease_id,
                preparation_stage=progress_stage,
            )
        except Exception:
            _log_persistence_failure(app_id, progress_stage, exc)
        raise exc

    prepared: pet_cutout_service.PreparedCutoutSet | None = None
    archive_bucket = archive_storage.archive_storage_bucket()
    try:
        await _progress("CUTOUT_PREPARATION", 10)
        with archive_storage.archive_scope(archive_bucket):
            prepared = await pet_cutout_service.prepare_strict_cutouts(
                user_id=owner_id,
                pet_id=pet_id,
                stage_observer=archive_preparation_diagnostics.observer(app_id),
            )
        await _progress("CUTOUT_PREPARED", 85)

        business_qa_user_test.require_admission(user_id=owner_id, pet_id=pet_id)
        await _progress("GENERATION_QUEUE", 95)

        # A future recovery may have persisted the run ID before leaving the
        # intake retryable. Never start a second paid run in that case.
        refreshed = await archive_intake_service.get_intake(app_id)
        existing_run_id = str((refreshed or {}).get("generation_run_id") or "")
        if existing_run_id:
            queued = await archive_intake_service.mark_generation_queued(
                app_id, existing_run_id, lease_id=lease_id
            )
            return ArchivePreparationResult(
                intake=queued,
                original=prepared.pairs[0].original,
                cutout=prepared.pairs[0].cutout,
                cutout_generated=any(pair.generated for pair in prepared.pairs),
                prepared_pairs=prepared.pairs,
                failed_cutout_count=len(prepared.failures),
            )

        run = await pet_generation_run_service.start_generation_run(
            user_id=owner_id,
            pet_id=pet_id,
            idempotency_key=generation_idempotency_key(content_id),
            storage_bucket=archive_bucket,
        )
        queued = await archive_intake_service.mark_generation_queued(
            app_id, run.id, lease_id=lease_id
        )
        return ArchivePreparationResult(
            intake=queued,
            original=prepared.pairs[0].original,
            cutout=prepared.pairs[0].cutout,
            cutout_generated=any(pair.generated for pair in prepared.pairs),
            prepared_pairs=prepared.pairs,
            failed_cutout_count=len(prepared.failures),
        )
    except Exception as exc:
        code, message = _safe_failure(exc)
        try:
            await archive_intake_service.mark_generation_failed(
                app_id,
                code,
                lease_id=lease_id,
                preparation_stage=progress_stage,
            )
        except Exception:
            _log_persistence_failure(app_id, progress_stage, exc)
        if isinstance(exc, ArchivePreparationError):
            raise
        raise ArchivePreparationError(code, message) from exc
