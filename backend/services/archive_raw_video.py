"""Archive-only resolution of the selected generated raw motion artifact.

This module is deliberately read-only.  It does not generate, package, publish,
or mutate any Eternal Beam artifact.  It resolves the existing generation-run
and motion-candidate lineage, then returns the raw storage location for the
authenticated Archive preview route.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import (
    motion_publication_service,
    pet_generation_run_service,
    pet_reference_service,
    supabase_assets,
)


_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SAFE_MOTION_PATH = re.compile(r"^[A-Za-z0-9._/-]+$")


class ArchiveRawVideoResolutionError(RuntimeError):
    """A safe failure while resolving Archive's selected raw video."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ArchiveRawVideoArtifact:
    application_id: str
    generation_run_id: str
    owner_id: str
    content_id: str
    pet_id: str
    motion_version_id: str
    candidate_id: str
    bucket: str
    storage_path: str


BucketPrivacyVerifier = Callable[[str], Awaitable[bool]]


def _valid_segment(value: str, name: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_SEGMENT.fullmatch(text):
        raise ArchiveRawVideoResolutionError(
            "ARCHIVE_RAW_ID_INVALID",
            f"The Archive {name} is invalid.",
        )
    return text


def _expected_identity(application_id: str) -> tuple[str, str, str]:
    owner_id = f"archive_{application_id}"
    content_id = owner_id
    pet_id = pet_reference_service.pet_id_for_content(content_id)
    return owner_id, content_id, pet_id


def _safe_raw_path(value: object) -> str:
    path = str(value or "").strip()
    if (
        not path
        or len(path) > 1024
        or path.startswith(("/", "\\"))
        or ".." in path.split("/")
        or not _SAFE_MOTION_PATH.fullmatch(path)
    ):
        raise ArchiveRawVideoResolutionError(
            "ARCHIVE_RAW_ARTIFACT_INVALID",
            "The selected raw video path is invalid.",
        )
    return path


async def resolve_selected_raw_video(
    application_id: str,
    generation_run_id: str,
    *,
    bucket_privacy_verifier: BucketPrivacyVerifier | None = None,
) -> ArchiveRawVideoArtifact:
    """Resolve one completed Archive run to its selected raw MP4.

    All identifiers used to select the object come from the authenticated
    application/run lineage or the selected candidate row.  No storage path
    is accepted from the caller.
    """

    app_id = _valid_segment(application_id, "application ID")
    run_id = _valid_segment(generation_run_id, "generation run ID")
    owner_id, content_id, pet_id = _expected_identity(app_id)

    try:
        run = await pet_generation_run_service.get_generation_run(
            user_id=owner_id,
            run_id=run_id,
        )
    except pet_generation_run_service.PetGenerationRunError as exc:
        raise ArchiveRawVideoResolutionError(
            "ARCHIVE_RAW_GENERATION_UNAVAILABLE",
            "The Archive generation run is not available for raw video retrieval.",
        ) from exc
    except Exception as exc:
        raise ArchiveRawVideoResolutionError(
            "ARCHIVE_RAW_GENERATION_UNAVAILABLE",
            "The Archive generation run could not be verified.",
        ) from exc

    if (
        run.status != pet_generation_run_service.STATUS_PUBLISHED
        or run.user_id != owner_id
        or run.content_id != content_id
        or run.pet_id != pet_id
        or run.motion_id != pet_generation_run_service.MOTION_BREATHING
        or run.request_kind != pet_generation_run_service.REQUEST_FREE_HOME
    ):
        raise ArchiveRawVideoResolutionError(
            "ARCHIVE_RAW_GENERATION_NOT_READY",
            "The Archive generation has not completed successfully.",
        )

    business = dict((run.provider_state or {}).get("_business_qa") or {})
    if business.get("terminal_state") != pet_generation_run_service.BUSINESS_DELIVERED_GENERATED:
        raise ArchiveRawVideoResolutionError(
            "ARCHIVE_RAW_GENERATION_NOT_READY",
            "The Archive generation did not produce a generated customer video.",
        )

    motion_version_id = str(run.motion_version_id or "").strip()
    candidate_id = str(run.selected_candidate_id or "").strip()
    if not motion_version_id or not candidate_id or not run.publication_id:
        raise ArchiveRawVideoResolutionError(
            "ARCHIVE_RAW_LINEAGE_INCOMPLETE",
            "The selected Archive video lineage is incomplete.",
        )

    try:
        version = await motion_publication_service._load_version(motion_version_id)
        candidate = await motion_publication_service._load_selected_candidate(
            version=version,
            user_id=owner_id,
            pet_id=pet_id,
        )
    except motion_publication_service.MotionPublicationError as exc:
        raise ArchiveRawVideoResolutionError(
            "ARCHIVE_RAW_LINEAGE_INVALID",
            "The selected Archive video lineage could not be verified.",
        ) from exc
    except Exception as exc:
        raise ArchiveRawVideoResolutionError(
            "ARCHIVE_RAW_LINEAGE_UNAVAILABLE",
            "The selected Archive video lineage could not be loaded.",
        ) from exc

    if (
        str(version.get("id") or "") != motion_version_id
        or str(version.get("user_id") or "") != owner_id
        or str(version.get("pet_id") or "") != pet_id
        or str(version.get("motion_id") or "").upper()
        != pet_generation_run_service.MOTION_BREATHING
        or str(version.get("selected_candidate_id") or "") != candidate_id
        or str(candidate.get("id") or "") != candidate_id
        or str(candidate.get("motion_version_id") or "") != motion_version_id
        or str(candidate.get("user_id") or "") != owner_id
        or str(candidate.get("pet_id") or "") != pet_id
        or str(candidate.get("motion_id") or "").upper()
        != pet_generation_run_service.MOTION_BREATHING
        or candidate.get("selected") is not True
    ):
        raise ArchiveRawVideoResolutionError(
            "ARCHIVE_RAW_LINEAGE_INVALID",
            "The selected Archive video lineage is inconsistent.",
        )

    if str(version.get("selected_candidate_id") or "") != str(run.selected_candidate_id or ""):
        raise ArchiveRawVideoResolutionError(
            "ARCHIVE_RAW_CANDIDATE_MISMATCH",
            "The selected candidate does not match the generation run.",
        )

    bucket = str(candidate.get("raw_bucket") or "").strip()
    if not bucket:
        raise ArchiveRawVideoResolutionError(
            "ARCHIVE_RAW_BUCKET_MISSING",
            "The selected raw video has no explicit storage bucket.",
        )
    storage_path = _safe_raw_path(candidate.get("raw_video_path"))

    verify_bucket = bucket_privacy_verifier or supabase_assets.verify_private_storage_bucket
    try:
        verified = await verify_bucket(bucket)
    except Exception as exc:
        raise ArchiveRawVideoResolutionError(
            "ARCHIVE_RAW_BUCKET_UNVERIFIED",
            "The selected raw video storage bucket could not be verified private.",
        ) from exc
    if verified is not True:
        raise ArchiveRawVideoResolutionError(
            "ARCHIVE_RAW_BUCKET_UNVERIFIED",
            "The selected raw video storage bucket is not verified private.",
        )

    return ArchiveRawVideoArtifact(
        application_id=app_id,
        generation_run_id=run_id,
        owner_id=owner_id,
        content_id=content_id,
        pet_id=pet_id,
        motion_version_id=motion_version_id,
        candidate_id=candidate_id,
        bucket=bucket,
        storage_path=storage_path,
    )
