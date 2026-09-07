"""
/api/v1/motion-references — 모션 레퍼런스 라이브러리 (Phase 6.6). 내부/디버그 용도.

    GET  /                              목록 (species/motion/quality/enabled 필터)
    POST /                              등록 (출처/라이선스 필수; 새 key 또는 새 버전)
    POST /{reference_key}/{version}/status   품질/활성 변경 (출처 없으면 승인 불가)
    GET  /resolve/{pet_id}/{motion_id}  펫+모션 → 최적 레퍼런스 + 후보/이유
                                        (?view=&direction=&speed=&size=&legs=&body=&species=)

고객 UI 아님. 영상 생성 호출 없음. 프로덕션 Luma/Wan 무관.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import AuthedUser, require_user
from ..services import motion_reference_service as svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/motion-references", tags=["motion-references"])


def _http(e: svc.MotionReferenceError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


def _ref_out(r: svc.MotionReference) -> dict[str, Any]:
    return {
        "id": r.id,
        "reference_key": r.reference_key,
        "version": r.version,
        "species": r.species,
        "body_size_class": r.body_size_class,
        "leg_length_class": r.leg_length_class,
        "body_length_class": r.body_length_class,
        "motion_id": r.motion_id,
        "motion_class": r.motion_class,
        "camera_view": r.camera_view,
        "travel_direction": r.travel_direction,
        "speed_class": r.speed_class,
        "start_pose": r.start_pose,
        "end_pose": r.end_pose,
        "duration_sec": r.duration_sec,
        "loopable": r.loopable,
        "object_path": r.object_path,
        "pet_id": r.pet_id,
        "source_type": r.source_type,
        "source_description": r.source_description,
        "license": r.license,
        "license_reference": r.license_reference,
        "provider_name": r.provider_name,
        "commercial_use_allowed": r.commercial_use_allowed,
        "provenance_notes": r.provenance_notes,
        "quality_status": r.quality_status,
        "enabled": r.enabled,
        "created_at": r.created_at,
    }


@router.get("/")
async def list_references(
    species: Optional[str] = Query(default=None),
    motion_id: Optional[str] = Query(default=None),
    quality_status: Optional[str] = Query(default=None),
    enabled: Optional[bool] = Query(default=None),
    user: AuthedUser = Depends(require_user),
):
    try:
        refs = await svc.list_references(
            species=species, motion_id=motion_id, quality_status=quality_status, enabled=enabled
        )
    except svc.MotionReferenceError as e:
        raise _http(e) from e
    return {"matching_version": svc.MOTION_MATCHING_VERSION, "references": [_ref_out(r) for r in refs]}


class RegisterRequest(BaseModel):
    reference_key: str
    species: str
    motion_id: str
    #: 출처는 필수다 — 무허가/스크랩 영상은 등록할 수 없다.
    source_type: str
    license: str
    provider_name: str
    license_reference: str | None = None
    source_description: str | None = None
    provenance_notes: str | None = None
    commercial_use_allowed: bool = False
    body_size_class: str = "UNKNOWN"
    leg_length_class: str = "UNKNOWN"
    body_length_class: str = "UNKNOWN"
    camera_view: str = "UNKNOWN"
    travel_direction: str = "UNKNOWN"
    speed_class: str = "UNKNOWN"
    start_pose: str | None = None
    end_pose: str | None = None
    duration_sec: float | None = None
    fps: float | None = None
    resolution: str | None = None
    loopable: bool = False
    object_path: str | None = None
    bucket: str | None = None
    pet_id: str | None = None


@router.post("/")
async def register_reference(body: RegisterRequest, user: AuthedUser = Depends(require_user)):
    try:
        ref = await svc.register_reference(**body.model_dump())
    except svc.MotionReferenceError as e:
        raise _http(e) from e
    return _ref_out(ref)


class StatusRequest(BaseModel):
    quality_status: str | None = None
    enabled: bool | None = None


@router.post("/{reference_key}/{version}/status")
async def set_status(
    reference_key: str,
    version: int,
    body: StatusRequest,
    user: AuthedUser = Depends(require_user),
):
    try:
        ref = await svc.set_status(
            reference_key=reference_key, version=version,
            quality_status=body.quality_status, enabled=body.enabled,
        )
    except svc.MotionReferenceError as e:
        raise _http(e) from e
    return _ref_out(ref)


@router.get("/resolve/{pet_id}/{motion_id}")
async def resolve_for_pet(
    pet_id: str,
    motion_id: str,
    view: Optional[str] = Query(default=None),
    direction: Optional[str] = Query(default=None),
    speed: Optional[str] = Query(default=None),
    # 벤치마크 매트릭스용 형태 오버라이드 (품종 아님 — 형태 클래스만).
    species: Optional[str] = Query(default=None),
    size: Optional[str] = Query(default=None),
    legs: Optional[str] = Query(default=None),
    body: Optional[str] = Query(default=None),
    user: AuthedUser = Depends(require_user),
):
    """펫 프로필 + 모션 → 최적 레퍼런스와 후보들 (선택 이유 전체 노출)."""
    from ..services import pet_identity_service

    identity_profile = None
    try:
        identity_profile = await pet_identity_service.get_profile(
            user_id=user.user_id, pet_id=pet_id
        )
    except pet_identity_service.PetIdentityError as e:
        raise HTTPException(
            status_code=e.status, detail={"code": e.code, "message": e.message}
        ) from e

    overrides = {
        k: v
        for k, v in (
            ("species", species),
            ("body_size_class", size),
            ("leg_length_class", legs),
            ("body_length_class", body),
        )
        if v
    }
    profile = svc.derive_motion_profile(identity_profile, overrides=overrides)
    try:
        resolved = await svc.resolve_motion_reference(
            profile=profile, motion_id=motion_id, pet_id=pet_id,
            desired_view=view, direction=direction, speed=speed,
            include_candidates=True,
        )
    except svc.MotionReferenceError as e:
        raise _http(e) from e
    return {
        "pet_id": pet_id,
        "motion_id": motion_id.upper(),
        "pet_motion_profile": profile,
        "resolution": resolved,  # None = LEVEL_4 (호환 레퍼런스 없음 — 정책은 5.1)
    }
