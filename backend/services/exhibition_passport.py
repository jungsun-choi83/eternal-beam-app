"""Server-side Pet Passport registration and opaque-token access.

The Passport registration is intentionally not an exhibition prep run. It owns
the visitor data and the number reservation until a later staff photo flow
associates an exhibition_prep_runs row with that number.
"""

from __future__ import annotations

import hashlib
import re
import threading
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from . import exhibition_queue_service as queue

# token_urlsafe(32) is 43 characters. Requiring that minimum keeps the
# public contract at 256 bits of client-side randomness or better.
TOKEN_MIN_LEN = 43
TOKEN_MAX_LEN = 128
_TOKEN_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class PassportError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class PassportConflict(PassportError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, status=409)


class PassportStore(Protocol):
    def register(
        self,
        *,
        exhibition_id: str,
        pet_name: str,
        email: str,
        privacy_policy_version: str,
        token_hash: str,
        request_fingerprint: str,
    ) -> dict[str, Any]: ...

    def get_by_token_hash(self, token_hash: str) -> Optional[dict[str, Any]]: ...

    def get_photo_ref_by_token_hash(self, token_hash: str) -> Optional[dict[str, Any]]: ...

    def get_by_queue_number(self, exhibition_id: str, queue_number: int) -> Optional[dict[str, Any]]: ...

    def attach_photo(
        self, *, exhibition_id: str, queue_number: int, run_id: str
    ) -> dict[str, Any]: ...


def normalize_email(value: str | None) -> str:
    email = " ".join((value or "").split()).strip().casefold()
    if len(email) > 320 or not _EMAIL_RE.fullmatch(email):
        raise PassportError("INVALID_EMAIL", "Enter a valid email address.")
    return email


def normalize_access_token(value: str | None) -> str:
    token = (value or "").strip()
    if not TOKEN_MIN_LEN <= len(token) <= TOKEN_MAX_LEN or not set(token) <= _TOKEN_ALPHABET:
        raise PassportError("INVALID_REGISTRATION_TOKEN", "The registration token is invalid.")
    return token


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _fingerprint(*, exhibition_id: str, pet_name: str, email: str, privacy_policy_version: str) -> str:
    value = "\x1f".join((exhibition_id, pet_name, email, privacy_policy_version))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "exhibition_id": row.get("exhibition_id"),
        "queue_number": row.get("queue_number"),
        "pet_name": row.get("pet_name"),
        "photo_status": row.get("photo_status", "WAITING_FOR_PHOTO"),
        "photo_received_at": row.get("photo_received_at"),
        "created_at": row.get("created_at"),
    }


def register(
    *,
    exhibition_id: str | None,
    pet_name: str | None,
    email: str | None,
    privacy_consent: bool,
    idempotency_key: str | None,
    privacy_policy_version: str = "v1",
    store: PassportStore,
) -> dict[str, Any]:
    eid = queue.normalize_exhibition_id(exhibition_id)
    name = queue.normalize_pet_name(pet_name)
    if not name:
        raise PassportError("PET_NAME_REQUIRED", "Pet name is required.")
    if not privacy_consent:
        raise PassportError("PRIVACY_CONSENT_REQUIRED", "Privacy consent is required.")
    policy = " ".join((privacy_policy_version or "").split()).strip()
    if not 1 <= len(policy) <= 40:
        raise PassportError("INVALID_PRIVACY_POLICY_VERSION", "Privacy policy version is invalid.")
    normalized_email = normalize_email(email)
    token = normalize_access_token(idempotency_key)
    row = store.register(
        exhibition_id=eid,
        pet_name=name,
        email=normalized_email,
        privacy_policy_version=policy,
        token_hash=hash_token(token),
        request_fingerprint=_fingerprint(
            exhibition_id=eid,
            pet_name=name,
            email=normalized_email,
            privacy_policy_version=policy,
        ),
    )
    result = _safe_row(row)
    # The client-generated 256-bit idempotency key is also the opaque bearer
    # token. It is never stored in raw form and makes retries replayable.
    result["passport_token"] = token
    result["replayed"] = bool(row.get("replayed", False))
    return result


def retrieve(*, token: str | None, store: PassportStore) -> Optional[dict[str, Any]]:
    raw = normalize_access_token(token)
    row = store.get_by_token_hash(hash_token(raw))
    return _safe_row(row) if row else None


PHOTO_URL_TTL_SECONDS = 300


def retrieve_with_photo(
    *,
    token: str | None,
    store: PassportStore,
    run_store,
    artifacts,
) -> Optional[dict[str, Any]]:
    """Return the token-scoped Passport and a short-lived source-photo URL.

    The registration RPC remains the authority for the public Passport fields.
    The photo reference is resolved separately and is never included in the
    public database response. A missing artifact is represented as an
    unavailable photo so the visitor can retry without seeing internal paths
    or storage errors.
    """
    raw = normalize_access_token(token)
    token_hash = hash_token(raw)
    row = store.get_by_token_hash(token_hash)
    if not row:
        return None

    result = _safe_row(row)
    result["photo_url"] = None
    result["photo_access_error"] = None
    if result["photo_status"] != "PHOTO_RECEIVED":
        return result

    try:
        ref = store.get_photo_ref_by_token_hash(token_hash)
        run_id = ref.get("photo_run_id") if ref else None
        run = run_store.get(run_id) if run_id else None
        if (
            not run
            or run.get("exhibition_id") != result["exhibition_id"]
            or run.get("id") != run_id
            or not run.get("source_path")
        ):
            result["photo_access_error"] = "PHOTO_UNAVAILABLE"
            return result
        result["photo_url"] = artifacts.signed_url(
            run["source_path"], ttl_seconds=PHOTO_URL_TTL_SECONDS
        )
        if not result["photo_url"]:
            result["photo_access_error"] = "PHOTO_UNAVAILABLE"
    except Exception:  # noqa: BLE001 - do not expose storage internals publicly
        result["photo_access_error"] = "PHOTO_UNAVAILABLE"
    return result


def submit_photo(
    raw: bytes,
    *,
    created_by: str,
    exhibition_id: str | None,
    queue_number: int,
    run_store,
    artifacts,
    store: PassportStore,
) -> dict[str, Any]:
    """Store a source photo, then atomically attach its run to a Passport.

    The source photo is intentionally created as an unqueued run first. The
    database RPC then updates both the run's queue fields and the Passport
    photo link in one transaction. Storage and database writes cannot be one
    atomic unit, so failed associations are quarantined as failed unqueued
    runs and can be retried safely without allocating a number.
    """
    eid = queue.normalize_exhibition_id(exhibition_id)
    if not isinstance(queue_number, int) or isinstance(queue_number, bool) or queue_number <= 0:
        raise PassportError("INVALID_QUEUE_NUMBER", "queue_number must be a positive integer.")

    registration = store.get_by_queue_number(eid, queue_number)
    if registration is None:
        raise PassportError("PASSPORT_NOT_FOUND", "Passport queue number was not found.", status=404)
    if registration.get("photo_status") != "WAITING_FOR_PHOTO" or registration.get("photo_run_id"):
        raise PassportConflict(
            "PASSPORT_PHOTO_ALREADY_ASSOCIATED",
            "This Passport already has an exhibition photo.",
        )

    from . import exhibition_prep_service

    row = exhibition_prep_service.create_pending_source_run(
        raw,
        created_by=created_by,
        store=run_store,
        artifacts=artifacts,
        exhibition_id=eid,
        pet_name=registration.get("pet_name"),
    )
    try:
        return store.attach_photo(exhibition_id=eid, queue_number=queue_number, run_id=row["id"])
    except Exception as exc:
        # The source object/run may already exist because storage is outside
        # the database transaction. Keep it out of the display queue and make
        # the failure visible to Ops rather than claiming a successful link.
        try:
            run_store.update(
                row["id"],
                {
                    "status": exhibition_prep_service.STATUS_FAILED,
                    "error_code": "PASSPORT_PHOTO_ASSOCIATION_FAILED",
                    "error_message": str(exc)[:500],
                },
            )
        except Exception:  # noqa: BLE001 - preserve the original association error
            pass
        raise


class SupabasePassportStore:
    def __init__(self, client) -> None:
        self._c = client

    @staticmethod
    def _row(data: Any) -> dict[str, Any]:
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            raise RuntimeError("Passport RPC returned no registration.")
        return data

    def register(self, **kwargs: Any) -> dict[str, Any]:
        try:
            res = self._c.rpc(
                "exhibition_register_pet_passport",
                {
                    "p_exhibition_id": kwargs["exhibition_id"],
                    "p_pet_name": kwargs["pet_name"],
                    "p_email": kwargs["email"],
                    "p_privacy_consent": True,
                    "p_privacy_policy_version": kwargs["privacy_policy_version"],
                    "p_token_hash": kwargs["token_hash"],
                    "p_request_fingerprint": kwargs["request_fingerprint"],
                },
            ).execute()
            return self._row(res.data)
        except Exception as exc:  # noqa: BLE001 - translate only known DB conflict
            if "IDEMPOTENCY_KEY_REUSED" in str(exc):
                raise PassportConflict(
                    "IDEMPOTENCY_KEY_REUSED",
                    "This registration token was already used for different details.",
                ) from exc
            if "REGISTRATION_TOKEN_ALREADY_USED" in str(exc):
                raise PassportConflict(
                    "REGISTRATION_TOKEN_ALREADY_USED",
                    "This registration token was already used.",
                ) from exc
            raise

    def get_by_token_hash(self, token_hash: str) -> Optional[dict[str, Any]]:
        res = self._c.rpc("exhibition_get_pet_passport", {"p_token_hash": token_hash}).execute()
        data = res.data
        if isinstance(data, list):
            return data[0] if data else None
        return data if isinstance(data, dict) else None

    def get_photo_ref_by_token_hash(self, token_hash: str) -> Optional[dict[str, Any]]:
        res = self._c.rpc(
            "exhibition_get_pet_passport_photo_ref", {"p_token_hash": token_hash}
        ).execute()
        data = res.data
        if isinstance(data, list):
            return data[0] if data else None
        return data if isinstance(data, dict) else None

    def get_by_queue_number(self, exhibition_id: str, queue_number: int) -> Optional[dict[str, Any]]:
        res = (
            self._c.table("exhibition_pet_passport_registrations")
            .select("exhibition_id,queue_number,pet_name,photo_status,photo_run_id")
            .eq("exhibition_id", exhibition_id)
            .eq("queue_number", queue_number)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def attach_photo(self, *, exhibition_id: str, queue_number: int, run_id: str) -> dict[str, Any]:
        try:
            res = self._c.rpc(
                "exhibition_attach_pet_passport_photo",
                {
                    "p_exhibition_id": exhibition_id,
                    "p_queue_number": queue_number,
                    "p_run_id": run_id,
                },
            ).execute()
            return self._row(res.data)
        except Exception as exc:  # noqa: BLE001 - translate known association conflicts
            message = str(exc)
            if "PASSPORT_NOT_FOUND" in message:
                raise PassportError("PASSPORT_NOT_FOUND", "Passport queue number was not found.", status=404) from exc
            if "PASSPORT_PHOTO_ALREADY_ASSOCIATED" in message:
                raise PassportConflict(
                    "PASSPORT_PHOTO_ALREADY_ASSOCIATED",
                    "This Passport already has an exhibition photo.",
                ) from exc
            if "PASSPORT_RUN_NOT_ATTACHABLE" in message:
                raise PassportConflict(
                    "PASSPORT_RUN_NOT_ATTACHABLE",
                    "The exhibition photo run cannot be attached.",
                ) from exc
            raise


def default_store() -> SupabasePassportStore:
    from . import exhibition_prep_store

    if exhibition_prep_store.local_preview_requested():
        raise RuntimeError(
            "Exhibition local preview is active; the Supabase Passport store is disabled."
        )
    from .supabase_assets import get_client

    client = get_client()
    if client is None:
        raise RuntimeError("Supabase is not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY).")
    return SupabasePassportStore(client)


class InMemoryPassportStore:
    """Small local/test store with the same idempotency contract as Supabase."""

    def __init__(self, queue_store=None) -> None:
        self.queue_store = queue_store
        self.rows: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def register(self, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            key = f"{kwargs['exhibition_id']}:{kwargs['token_hash']}"
            existing = self.rows.get(key)
            token_owner = next(
                (row for row in self.rows.values() if row["token_hash"] == kwargs["token_hash"]),
                None,
            )
            if token_owner is not None and existing is None:
                raise PassportConflict(
                    "REGISTRATION_TOKEN_ALREADY_USED",
                    "This registration token was already used.",
                )
            if existing:
                if existing["request_fingerprint"] != kwargs["request_fingerprint"]:
                    raise PassportConflict(
                        "IDEMPOTENCY_KEY_REUSED",
                        "This registration token was already used for different details.",
                    )
                return {**existing, "replayed": True}

            if self.queue_store is not None:
                number = self.queue_store.next_queue_number(kwargs["exhibition_id"])
            else:
                numbers = [
                    r["queue_number"]
                    for r in self.rows.values()
                    if r["exhibition_id"] == kwargs["exhibition_id"]
                ]
                number = max(numbers, default=0) + 1
            now = datetime.now(timezone.utc).isoformat()
            row = {
                **kwargs,
                "queue_number": number,
                "photo_status": "WAITING_FOR_PHOTO",
                "photo_received_at": None,
                "created_at": now,
                "replayed": False,
            }
            self.rows[key] = row
            return dict(row)

    def get_by_token_hash(self, token_hash: str) -> Optional[dict[str, Any]]:
        for row in self.rows.values():
            if row["token_hash"] == token_hash:
                return dict(row)
        return None

    def get_photo_ref_by_token_hash(self, token_hash: str) -> Optional[dict[str, Any]]:
        row = self.get_by_token_hash(token_hash)
        if not row or not row.get("photo_run_id"):
            return None
        return {
            "exhibition_id": row.get("exhibition_id"),
            "queue_number": row.get("queue_number"),
            "photo_status": row.get("photo_status"),
            "photo_run_id": row.get("photo_run_id"),
        }

    def get_by_queue_number(self, exhibition_id: str, queue_number: int) -> Optional[dict[str, Any]]:
        with self._lock:
            for row in self.rows.values():
                if row["exhibition_id"] == exhibition_id and row["queue_number"] == queue_number:
                    return dict(row)
        return None

    def attach_photo(self, *, exhibition_id: str, queue_number: int, run_id: str) -> dict[str, Any]:
        with self._lock:
            row = next(
                (
                    value
                    for value in self.rows.values()
                    if value["exhibition_id"] == exhibition_id and value["queue_number"] == queue_number
                ),
                None,
            )
            if row is None:
                raise PassportError("PASSPORT_NOT_FOUND", "Passport queue number was not found.", status=404)
            if row.get("photo_status") != "WAITING_FOR_PHOTO" or row.get("photo_run_id"):
                raise PassportConflict(
                    "PASSPORT_PHOTO_ALREADY_ASSOCIATED",
                    "This Passport already has an exhibition photo.",
                )

            run_store = self.queue_store
            run = run_store.get(run_id) if run_store is not None else None
            if (
                not run
                or run.get("exhibition_id") != exhibition_id
                or run.get("status") != "FAILED"
                or run.get("error_code") != "PASSPORT_PHOTO_PENDING_ASSOCIATION"
                or run.get("queue_number") is not None
            ):
                raise PassportConflict("PASSPORT_RUN_NOT_ATTACHABLE", "The exhibition photo run cannot be attached.")

            now = datetime.now(timezone.utc).isoformat()
            row.update(photo_status="PHOTO_RECEIVED", photo_run_id=run_id, photo_received_at=now, updated_at=now)
            run_store.update(
                run_id,
                {
                    "status": "QUEUED",
                    "error_code": None,
                    "error_message": None,
                    "queue_number": int(queue_number),
                    "pet_name": row["pet_name"],
                    "display_status": "WAITING",
                },
            )
            return {
                "run_id": run_id,
                "exhibition_id": exhibition_id,
                "queue_number": int(queue_number),
                "pet_name": row["pet_name"],
                "created_at": run.get("created_at"),
                "display_status": "WAITING",
                "status": "QUEUED",
                "photo_status": row["photo_status"],
                "photo_received_at": row["photo_received_at"],
            }
