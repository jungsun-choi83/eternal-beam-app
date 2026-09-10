"""
/api/v1/pet/motions — 모션 비디오 빌더 (Phase 6). 내부/디버그 용도.

    POST /{pet_id}/{motion_id}/build          모션 영상 빌드 (멱등; force 재빌드)
    POST /{pet_id}/BREATHING/publish          Phase 6 PASS → 제품 breathing 포인터 (Phase 7A)
    POST /{pet_id}/BREATHING/package          raw → packed-alpha 파생물 포장 (Phase 7F)
    GET  /{pet_id}/BREATHING/published        발행된 BREATHING 하이드레이션 — 새 서명 URL (Phase 7F)
    GET  /{pet_id}                            모션별 최신 버전 요약
    GET  /{pet_id}/{motion_id}                후보/QA 포함 상세 (?version=)
    POST /{pet_id}/{motion_id}/evaluations    사람 평가 (Phase 4/5 하네스 확장)

프로덕션 Luma/Wan·테마·크레딧·디바이스는 이 라우터와 무관하다.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import AuthedUser, require_user
from ..services import motion_delivery_service as delivery_svc
from ..services import motion_video_service as svc
from ..services import motion_publication_service as publication_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/pet/motions", tags=["pet-motions"])


def _http(e: svc.MotionVideoError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


# ⚠️ 리터럴 경로는 파라미터 경로(/{pet_id}/{motion_id})보다 먼저 등록돼야 한다.
@router.get("/calibration/report")
async def qa_calibration(user: AuthedUser = Depends(require_user)):
    """자동 QA vs 사람 판정 (Phase 6.5) — 임계값 재캘리브레이션 근거."""
    try:
        return await svc.qa_calibration_report(user_id=user.user_id)
    except svc.MotionVideoError as e:
        raise _http(e) from e


class BuildRequest(BaseModel):
    force: bool = False


class QaRerunRequest(BaseModel):
    motion_version_id: str
    candidate_id: str


class PublishBreathingRequest(BaseModel):
    motion_version_id: str


class BreathingPublicationOut(BaseModel):
    publication_id: str
    motion_version_id: str
    selected_candidate_id: str
    pet_id: str
    content_id: str | None = None
    breathing_bucket: str
    breathing_object_path: str
    # StoredPipeline.idle_video_url 과 같은 필드명 — 기존 브라우저 재생 계약.
    idle_video_url: str
    background_baked: bool = False
    # Phase 7F — 발행 객체의 명시적 전달 포맷 ('packed_alpha' | None=레거시).
    delivery_format: str | None = None
    published_at: str | None = None
    deduplicated: bool = False


class PackageBreathingRequest(BaseModel):
    motion_version_id: str
    #: 미지정이면 selected 후보. REVIEW 후보 포장(개발 검증)에는 명시 지정.
    candidate_id: str | None = None
    force: bool = False


class MotionDeliveryOut(BaseModel):
    motion_version_id: str
    candidate_id: str
    pet_id: str
    delivery_format: str
    derived_bucket: str
    derived_video_path: str
    raw_video_path: str
    frame_count: int
    fps: float
    matte_backend: str
    warnings: list[str] = []
    deduplicated: bool = False


class PublishedBreathingOut(BaseModel):
    pet_id: str
    motion_id: str
    breathing_bucket: str
    breathing_object_path: str
    #: 매 호출 새로 서명된 URL — 저장하지 말고 그대로 재생에 쓴다.
    url: str
    background_baked: bool
    motion_version_id: str | None = None
    delivery_format: str | None = None
    publication_id: str | None = None
    content_id: str | None = None


class CandidateOut(BaseModel):
    id: str
    provider: str
    model: str | None = None
    attempt: int
    provider_job_id: str | None = None
    raw_video_path: str | None = None
    derived_video_path: str | None = None
    start_keyframe_id: str | None = None
    target_keyframe_id: str | None = None
    motion_reference_id: str | None = None
    input_references: list[dict[str, Any]] = []
    generation_metadata: dict[str, Any] = {}
    qa_result: dict[str, Any] = {}
    decision: str
    selected: bool = False
    error: str | None = None


class MotionVersionOut(BaseModel):
    id: str
    pet_id: str
    motion_id: str
    motion_class: str
    version: int
    status: str
    motion_spec_version: str | None = None
    start_keyframe_id: str | None = None
    target_keyframe_id: str | None = None
    canonical_version_id: str | None = None
    selected_candidate_id: str | None = None
    selection_reason: str | None = None
    video_strategy: str | None = None
    output_spec: dict[str, Any] = {}
    prompt: str | None = None
    prompt_version: str | None = None
    qa_summary: dict[str, Any] = {}
    analyzer_versions: dict[str, Any] = {}
    warnings: list[str] = []
    candidates: list[CandidateOut] = []
    created_at: str | None = None
    completed_at: str | None = None
    deduplicated: bool = False


def _out(v: svc.MotionVersion) -> MotionVersionOut:
    return MotionVersionOut(
        id=v.id,
        pet_id=v.pet_id,
        motion_id=v.motion_id,
        motion_class=v.motion_class,
        version=v.version,
        status=v.status,
        motion_spec_version=v.motion_spec_version,
        start_keyframe_id=v.start_keyframe_id,
        target_keyframe_id=v.target_keyframe_id,
        canonical_version_id=v.canonical_version_id,
        selected_candidate_id=v.selected_candidate_id,
        selection_reason=v.selection_reason,
        video_strategy=v.video_strategy,
        output_spec=v.output_spec,
        prompt=v.prompt,
        prompt_version=v.prompt_version,
        qa_summary=v.qa_summary,
        analyzer_versions=v.analyzer_versions,
        warnings=v.warnings,
        candidates=[
            CandidateOut(
                id=c.id,
                provider=c.provider,
                model=c.model,
                attempt=c.attempt,
                provider_job_id=c.provider_job_id,
                raw_video_path=c.raw_video_path,
                derived_video_path=c.derived_video_path,
                start_keyframe_id=c.start_keyframe_id,
                target_keyframe_id=c.target_keyframe_id,
                motion_reference_id=c.motion_reference_id,
                input_references=c.input_references,
                generation_metadata=c.generation_metadata,
                qa_result=c.qa_result,
                decision=c.decision,
                selected=c.selected,
                error=c.error,
            )
            for c in v.candidates
        ],
        created_at=v.created_at,
        completed_at=v.completed_at,
        deduplicated=v.deduplicated,
    )


@router.post("/{pet_id}/BREATHING/publish", response_model=BreathingPublicationOut)
async def publish_breathing(
    pet_id: str,
    body: PublishBreathingRequest,
    user: AuthedUser = Depends(require_user),
):
    """
    기존 Phase 6 QA PASS BREATHING 을 제품 ``pets.breathing_*`` 포인터로 발행한다.

    생성·프로바이더 호출·업로드는 하지 않는다. 같은 motion_version_id 재호출은
    기존 발행을 돌려주는 멱등 성공이다.
    """
    try:
        result = await publication_svc.publish_breathing(
            user_id=user.user_id,
            pet_id=pet_id,
            motion_version_id=body.motion_version_id,
        )
    except publication_svc.MotionPublicationError as e:
        raise HTTPException(
            status_code=e.status, detail={"code": e.code, "message": e.message}
        ) from e
    return BreathingPublicationOut(**result.__dict__)


@router.post("/{pet_id}/BREATHING/package", response_model=MotionDeliveryOut)
async def package_breathing(
    pet_id: str,
    body: PackageBreathingRequest,
    user: AuthedUser = Depends(require_user),
):
    """
    Phase 6 raw(회색 배경) → packed-alpha 파생물 포장 (Phase 7F).

    raw_video_path 는 불변으로 남고 derived_video_path 에 결과가 기록된다.
    후보 decision/selected 는 절대 바뀌지 않는다 — 발행 게이트는 Phase 7A 그대로.
    같은 후보 재호출은 기존 파생물을 돌려주는 멱등 성공이다.
    """
    try:
        result = await delivery_svc.package_breathing_for_delivery(
            user_id=user.user_id,
            pet_id=pet_id,
            motion_version_id=body.motion_version_id,
            candidate_id=body.candidate_id,
            force=body.force,
        )
    except delivery_svc.MotionDeliveryError as e:
        raise HTTPException(
            status_code=e.status, detail={"code": e.code, "message": e.message}
        ) from e
    return MotionDeliveryOut(**result.__dict__)


@router.get("/{pet_id}/BREATHING/published", response_model=PublishedBreathingOut)
async def get_published_breathing(
    pet_id: str,
    user: AuthedUser = Depends(require_user),
):
    """
    현재 발행된 BREATHING 하이드레이션 (Phase 7F). 읽기 전용.

    저장된 서명 URL 을 재사용하지 않는다 — 호출 시점에 새로 서명한다.
    delivery_format='packed_alpha' 면 브라우저는 packed 렌더러를 명시 선택해야
    하고, None 이면 레거시 규칙(blackkey/baked)이 그대로 적용된다.
    """
    try:
        result = await publication_svc.get_published_breathing(
            user_id=user.user_id, pet_id=pet_id
        )
    except publication_svc.MotionPublicationError as e:
        raise HTTPException(
            status_code=e.status, detail={"code": e.code, "message": e.message}
        ) from e
    return PublishedBreathingOut(**result.__dict__)


@router.post("/{pet_id}/{motion_id}/build", response_model=MotionVersionOut)
async def build_motion(
    pet_id: str,
    motion_id: str,
    body: BuildRequest | None = None,
    user: AuthedUser = Depends(require_user),
):
    try:
        v = await svc.build_motion_video(
            user_id=user.user_id,
            pet_id=pet_id,
            motion_id=motion_id,
            skip_if_unchanged=not (body and body.force),
        )
    except svc.MotionVideoError as e:
        raise _http(e) from e
    return _out(v)


@router.post("/{pet_id}/{motion_id}/qa-rerun", response_model=MotionVersionOut)
async def rerun_motion_qa(
    pet_id: str,
    motion_id: str,
    body: QaRerunRequest,
    user: AuthedUser = Depends(require_user),
):
    """Re-evaluate one stored candidate; never submits a generation provider job."""
    try:
        version = await svc.reevaluate_motion_candidate(
            user_id=user.user_id,
            pet_id=pet_id,
            motion_id=motion_id,
            motion_version_id=body.motion_version_id,
            candidate_id=body.candidate_id,
        )
    except svc.MotionVideoError as e:
        raise _http(e) from e
    return _out(version)


@router.get("/{pet_id}")
async def list_motions(pet_id: str, user: AuthedUser = Depends(require_user)):
    try:
        versions = await svc.list_motion_versions(user_id=user.user_id, pet_id=pet_id)
    except svc.MotionVideoError as e:
        raise _http(e) from e
    return {
        "pet_id": pet_id,
        "motions": [
            {
                "id": v.id,
                "motion_id": v.motion_id,
                "motion_class": v.motion_class,
                "version": v.version,
                "status": v.status,
                "video_strategy": v.video_strategy,
                "selected_candidate_id": v.selected_candidate_id,
                "qa_summary": v.qa_summary,
                "created_at": v.created_at,
            }
            for v in sorted(versions, key=lambda v: v.motion_id)
        ],
    }


@router.get("/{pet_id}/{motion_id}", response_model=MotionVersionOut)
async def get_motion(
    pet_id: str,
    motion_id: str,
    version: Optional[int] = Query(default=None, ge=1),
    user: AuthedUser = Depends(require_user),
):
    try:
        v = await svc.get_motion_version(
            user_id=user.user_id, pet_id=pet_id, motion_id=motion_id, version=version
        )
    except svc.MotionVideoError as e:
        raise _http(e) from e
    if not v:
        raise HTTPException(
            status_code=404,
            detail={"code": "MOTION_NOT_FOUND", "message": "모션 버전이 없습니다."},
        )
    return _out(v)


class EvaluationRequest(BaseModel):
    motion_version_id: str
    candidate_id: str | None = None
    #: {identity_fidelity, markings, anatomy, motion_correctness,
    #:  temporal_stability, naturalness, start_end_quality} 각 0~10.
    scores: dict[str, float] = {}
    verdict: str
    overall_usable: bool | None = None
    notes: str | None = None


@router.post("/{pet_id}/{motion_id}/evaluations")
async def post_evaluation(
    pet_id: str,
    motion_id: str,  # noqa: ARG001 — 경로 문서화용; 평가는 motion_version_id 로 귀속
    body: EvaluationRequest,
    user: AuthedUser = Depends(require_user),
):
    try:
        return await svc.record_motion_evaluation(
            user_id=user.user_id,
            pet_id=pet_id,
            motion_version_id=body.motion_version_id,
            candidate_id=body.candidate_id,
            scores=body.scores,
            verdict=body.verdict,
            overall_usable=body.overall_usable,
            notes=body.notes,
        )
    except svc.MotionVideoError as e:
        raise _http(e) from e
