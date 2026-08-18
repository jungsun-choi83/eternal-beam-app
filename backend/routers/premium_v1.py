"""
프로덕션 프리미엄 API — 인증 필수.

dev_premium 과 **완전히 별개**다. 그쪽은 ENABLE_DEV_PREMIUM_TRIGGER 로 잠긴
개발 전용 경로이고 인증도 과금도 없다. 이 라우터는 항상 마운트되며, 모든
요청이 검증된 토큰을 요구한다.

엔드포인트는 두 개뿐이고 역할이 **엄격히 갈린다**:

    GET  /assets    발견(discovery). 조회만 한다 — 생성도 과금도 절대 없다.
    POST /purchase  구매 의사(purchase intent). 여기서만 크레딧이 나간다.

이 분리가 요구사항의 핵심이다: 화면이 마운트됐다는 이유로 결제가 일어나면 안 된다.
프론트는 GET 으로 상태를 폴링하고, POST 는 사용자가 명시적으로 구매할 때만 부른다.

생성 인프라는 재사용만 한다 — 큐·프로바이더·검증·승격·스토리지는 그대로다.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth import AuthedUser, require_user
from ..scenarios.pet_scenarios import IDLE_EVENTS, PET_ACTIONS, THEME_INDEPENDENT_PLACE_ID
from ..services import generated_motions_service as motions_svc
from ..services import premium_purchase
from ..services.credit_keyframe import is_remote_asset_url

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/pet/premium", tags=["pet-premium"])


def _public_api_base(request: Request) -> str:
    explicit = (os.getenv("PUBLIC_API_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip()
    return explicit.rstrip("/") if explicit else str(request.base_url).rstrip("/")


def _as_http(e: premium_purchase.PurchaseError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


class PurchaseRequest(BaseModel):
    #: "IDLE_BUNDLE" 또는 "ACTION:COME_CLOSER" (액션 id 만 줘도 된다)
    kind: str
    pet_id: str
    #: 누락분 생성을 위해 필요. 전부 READY 면 없어도 된다.
    pet_image_url: str | None = None


class PurchaseResponse(BaseModel):
    kind: str
    #: "ready" = 대상 전부 준비됨 / "processing" = 생성 중
    status: str
    #: **이번 호출이 실제로 차감한 크레딧.** 멱등 호출이면 0.
    credits_charged: int
    credits_remaining: int | None = None
    #: 액션 id → 재생 가능한 URL
    ready: dict[str, str] = {}
    #: 아직 만들어지는 중인 액션 id
    generating: list[str] = []
    #: 이번 호출이 프로바이더에 새로 넣은 액션 id
    submitted: list[str] = []
    #: 이전에 이미 구매한 사용자였는가
    already_owned: bool = False
    pet_id: str
    place_id: str = THEME_INDEPENDENT_PLACE_ID


class AssetsResponse(BaseModel):
    pet_id: str
    place_id: str = THEME_INDEPENDENT_PLACE_ID
    #: 액션 id → URL (재생 가능한 것만)
    ready: dict[str, str] = {}
    generating: list[str] = []
    missing: list[str] = []
    #: 레지스트리 그대로 — 프론트가 개수를 하드코딩하지 않게.
    idle_events: list[str] = []
    action_events: list[str] = []
    idle_bundle_credits: int = premium_purchase.IDLE_BUNDLE_CREDITS
    action_event_credits: int = premium_purchase.ACTION_EVENT_CREDITS


class IdentityResponse(BaseModel):
    """확정된 Eternal Beam 신원. 프론트는 이 값으로 로컬 user_id 를 맞춘다."""

    user_id: str
    email: str | None = None


@router.get("/identity", response_model=IdentityResponse)
async def get_identity(user: AuthedUser = Depends(require_user)):
    """
    이 토큰이 실제로 어떤 Eternal Beam 신원인가.

    프론트가 이걸 물어야 하는 이유: 로컬 user_id 는 예전 로그인 화면이 써 둔
    문자열이고, 지갑 조회 같은 레거시 경로가 그 값을 쓴다. 서버가 확정한 신원과
    다르면 프리미엄은 A 를, 잔액 표시는 B 를 보게 된다. 이 응답으로 둘을 맞춘다.
    """
    return IdentityResponse(user_id=user.user_id, email=user.email)


@router.get("/assets", response_model=AssetsResponse)
async def get_premium_assets(
    pet_id: str,
    user: AuthedUser = Depends(require_user),
):
    """
    이 펫의 프리미엄 자산 상태. **절대 생성하지 않고 절대 과금하지 않는다.**

    프론트의 발견/폴링 경로가 이것 하나다. 자산이 없다고 해서 이 호출이 생성을
    시작하는 일은 없다 — 그러려면 POST /purchase 로 명시적 의사가 있어야 한다.
    """
    pid = motions_svc.default_pet_id(user.user_id, pet_id)
    try:
        await premium_purchase.assert_pet_owned(user.user_id, pid)
    except premium_purchase.PurchaseError as e:
        raise _as_http(e) from e

    all_actions = tuple(IDLE_EVENTS) + tuple(PET_ACTIONS)
    state = await premium_purchase.asset_state(user.user_id, pid, all_actions)
    return AssetsResponse(
        pet_id=pid,
        ready=state.ready,
        generating=sorted(state.active),
        missing=sorted(state.missing),
        idle_events=list(IDLE_EVENTS),
        action_events=list(PET_ACTIONS),
    )


@router.post("/purchase", response_model=PurchaseResponse)
async def purchase_premium(
    request: Request,
    body: PurchaseRequest,
    user: AuthedUser = Depends(require_user),
):
    """
    명시적 구매. **크레딧이 나가는 유일한 프리미엄 경로다.**

    IDLE_BUNDLE        = 1 크레딧, 등록된 아이들 이벤트 전체
    ACTION:<ACTION_ID> = 1 크레딧, 액션 1건

    이미 구매했거나 이미 READY 면 charged=0 으로 돌아온다. 동시 요청은 구매 원장의
    부분 unique 인덱스가 하나로 좁힌다.
    """
    try:
        kind = premium_purchase.resolve_kind(body.kind)
    except premium_purchase.PurchaseError as e:
        raise _as_http(e) from e

    pid = motions_svc.default_pet_id(user.user_id, body.pet_id)
    image_url = (body.pet_image_url or "").strip() or None

    # data: URL 은 백엔드가 가져올 수 없다. 세션을 만들기 전에 거른다.
    if image_url and not is_remote_asset_url(image_url):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "PET_IMAGE_URL_NOT_REMOTE",
                "message": "pet_image_url 은 http(s) URL 이어야 합니다.",
            },
        )

    try:
        result = await premium_purchase.purchase(
            user_id=user.user_id,
            pet_id=pid,
            kind=kind,
            pet_image_url=image_url,
            api_base=_public_api_base(request),
        )
    except premium_purchase.PurchaseError as e:
        raise _as_http(e) from e

    if result.credits_charged:
        logger.warning(
            "프리미엄 구매 — kind=%s user=%s pet=%s credits=%s submitted=%s",
            kind, user.user_id, pid, result.credits_charged, result.submitted,
        )

    return PurchaseResponse(
        kind=result.kind,
        status=result.status,
        credits_charged=result.credits_charged,
        credits_remaining=result.credits_remaining,
        ready=result.ready,
        generating=result.generating,
        submitted=result.submitted,
        already_owned=result.already_owned,
        pet_id=pid,
    )
