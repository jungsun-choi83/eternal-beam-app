"""One authenticated API for Phase 7C's server-owned Phase 2–7A pipeline."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..auth import AuthedUser, require_user
from ..services import pet_generation_run_service as service

router = APIRouter(prefix="/v1/pet/generation-runs", tags=["pet-generation-runs"])


class StartGenerationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pet_id: str
    motion_id: str = service.MOTION_BREATHING
    request_kind: str = service.REQUEST_FREE_HOME
    idempotency_key: str = Field(min_length=1, max_length=200)


class ReplacementGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)


class GenerationRunResponse(BaseModel):
    run_id: str
    user_id: str
    pet_id: str
    content_id: str
    motion_id: str
    request_kind: str
    idempotency_key: str
    status: str
    current_stage: str
    identity_profile_id: str | None = None
    identity_profile_version: int | None = None
    reference_set_id: str | None = None
    reference_set_version: int | None = None
    canonical_version_id: str | None = None
    canonical_version: int | None = None
    keyframes: dict[str, Any] = Field(default_factory=dict)
    motion_spec_version: str | None = None
    motion_version_id: str | None = None
    motion_version: int | None = None
    selected_candidate_id: str | None = None
    publication_id: str | None = None
    provider_state: dict[str, Any] = Field(default_factory=dict)
    last_error: dict[str, Any] | None = None
    retry_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    worker_id: str | None = None
    lease_expires_at: str | None = None
    next_attempt_at: str | None = None


def _response(run: service.PetGenerationRun) -> GenerationRunResponse:
    hidden = {"id", "execution_token"}
    payload = {key: value for key, value in service.run_dict(run).items() if key not in hidden}
    return GenerationRunResponse(run_id=run.id, **payload)


def _http(exc: service.PetGenerationRunError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message},
    )


@router.post("", response_model=GenerationRunResponse, status_code=202)
async def start_generation_run(
    body: StartGenerationRunRequest,
    user: AuthedUser = Depends(require_user),
):
    """Create/reuse one logical run; a separate worker performs all generation."""
    try:
        run = await service.start_generation_run(
            user_id=user.user_id,
            pet_id=body.pet_id,
            motion_id=body.motion_id,
            request_kind=body.request_kind,
            idempotency_key=body.idempotency_key,
        )
    except service.PetGenerationRunError as exc:
        raise _http(exc) from exc
    return _response(run)


@router.get("/{run_id}", response_model=GenerationRunResponse)
async def get_generation_run(
    run_id: str,
    user: AuthedUser = Depends(require_user),
):
    try:
        run = await service.get_generation_run(user_id=user.user_id, run_id=run_id)
    except service.PetGenerationRunError as exc:
        raise _http(exc) from exc
    return _response(run)


class RunPlaybackResponse(BaseModel):
    run_id: str
    status: str
    #: True = Phase 7A 발행 재생 (pets 포인터). False = 개발/현재-실행 재생 —
    #: 발행이 아니며 QA 결정(qa_decision)이 데이터베이스 그대로 실린다.
    published: bool
    #: Device D1 — 미발행 자산은 **기기 전송 테스트 용도로만** 쓸 수 있다는 명시
    #: 표식. published 의 역이지만 계약을 이름으로 못 박는다: 이 값이 true 인
    #: 재생을 프로덕션 홈 모션으로 제시하면 안 된다.
    device_test_only: bool = False
    qa_decision: str
    url: str
    delivery_format: str | None = None
    background_baked: bool = False
    motion_version_id: str | None = None
    candidate_id: str | None = None
    breathing_object_path: str | None = None


@router.get("/{run_id}/playback", response_model=RunPlaybackResponse)
async def get_run_playback(
    run_id: str,
    user: AuthedUser = Depends(require_user),
):
    """
    이 실행이 만든 BREATHING 의 재생 해석 (Phase 7G). 읽기 전용.

    PUBLISHED 실행은 발행 포인터(하이드레이션과 같은 근거)로 답한다.
    REVIEW 로 끝난 실행은 포장된 후보를 **발행 없이** 돌려준다 — QA 상태는
    그대로 REVIEW 이고, pets 포인터는 만들어지지 않는다. FAIL/ERROR 는 409.
    """
    try:
        run = await service.get_generation_run(user_id=user.user_id, run_id=run_id)
    except service.PetGenerationRunError as exc:
        raise _http(exc) from exc

    from ..services import motion_delivery_service as delivery
    from ..services import motion_publication_service as publication

    # 발행 포인터(pets.breathing_*)는 BREATHING 전용이다 — 프리미엄 실행(Phase 7H)의
    # 발행 재생은 아래 delivery 리졸버가 후보의 packed 파생물로 직접 해석한다.
    if run.status == service.STATUS_PUBLISHED and run.motion_id == service.MOTION_BREATHING:
        try:
            published = await publication.get_published_breathing(
                user_id=user.user_id, pet_id=run.pet_id
            )
        except publication.MotionPublicationError as exc:
            raise HTTPException(
                status_code=exc.status, detail={"code": exc.code, "message": exc.message}
            ) from exc
        return RunPlaybackResponse(
            run_id=run.id,
            status=run.status,
            published=True,
            device_test_only=False,
            qa_decision="PASS",
            url=published.url,
            delivery_format=published.delivery_format,
            background_baked=published.background_baked,
            motion_version_id=published.motion_version_id,
            candidate_id=run.selected_candidate_id,
            breathing_object_path=published.breathing_object_path,
        )

    if not run.motion_version_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "RUN_NOT_PLAYABLE",
                "message": "이 실행은 아직 재생 가능한 모션을 만들지 못했습니다.",
            },
        )
    try:
        playback = await delivery.resolve_breathing_playback(
            user_id=user.user_id,
            pet_id=run.pet_id,
            motion_version_id=run.motion_version_id,
            candidate_id=run.selected_candidate_id,
        )
    except delivery.MotionDeliveryError as exc:
        raise HTTPException(
            status_code=exc.status, detail={"code": exc.code, "message": exc.message}
        ) from exc
    return RunPlaybackResponse(
        run_id=run.id,
        status=run.status,
        # 프리미엄 실행은 PUBLISHED 면 발행 재생이다 (발행 원장 + 포인터가 있다).
        # BREATHING 은 위 분기가 담당하므로 여기 도달하면 항상 미발행(REVIEW)이다.
        published=(run.status == service.STATUS_PUBLISHED),
        device_test_only=(run.status != service.STATUS_PUBLISHED),
        qa_decision=playback.qa_decision,
        url=playback.url,
        delivery_format=playback.delivery_format,
        background_baked=False,
        motion_version_id=playback.motion_version_id,
        candidate_id=playback.candidate_id,
        breathing_object_path=playback.derived_video_path,
    )


@router.post("/{run_id}/retry", response_model=GenerationRunResponse)
async def retry_generation_run(
    run_id: str,
    user: AuthedUser = Depends(require_user),
):
    try:
        run = await service.retry_generation_run(user_id=user.user_id, run_id=run_id)
    except service.PetGenerationRunError as exc:
        raise _http(exc) from exc
    return _response(run)


@router.post("/{run_id}/replacement", response_model=GenerationRunResponse, status_code=202)
async def request_replacement_generation(
    run_id: str,
    body: ReplacementGenerationRequest,
    user: AuthedUser = Depends(require_user),
):
    """Queue one QA-justified replacement; the API never calls the provider."""
    try:
        run = await service.request_replacement_generation(
            user_id=user.user_id,
            run_id=run_id,
            idempotency_key=body.idempotency_key,
            reason=body.reason,
        )
    except service.PetGenerationRunError as exc:
        raise _http(exc) from exc
    return _response(run)
