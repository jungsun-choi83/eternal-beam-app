"""
/api/v1/pet/canonical — 정본 펫 빌더 (Phase 4). 내부/디버그 용도 — Swagger 로 충분.

    POST /{pet_id}/build            새 정본 버전 빌드 (멱등; force 로 강제 재빌드)
    GET  /{pet_id}                  최신(또는 ?version=) 정본 + 후보 전체
    GET  /{pet_id}/versions         버전 요약 목록
    GET  /{pet_id}/review           사람 검토용: 실제 레퍼런스 vs 후보 비교 페이로드
    POST /{pet_id}/evaluations      사람 평가 기록 (검증 하네스)
    GET  /evaluations/summary       프로바이더별 평가 요약

고객 온보딩 UI 는 바꾸지 않는다. 프로덕션 영상 생성은 이 라우터와 무관하다.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import AuthedUser, require_user
from ..services import canonical_pet_service as svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/pet/canonical", tags=["pet-canonical"])


def _http(e: svc.CanonicalPetError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


class BuildRequest(BaseModel):
    force: bool = False


class CandidateOut(BaseModel):
    id: str
    provider: str
    model: str | None = None
    model_version: str | None = None
    attempt: int
    external_job_id: str | None = None
    raw_object_path: str | None = None
    cutout_object_path: str | None = None
    input_reference_ids: list[str] = []
    generation_metadata: dict[str, Any] = {}
    qa_result: dict[str, Any] = {}
    decision: str
    selected: bool = False
    error: str | None = None
    created_at: str | None = None


class CanonicalVersionOut(BaseModel):
    id: str
    pet_id: str
    version: int
    status: str
    reference_set_version: int | None = None
    identity_profile_version: int | None = None
    input_reference_ids: list[str] = []
    prompt: str | None = None
    prompt_version: str | None = None
    output_spec: dict[str, Any] = {}
    selected_candidate_id: str | None = None
    selection_reason: str | None = None
    qa_summary: dict[str, Any] = {}
    analyzer_versions: dict[str, Any] = {}
    candidates: list[CandidateOut] = []
    created_at: str | None = None
    completed_at: str | None = None
    deduplicated: bool = False


def _out(v: svc.CanonicalVersion) -> CanonicalVersionOut:
    return CanonicalVersionOut(
        id=v.id,
        pet_id=v.pet_id,
        version=v.version,
        status=v.status,
        reference_set_version=v.reference_set_version,
        identity_profile_version=v.identity_profile_version,
        input_reference_ids=v.input_reference_ids,
        prompt=v.prompt,
        prompt_version=v.prompt_version,
        output_spec=v.output_spec,
        selected_candidate_id=v.selected_candidate_id,
        selection_reason=v.selection_reason,
        qa_summary=v.qa_summary,
        analyzer_versions=v.analyzer_versions,
        candidates=[
            CandidateOut(
                id=c.id,
                provider=c.provider,
                model=c.model,
                model_version=c.model_version,
                attempt=c.attempt,
                external_job_id=c.external_job_id,
                raw_object_path=c.raw_object_path,
                cutout_object_path=c.cutout_object_path,
                input_reference_ids=c.input_reference_ids,
                generation_metadata=c.generation_metadata,
                qa_result=c.qa_result,
                decision=c.decision,
                selected=c.selected,
                error=c.error,
                created_at=c.created_at,
            )
            for c in v.candidates
        ],
        created_at=v.created_at,
        completed_at=v.completed_at,
        deduplicated=v.deduplicated,
    )


@router.post("/{pet_id}/build", response_model=CanonicalVersionOut)
async def build_canonical(
    pet_id: str,
    body: BuildRequest | None = None,
    user: AuthedUser = Depends(require_user),
):
    try:
        v = await svc.build_canonical(
            user_id=user.user_id, pet_id=pet_id, skip_if_unchanged=not (body and body.force)
        )
    except svc.CanonicalPetError as e:
        raise _http(e) from e
    return _out(v)


@router.get("/{pet_id}/versions")
async def list_versions(pet_id: str, user: AuthedUser = Depends(require_user)):
    try:
        versions = await svc.list_canonical_versions(user_id=user.user_id, pet_id=pet_id)
    except svc.CanonicalPetError as e:
        raise _http(e) from e
    return {
        "pet_id": pet_id,
        "versions": [
            {
                "id": v.id,
                "version": v.version,
                "status": v.status,
                "reference_set_version": v.reference_set_version,
                "selected_candidate_id": v.selected_candidate_id,
                "qa_summary": v.qa_summary,
                "created_at": v.created_at,
            }
            for v in versions
        ],
    }


@router.get("/{pet_id}/review")
async def review_payload(
    pet_id: str,
    version: Optional[int] = Query(default=None, ge=1),
    user: AuthedUser = Depends(require_user),
):
    """실제 레퍼런스 vs 정본 후보 — 사람 눈으로 비교하기 위한 서명 URL 페이로드."""
    from ..services import pet_reference_service
    from ..services.asset_url_refresh import StorageObject, sign_object

    try:
        v = await svc.get_canonical(user_id=user.user_id, pet_id=pet_id, version=version)
    except svc.CanonicalPetError as e:
        raise _http(e) from e
    if not v:
        raise HTTPException(
            status_code=404,
            detail={"code": "CANONICAL_NOT_FOUND", "message": "정본 버전이 없습니다."},
        )

    refs = await pet_reference_service.list_references(user_id=user.user_id, pet_id=pet_id)
    refs_by_id = {str(r.id): r for r in refs}

    def _sign(bucket: str | None, path: str | None) -> str | None:
        if not path:
            return None
        try:
            return sign_object(StorageObject(bucket=bucket or "", path=path))
        except Exception:
            return None

    return {
        "pet_id": pet_id,
        "canonical_version": v.version,
        "status": v.status,
        "selection_reason": v.selection_reason,
        "references": [
            {
                "reference_id": rid,
                "role": next(
                    (
                        p.get("role")
                        for p in (v.output_spec.get("input_references") or [])
                        if p.get("reference_id") == rid
                    ),
                    None,
                ),
                "url": _sign(refs_by_id[rid].bucket, refs_by_id[rid].object_path)
                if rid in refs_by_id
                else None,
            }
            for rid in v.input_reference_ids
        ],
        "candidates": [
            {
                "id": c.id,
                "provider": c.provider,
                "model": c.model,
                "attempt": c.attempt,
                "decision": c.decision,
                "selected": c.selected,
                "qa_result": c.qa_result,
                "raw_url": _sign(c.raw_bucket, c.raw_object_path),
                "cutout_url": _sign(c.cutout_bucket, c.cutout_object_path),
                "error": c.error,
            }
            for c in v.candidates
        ],
    }


@router.get("/{pet_id}", response_model=CanonicalVersionOut)
async def get_canonical(
    pet_id: str,
    version: Optional[int] = Query(default=None, ge=1),
    user: AuthedUser = Depends(require_user),
):
    try:
        v = await svc.get_canonical(user_id=user.user_id, pet_id=pet_id, version=version)
    except svc.CanonicalPetError as e:
        raise _http(e) from e
    if not v:
        raise HTTPException(
            status_code=404,
            detail={"code": "CANONICAL_NOT_FOUND", "message": "정본 버전이 없습니다."},
        )
    return _out(v)


class EvaluationRequest(BaseModel):
    canonical_version_id: str
    candidate_id: str | None = None
    #: {face_identity, markings, body_proportions, tail_ears_paws, anatomy,
    #:  overall_same_pet} 각 0~10.
    scores: dict[str, float] = {}
    verdict: str
    notes: str | None = None


@router.post("/{pet_id}/evaluations")
async def post_evaluation(
    pet_id: str,
    body: EvaluationRequest,
    user: AuthedUser = Depends(require_user),
):
    try:
        row = await svc.record_evaluation(
            user_id=user.user_id,
            pet_id=pet_id,
            canonical_version_id=body.canonical_version_id,
            candidate_id=body.candidate_id,
            scores=body.scores,
            verdict=body.verdict,
            notes=body.notes,
        )
    except svc.CanonicalPetError as e:
        raise _http(e) from e
    return row


@router.get("/evaluations/summary")
async def get_evaluation_summary(user: AuthedUser = Depends(require_user)):
    try:
        return await svc.evaluation_summary(user_id=user.user_id)
    except svc.CanonicalPetError as e:
        raise _http(e) from e
