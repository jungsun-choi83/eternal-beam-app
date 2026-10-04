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


class CancelGenerationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: 왜 멈추는가 — 진단용 자유 문자열 (예: user_cancelled, poll_timeout).
    reason: str = Field(default="user_cancelled", min_length=1, max_length=200)


class BusinessQAFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    complaints: list[str] = Field(default_factory=list, max_length=4)
    comment: str | None = Field(default=None, max_length=1000)


class BusinessQAFeedbackResponse(BaseModel):
    run_id: str
    accepted: bool
    complaints: list[str]
    recorded: bool = True


class GenerationRunResponse(BaseModel):
    run_id: str
    user_id: str
    pet_id: str
    content_id: str
    motion_id: str
    request_kind: str
    idempotency_key: str
    status: str
    terminal_state: str | None = None
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
    lease_recoveries: int = 0


#: Internal Business QA receipts (authority evidence, advisory reasons, legacy
#: decision). Only the derived terminal_state is customer-facing.
_INTERNAL_PROVIDER_STATE_KEYS = frozenset({"_business_qa"})
_BUSINESS_DELIVER_ACTIONS = frozenset({"DELIVER", "DELIVER_WITH_ADVISORY"})


def _response(run: service.PetGenerationRun) -> GenerationRunResponse:
    hidden = {"id", "execution_token"}
    payload = {key: value for key, value in service.run_dict(run).items() if key not in hidden}
    payload["terminal_state"] = (
        ((run.provider_state or {}).get("_business_qa") or {}).get("terminal_state")
    )
    payload["provider_state"] = {
        key: value
        for key, value in dict(payload.get("provider_state") or {}).items()
        if key not in _INTERNAL_PROVIDER_STATE_KEYS
    }
    return GenerationRunResponse(run_id=run.id, **payload)


def _public_qa_decision(run: service.PetGenerationRun, legacy: str | None) -> str:
    """Customer-facing QA label for a playback response.

    A published run whose Business QA receipt authorized delivery reports PASS;
    the advisory flag and the stored legacy decision stay internal. Without a
    delivering receipt the legacy value is returned unchanged.
    """

    business = dict(((run.provider_state or {}).get("_business_qa") or {}))
    receipt = business.get("decision") if isinstance(business.get("decision"), dict) else {}
    if (
        run.status == service.STATUS_PUBLISHED
        and receipt.get("delivery_action") in _BUSINESS_DELIVER_ACTIONS
        and receipt.get("integrity_status") != "FAIL"
    ):
        return "PASS"
    return legacy or "PASS"


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


@router.post("/{run_id}/feedback", response_model=BusinessQAFeedbackResponse)
async def record_business_qa_feedback(
    run_id: str,
    body: BusinessQAFeedbackRequest,
    user: AuthedUser = Depends(require_user),
):
    """Record Phase-12 product feedback; it has no QA or retry authority."""

    from ..services import business_qa_user_test

    try:
        run = await service.get_generation_run(user_id=user.user_id, run_id=run_id)
        row = business_qa_user_test.record_feedback(
            run=run,
            user_id=user.user_id,
            accepted=body.accepted,
            complaints=body.complaints,
            comment=body.comment,
        )
    except service.PetGenerationRunError as exc:
        raise _http(exc) from exc
    except business_qa_user_test.UserTestError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return BusinessQAFeedbackResponse(
        run_id=run.id,
        accepted=bool(row["accepted"]),
        complaints=list(row.get("complaints") or []),
    )


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
    terminal_state: str | None = None
    fallback_tier: str | None = None
    asset_kind: str | None = None


@router.get("/{run_id}/playback", response_model=RunPlaybackResponse)
async def get_run_playback(
    run_id: str,
    user: AuthedUser = Depends(require_user),
):
    """
    이 실행이 만든 BREATHING 의 재생 해석 (Phase 7G). 읽기 전용.

    PUBLISHED 실행은 발행 포인터(하이드레이션과 같은 근거)로 답한다.
    REVIEW 로 끝난 실행은 포장된 후보를 **발행 없이** 돌려준다 — QA 상태는
    그대로 REVIEW 이고, pets 포인터는 만들어지지 않는다. Phase 7 fallback은
    기록된 provenance에서 재생 URL만 다시 해석하며 발행/소유권 포인터를 바꾸지 않는다.
    """
    try:
        run = await service.get_generation_run(user_id=user.user_id, run_id=run_id)
    except service.PetGenerationRunError as exc:
        raise _http(exc) from exc

    from ..services import motion_delivery_service as delivery
    from ..services import motion_publication_service as publication
    from ..services import customer_fallback_service as fallback_service

    business = dict(((run.provider_state or {}).get("_business_qa") or {}))
    if business.get("terminal_state") == service.BUSINESS_DELIVERED_FALLBACK:
        asset = business.get("fallback_asset")
        if not isinstance(asset, dict):
            raise HTTPException(
                status_code=409,
                detail={"code": "FALLBACK_PROVENANCE_MISSING", "message": "Fallback asset provenance is missing."},
            )
        try:
            url = fallback_service.resolve_fallback_url(asset)
        except fallback_service.FallbackInfrastructureError as exc:
            raise HTTPException(
                status_code=503, detail={"code": exc.code, "message": exc.message}
            ) from exc
        return RunPlaybackResponse(
            run_id=run.id,
            status=run.status,
            published=False,
            device_test_only=False,
            qa_decision="FALLBACK",
            url=url,
            delivery_format=str(asset.get("delivery_format") or "") or None,
            background_baked=False,
            motion_version_id=str(asset.get("motion_version_id") or "") or None,
            candidate_id=str(asset.get("candidate_id") or "") or None,
            breathing_object_path=str(asset.get("object_path") or "") or None,
            terminal_state=service.BUSINESS_DELIVERED_FALLBACK,
            fallback_tier=str(asset.get("tier") or "") or None,
            asset_kind=str(asset.get("asset_kind") or "") or None,
        )

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
            # 무결성 게이트로 발행된 REVIEW/FAIL 후보는 그 결정을 그대로 싣는다.
            qa_decision=_public_qa_decision(run, getattr(published, "qa_decision", None)),
            url=published.url,
            delivery_format=published.delivery_format,
            background_baked=published.background_baked,
            motion_version_id=published.motion_version_id,
            candidate_id=run.selected_candidate_id,
            breathing_object_path=published.breathing_object_path,
            terminal_state=service.BUSINESS_DELIVERED_GENERATED,
        )

    if not run.motion_version_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "RUN_NOT_PLAYABLE",
                "message": "이 실행은 아직 재생 가능한 모션을 만들지 못했습니다.",
            },
        )
    from ..services import business_qa

    try:
        # Unpublished playback is gated under the run's stamped QA authority.
        with business_qa.qa_authority_scope(business_qa.run_qa_authority(run.provider_state)):
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
        qa_decision=_public_qa_decision(run, playback.qa_decision),
        url=playback.url,
        delivery_format=playback.delivery_format,
        background_baked=False,
        motion_version_id=playback.motion_version_id,
        candidate_id=playback.candidate_id,
        breathing_object_path=playback.derived_video_path,
        terminal_state=(
            service.BUSINESS_DELIVERED_GENERATED
            if run.status == service.STATUS_PUBLISHED else None
        ),
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


@router.post("/{run_id}/cancel", response_model=GenerationRunResponse)
async def cancel_generation_run(
    run_id: str,
    body: CancelGenerationRunRequest | None = None,
    user: AuthedUser = Depends(require_user),
):
    """
    명시적 정지. 활성 실행을 CANCELLED 로 옮기고 lease 를 비운다 — 워커는 다음
    fenced 쓰기에서 멈추고, 이 실행은 다시 자동으로 집히지 않는다. 이미 끝난
    실행은 그대로 돌려준다(멱등).
    """
    try:
        run = await service.cancel_generation_run(
            user_id=user.user_id,
            run_id=run_id,
            reason=(body.reason if body else "user_cancelled"),
        )
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
