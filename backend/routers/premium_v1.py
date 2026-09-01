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
from ..services import behavior_preferences
from ..services import generated_motions_service as motions_svc
from ..services import premium_entitlement
from ..services import premium_purchase
from ..services import generation_credits
from ..services import product_catalog
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
    #: 상품 키 → 크레딧 가격. **상품마다 다를 수 있다** (Phase 3).
    #:
    #: 예전에는 idle_bundle_credits / action_event_credits 두 스칼라였고, 그 값은
    #: 환경변수에서 **import 시점에** 읽혀 응답 모델의 기본값으로 박혔다. 그래서
    #: (a) 아이들 이벤트 넷이 반드시 같은 값이어야 했고,
    #: (b) 가격을 바꾸려면 재배포해야 했으며,
    #: (c) 프로세스가 뜬 뒤 값을 바꿔도 응답은 옛 값을 계속 실어 보냈다.
    #:
    #: 이제 요청마다 카탈로그에서 읽는다. 화면은 이 map 을 그대로 보여 주면 된다.
    prices: dict[str, int] = {}
    #: 행동 id → ON/OFF. **등록된 프리미엄 행동 전체**가 들어온다(기본 켬).
    #: ⚠️ READY 와 무관한 별개 상태다 — 아직 만들지 않은 행동에도 값이 있다.
    #: ⚠️ 아직 재생에 연결되지 않았다 (Phase 5 는 저장까지).
    preferences: dict[str, bool] = {}
    #: 프리미엄 **생성**이 허용되는가 (구독 active 또는 해지 유예 기간).
    #: ⚠️ 재생 권한이 아니다 — ready 의 자산은 false 여도 계속 재생된다.
    entitled: bool = False
    #: "active" | "canceled" | "expired" | null(구독 이력 없음)
    subscription_status: str | None = None
    #: 구독 게이트가 켜져 있는가. false 면 예전 크레딧 과금 경로다.
    subscription_required: bool = True


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

    # 구독 상태 조회가 실패해도 **발견은 계속돼야 한다.** 이 응답의 ready 목록은
    # 재생에 쓰이고, 재생은 구독과 무관하다(만료돼도 READY 는 재생된다). 여기서
    # 503 을 던지면 구독 조회 장애가 재생 화면을 통째로 죽인다. 생성 인가는
    # POST /purchase 가 자기 자리에서 다시, fail-closed 로 판정한다.
    try:
        ent = await premium_entitlement.get_entitlement(user.user_id)
    except premium_entitlement.EntitlementUnavailableError:
        logger.warning("자산 조회 중 구독 상태 확인 실패 — 발견은 계속한다 (user=%s)", user.user_id)
        ent = premium_entitlement.EntitlementState(
            entitled=False, status=None, enforced=premium_entitlement.subscription_required()
        )

    # 선호는 구독과 무관하게 읽는다 — 만료된 사용자도 자기가 꺼 둔 설정을 볼 수
    # 있어야 하고, 갱신하면 그대로 돌아와야 한다.
    try:
        prefs = await behavior_preferences.get_preferences(user.user_id, pid)
    except behavior_preferences.PreferenceError as e:
        raise HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message}) from e

    # 가격은 **요청 시점의 카탈로그**에서 읽는다. 카탈로그가 잠깐 죽어도 발견은
    # 계속돼야 하므로(위 구독 조회와 같은 이유 — ready 목록은 재생에 쓰이고 재생은
    # 가격과 무관하다) 빈 map 으로 떨어진다. 실제 과금은 POST /purchase 가 자기
    # 자리에서 fail-closed 로 다시 판정한다.
    prices: dict[str, int] = {}
    try:
        for p in await product_catalog.list_products():
            if p.product_type in (product_catalog.TYPE_IDLE, product_catalog.TYPE_ACTION):
                prices[p.product_key] = p.credit_price
    except product_catalog.CatalogUnavailableError:
        logger.warning("자산 조회 중 카탈로그 확인 실패 — 발견은 계속한다 (user=%s)", user.user_id)

    return AssetsResponse(
        pet_id=pid,
        ready=state.ready,
        generating=sorted(state.active),
        missing=sorted(state.missing),
        idle_events=list(IDLE_EVENTS),
        action_events=list(PET_ACTIONS),
        prices=prices,
        preferences=prefs,
        entitled=ent.entitled,
        subscription_status=ent.status,
        subscription_required=ent.enforced,
    )


class PreferenceRequest(BaseModel):
    pet_id: str
    #: PREMIUM_ACTIONS 의 canonical id (BLINKING / … / COME_CLOSER)
    action_id: str
    enabled: bool


class PreferenceResponse(BaseModel):
    pet_id: str
    #: 갱신된 **전체** 선호 — 프론트가 응답 하나로 화면을 다시 그린다.
    preferences: dict[str, bool] = {}


@router.put("/preference", response_model=PreferenceResponse)
async def set_behavior_preference(
    body: PreferenceRequest,
    user: AuthedUser = Depends(require_user),
):
    """
    행동 하나의 ON/OFF 저장. **생성을 일으키지 않는다.**

    인가 규칙이 /purchase 와 **의도적으로 다르다**:

      소유권  필요하다 — 남의 펫 설정을 바꿀 수 없다.
      구독    요구하지 않는다.

    구독을 요구하지 않는 이유: 선호는 결제 대상이 아니라 사용자 데이터다. 만료를
    이유로 쓰기를 막으면 "만료돼도 설정은 보존된다"는 계약이 사용자 입장에서
    반쪽이 된다(볼 수는 있는데 고칠 수는 없다). 토글은 프로바이더 비용을 쓰지
    않고 아직 재생에도 연결되지 않으므로, 막아서 지킬 것이 없다.
    생성 컨트롤은 예전 그대로 구독이 필요하다 — 그쪽이 비용을 쓰는 경로다.
    """
    pid = motions_svc.default_pet_id(user.user_id, body.pet_id)
    try:
        await premium_purchase.assert_pet_owned(user.user_id, pid)
    except premium_purchase.PurchaseError as e:
        raise _as_http(e) from e

    try:
        prefs = await behavior_preferences.set_preference(
            user_id=user.user_id, pet_id=pid,
            action_id=body.action_id, enabled=body.enabled,
        )
    except behavior_preferences.PreferenceError as e:
        raise HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message}) from e

    return PreferenceResponse(pet_id=pid, preferences=prefs)


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


# ── 공용 생성 구매 (Phase 8) ─────────────────────────────────────────────────


class GeneratePurchaseRequest(BaseModel):
    pet_id: str
    #: IDLE_EVENTS 또는 PET_ACTIONS 의 canonical id (BLINKING / COME_CLOSER …)
    action_id: str
    #: **한 번의 사용자 조작**을 가리키는 키. 재시도·새로고침은 같은 값을,
    #: 새로 만들기는 새 값을 보낸다. 이것이 "다시 만들면 다시 낸다"를 가능하게 한다.
    idempotency_key: str
    pet_image_url: str | None = None


class GeneratePurchaseResponse(BaseModel):
    action_id: str
    product_key: str
    #: **이번 호출이 실제로 잡은 크레딧.** 재시도면 0.
    credits_charged: int
    credits_remaining: int
    submitted: bool
    #: 이번 생성 **전** 기준으로 이미 갖고 있던 버전 수. 게이트가 아니라 표시용.
    owned_versions: int


@router.post("/generate", response_model=GeneratePurchaseResponse)
async def post_generate_with_credits(
    request: Request,
    body: GeneratePurchaseRequest,
    user: AuthedUser = Depends(require_user),
):
    """
    **아이들·액션 공용** 크레딧 생성 (Phase 8).

        Paw Wave → 4 예약 → 생성 → 검증 → 확정 → Paw Wave #1 영구 소유
        또 Paw Wave → 또 4  → …            → Paw Wave #2 영구 소유

    테마 구매(/v1/themes/purchase-with-credits)와 **같은 지갑·같은 원장·같은
    카탈로그·같은 멱등 모델**을 쓴다. 다른 것은 가격과 사유뿐이고 둘 다 데이터다.

    이미 갖고 있어도 막지 않는다 — 소유 개수는 표시용이지 게이트가 아니다.
    """
    pid = motions_svc.default_pet_id(user.user_id, body.pet_id)
    try:
        await premium_purchase.assert_pet_owned(user.user_id, pid)
    except premium_purchase.PurchaseError as e:
        raise _as_http(e) from e

    image_url = (body.pet_image_url or "").strip() or None
    if image_url and not is_remote_asset_url(image_url):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "PET_IMAGE_URL_NOT_REMOTE",
                "message": "pet_image_url 은 http(s) URL 이어야 합니다.",
            },
        )
    if not image_url:
        raise HTTPException(
            status_code=400,
            detail={"code": "PET_IMAGE_REQUIRED", "message": "pet_image_url 이 필요합니다."},
        )

    try:
        out = await generation_credits.purchase_generation(
            user_id=user.user_id,
            pet_id=pid,
            action_id=body.action_id,
            idempotency_key=body.idempotency_key,
            pet_image_url=image_url,
            api_base=_public_api_base(request),
        )
    except generation_credits.GenerationCreditError as e:
        raise HTTPException(
            status_code=e.status, detail={"code": e.code, "message": e.message}
        ) from e

    if out.credits_charged:
        logger.warning(
            "생성 구매 — user=%s pet=%s product=%s credits=%s 잔액=%s",
            user.user_id, pid, out.product_key, out.credits_charged, out.credits_remaining,
        )
    return GeneratePurchaseResponse(
        action_id=out.action_id,
        product_key=out.product_key,
        credits_charged=out.credits_charged,
        credits_remaining=out.credits_remaining,
        submitted=out.submitted,
        owned_versions=out.owned_versions,
    )
