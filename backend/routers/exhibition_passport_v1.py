"""Public, token-scoped Pet Passport registration endpoints.

No visitor email or registration row is exposed through a public list/query
route. The access token is submitted in a POST body to avoid putting it in URL
logs and referrers.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ..services import exhibition_passport as passport
from ..services import exhibition_prep_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/exhibition/passport", tags=["exhibition-passport"])


class RegistrationRequest(BaseModel):
    exhibition_id: Optional[str] = None
    pet_name: str
    email: str
    privacy_consent: bool
    # Must be generated with at least 256 bits of client-side randomness. It is
    # also the bearer token so a lost response can be replayed safely.
    idempotency_key: str
    privacy_policy_version: str = "v1"


class AccessRequest(BaseModel):
    passport_token: str


def get_store() -> passport.PassportStore:
    try:
        return passport.default_store()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "EXHIBITION_PASSPORT_STORE_UNAVAILABLE", "message": str(exc)},
        ) from exc


def get_photo_access_stores():
    """Resolve private artifact access lazily; waiting Passports need no bucket."""
    try:
        return exhibition_prep_store.default_stores()
    except RuntimeError:
        return None


def _error(exc: passport.PassportError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


@router.post("/registrations")
async def post_registration(body: RegistrationRequest, store=Depends(get_store)):
    try:
        result = await asyncio.to_thread(
            passport.register,
            exhibition_id=body.exhibition_id,
            pet_name=body.pet_name,
            email=body.email,
            privacy_consent=body.privacy_consent,
            idempotency_key=body.idempotency_key,
            privacy_policy_version=body.privacy_policy_version,
            store=store,
        )
    except passport.PassportError as exc:
        raise _error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("exhibition Passport registration failed")
        raise HTTPException(
            status_code=503,
            detail={"code": "EXHIBITION_PASSPORT_REGISTRATION_FAILED", "message": "Could not register."},
        ) from exc
    return result


@router.post("/retrieve")
async def post_retrieve(
    body: AccessRequest,
    response: Response,
    store=Depends(get_store),
    stores=Depends(get_photo_access_stores),
):
    try:
        if stores is None:
            result = await asyncio.to_thread(passport.retrieve, token=body.passport_token, store=store)
            if result:
                result["photo_url"] = None
                result["photo_access_error"] = (
                    "PHOTO_UNAVAILABLE" if result["photo_status"] == "PHOTO_RECEIVED" else None
                )
        else:
            run_store, artifacts = stores
            result = await asyncio.to_thread(
                passport.retrieve_with_photo,
                token=body.passport_token,
                store=store,
                run_store=run_store,
                artifacts=artifacts,
            )
    except passport.PassportError as exc:
        # Do not distinguish malformed, missing, or unknown tokens.
        if exc.code == "INVALID_REGISTRATION_TOKEN":
            result = None
        else:
            raise _error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("exhibition Passport retrieval failed")
        raise HTTPException(
            status_code=503,
            detail={"code": "EXHIBITION_PASSPORT_STORE_UNAVAILABLE", "message": "Could not retrieve Passport."},
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "PASSPORT_NOT_FOUND", "message": "Passport not found."})
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return result
