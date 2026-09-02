"""
/api/v1/pet/references — 펫 레퍼런스 대장 조회 (Durable Pet Identity Intake).

    GET /{pet_id}       내 펫의 레퍼런스(원본 + 파생) 목록

── 왜 조회만 있는가 ────────────────────────────────────────────────────────
쓰기는 인테이크 시점의 무료 파이프라인(/api/assets/original, 누끼 훅)에서
일어난다 — 그 시점에는 Supabase 세션이 아직 없을 수 있어 인증을 요구할 수 없다
(backend/auth.py 의 레거시 경로 원칙). 조회는 서명 대상이 아니라 대장 자체라
검증된 신원으로만 연다. 멀티뷰 업로더가 생기면 인증된 쓰기 경로가 여기 추가된다.

소유권은 pet_registry(등록된 펫) 또는 레퍼런스 행의 최초 신원(TOFU)이 정한다 —
pet_reference_service._assert_pet_accessible 참고.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import AuthedUser, require_user
from ..services import pet_reference_service, pet_reference_set_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/pet/references", tags=["pet-references"])


class ReferenceOut(BaseModel):
    id: str | None = None
    pet_id: str
    content_id: str
    role: str
    source: str
    derived_kind: str | None = None
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
    references: list[ReferenceOut] = []


@router.get("/{pet_id}", response_model=ReferencesResponse)
async def list_pet_references(
    pet_id: str,
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

    return ReferencesResponse(
        pet_id=pet_id,
        references=[
            ReferenceOut(
                id=r.id,
                pet_id=r.pet_id,
                content_id=r.content_id,
                role=r.role,
                source=r.source,
                derived_kind=r.derived_kind,
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
            for r in refs
        ],
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
