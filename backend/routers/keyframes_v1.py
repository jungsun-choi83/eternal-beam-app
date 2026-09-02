"""
/api/v1/pet/keyframes — 액션 키프레임 빌더 (Phase 5). 내부/디버그 용도.

    GET  /roles                       지원 역할 + 액션 매핑 (레지스트리 조회)
    POST /{pet_id}/build              특정 역할 키프레임 빌드 (멱등; force 재빌드)
    GET  /{pet_id}                    역할별 최신 키프레임 요약
    GET  /{pet_id}/{keyframe_role}    후보/QA 포함 상세 (?version=)
    POST /{pet_id}/{keyframe_role}/evaluations   사람 평가 (Phase 4 하네스 확장)

프로덕션 Luma/Wan 경로는 이 라우터와 무관하다.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import AuthedUser, require_user
from ..services import action_keyframe_service as svc
from ..services import action_keyframe_spec as spec_mod
from ..services import motion_spec as motion_mod

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/pet/keyframes", tags=["pet-keyframes"])


def _http(e: svc.ActionKeyframeError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


@router.get("/roles")
async def list_roles(user: AuthedUser = Depends(require_user)):
    """지원 키프레임 역할 — 액션 id 는 기존 레지스트리의 것을 그대로 매핑한다."""
    return {
        "spec_version": spec_mod.KEYFRAME_SPEC_VERSION,
        "prompt_version": spec_mod.KEYFRAME_PROMPT_VERSION,
        "roles": [
            spec_mod.role_spec_snapshot(spec_mod.KEYFRAME_ROLES[r])
            for r in spec_mod.KEYFRAME_ROLE_ORDER
        ],
    }


@router.get("/motions")
async def list_motions(user: AuthedUser = Depends(require_user)):
    """모션 레지스트리 (Phase 5.1) — 트리거는 모션이 아니라 모션으로 해석된다."""
    return {
        "motion_spec_version": motion_mod.MOTION_SPEC_VERSION,
        "contract_version": motion_mod.PHASE6_CONTRACT_VERSION,
        "motions": [
            motion_mod.motion_snapshot(motion_mod.MOTIONS[m]) for m in motion_mod.MOTION_ORDER
        ],
        "triggers": dict(motion_mod.TRIGGERS),
    }


@router.get("/{pet_id}/motions/{motion_id}/spec")
async def resolve_motion_spec(
    pet_id: str,
    motion_id: str,
    user: AuthedUser = Depends(require_user),
):
    """Phase 6 정본 입력 계약 — 읽기 전용, 생성/과금 없음."""
    try:
        return await motion_mod.resolve_video_generation_spec(
            user_id=user.user_id, pet_id=pet_id, motion_id=motion_id
        )
    except motion_mod.MotionSpecError as e:
        raise HTTPException(
            status_code=e.status, detail={"code": e.code, "message": e.message}
        ) from e


class BuildRequest(BaseModel):
    keyframe_role: str
    force: bool = False


class CandidateOut(BaseModel):
    id: str
    provider: str
    model: str | None = None
    attempt: int
    external_job_id: str | None = None
    raw_object_path: str | None = None
    cutout_object_path: str | None = None
    input_canonical_candidate_id: str | None = None
    input_reference_ids: list[str] = []
    qa_result: dict[str, Any] = {}
    decision: str
    selected: bool = False
    error: str | None = None


class KeyframeOut(BaseModel):
    id: str
    pet_id: str
    keyframe_role: str
    version: int
    status: str
    canonical_version_id: str | None = None
    canonical_version: int | None = None
    selected_candidate_id: str | None = None
    selection_reason: str | None = None
    prompt: str | None = None
    prompt_version: str | None = None
    spec: dict[str, Any] = {}
    qa_summary: dict[str, Any] = {}
    analyzer_versions: dict[str, Any] = {}
    candidates: list[CandidateOut] = []
    created_at: str | None = None
    completed_at: str | None = None
    deduplicated: bool = False


def _out(k: svc.ActionKeyframe) -> KeyframeOut:
    return KeyframeOut(
        id=k.id,
        pet_id=k.pet_id,
        keyframe_role=k.keyframe_role,
        version=k.version,
        status=k.status,
        canonical_version_id=k.canonical_version_id,
        canonical_version=k.canonical_version,
        selected_candidate_id=k.selected_candidate_id,
        selection_reason=k.selection_reason,
        prompt=k.prompt,
        prompt_version=k.prompt_version,
        spec=k.spec,
        qa_summary=k.qa_summary,
        analyzer_versions=k.analyzer_versions,
        candidates=[
            CandidateOut(
                id=c.id,
                provider=c.provider,
                model=c.model,
                attempt=c.attempt,
                external_job_id=c.external_job_id,
                raw_object_path=c.raw_object_path,
                cutout_object_path=c.cutout_object_path,
                input_canonical_candidate_id=c.input_canonical_candidate_id,
                input_reference_ids=c.input_reference_ids,
                qa_result=c.qa_result,
                decision=c.decision,
                selected=c.selected,
                error=c.error,
            )
            for c in k.candidates
        ],
        created_at=k.created_at,
        completed_at=k.completed_at,
        deduplicated=k.deduplicated,
    )


@router.post("/{pet_id}/build", response_model=KeyframeOut)
async def build_keyframe(
    pet_id: str,
    body: BuildRequest,
    user: AuthedUser = Depends(require_user),
):
    try:
        k = await svc.build_keyframe(
            user_id=user.user_id,
            pet_id=pet_id,
            keyframe_role=body.keyframe_role,
            skip_if_unchanged=not body.force,
        )
    except svc.ActionKeyframeError as e:
        raise _http(e) from e
    return _out(k)


@router.get("/{pet_id}")
async def list_keyframes(pet_id: str, user: AuthedUser = Depends(require_user)):
    try:
        keyframes = await svc.list_keyframes(user_id=user.user_id, pet_id=pet_id)
    except svc.ActionKeyframeError as e:
        raise _http(e) from e
    return {
        "pet_id": pet_id,
        "keyframes": [
            {
                "id": k.id,
                "keyframe_role": k.keyframe_role,
                "version": k.version,
                "status": k.status,
                "canonical_version": k.canonical_version,
                "selected_candidate_id": k.selected_candidate_id,
                "qa_summary": k.qa_summary,
                "created_at": k.created_at,
            }
            for k in sorted(keyframes, key=lambda k: k.keyframe_role)
        ],
    }


@router.get("/{pet_id}/{keyframe_role}", response_model=KeyframeOut)
async def get_keyframe(
    pet_id: str,
    keyframe_role: str,
    version: Optional[int] = Query(default=None, ge=1),
    user: AuthedUser = Depends(require_user),
):
    try:
        k = await svc.get_keyframe(
            user_id=user.user_id, pet_id=pet_id, keyframe_role=keyframe_role, version=version
        )
    except svc.ActionKeyframeError as e:
        raise _http(e) from e
    if not k:
        raise HTTPException(
            status_code=404,
            detail={"code": "KEYFRAME_NOT_FOUND", "message": "키프레임이 없습니다."},
        )
    return _out(k)


class EvaluationRequest(BaseModel):
    keyframe_id: str
    candidate_id: str | None = None
    #: {face_identity, markings, body_proportions, pose_correctness, anatomy,
    #:  phase6_suitability, overall_same_pet, tail_ears_paws} 각 0~10.
    scores: dict[str, float] = {}
    verdict: str
    notes: str | None = None


@router.post("/{pet_id}/{keyframe_role}/evaluations")
async def post_evaluation(
    pet_id: str,
    keyframe_role: str,  # noqa: ARG001 — 경로 문서화용; 평가는 keyframe_id 로 귀속된다
    body: EvaluationRequest,
    user: AuthedUser = Depends(require_user),
):
    try:
        return await svc.record_keyframe_evaluation(
            user_id=user.user_id,
            pet_id=pet_id,
            keyframe_id=body.keyframe_id,
            candidate_id=body.candidate_id,
            scores=body.scores,
            verdict=body.verdict,
            notes=body.notes,
        )
    except svc.ActionKeyframeError as e:
        raise _http(e) from e
