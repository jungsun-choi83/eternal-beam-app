from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from ..services import archive_intake_service, pet_reference_service


router = APIRouter(
    prefix="/internal/archive-intake",
    tags=["archive-intake"],
)

MAX_PHOTOS = 3
MAX_PHOTO_BYTES = 40 * 1024 * 1024
MAX_EMAIL_LENGTH = 254
MAX_PET_NAME_LENGTH = 200
MAX_OPTIONAL_METADATA_LENGTH = 200

ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}

APPLICATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
EMAIL_LOCAL_RE = re.compile(r"^[^\s@]+$")
EMAIL_DOMAIN_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
HEIF_BRANDS = {
    b"heic",
    b"heix",
    b"hevc",
    b"hevx",
    b"heim",
    b"heis",
    b"hevm",
    b"hevs",
    b"mif1",
    b"msf1",
}


class ArchiveReferenceOut(BaseModel):
    reference_id: str | None
    filename: str | None
    content_hash: str | None
    deduplicated: bool


class ArchiveIntakeResponse(BaseModel):
    application_id: str
    owner_id: str
    content_id: str
    pet_id: str
    reference_count: int
    references: list[ArchiveReferenceOut]


@dataclass(frozen=True)
class _PreparedPhoto:
    data: bytes
    mime_type: str
    filename: str | None
    content_hash: str


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _require_archive_secret(provided: str | None) -> None:
    expected = os.getenv("ARCHIVE_SERVICE_TOKEN")

    if expected is None or not expected.strip():
        raise _error(
            503,
            "ARCHIVE_INTAKE_NOT_CONFIGURED",
            "Archive integration is not configured.",
        )

    # Compare the exact values. In particular, do not silently trim a supplied
    # credential into a valid one.
    if not hmac.compare_digest(provided or "", expected):
        raise _error(
            401,
            "ARCHIVE_INTAKE_FORBIDDEN",
            "Invalid Archive service token.",
        )


def _valid_email(value: str) -> bool:
    if not value or len(value) > MAX_EMAIL_LENGTH or value.count("@") != 1:
        return False

    local, domain = value.rsplit("@", 1)
    if (
        not local
        or len(local) > 64
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or not EMAIL_LOCAL_RE.fullmatch(local)
    ):
        return False

    if not domain or len(domain) > 253:
        return False
    labels = domain.split(".")
    return len(labels) >= 2 and all(EMAIL_DOMAIN_LABEL_RE.fullmatch(label) for label in labels)


def _clean_required(value: str, *, field: str, max_length: int) -> str:
    cleaned = (value or "").strip()
    if not cleaned or len(cleaned) > max_length:
        raise _error(
            400,
            f"ARCHIVE_{field.upper()}_INVALID",
            f"{field} is invalid.",
        )
    return cleaned


def _clean_optional(value: str | None, *, field: str) -> str | None:
    cleaned = (value or "").strip()
    if len(cleaned) > MAX_OPTIONAL_METADATA_LENGTH:
        raise _error(
            400,
            f"ARCHIVE_{field.upper()}_INVALID",
            f"{field} is invalid.",
        )
    return cleaned or None


def _matches_image_signature(data: bytes, mime_type: str) -> bool:
    if mime_type in {"image/jpeg", "image/jpg"}:
        return data.startswith(b"\xff\xd8\xff")
    if mime_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if mime_type in {"image/heic", "image/heif"}:
        if len(data) < 12 or data[4:8] != b"ftyp":
            return False
        box_size = int.from_bytes(data[:4], "big")
        if box_size < 12 or box_size > len(data):
            return False
        brands = {data[8:12]}
        brands.update(
            data[offset : offset + 4]
            for offset in range(16, box_size - 3, 4)
        )
        return bool(brands & HEIF_BRANDS)
    return False


async def _prepare_photos(photos: list[UploadFile]) -> list[_PreparedPhoto]:
    # Validate every declared type before uploading anything. This prevents a
    # request such as [valid.jpg, payload.exe] from being partially accepted.
    mime_types = [(photo.content_type or "").lower().strip() for photo in photos]
    for mime_type in mime_types:
        if mime_type not in ALLOWED_MIME_TYPES:
            raise _error(
                415,
                "ARCHIVE_PHOTO_TYPE_UNSUPPORTED",
                f"Unsupported image type: {mime_type or 'unknown'}",
            )

    prepared: list[_PreparedPhoto] = []
    for photo, mime_type in zip(photos, mime_types):
        data = await photo.read()
        if not data:
            raise _error(
                400,
                "ARCHIVE_PHOTO_EMPTY",
                "One of the uploaded photos is empty.",
            )
        if len(data) > MAX_PHOTO_BYTES:
            raise _error(
                413,
                "ARCHIVE_PHOTO_TOO_LARGE",
                "One of the uploaded photos exceeds the size limit.",
            )
        if not _matches_image_signature(data, mime_type):
            raise _error(
                415,
                "ARCHIVE_PHOTO_CONTENT_INVALID",
                "One of the uploaded files does not match its declared image type.",
            )
        prepared.append(
            _PreparedPhoto(
                data=data,
                mime_type=mime_type,
                filename=photo.filename,
                content_hash=hashlib.sha256(data).hexdigest(),
            )
        )
    return prepared


def _accepted_original_count(refs: list[pet_reference_service.PetReference]) -> int:
    return sum(
        1
        for ref in refs
        if ref.role == pet_reference_service.ROLE_ORIGINAL
        and ref.acceptance_state == pet_reference_service.STATE_ACCEPTED
    )


def _would_exceed_reference_limit(
    refs: list[pet_reference_service.PetReference],
    prepared: list[_PreparedPhoto],
) -> bool:
    accepted_count = _accepted_original_count(refs)
    by_hash = {
        ref.content_hash: ref
        for ref in refs
        if ref.role == pet_reference_service.ROLE_ORIGINAL and ref.content_hash
    }

    additions: set[str] = set()
    for photo in prepared:
        existing = by_hash.get(photo.content_hash)
        if existing is None or (
            existing.acceptance_state != pet_reference_service.STATE_ACCEPTED
            and existing.rejection_code
            == pet_reference_service.REJECTION_SUPERSEDED_BY_USER
        ):
            additions.add(photo.content_hash)

    return accepted_count + len(additions) > MAX_PHOTOS


def _reference_error(exc: pet_reference_service.PetReferenceError) -> HTTPException:
    return _error(exc.status, exc.code, exc.message)


@router.post("", response_model=ArchiveIntakeResponse, status_code=201)
async def create_archive_intake(
    application_id: str = Form(...),
    customer_email: str = Form(...),
    pet_name: str = Form(...),
    pet_type: str | None = Form(None),
    breed: str | None = Form(None),
    photos: list[UploadFile] = File(default=[]),
    x_archive_service_token: str | None = Header(default=None),
):
    _require_archive_secret(x_archive_service_token)

    app_id = (application_id or "").strip()
    email = (customer_email or "").strip().lower()

    if not APPLICATION_ID_RE.fullmatch(app_id):
        raise _error(
            400,
            "ARCHIVE_APPLICATION_ID_INVALID",
            "application_id is invalid.",
        )
    if not _valid_email(email):
        raise _error(
            400,
            "ARCHIVE_EMAIL_INVALID",
            "customer_email is invalid.",
        )

    clean_pet_name = _clean_required(
        pet_name,
        field="pet_name",
        max_length=MAX_PET_NAME_LENGTH,
    )
    clean_pet_type = _clean_optional(pet_type, field="pet_type")
    clean_breed = _clean_optional(breed, field="breed")

    if not 1 <= len(photos) <= MAX_PHOTOS:
        raise _error(
            400,
            "ARCHIVE_PHOTO_COUNT_INVALID",
            "Upload between 1 and 3 photos.",
        )

    prepared = await _prepare_photos(photos)

    owner_id = f"archive_{app_id}"
    content_id = f"archive_{app_id}"
    pet_id = pet_reference_service.pet_id_for_content(content_id)

    results: list[ArchiveReferenceOut] = []

    async with pet_reference_service.pet_input_gate(pet_id).shared():
        try:
            refs = await pet_reference_service.list_references(
                user_id=owner_id,
                pet_id=pet_id,
            )
            await pet_reference_service.assert_pet_inputs_unlocked(pet_id, refs)
        except pet_reference_service.PetReferenceError as exc:
            raise _reference_error(exc) from exc

        if _would_exceed_reference_limit(refs, prepared):
            raise _error(
                409,
                "ARCHIVE_REFERENCE_LIMIT_EXCEEDED",
                "This Archive application already has the maximum of 3 photos.",
            )

        for photo in prepared:
            try:
                ref = await pet_reference_service.record_original(
                    user_id=owner_id,
                    content_id=content_id,
                    data=photo.data,
                    mime_type=photo.mime_type,
                    original_filename=photo.filename,
                    source=pet_reference_service.SOURCE_OPS,
                )
            except pet_reference_service.PetReferenceError as exc:
                raise _reference_error(exc) from exc

            if not ref.recorded or not ref.id:
                raise _error(
                    503,
                    "ARCHIVE_REFERENCE_PERSIST_FAILED",
                    "The pet reference could not be stored.",
                )

            results.append(
                ArchiveReferenceOut(
                    reference_id=ref.id,
                    filename=ref.original_filename,
                    content_hash=ref.content_hash,
                    deduplicated=ref.deduplicated,
                )
            )

        try:
            refs = await pet_reference_service.list_references(
                user_id=owner_id,
                pet_id=pet_id,
            )
        except pet_reference_service.PetReferenceError as exc:
            raise _reference_error(exc) from exc

    reference_count = _accepted_original_count(refs)

    try:
        await archive_intake_service.save_intake(
            archive_application_id=app_id,
            owner_id=owner_id,
            content_id=content_id,
            pet_id=pet_id,
            customer_email=email,
            pet_name=clean_pet_name,
            pet_type=clean_pet_type,
            breed=clean_breed,
            reference_count=reference_count,
        )
    except Exception as exc:
        raise _error(
            503,
            "ARCHIVE_INTAKE_PERSIST_FAILED",
            "The Archive intake metadata could not be stored.",
        ) from exc

    return ArchiveIntakeResponse(
        application_id=app_id,
        owner_id=owner_id,
        content_id=content_id,
        pet_id=pet_id,
        reference_count=reference_count,
        references=results,
    )
