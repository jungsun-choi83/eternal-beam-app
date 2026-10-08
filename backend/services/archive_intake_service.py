from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from typing import Any


DEFAULT_TABLE = "archive_intakes"
STATUS_REFERENCES_READY = "REFERENCES_READY"
STATUS_PREPARING = "PREPARING"
STATUS_GENERATION_QUEUED = "GENERATION_QUEUED"
STATUS_GENERATION_FAILED = "GENERATION_FAILED"

_MOCK_INTAKES: dict[str, dict[str, Any]] = {}
_SAFE_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,199}$")


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


async def claim_preparation(
    archive_application_id: str,
) -> dict[str, Any] | None:
    """Atomically claim expensive cutout preparation for one application."""
    app_id = (archive_application_id or "").strip()
    if not app_id:
        raise ValueError("archive_application_id must not be empty")
    if not _use_db():
        row = _MOCK_INTAKES.get(app_id)
        if not row or row.get("status") not in (
            "RECEIVED",
            STATUS_REFERENCES_READY,
            STATUS_GENERATION_FAILED,
        ):
            return None
        row.update(
            {
                "status": STATUS_PREPARING,
                "last_error": None,
                "updated_at": _now_iso(),
            }
        )
        return dict(row)

    supabase = _supabase()
    if not supabase:
        raise RuntimeError(
            "Supabase server credentials are not configured for Archive intake."
        )

    def _claim():
        return supabase.rpc(
            "claim_archive_intake_preparation",
            {"p_archive_application_id": app_id},
        ).execute()

    return _first_row(await asyncio.to_thread(_claim))


async def _update_preparing(
    archive_application_id: str, fields: dict[str, Any]
) -> dict[str, Any]:
    app_id = (archive_application_id or "").strip()
    payload = {**fields, "updated_at": _now_iso()}
    if not _use_db():
        row = _MOCK_INTAKES.get(app_id)
        if not row:
            raise RuntimeError("Archive intake does not exist.")
        if row.get("status") == STATUS_PREPARING:
            row.update(payload)
        return dict(row)

    supabase = _supabase()
    if not supabase:
        raise RuntimeError(
            "Supabase server credentials are not configured for Archive intake."
        )

    def _update():
        return (
            supabase.table(_table())
            .update(payload)
            .eq("archive_application_id", app_id)
            .eq("status", STATUS_PREPARING)
            .execute()
        )

    updated = _first_row(await asyncio.to_thread(_update))
    if updated:
        return updated
    current = await get_intake(app_id)
    if not current:
        raise RuntimeError("Archive intake does not exist.")
    return current


async def mark_generation_queued(
    archive_application_id: str, generation_run_id: str
) -> dict[str, Any]:
    run_id = (generation_run_id or "").strip()
    if not run_id:
        raise ValueError("generation_run_id must not be empty")
    return await _update_preparing(
        archive_application_id,
        {
            "status": STATUS_GENERATION_QUEUED,
            "generation_run_id": run_id,
            "last_error": None,
        },
    )


async def mark_generation_failed(
    archive_application_id: str, error_code: str
) -> dict[str, Any]:
    # Persist a bounded machine-readable code only; never provider payloads,
    # exception strings, credentials, or stack traces.
    safe_code = safe_error_code(error_code)
    return await _update_preparing(
        archive_application_id,
        {"status": STATUS_GENERATION_FAILED, "last_error": safe_code},
    )


def mock_get(application_id: str) -> dict[str, Any] | None:
    return _MOCK_INTAKES.get((application_id or "").strip())
