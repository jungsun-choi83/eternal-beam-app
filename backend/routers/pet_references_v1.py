"""
/api/v1/pet/references — 펫 레퍼런스 대장 조회 (Durable Pet Identity Intake).

    GET  /{pet_id}       내 펫의 레퍼런스(원본 + 파생) 목록
    POST /{pet_id}/sync  대장을 사용자의 현재 사진 집합에 맞춘다 (뺀 사진 퇴장)

── 왜 행 추가는 여기 없는가 ────────────────────────────────────────────────
행 추가는 인테이크 시점의 무료 파이프라인(/api/assets/original, 누끼 훅)에서
일어난다 — 그 시점에는 Supabase 세션이 아직 없을 수 있어 인증을 요구할 수 없다
(backend/auth.py 의 레거시 경로 원칙). 조회와 동기화는 대장 자체를 다루므로
검증된 신원으로만 연다.

소유권은 pet_registry(등록된 펫) 또는 레퍼런스 행의 최초 신원(TOFU)이 정한다 —
pet_reference_service._assert_pet_accessible 참고.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import AuthedUser, require_user
from ..services import asset_url_refresh, pet_reference_service, pet_reference_set_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/pet/references", tags=["pet-references"])

CUTOUT_SIGNED_URL_TTL_SECONDS = 60 * 60


class ReferenceOut(BaseModel):
    id: str | None = None
    pet_id: str
    content_id: str
    role: str
    source: str
    derived_kind: str | None = None
    parent_reference_id: str | None = None
    bucket: str
    object_path: str
    original_filename: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    bytes_size: int | None = None
    content_hash: str | None = None
    view_label: str
    acceptance_state: str
    rejection_code: str | None = None
    version: int
    created_at: str | None = None


class ReferencesResponse(BaseModel):
    pet_id: str
    content_id: str | None = None
    intake_ready: bool = False
    original_reference_id: str | None = None
    cutout_reference_id: str | None = None
    cutout_signed_url: str | None = None
    cutout_signed_url_expires_at: str | None = None
    #: 생성이 시작되어 사진·누끼를 더 바꿀 수 없는가 (pet_inputs_locked).
    #: 판정할 수 없으면 null — 읽기 응답은 그 때문에 실패하지 않는다.
    inputs_locked: bool | None = None
    references: list[ReferenceOut] = []


@router.get("/{pet_id}", response_model=ReferencesResponse)
async def list_pet_references(
    pet_id: str,
    content_id: str | None = Query(default=None),
    user: AuthedUser = Depends(require_user),
):
    """내 펫의 레퍼런스만. 남의 펫은 403 이다."""
    try:
        refs = await pet_reference_service.list_references(
            user_id=user.user_id, pet_id=pet_id
        )
    except pet_reference_service.PetReferenceError as e:
        raise HTTPException(
            status_code=e.status, detail={"code": e.code, "message": e.message}
        ) from e

    requested_content_id = (content_id or "").strip()
    scoped_refs = (
        [ref for ref in refs if ref.content_id == requested_content_id]
        if requested_content_id
        else refs
    )
    ready, original, cutout = pet_reference_service.intake_readiness(scoped_refs)

    cutout_signed_url = None
    cutout_signed_url_expires_at = None
    if ready and cutout:
        cutout_signed_url = asset_url_refresh.sign_object(
            asset_url_refresh.StorageObject(
                bucket=cutout.bucket, path=cutout.object_path
            ),
            ttl=CUTOUT_SIGNED_URL_TTL_SECONDS,
        )
        if not cutout_signed_url:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "CUTOUT_SIGNING_FAILED",
                    "message": "누끼 표시 주소를 준비하지 못했습니다.",
                },
            )
        cutout_signed_url_expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=CUTOUT_SIGNED_URL_TTL_SECONDS)
        ).isoformat()

    try:
        inputs_locked: bool | None = await pet_reference_service.pet_inputs_locked(pet_id, refs)
    except pet_reference_service.PetReferenceError:
        inputs_locked = None

    return ReferencesResponse(
        pet_id=pet_id,
        content_id=(original.content_id if original else requested_content_id or None),
        intake_ready=ready,
        inputs_locked=inputs_locked,
        original_reference_id=original.id if original else None,
        cutout_reference_id=cutout.id if cutout else None,
        cutout_signed_url=cutout_signed_url,
        cutout_signed_url_expires_at=cutout_signed_url_expires_at,
        references=[
            ReferenceOut(
                id=r.id,
                pet_id=r.pet_id,
                content_id=r.content_id,
                role=r.role,
                source=r.source,
                derived_kind=r.derived_kind,
                parent_reference_id=r.parent_reference_id,
                bucket=r.bucket,
                object_path=r.object_path,
                original_filename=r.original_filename,
                mime_type=r.mime_type,
                width=r.width,
                height=r.height,
                bytes_size=r.bytes_size,
                content_hash=r.content_hash,
                view_label=r.view_label,
                acceptance_state=r.acceptance_state,
                rejection_code=r.rejection_code,
                version=r.version,
                created_at=r.created_at,
            )
            for r in scoped_refs
        ],
    )


# ══════════════════════════════════════════════════════════════════════════
# 현재 사진 집합 동기화 (사용자가 뺀/바꾼 사진의 퇴장)
# ══════════════════════════════════════════════════════════════════════════


class SyncReferencesRequest(BaseModel):
    #: 지금 UI 에 있는 **모든** 사진 바이트의 sha256 hex. 이번 패스에서 업로드가
    #: 실패한 사진도 포함한다 — 목록에 있는 해시의 원본은 거절되지 않는다.
    content_hashes: list[str] = Field(min_length=1, max_length=16)


class ActiveReferenceOut(BaseModel):
    reference_id: str
    content_hash: str | None = None


class SyncReferencesResponse(BaseModel):
    pet_id: str
    active: list[ActiveReferenceOut] = []
    rejected_reference_ids: list[str] = []
    #: 보낸 해시 중 대장에 살아 있는 원본이 없는 것 (아직 업로드되지 않았다).
    missing_hashes: list[str] = []


@router.post("/{pet_id}/sync", response_model=SyncReferencesResponse)
async def sync_pet_references(
    pet_id: str,
    body: SyncReferencesRequest,
    user: AuthedUser = Depends(require_user),
):
    """목록에 없는 accepted 원본을 누끼와 함께 거절한다. 남의 펫은 403 이다."""
    hashes = [str(h or "").strip().lower() for h in body.content_hashes]
    if any(len(h) != 64 or any(c not in "0123456789abcdef" for c in h) for h in hashes):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "PET_REFERENCE_SYNC_INVALID_HASH",
                "message": "content_hashes 는 sha256 hex 여야 합니다.",
            },
        )
    try:
        result = await pet_reference_service.sync_active_originals(
            user_id=user.user_id, pet_id=pet_id, content_hashes=hashes
        )
    except pet_reference_service.PetReferenceError as e:
        raise HTTPException(
            status_code=e.status, detail={"code": e.code, "message": e.message}
        ) from e

    return SyncReferencesResponse(
        pet_id=pet_id,
        active=[
            ActiveReferenceOut(reference_id=str(r.id), content_hash=r.content_hash)
            for r in result.active
            if r.id
        ],
        rejected_reference_ids=result.rejected_original_ids,
        missing_hashes=result.missing_hashes,
    )


# ══════════════════════════════════════════════════════════════════════════
# 신뢰 레퍼런스 세트 (Phase 3)
# ══════════════════════════════════════════════════════════════════════════


class BuildSetRequest(BaseModel):
    #: true 면 입력이 안 바뀌었어도 강제로 새 버전을 만든다 (재분석).
    force: bool = False


class ReferenceSetResponse(BaseModel):
    id: str | None = None
    pet_id: str
    version: int
    status: str
    identity_profile_id: str | None = None
    identity_profile_version: int | None = None
    morphology_profile_id: str | None = None
    morphology_profile_version: int | None = None
    source_reference_ids: list[str] = []
    items: list[dict[str, Any]] = []
    reference_analysis: dict[str, Any] = {}
    coverage: dict[str, str] = {}
    completeness_tier: str = "LIMITED"
    completeness_score: float = 0.0
    analyzer_versions: dict[str, Any] = {}
    created_at: str | None = None
    deduplicated: bool = False


class ReferenceSetSummary(BaseModel):
    id: str | None = None
    version: int
    status: str
    completeness_tier: str
    completeness_score: float
    coverage: dict[str, str] = {}
    item_count: int
    created_at: str | None = None


class ReferenceSetsResponse(BaseModel):
    pet_id: str
    sets: list[ReferenceSetSummary] = []


def _set_response(s: pet_reference_set_service.PetReferenceSet) -> ReferenceSetResponse:
    return ReferenceSetResponse(
        id=s.id,
        pet_id=s.pet_id,
        version=s.version,
        status=s.status,
        identity_profile_id=s.identity_profile_id,
        identity_profile_version=s.identity_profile_version,
        morphology_profile_id=s.morphology_profile_id,
        morphology_profile_version=s.morphology_profile_version,
        source_reference_ids=s.source_reference_ids,
        items=s.items,
        reference_analysis=s.reference_analysis,
        coverage=s.coverage,
        completeness_tier=s.completeness_tier,
        completeness_score=s.completeness_score,
        analyzer_versions=s.analyzer_versions,
        created_at=s.created_at,
        deduplicated=s.deduplicated,
    )


def _set_http(e: pet_reference_set_service.PetReferenceSetError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


@router.post("/{pet_id}/build-set", response_model=ReferenceSetResponse)
async def build_reference_set(
    pet_id: str,
    body: BuildSetRequest | None = None,
    user: AuthedUser = Depends(require_user),
):
    """새 신뢰 레퍼런스 세트 버전을 빌드한다. 입력이 안 바뀌면 멱등이다."""
    try:
        s = await pet_reference_set_service.build_reference_set(
            user_id=user.user_id,
            pet_id=pet_id,
            skip_if_unchanged=not (body and body.force),
        )
    except pet_reference_set_service.PetReferenceSetError as e:
        raise _set_http(e) from e
    return _set_response(s)


@router.get("/{pet_id}/sets", response_model=ReferenceSetsResponse)
async def list_reference_sets(
    pet_id: str,
    user: AuthedUser = Depends(require_user),
):
    try:
        sets = await pet_reference_set_service.list_sets(user_id=user.user_id, pet_id=pet_id)
    except pet_reference_set_service.PetReferenceSetError as e:
        raise _set_http(e) from e
    return ReferenceSetsResponse(
        pet_id=pet_id,
        sets=[
            ReferenceSetSummary(
                id=s.id,
                version=s.version,
                status=s.status,
                completeness_tier=s.completeness_tier,
                completeness_score=s.completeness_score,
                coverage=s.coverage,
                item_count=len(s.items),
                created_at=s.created_at,
            )
            for s in sets
        ],
    )


@router.get("/{pet_id}/sets/{version}", response_model=ReferenceSetResponse)
async def get_reference_set(
    pet_id: str,
    version: int,
    user: AuthedUser = Depends(require_user),
):
    try:
        s = await pet_reference_set_service.get_set(
            user_id=user.user_id, pet_id=pet_id, version=version
        )
    except pet_reference_set_service.PetReferenceSetError as e:
        raise _set_http(e) from e
    if not s:
        raise HTTPException(
            status_code=404,
            detail={"code": "REFERENCE_SET_NOT_FOUND", "message": "레퍼런스 세트가 없습니다."},
        )
    return _set_response(s)
