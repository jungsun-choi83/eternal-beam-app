from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any


DEFAULT_TABLE = "archive_intakes"
STATUS_REFERENCES_READY = "REFERENCES_READY"

_MOCK_INTAKES: dict[str, dict[str, Any]] = {}


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
    row = {
        "archive_application_id": app_id,
        "owner_id": owner_id,
        "content_id": content_id,
        "pet_id": pet_id,
        "customer_email": email,
        "pet_name": clean_pet_name,
        "pet_type": _clean_optional(pet_type),
        "breed": _clean_optional(breed),
        "reference_count": reference_count,
        "status": STATUS_REFERENCES_READY,
        "updated_at": now,
    }

    if _use_db():
        supabase = _supabase()
        if not supabase:
            raise RuntimeError(
                "Supabase server credentials are not configured for Archive intake."
            )

        def _upsert():
            return (
                supabase.table(_table())
                .upsert(row, on_conflict="archive_application_id")
                .execute()
            )

        result = await asyncio.to_thread(_upsert)
        data = getattr(result, "data", None) or []
        return data[0] if data else row

    existing = _MOCK_INTAKES.get(app_id)
    mock_row = {
        **row,
        "generation_run_id": (
            existing.get("generation_run_id") if existing else None
        ),
        "result_url": existing.get("result_url") if existing else None,
        "last_error": None,
        "created_at": existing.get("created_at") if existing else now,
    }
    _MOCK_INTAKES[app_id] = mock_row
    return mock_row


def mock_get(application_id: str) -> dict[str, Any] | None:
    return _MOCK_INTAKES.get((application_id or "").strip())
