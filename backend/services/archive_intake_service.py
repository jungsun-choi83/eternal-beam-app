from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any


DEFAULT_TABLE = "archive_intakes"
STATUS_REFERENCES_READY = "REFERENCES_READY"
STATUS_PREPARING = "PREPARING"
STATUS_GENERATION_QUEUED = "GENERATION_QUEUED"
STATUS_GENERATION_FAILED = "GENERATION_FAILED"
STATUS_GENERATING = "GENERATING"
STATUS_COMPLETED = "COMPLETED"

PREPARATION_STAGE_QUEUED = "QUEUED"
PREPARATION_STAGE_CLAIMED = "CLAIMED"
PREPARATION_STAGE_COMPLETE = "COMPLETE"
PREPARATION_STAGE_FAILED = "FAILED"
PREPARATION_LEASE_SECONDS = 300

RECOVERY_QUEUED = "QUEUED"
RECOVERY_ALREADY_QUEUED = "ALREADY_QUEUED"
RECOVERY_ALREADY_IN_PROGRESS = "ALREADY_IN_PROGRESS"
RECOVERY_GENERATION_EXISTS = "GENERATION_ALREADY_EXISTS"
RECOVERY_NOT_RECOVERABLE = "NOT_RECOVERABLE"

_MOCK_INTAKES: dict[str, dict[str, Any]] = {}
_PREPARATION_CLAIM_LOCK = asyncio.Lock()
_SAFE_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,199}$")
_APPLICATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@dataclass(frozen=True)
class PreparationRecovery:
    outcome: str
    intake: dict[str, Any] | None


def __reset_for_tests() -> None:
    _MOCK_INTAKES.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table() -> str:
    return os.getenv("ARCHIVE_INTAKES_TABLE", DEFAULT_TABLE)


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _supabase():
    # This customer-data table is server-only. Unlike older shared repository
    # helpers, this path must never fall back to VITE/anon credentials.
    if not (os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY")):
        return None

    from ..models.content import _supabase_client

    return _supabase_client()


def _clean_optional(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def safe_error_code(value: str | None) -> str:
    code = (value or "").strip()
    if _SAFE_ERROR_CODE_RE.fullmatch(code):
        return code
    return "ARCHIVE_GENERATION_PREPARATION_FAILED"


def _first_row(result: Any) -> dict[str, Any] | None:
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data:
        return data[0]
    return None


async def save_intake(
    *,
    archive_application_id: str,
    owner_id: str,
    content_id: str,
    pet_id: str,
    customer_email: str,
    pet_name: str,
    pet_type: str | None,
    breed: str | None,
    reference_count: int,
) -> dict[str, Any]:
    app_id = archive_application_id.strip()
    email = customer_email.strip().lower()
    clean_pet_name = pet_name.strip()

    if not app_id or not owner_id or not content_id or not pet_id:
        raise ValueError("Archive intake identifiers must not be empty.")
    if not email or not clean_pet_name:
        raise ValueError("Archive customer email and pet name must not be empty.")
    if not 1 <= reference_count <= 3:
        raise ValueError("Archive intake reference_count must be between 1 and 3.")

    now = _now_iso()
    metadata = {
        "archive_application_id": app_id,
        "owner_id": owner_id,
        "content_id": content_id,
        "pet_id": pet_id,
        "customer_email": email,
        "pet_name": clean_pet_name,
        "pet_type": _clean_optional(pet_type),
        "breed": _clean_optional(breed),
        "reference_count": reference_count,
        "updated_at": now,
    }

    if _use_db():
        supabase = _supabase()
        if not supabase:
            raise RuntimeError(
                "Supabase server credentials are not configured for Archive intake."
            )

        def _upsert():
            return supabase.rpc(
                "upsert_archive_intake_metadata",
                {
                    "p_archive_application_id": app_id,
                    "p_owner_id": owner_id,
                    "p_content_id": content_id,
                    "p_pet_id": pet_id,
                    "p_customer_email": email,
                    "p_pet_name": clean_pet_name,
                    "p_pet_type": _clean_optional(pet_type),
                    "p_breed": _clean_optional(breed),
                    "p_reference_count": reference_count,
                },
            ).execute()

        result = await asyncio.to_thread(_upsert)
        saved = _first_row(result)
        if not saved:
            raise RuntimeError("Archive intake metadata was not returned after upsert.")
        return saved

    existing = _MOCK_INTAKES.get(app_id)
    existing_status = str((existing or {}).get("status") or "")
    status = (
        STATUS_REFERENCES_READY
        if existing_status in ("", "RECEIVED", STATUS_REFERENCES_READY)
        else existing_status
    )
    mock_row = {
        **(existing or {}),
        **metadata,
        "status": status,
        "generation_run_id": (
            existing.get("generation_run_id") if existing else None
        ),
        "result_url": existing.get("result_url") if existing else None,
        "last_error": existing.get("last_error") if existing else None,
        "preparation_stage": existing.get("preparation_stage") if existing else None,
        "preparation_progress": int(existing.get("preparation_progress") or 0) if existing else 0,
        "preparation_attempt": int(existing.get("preparation_attempt") or 0) if existing else 0,
        "preparation_lease_id": existing.get("preparation_lease_id") if existing else None,
        "preparation_lease_expires_at": (
            existing.get("preparation_lease_expires_at") if existing else None
        ),
        "created_at": existing.get("created_at") if existing else now,
    }
    _MOCK_INTAKES[app_id] = mock_row
    return mock_row


async def get_intake(archive_application_id: str) -> dict[str, Any] | None:
    app_id = (archive_application_id or "").strip()
    if not app_id:
        return None
    if not _use_db():
        row = _MOCK_INTAKES.get(app_id)
        return dict(row) if row else None

    supabase = _supabase()
    if not supabase:
        raise RuntimeError(
            "Supabase server credentials are not configured for Archive intake."
        )

    def _select():
        return (
            supabase.table(_table())
            .select("*")
            .eq("archive_application_id", app_id)
            .limit(1)
            .execute()
        )

    return _first_row(await asyncio.to_thread(_select))

async def get_intake_by_generation_run_id(
    generation_run_id: str,
) -> dict[str, Any] | None:
    run_id = (generation_run_id or "").strip()
    if not run_id:
        return None

    if not _use_db():
        for row in _MOCK_INTAKES.values():
            if str(row.get("generation_run_id") or "") == run_id:
                return dict(row)
        return None

    supabase = _supabase()
    if not supabase:
        raise RuntimeError(
            "Supabase server credentials are not configured for Archive intake."
        )

    def _select():
        return (
            supabase.table(_table())
            .select("*")
            .eq("generation_run_id", run_id)
            .limit(1)
            .execute()
        )

    return _first_row(await asyncio.to_thread(_select))

async def claim_preparation(
    archive_application_id: str,
    *,
    worker_id: str = "archive-preparation-inline",
) -> dict[str, Any] | None:
    """Atomically claim expensive cutout preparation for one application."""
    app_id = (archive_application_id or "").strip()
    if not app_id:
        raise ValueError("archive_application_id must not be empty")
    if not _use_db():
        async with _PREPARATION_CLAIM_LOCK:
            return _claim_mock_preparation(app_id, worker_id)

    supabase = _supabase()
    if not supabase:
        raise RuntimeError(
            "Supabase server credentials are not configured for Archive intake."
        )

    def _claim():
        return supabase.rpc(
            "claim_archive_intake_preparation",
            {
                "p_archive_application_id": app_id,
                "p_worker_id": (worker_id or "archive-preparation-worker").strip(),
                "p_lease_seconds": _preparation_lease_seconds(),
            },
        ).execute()

    return _first_row(await asyncio.to_thread(_claim))


def _preparation_lease_seconds() -> int:
    try:
        return max(60, int(os.getenv("ARCHIVE_PREPARATION_LEASE_SECONDS", str(PREPARATION_LEASE_SECONDS))))
    except ValueError:
        return PREPARATION_LEASE_SECONDS


def _lease_until() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=_preparation_lease_seconds())).isoformat()


def _lease_expired(value: Any) -> bool:
    if not value:
        return True
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return True


def _claim_mock_preparation(app_id: str, worker_id: str) -> dict[str, Any] | None:
    row = _MOCK_INTAKES.get(app_id)
    if not row or row.get("generation_run_id"):
        return None
    if row.get("status") not in (
        "RECEIVED",
        STATUS_REFERENCES_READY,
        STATUS_PREPARING,
    ):
        return None
    if row.get("status") == STATUS_PREPARING and not _lease_expired(
        row.get("preparation_lease_expires_at")
    ):
        return None
    row.update(
        {
            "status": STATUS_PREPARING,
            "last_error": None,
            "preparation_stage": PREPARATION_STAGE_CLAIMED,
            "preparation_progress": 0,
            "preparation_attempt": int(row.get("preparation_attempt") or 0) + 1,
            "preparation_lease_id": f"{worker_id}:{uuid.uuid4().hex}",
            "preparation_lease_expires_at": _lease_until(),
            "updated_at": _now_iso(),
        }
    )
    return dict(row)


async def claim_next_preparation(
    worker_id: str,
    *,
    application_id: str | None = None,
) -> dict[str, Any] | None:
    """Claim one durable Archive preparation job for a worker process."""
    wid = (worker_id or "").strip()
    if not wid:
        raise ValueError("worker_id must not be empty")
    allowed_app_id = (application_id or "").strip() or None
    if allowed_app_id is not None and not _APPLICATION_ID_RE.fullmatch(allowed_app_id):
        raise ValueError("application_id is invalid")
    if not _use_db():
        async with _PREPARATION_CLAIM_LOCK:
            candidates = [
                row
                for row in _MOCK_INTAKES.values()
                if row.get("status") in (
                    STATUS_REFERENCES_READY,
                    STATUS_PREPARING,
                )
                and not row.get("generation_run_id")
                and (
                    allowed_app_id is None
                    or row.get("archive_application_id") == allowed_app_id
                )
                and (
                    row.get("status") != STATUS_PREPARING
                    or _lease_expired(row.get("preparation_lease_expires_at"))
                )
            ]
            if not candidates:
                return None
            row = min(candidates, key=lambda item: str(item.get("updated_at") or ""))
            claimed = _claim_mock_preparation(str(row["archive_application_id"]), wid)
            if (
                allowed_app_id is not None
                and claimed is not None
                and claimed.get("archive_application_id") != allowed_app_id
            ):
                raise RuntimeError(
                    "Archive single-application restriction was not enforced."
                )
            return claimed

    supabase = _supabase()
    if not supabase:
        raise RuntimeError(
            "Supabase server credentials are not configured for Archive intake."
        )

    def _claim_next():
        params = {
            "p_worker_id": wid,
            "p_lease_seconds": _preparation_lease_seconds(),
        }
        if allowed_app_id is None:
            return supabase.rpc(
                "claim_next_archive_intake_preparation",
                params,
            ).execute()
        params["p_archive_application_id"] = allowed_app_id
        return supabase.rpc(
            "claim_next_archive_intake_preparation",
            params,
        ).execute()

    claimed = _first_row(await asyncio.to_thread(_claim_next))
    if (
        allowed_app_id is not None
        and claimed is not None
        and claimed.get("archive_application_id") != allowed_app_id
    ):
        raise RuntimeError("Archive single-application restriction was not enforced.")
    return claimed


async def enqueue_preparation(archive_application_id: str) -> dict[str, Any]:
    """Durably enqueue preparation without doing model work in the request."""
    app_id = (archive_application_id or "").strip()
    if not app_id:
        raise ValueError("archive_application_id must not be empty")
    if not _use_db():
        row = _MOCK_INTAKES.get(app_id)
        if not row:
            raise RuntimeError("Archive intake does not exist.")
        if row.get("generation_run_id"):
            return dict(row)
        if row.get("status") not in (
            "RECEIVED",
            STATUS_REFERENCES_READY,
            STATUS_PREPARING,
        ):
            return dict(row)
        if row.get("status") != STATUS_PREPARING or _lease_expired(
            row.get("preparation_lease_expires_at")
        ):
            row.update(
                {
                    "status": STATUS_PREPARING,
                    "last_error": None,
                    "preparation_stage": PREPARATION_STAGE_QUEUED,
                    "preparation_progress": 0,
                    "preparation_lease_id": None,
                    "preparation_lease_expires_at": None,
                    "updated_at": _now_iso(),
                }
            )
        return dict(row)

    supabase = _supabase()
    if not supabase:
        raise RuntimeError(
            "Supabase server credentials are not configured for Archive intake."
        )

    def _enqueue():
        return (
            supabase.table(_table())
            .update(
                {
                    "status": STATUS_PREPARING,
                    "last_error": None,
                    "preparation_stage": PREPARATION_STAGE_QUEUED,
                    "preparation_progress": 0,
                    "preparation_lease_id": None,
                    "preparation_lease_expires_at": None,
                    "updated_at": _now_iso(),
                }
            )
            .eq("archive_application_id", app_id)
            .is_("generation_run_id", "null")
            .in_("status", ["RECEIVED", STATUS_REFERENCES_READY])
            .execute()
        )

    updated = _first_row(await asyncio.to_thread(_enqueue))
    if updated:
        return updated
    current = await get_intake(app_id)
    if not current:
        raise RuntimeError("Archive intake does not exist.")
    return current


async def request_preparation_recovery(
    archive_application_id: str,
) -> PreparationRecovery:
    """Atomically requeue one failed Archive preparation for an operator.

    This changes only the durable preparation state. The preparation worker
    remains the sole caller that can create the existing idempotent generation
    run.
    """
    app_id = (archive_application_id or "").strip()
    if not app_id:
        raise ValueError("archive_application_id must not be empty")

    if not _use_db():
        async with _PREPARATION_CLAIM_LOCK:
            row = _MOCK_INTAKES.get(app_id)
            if not row:
                return PreparationRecovery(RECOVERY_NOT_RECOVERABLE, None)
            current = dict(row)
            if current.get("generation_run_id"):
                return PreparationRecovery(RECOVERY_GENERATION_EXISTS, current)
            if current.get("status") == STATUS_PREPARING and not _lease_expired(
                current.get("preparation_lease_expires_at")
            ):
                return PreparationRecovery(RECOVERY_ALREADY_IN_PROGRESS, current)
            if current.get("status") == STATUS_PREPARING and current.get(
                "preparation_stage"
            ) == PREPARATION_STAGE_QUEUED:
                return PreparationRecovery(RECOVERY_ALREADY_QUEUED, current)
            if current.get("status") not in {
                "RECEIVED",
                STATUS_REFERENCES_READY,
                STATUS_GENERATION_FAILED,
                STATUS_PREPARING,
            }:
                return PreparationRecovery(RECOVERY_NOT_RECOVERABLE, current)
            row.update(
                {
                    "status": STATUS_PREPARING,
                    "last_error": None,
                    "preparation_stage": PREPARATION_STAGE_QUEUED,
                    "preparation_progress": 0,
                    "preparation_lease_id": None,
                    "preparation_lease_expires_at": None,
                    "updated_at": _now_iso(),
                }
            )
            return PreparationRecovery(RECOVERY_QUEUED, dict(row))

    supabase = _supabase()
    if not supabase:
        raise RuntimeError(
            "Supabase server credentials are not configured for Archive intake."
        )

    def _recover():
        return supabase.rpc(
            "recover_archive_intake_preparation",
            {"p_archive_application_id": app_id},
        ).execute()

    result = await asyncio.to_thread(_recover)
    payload = getattr(result, "data", None)
    if not isinstance(payload, dict):
        return PreparationRecovery(RECOVERY_NOT_RECOVERABLE, None)
    intake = payload.get("intake")
    if not isinstance(intake, dict):
        return PreparationRecovery(RECOVERY_NOT_RECOVERABLE, None)
    return PreparationRecovery(
        str(payload.get("outcome") or RECOVERY_NOT_RECOVERABLE), intake
    )


async def update_preparation_progress(
    archive_application_id: str,
    lease_id: str,
    *,
    stage: str,
    progress: int,
) -> dict[str, Any] | None:
    fields = {
        "preparation_stage": (stage or PREPARATION_STAGE_CLAIMED).strip()[:64],
        "preparation_progress": max(0, min(100, int(progress))),
        "updated_at": _now_iso(),
        "preparation_lease_expires_at": _lease_until(),
    }
    updated = await _update_preparing(
        archive_application_id,
        fields,
        lease_id=(lease_id or "").strip(),
    )
    return updated


async def heartbeat_preparation(
    archive_application_id: str, lease_id: str
) -> dict[str, Any] | None:
    app_id = (archive_application_id or "").strip()
    token = (lease_id or "").strip()
    if not app_id or not token:
        return None
    return await _update_preparing(
        app_id,
        {
            "updated_at": _now_iso(),
            "preparation_lease_expires_at": _lease_until(),
        },
        lease_id=token,
    )


async def _update_preparing(
    archive_application_id: str,
    fields: dict[str, Any],
    *,
    lease_id: str | None = None,
) -> dict[str, Any] | None:
    app_id = (archive_application_id or "").strip()
    payload = {**fields, "updated_at": _now_iso()}
    if not _use_db():
        row = _MOCK_INTAKES.get(app_id)
        if not row:
            raise RuntimeError("Archive intake does not exist.")
        if row.get("status") != STATUS_PREPARING:
            return None if lease_id is not None else dict(row)
        if lease_id is not None and row.get("preparation_lease_id") != lease_id:
            return None
        row.update(payload)
        return dict(row)

    supabase = _supabase()
    if not supabase:
        raise RuntimeError(
            "Supabase server credentials are not configured for Archive intake."
        )

    def _update():
        query = (
            supabase.table(_table())
            .update(payload)
            .eq("archive_application_id", app_id)
            .eq("status", STATUS_PREPARING)
        )
        if lease_id is not None:
            query = query.eq("preparation_lease_id", lease_id)
        return query.execute()

    updated = _first_row(await asyncio.to_thread(_update))
    if updated:
        return updated
    if lease_id is not None:
        return None
    current = await get_intake(app_id)
    if not current:
        raise RuntimeError("Archive intake does not exist.")
    return current


async def mark_generation_queued(
    archive_application_id: str,
    generation_run_id: str,
    *,
    lease_id: str | None = None,
) -> dict[str, Any]:
    run_id = (generation_run_id or "").strip()
    if not run_id:
        raise ValueError("generation_run_id must not be empty")
    updated = await _update_preparing(
        archive_application_id,
        {
            "status": STATUS_GENERATION_QUEUED,
            "generation_run_id": run_id,
            "last_error": None,
            "preparation_stage": PREPARATION_STAGE_COMPLETE,
            "preparation_progress": 100,
            "preparation_lease_id": None,
            "preparation_lease_expires_at": None,
        },
        lease_id=lease_id,
    )
    if updated is None:
        raise RuntimeError("Archive preparation lease was lost.")
    return updated


async def mark_generation_completed(
    generation_run_id: str, result_url: str
) -> dict[str, Any] | None:
    run_id = (generation_run_id or "").strip()
    url = (result_url or "").strip()

    if not run_id:
        raise ValueError("generation_run_id must not be empty")
    if not url:
        raise ValueError("result_url must not be empty")

    payload = {
        "status": STATUS_COMPLETED,
        "result_url": url,
        "last_error": None,
        "updated_at": _now_iso(),
    }

    if not _use_db():
        for row in _MOCK_INTAKES.values():
            if str(row.get("generation_run_id") or "") != run_id:
                continue

            if row.get("status") in (
                STATUS_GENERATION_QUEUED,
                STATUS_GENERATING,
                STATUS_COMPLETED,
            ):
                row.update(payload)

            return dict(row)

        return None

    supabase = _supabase()
    if not supabase:
        raise RuntimeError(
            "Supabase server credentials are not configured for Archive intake."
        )

    def _update():
        return (
            supabase.table(_table())
            .update(payload)
            .eq("generation_run_id", run_id)
            .in_(
                "status",
                [
                    STATUS_GENERATION_QUEUED,
                    STATUS_GENERATING,
                    STATUS_COMPLETED,
                ],
            )
            .execute()
        )

    updated = _first_row(await asyncio.to_thread(_update))
    return updated


async def mark_generation_failed(
    archive_application_id: str,
    error_code: str,
    *,
    lease_id: str | None = None,
    preparation_stage: str | None = None,
) -> dict[str, Any]:
    # Persist a bounded machine-readable code only; never provider payloads,
    # exception strings, credentials, or stack traces.
    safe_code = safe_error_code(error_code)
    updated = await _update_preparing(
        archive_application_id,
        {
            "status": STATUS_GENERATION_FAILED,
            "last_error": safe_code,
            "preparation_stage": (preparation_stage or PREPARATION_STAGE_FAILED)[:64],
            "preparation_progress": 0,
            "preparation_lease_id": None,
            "preparation_lease_expires_at": None,
        },
        lease_id=lease_id,
    )
    if updated is None:
        raise RuntimeError("Archive preparation lease was lost.")
    return updated


def mock_get(application_id: str) -> dict[str, Any] | None:
    return _MOCK_INTAKES.get((application_id or "").strip())
