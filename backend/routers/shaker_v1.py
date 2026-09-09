"""
QR Shaker API — 공개 재생 1개 + 소유자 관리 3개.

    GET  /shaker/pet                공개. 인증 없음. **조회만** 한다.
    POST /shaker/share              인증 필수. 공유 링크 발급.
    POST /shaker/share/{id}/revoke  인증 필수. 폐기.
    GET  /shaker/shares             인증 필수. 내 링크 목록(토큰 없음).

── 공개 엔드포인트의 계약 ────────────────────────────────────────────────────
1. **pet_id 로 조회하지 않는다.** 토큰이 펫을 데려온다. URL 의 petId 는 표시용이며
   넘어오면 일치 검사에만 쓴다 — 바꿔 넣어서 얻을 수 있는 것이 없다.

2. **절대 생성하지 않는다.** 이 모듈은 premium_generation / generation_queue /
   credit_generation_service 를 import 하지 않는다. 자산 조회는
   premium_purchase.asset_state() 하나뿐이고 그건 읽기 전용이다(과금도 제출도 없다).
   경로가 없으므로 실수로 생성될 수 없다 — 이것이 "Shaker 는 생성하지 않는다"의
   실제 보장이다.

3. **응답이 허용 목록이다.** ShakerPetResponse 에 선언된 필드만 나간다. 계정·지갑·
   구독·결제·주문·프로바이더 필드는 모델에 존재하지 않으므로 실을 방법이 없다.
   소유자 user_id 는 서버 안에서만 쓰이고 응답 모델에 자리가 없다.

4. **BREATHING 은 정책 밖이다.** 언제나 무료이며, 이 링크가 그것을 "잠금 해제"하는
   것이 아니라 그저 가리킨다. 더블탭 액션만 정책(shaker_policy)의 대상이다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ..auth import AuthedUser, require_user
from ..scenarios.pet_scenarios import IDLE_EVENTS, PET_ACTIONS
from ..services import asset_url_refresh
from ..services import generated_motions_service as motions_svc
from ..services import premium_purchase, shaker_policy, shaker_rate_limit, shaker_share

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/shaker", tags=["shaker-v1"])


def _as_http(e: shaker_share.ShareError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


def _fresh_url(object_path: str | None, bucket: str | None, stored_url: str | None) -> str | None:
    """
    지금 유효한 URL 하나.

    우선순위가 중요하다:
      1) 저장된 **객체 경로** — 만료되지 않는 정본. 여기서 새 서명을 만든다.
      2) 저장된 URL 을 파싱해 경로를 얻는다 — 경로 컬럼 이전에 만들어진 행.
      3) 저장된 URL 그대로 — 외부 CDN·공개 버킷이거나 Supabase 미설정(로컬).

    3단계가 있어야 재서명이 불가능한 환경에서도 재생이 멈추지 않는다. 재서명은
    개선이지 전제가 아니다.
    """
    if object_path:
        signed = asset_url_refresh.sign_object(
            asset_url_refresh.StorageObject(
                bucket=(bucket or "").strip() or asset_url_refresh.default_bucket(),
                path=object_path,
            )
        )
        if signed:
            return signed
    if stored_url:
        return asset_url_refresh.refresh_url(stored_url)
    return stored_url


def _client_key(request: Request) -> str:
    """
    레이트 리밋 키. 프록시(Vercel/Render) 뒤이므로 X-Forwarded-For 를 우선한다.

    위조 가능하지만 위조하는 쪽은 자기 카운터만 흩뜨린다 — 남의 카운터를 채워
    차단시키는 것이 아니므로 이 용도에는 충분하다.
    """
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if fwd:
        return fwd
    return (request.client.host if request.client else "") or "unknown"


# ── 공개 재생 ─────────────────────────────────────────────────────────────────


class ShakerAction(BaseModel):
    """재생 가능한 액션 하나. id 와 URL 뿐 — 가격도 상태도 싣지 않는다."""

    id: str
    url: str


class ShakerPetResponse(BaseModel):
    """
    공개 응답의 **전체**. 이 모델이 곧 허용 목록이다.

    여기에 없는 것: user_id, email, 구독 상태, 크레딧/지갑, 주문, 결제,
    프로바이더(luma/fal), 생성 진행 상태(generating/missing).

    generating/missing 을 뺀 것은 단순 누락이 아니다 — 그것은 소유자가 지금 무엇을
    만들고 있는지에 대한 정보이고, 링크를 받은 사람이 알 이유가 없다.
    """

    pet_id: str
    pet_name: str | None = None
    #: 무료 BREATHING 루프. 정책과 무관하게 언제나 실린다.
    breathing_url: str
    poster_url: str | None = None
    #: 이 펫의 BREATHING 영상이 배경을 이미 담고 있는가 (Phase 27).
    #:
    #: 프론트(shaker-api.ts)는 **이미 이 필드를 읽고 있었다.** 서버가 보낸 적이
    #: 없어서 언제나 false 로 떨어졌고, 그래서 구운 영상을 QR 로 열면 블랙키
    #: 제거가 걸려 장면의 어두운 픽셀이 뚫린 채 재생됐다.
    #:
    #: 기본 false = 레거시. 기존 인쇄물이 가리키는 자산은 전부 지금까지처럼 나간다.
    background_baked: bool = False
    #: 정책이 허용한 READY 액션만. 기본 정책(disabled)에서는 빈 목록이다.
    actions: list[ShakerAction] = []
    #: 더블탭이 재생할 액션 id. 없으면 null — 프론트는 더블탭을 무시한다.
    double_tap_action_id: str | None = None


def _proxy_assets_enabled() -> bool:
    """
    재생 URL 을 우리 도메인으로 감쌀 것인가. 기본 켜짐.

    끄면 서명 URL 이 응답에 그대로 실린다 — 되돌리기 스위치다.
    """
    return (os.getenv("SHAKER_PROXY_ASSET_URLS", "1").strip().lower()
            not in ("0", "false", "no"))


def _asset_proxy_url(share_token: str, kind: str) -> str:
    from urllib.parse import quote

    return f"/api/v1/shaker/asset?share={quote(share_token, safe='')}&k={quote(kind, safe='')}"


@dataclass
class _ResolvedShaker:
    """토큰 검증 + 정책 판정 + 서명 재발급을 마친 결과."""

    rec: shaker_share.ShareRecord
    breathing_url: str
    poster_url: str | None
    #: 정책이 허용한 액션 → 지금 유효한 URL
    action_urls: dict[str, str]
    double_tap: str | None


async def _resolve_public_shaker(
    request: Request, share: str, pet_id: str | None
) -> _ResolvedShaker:
    """
    공개 경로 **공통** 해석기.

    /pet 과 /asset 이 이것을 함께 쓴다. 나눠 두면 한쪽만 정책을 검사하는 순간
    /asset 이 멤버십 게이트를 통째로 우회하는 구멍이 된다 — 실제로 가장 쉽게
    생기는 종류의 실수라, 판정을 물리적으로 한 함수에 묶는다.
    """
    verdict = shaker_rate_limit.check(_client_key(request))
    if not verdict.allowed:
        raise HTTPException(
            status_code=429,
            detail={"code": "RATE_LIMITED", "message": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."},
            headers={"Retry-After": str(verdict.retry_after)},
        )

    try:
        rec = await shaker_share.resolve_share(share, expected_pet_id=pet_id)
    except shaker_share.ShareError as e:
        raise _as_http(e) from e

    # ── READY 자산 조회 — 읽기 전용 경로 하나뿐 ────────────────────────────────
    # asset_state() 는 과금도 제출도 하지 않는다(services/premium_purchase.py).
    # 실패해도 **BREATHING 은 계속 나가야 한다** — BREATHING 은 무료이고 프리미엄
    # 자산 조회 장애와 무관하다. 여기서 500 을 던지면 조회 장애가 무료 경험을 죽인다.
    ready_ids: list[str] = []
    ready_urls: dict[str, str] = {}
    try:
        state = await premium_purchase.asset_state(
            rec.user_id, rec.pet_id, tuple(IDLE_EVENTS) + tuple(PET_ACTIONS)
        )
        ready_urls = dict(state.ready)
        ready_ids = list(ready_urls.keys())
    except Exception:  # noqa: BLE001 — 프리미엄 조회 실패가 무료 재생을 막지 않는다
        logger.warning("Shaker READY 자산 조회 실패 — BREATHING 만 제공한다 (pet=%s)", rec.pet_id)

    # ── 정책 판정 입력 ─────────────────────────────────────────────────────────
    # 정책이 요구할 때만 조회한다. disabled/A/B 경로는 구독·선호 테이블을 아예
    # 건드리지 않는다 — 건드리지 않으면 새어 나갈 수도 없다.
    #
    # 둘 다 **판정 불가는 거절**이다(fail closed). 조회 장애가 곧 무료 배포가 되면
    # 안 된다. 거절돼도 BREATHING 은 그대로 나간다.
    owner_entitled: bool | None = None
    preferences: dict[str, bool] | None = None

    if ready_ids and shaker_policy.requires_owner_entitlement():
        try:
            from ..services import premium_entitlement

            owner_entitled = (await premium_entitlement.get_entitlement(rec.user_id)).entitled
        except Exception:  # noqa: BLE001
            logger.warning("Shaker 구독 조회 실패 — 액션을 잠근다 (pet=%s)", rec.pet_id)
            owner_entitled = False

    # 선호는 구독이 유효할 때만 읽는다. 자격이 없으면 어차피 결과가 빈 목록이라
    # 조회가 낭비이고, 공개 요청이 소유자 테이블을 불필요하게 두드리게 된다.
    if ready_ids and shaker_policy.requires_preferences() and owner_entitled:
        try:
            from ..services import behavior_preferences

            preferences = await behavior_preferences.get_preferences(rec.user_id, rec.pet_id)
        except Exception:  # noqa: BLE001
            logger.warning("Shaker 선호 조회 실패 — 액션을 잠근다 (pet=%s)", rec.pet_id)
            preferences = None  # permitted_action_ids 가 None 을 거절로 해석한다

    permitted = shaker_policy.permitted_action_ids(
        ready_ids, owner_entitled=owner_entitled, preferences=preferences
    )
    double_tap = shaker_policy.select_double_tap_action(
        ready_ids, owner_entitled=owner_entitled, preferences=preferences
    )

    # ── 서명 재발급 ────────────────────────────────────────────────────────────
    # 저장된 URL 을 그대로 내보내지 않는다. 업로드 시점 서명은 7일짜리인데 QR 은
    # 인쇄되어 그보다 오래 산다 — 저장값을 믿으면 8일째부터 재생이 죽는다.
    # 재서명은 **읽기 서명 생성**일 뿐이라 생성/업로드를 하지 않는다.
    breathing_url = _fresh_url(rec.breathing_object_path, rec.breathing_bucket, rec.breathing_url)
    poster_url = _fresh_url(rec.poster_object_path, rec.poster_bucket, rec.poster_url)
    fresh_actions = asset_url_refresh.refresh_urls(
        {a: ready_urls[a] for a in permitted if a in ready_urls}
    )

    return _ResolvedShaker(
        rec=rec,
        breathing_url=breathing_url or "",
        poster_url=poster_url,
        action_urls=fresh_actions,
        double_tap=double_tap if (double_tap in fresh_actions) else None,
    )


@router.get("/pet", response_model=ShakerPetResponse)
async def get_shaker_pet(
    request: Request,
    response: Response,
    share: str,
    pet_id: str | None = None,
):
    """
    공유 링크로 펫 하나를 연다. **인증 없음, 생성 없음, 과금 없음.**

    pet_id 는 선택이며 조회에 쓰이지 않는다. 넘어오면 토큰이 데려온 펫과 같은지
    확인하고, 다르면 404 다(유효하지 않은 링크와 같은 답 — 탐색 힌트를 주지 않는다).
    """
    resolved = await _resolve_public_shaker(request, share, pet_id)

    # 공유 링크는 개인적인 것이다. 중간 캐시가 응답을 들고 있으면 폐기해도 계속
    # 열리는 것처럼 보인다 — 폐기를 실효화하려면 캐시를 금지해야 한다.
    # 새 서명이 매번 달라지므로 캐시되면 만료 문제도 그대로 돌아온다.
    response.headers["Cache-Control"] = "no-store"

    # ── 재생 URL 을 우리 도메인으로 감싼다 ─────────────────────────────────────
    # 스토리지 객체 경로가 `{user_id}/{content_id}/…` 이고 user_id 는 **이메일**이다.
    # 서명 URL 을 그대로 실으면 공개 응답이 고객 이메일을 노출한다 —
    # "계정/개인정보를 절대 노출하지 않는다"는 계약과 정면으로 어긋난다.
    #
    # 프록시는 바이트를 흘려보내지 않는다. 302 로 갓 서명한 URL 을 가리킬 뿐이라
    # 대역폭 비용이 없고, 응답 본문에서는 경로가 사라진다.
    if _proxy_assets_enabled():
        breathing = _asset_proxy_url(share, "breathing")
        poster = _asset_proxy_url(share, "poster") if resolved.poster_url else None
        actions = [
            ShakerAction(id=a, url=_asset_proxy_url(share, a)) for a in sorted(resolved.action_urls)
        ]
    else:
        breathing = resolved.breathing_url
        poster = resolved.poster_url
        actions = [
            ShakerAction(id=a, url=resolved.action_urls[a]) for a in sorted(resolved.action_urls)
        ]

    return ShakerPetResponse(
        pet_id=resolved.rec.pet_id,
        pet_name=resolved.rec.pet_name,
        breathing_url=breathing,
        poster_url=poster,
        background_baked=resolved.rec.background_baked,
        actions=actions,
        double_tap_action_id=resolved.double_tap,
    )


@router.get("/asset")
async def get_shaker_asset(
    request: Request,
    share: str,
    k: str,
    pet_id: str | None = None,
):
    """
    재생 자산 리다이렉트. **바이트를 흘려보내지 않는다** — 302 만 준다.

    k = "breathing" | "poster" | 액션 id

    액션은 /pet 과 **같은 정책 판정**을 통과해야 한다. 이 엔드포인트가 판정을
    건너뛰면 멤버십 게이트를 통째로 우회하는 구멍이 된다 — 그래서 두 경로가
    _resolve_public_shaker 하나를 공유한다.
    """
    resolved = await _resolve_public_shaker(request, share, pet_id)

    kind = (k or "").strip()
    if kind == "breathing":
        target = resolved.breathing_url
    elif kind == "poster":
        target = resolved.poster_url or ""
    else:
        # 정책이 허용한 액션만. 허용 목록 밖이면 존재 여부도 알려 주지 않는다.
        target = resolved.action_urls.get(kind.upper(), "")

    if not target:
        raise HTTPException(
            status_code=404,
            detail={"code": "ASSET_NOT_AVAILABLE", "message": "재생할 수 없는 자산입니다."},
        )

    return RedirectResponse(
        url=target,
        status_code=302,
        headers={"Cache-Control": "no-store"},
    )


# ── 소유자 관리 (인증 필수) ───────────────────────────────────────────────────


class CreateShareRequest(BaseModel):
    pet_id: str
    #: 공개 표시용. 서버에 펫 이름 저장소가 없어 소유자가 넘긴다(발급 시점 스냅샷).
    pet_name: str | None = None
    #: 무료 BREATHING 루프 URL. 프론트의 pipeline.idle_video_url 이 그대로 온다.
    breathing_url: str
    poster_url: str | None = None
    #: null 이면 무기한. 인쇄된 QR 은 회수할 수 없으므로 기본이 무기한이다.
    ttl_days: int | None = None


class CreateShareResponse(BaseModel):
    share_id: str
    #: ⚠️ **이 응답에서만 존재한다.** 서버는 해시만 저장하므로 다시 볼 수 없다.
    token: str
    #: QR 에 그대로 넣을 상대 경로.
    share_path: str
    pet_id: str


def _share_path(pet_id: str, token: str) -> str:
    from urllib.parse import quote

    return f"/shaker?petId={quote(pet_id, safe='')}&share={quote(token, safe='')}"


@router.post("/share", response_model=CreateShareResponse)
async def create_shaker_share(
    body: CreateShareRequest,
    user: AuthedUser = Depends(require_user),
):
    """
    공유 링크 발급. **생성을 일으키지 않는다** — 이미 있는 URL 을 가리킬 뿐이다.

    소유권을 요구한다: 남의 펫으로 공유 링크를 만들 수 없다. 구독은 요구하지
    않는다 — BREATHING 은 무료이고, 그것을 공유하는 것도 무료다.
    """
    pid = motions_svc.default_pet_id(user.user_id, body.pet_id)
    try:
        await premium_purchase.assert_pet_owned(user.user_id, pid)
    except premium_purchase.PurchaseError as e:
        raise HTTPException(
            status_code=e.status, detail={"code": e.code, "message": e.message}
        ) from e

    try:
        share_id, token = await shaker_share.create_share(
            user_id=user.user_id,
            pet_id=pid,
            pet_name=body.pet_name,
            breathing_url=body.breathing_url,
            poster_url=body.poster_url,
            ttl_days=body.ttl_days,
        )
    except shaker_share.ShareError as e:
        raise _as_http(e) from e

    logger.info("Shaker 공유 발급 — user=%s pet=%s share=%s", user.user_id, pid, share_id)
    return CreateShareResponse(
        share_id=share_id, token=token, share_path=_share_path(pid, token), pet_id=pid
    )


class RevokeResponse(BaseModel):
    share_id: str
    #: 이번 호출이 실제로 폐기했는가. 이미 폐기된 링크면 false (오류가 아니다).
    revoked: bool


@router.post("/share/{share_id}/revoke", response_model=RevokeResponse)
async def revoke_shaker_share(
    share_id: str,
    user: AuthedUser = Depends(require_user),
):
    """링크 폐기. 멱등 — 두 번 눌러도 오류가 아니다."""
    try:
        changed = await shaker_share.revoke_share(user_id=user.user_id, share_id=share_id)
    except shaker_share.ShareError as e:
        raise _as_http(e) from e
    return RevokeResponse(share_id=share_id, revoked=changed)


class ShareSummary(BaseModel):
    """
    소유자용 요약. **토큰이 없다** — 저장하지 않으므로 돌려줄 수가 없다.
    잃어버렸으면 폐기하고 새로 발급하는 것이 유일한 경로다.
    """

    share_id: str
    pet_id: str
    pet_name: str | None = None
    created_at: str | None = None
    revoked_at: str | None = None
    expires_at: str | None = None


class ShareListResponse(BaseModel):
    shares: list[ShareSummary] = []


@router.get("/shares", response_model=ShareListResponse)
async def list_shaker_shares(
    pet_id: str | None = None,
    user: AuthedUser = Depends(require_user),
):
    """내가 만든 공유 링크 목록. 남의 것은 조회되지 않는다(user_id 로 좁힌다)."""
    try:
        rows = await shaker_share.list_shares(user_id=user.user_id, pet_id=pet_id)
    except shaker_share.ShareError as e:
        raise _as_http(e) from e
    return ShareListResponse(
        shares=[
            ShareSummary(
                share_id=r.share_id,
                pet_id=r.pet_id,
                pet_name=r.pet_name,
                created_at=r.created_at,
                revoked_at=r.revoked_at,
                expires_at=r.expires_at,
            )
            for r in rows
        ]
    )
