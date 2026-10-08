"""Prepare strict Archive intake lineage and queue the existing generation run."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from . import (
    archive_intake_service,
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
    """Prepare all possible strict pairs and queue the normal generation run."""
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

        owner_id = str(claimed.get("owner_id") or "")
        content_id = str(claimed.get("content_id") or "")
        pet_id = str(claimed.get("pet_id") or "")
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
                await archive_intake_service.mark_generation_failed(app_id, exc.code)
            except Exception:
                logger.exception(
                    "Could not persist Archive identity failure (application=%s)",
                    app_id,
                )
            raise exc

        prepared: pet_cutout_service.PreparedCutoutSet | None = None
        try:
            prepared = await pet_cutout_service.prepare_strict_cutouts(
                user_id=owner_id, pet_id=pet_id
            )

            # Keep the explicit admission checkpoint in this orchestration.
            # start_generation_run() checks it again and stamps its receipt.
            business_qa_user_test.require_admission(
                user_id=owner_id, pet_id=pet_id
            )

            # A future recovery may have persisted the run ID before leaving
            # the intake retryable. Never start a second paid run in that case.
            refreshed = await archive_intake_service.get_intake(app_id)
            existing_run_id = str((refreshed or {}).get("generation_run_id") or "")
            if existing_run_id:
                queued = await archive_intake_service.mark_generation_queued(
                    app_id, existing_run_id
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
            )
            queued = await archive_intake_service.mark_generation_queued(
                app_id, run.id
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
                await archive_intake_service.mark_generation_failed(app_id, code)
            except Exception:
                logger.exception(
                    "Could not persist Archive preparation failure (application=%s)",
                    app_id,
                )
            if isinstance(exc, ArchivePreparationError):
                raise
            raise ArchivePreparationError(code, message) from exc
