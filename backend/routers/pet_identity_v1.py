"""
/api/v1/pet/identity — 버전드 펫 신원 프로필 (Phase 2).

    POST /{pet_id}/build    새 프로필 버전을 빌드 (기본: 입력이 안 바뀌면 멱등)
    GET  /{pet_id}          최신 프로필 (?version= 로 특정 버전)

원본 사진이 정본이고 프로필은 파생 메타데이터다 — 빌드는 레퍼런스를 읽기만
한다. 인증 필수 (require_user); 소유권은 레퍼런스 대장과 같은 규칙이다.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import AuthedUser, require_user
from ..services import pet_identity_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/pet/identity", tags=["pet-identity"])


class BuildRequest(BaseModel):
    #: true 면 입력이 안 바뀌었어도 강제로 새 버전을 만든다 (재분석).
    force: bool = False


class ProfileResponse(BaseModel):
    id: str | None = None
    pet_id: str
    version: int
    status: str
    source_reference_ids: list[str] = []
    reference_eligibility: dict[str, Any] = {}
    visual_identity: dict[str, Any] = {}
    structural_identity: dict[str, Any] = {}
    completeness: dict[str, Any] = {}
    analyzer_versions: dict[str, Any] = {}
    created_at: str | None = None
    deduplicated: bool = False


def _to_response(p: pet_identity_service.PetIdentityProfile) -> ProfileResponse:
    return ProfileResponse(
        id=p.id,
        pet_id=p.pet_id,
        version=p.version,
        status=p.status,
        source_reference_ids=p.source_reference_ids,
        reference_eligibility=p.reference_eligibility,
        visual_identity=p.visual_identity,
        structural_identity=p.structural_identity,
        completeness=p.completeness,
        analyzer_versions=p.analyzer_versions,
        created_at=p.created_at,
        deduplicated=p.deduplicated,
    )


def _http(e: pet_identity_service.PetIdentityError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


@router.post("/{pet_id}/build", response_model=ProfileResponse)
async def build_profile(
    pet_id: str,
    body: BuildRequest | None = None,
    user: AuthedUser = Depends(require_user),
):
    try:
        profile = await pet_identity_service.build_identity_profile(
            user_id=user.user_id,
            pet_id=pet_id,
            skip_if_unchanged=not (body and body.force),
        )
    except pet_identity_service.PetIdentityError as e:
        raise _http(e) from e
    return _to_response(profile)


@router.get("/{pet_id}", response_model=ProfileResponse)
async def get_profile(
    pet_id: str,
    version: Optional[int] = Query(default=None, ge=1),
    user: AuthedUser = Depends(require_user),
):
    try:
        profile = await pet_identity_service.get_profile(
            user_id=user.user_id, pet_id=pet_id, version=version
        )
    except pet_identity_service.PetIdentityError as e:
        raise _http(e) from e
    if not profile:
        raise HTTPException(
            status_code=404,
            detail={"code": "IDENTITY_PROFILE_NOT_FOUND", "message": "신원 프로필이 없습니다."},
        )
    return _to_response(profile)
