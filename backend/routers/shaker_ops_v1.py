"""
판매자/운영 Shaker 도구 — **내부 전용.** 물리 제품에 인쇄될 QR 을 만드는 경로다.

    GET  /shaker/ops/pets            고객 펫 찾기
    GET  /shaker/ops/shares          이 펫의 공유 링크 보기
    POST /shaker/ops/share           공유 생성 (없으면 만들고 있으면 그대로 씀)
    POST /shaker/ops/share/{id}/revoke
    GET  /shaker/ops/share/{id}/qr   QR 내려받기 (svg/png)

── 인가 ─────────────────────────────────────────────────────────────────────
require_ops = 검증된 JWT **위에** 운영자 allowlist. 공유 시크릿 하나로 열지
않는 이유는 감사 추적이다 — 인쇄되어 나가는 링크라 "누가 만들었는가"가 남아야 한다.
SHAKER_OPS_USER_IDS 가 비어 있으면 전원 403 (fail closed).

── canonical petId 를 하나로 유지한다 ───────────────────────────────────────
이 라우터에는 **펫 생성 경로가 없다.** 운영자는 이미 존재하는 petId 를 찾아
가리킬 뿐이다. 소유자도 운영자가 입력하지 않는다 — 서버가 canonical 바인딩에서
읽는다(오타 하나로 남의 펫에 QR 이 붙는 것을 구조적으로 막는다).

편지·메모리 박스·웹앱·기기가 전부 같은 petId 를 가리킨다. 제품별로 펫이 갈라질
자리가 애초에 없다.

── 생성하지 않는다 ──────────────────────────────────────────────────────────
프로바이더를 호출하지 않는다. 자산이 없으면 만들지 않고 **거절**한다.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..auth import AuthedUser
from ..services import (
    asset_url_refresh,
    pet_registry,
    qr_service,
    shaker_ops,
    shaker_qr_artifact,
    shaker_share,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/shaker/ops", tags=["shaker-ops"])

#: 공유가 어디에 쓰이는가. 인쇄물에 붙은 링크와 고객 링크를 구분한다.
_VALID_PURPOSES = ("OPS", "LETTER", "MEMORY_BOX", "CUSTOMER")


def _as_http(e: Exception) -> HTTPException:
    code = getattr(e, "code", "ERROR")
    message = getattr(e, "message", str(e))
    status = getattr(e, "status", 400)
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _public_web_base(request: Request, *, purpose: str | None = None) -> str:
    """
    QR 에 넣을 **고객이 여는** 주소의 베이스.

    API 베이스가 아니다 — Shaker 는 프론트 라우트다. 잘못 쓰면 QR 이 API 도메인을
    가리켜 아무것도 열리지 않는다.

    ⚠️ **인쇄물 목적(LETTER/MEMORY_BOX)이면 요청 호스트로 유도하지 않는다.**
    인쇄된 QR 은 회수할 수 없어서, 개발 장비나 API 도메인을 가리킨 카드가 찍혀
    나가는 것이 가장 비싼 실패다. 그때는 PUBLIC_WEB_BASE_URL 이 없으면 거절한다
    (fail closed). 화면용(OPS/CUSTOMER)만 폴백한다.

    화면용 폴백 순서:
      1) PUBLIC_WEB_BASE_URL
      2) **요청을 보낸 운영 콘솔의 오리진** — /ops/shaker 는 웹앱 안의 화면이므로
         그 오리진이 /shaker 가 실제로 사는 곳이다.
      3) request.base_url (최후) — API 오리진일 수 있다.

    2) 가 없으면 로컬/분리 도메인 배포에서 미리보기 링크가 **API 도메인**을
    가리킨다. 거기에는 /shaker 라우트가 없어서 운영자는 Shaker 대신 404 를 만나고,
    거기서 웹앱 루트로 되돌아가면 고객 앱 업로드 화면이 열린다 — 이미 만든 펫을
    다시 만들게 되는 경로다.
    """
    if qr_service.is_print_purpose(purpose):
        return qr_service.assert_printable_base()

    explicit = qr_service.configured_web_base()
    if explicit:
        return explicit
    caller = _requesting_web_origin(request)
    if caller:
        return caller
    return str(request.base_url).rstrip("/")


def _requesting_web_origin(request: Request) -> str:
    """
    이 요청을 보낸 **웹앱**의 오리진 (없으면 빈 문자열).

    운영 콘솔은 웹앱 번들 안의 화면이라(App.tsx 가 /ops/shaker 에서 분기한다),
    그 오리진에는 /shaker 라우트가 반드시 있다. API 오리진에는 없다.

    ⚠️ 이 값은 **화면용 미리보기에만** 쓴다. 헤더는 위조 가능하므로 인쇄용 QR 은
    여전히 PUBLIC_WEB_BASE_URL 만 본다(fail closed). 위조해 봐야 인증된 운영자가
    자기 미리보기 링크를 자기 발로 어긋나게 만드는 것뿐이다.
    """
    origin = (request.headers.get("origin") or "").strip()
    if origin and origin.lower() != "null":
        return origin.rstrip("/")

    from urllib.parse import urlsplit

    parts = urlsplit((request.headers.get("referer") or "").strip())
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return ""


# ── 펫 찾기 ───────────────────────────────────────────────────────────────────


class OpsPet(BaseModel):
    pet_id: str
    owner_user_id: str
    ready_count: int
    source: str


class OpsPetsResponse(BaseModel):
    pets: list[OpsPet] = []
    degraded: bool = False
    registry_available: bool = True


@router.get("/pets", response_model=OpsPetsResponse)
async def ops_search_pets(
    query: str | None = None,
    limit: int = 200,
    includeLegacy: bool = False,
    _ops: AuthedUser = Depends(shaker_ops.require_ops),
):
    """고객 펫 검색. **펫을 만들지 않는다** — 이미 있는 것을 찾을 뿐이다."""
    try:
        result = await shaker_ops.search_pets(
            query, limit=limit, include_legacy=includeLegacy
        )
    except shaker_ops.OpsError as e:
        raise _as_http(e) from e
    return OpsPetsResponse(
        pets=[
            OpsPet(
                pet_id=p.pet_id,
                owner_user_id=p.owner_user_id,
                ready_count=p.ready_count,
                source=p.source,
            )
            for p in result.pets
        ],
        degraded=result.degraded,
        registry_available=result.registry_available,
    )


# ── 펫 수동 등록 · 백필 (Phase 13.2) ──────────────────────────────────────────


class OpsRegisterPetRequest(BaseModel):
    """
    운영 수동 등록 — 레지스트리 도입 **이전에** 만들어진 펫을 위한 백필 경로.

    소유자를 운영이 명시해야 한다: 이 펫들은 앱이 등록해 주지 못했고, 서버가
    canonical 신원을 알 수 없기 때문이다. 오타는 남의 펫에 QR 을 붙이는 것이므로
    운영이 고객 계정을 확인하고 넣어야 한다.
    """

    pet_id: str
    user_id: str
    content_id: str | None = None
    #: 둘 중 하나로 BREATHING 위치를 준다.
    breathing_url: str | None = None
    breathing_object_path: str | None = None
    breathing_bucket: str | None = None


class OpsRegisteredPet(BaseModel):
    pet_id: str
    owner_user_id: str
    content_id: str | None = None
    source: str


@router.post("/pets/register", response_model=OpsRegisteredPet)
async def ops_register_pet(
    body: OpsRegisterPetRequest,
    ops: AuthedUser = Depends(shaker_ops.require_ops),
):
    """
    운영 백필. **BREATHING 존재를 똑같이 검증한다** — 없는 펫을 등록하면
    열어도 아무것도 재생되지 않는 QR 이 인쇄돼 나간다.

    멱등: 이미 같은 소유자로 등록돼 있으면 그대로 돌려준다.
    """
    try:
        pet = await pet_registry.register(
            user_id=body.user_id,
            pet_id=body.pet_id,
            content_id=body.content_id,
            breathing_url=body.breathing_url,
            breathing_bucket=body.breathing_bucket,
            breathing_object_path=body.breathing_object_path,
            source=pet_registry.SOURCE_OPS,
        )
    except pet_registry.PetRegistryError as e:
        raise _as_http(e) from e

    logger.warning(
        "운영 펫 백필 — ops=%s pet=%s owner=%s", ops.user_id, pet.pet_id, pet.user_id
    )
    return OpsRegisteredPet(
        pet_id=pet.pet_id, owner_user_id=pet.user_id,
        content_id=pet.content_id, source=pet.source,
    )


# ── 공유 만들기 / 보기 ────────────────────────────────────────────────────────


class OpsCreateShareRequest(BaseModel):
    #: **이미 존재하는** canonical petId. 새로 만들지 않는다.
    pet_id: str
    #: 인쇄물 표시용. 없으면 서버에 저장된 이름이 없으므로 비워 둔다.
    pet_name: str | None = None
    purpose: str = "OPS"
    #: 규약 밖에 저장된 펫을 위한 수동 지정. 보통은 비워 둔다.
    breathing_url: str | None = None
    poster_url: str | None = None
    ttl_days: int | None = None
    #: Phase 12–13 에서 주문을 연결할 자리. 지금은 저장만 한다.
    order_ref: str | None = None
    #: Optional canonical scene for a new READY V2 snapshot. QR format is unchanged.
    scene_id: str | None = None
    layered_asset_id: str | None = None


class OpsShareResponse(BaseModel):
    share_id: str
    pet_id: str
    owner_user_id: str
    #: ⚠️ 이 응답에서만 존재한다 (서버는 해시만 저장한다).
    token: str
    share_path: str
    #: QR 에 인코딩되는 **완전한** URL. Supabase/영상 주소가 아니다.
    share_url: str
    purpose: str
    order_ref: str | None = None


def _share_url(base: str, pet_id: str, token: str) -> str:
    from urllib.parse import quote

    return f"{base}/shaker?petId={quote(pet_id, safe='')}&share={quote(token, safe='')}"


@router.post("/share", response_model=OpsShareResponse)
async def ops_create_share(
    request: Request,
    body: OpsCreateShareRequest,
    ops: AuthedUser = Depends(shaker_ops.require_ops),
):
    """
    이 펫의 공유 링크를 만든다. **고객 브라우저 상태 없이 서버만으로 완결된다.**

    소유자와 BREATHING 위치를 서버가 직접 찾는다:
      소유자   generated_motions 의 canonical 바인딩
      BREATHING  `{user_id}/{content_id}/idle_loop.mp4` 규약 → 서명 성공이 존재 증명

    둘 중 하나라도 확인되지 않으면 **거절한다.** 없는 것을 만들지 않는다 —
    자산이 없다는 것은 아직 QR 을 붙일 단계가 아니라는 뜻이다.
    """
    pet_id = (body.pet_id or "").strip()
    purpose = (body.purpose or "OPS").strip().upper()
    if purpose not in _VALID_PURPOSES:
        raise HTTPException(
            status_code=400,
            detail={"code": "PURPOSE_INVALID", "message": f"purpose 는 {_VALID_PURPOSES} 중 하나여야 합니다."},
        )

    # ⚠️ 인쇄 안전을 **가장 먼저** 확인한다. 공유를 만든 뒤에 거절하면 쓰이지 않는
    # 공유만 쌓이고, 그중 하나가 나중에 잘못 인쇄될 수 있다.
    if qr_service.is_print_purpose(purpose):
        try:
            qr_service.assert_printable_base()
        except qr_service.QrError as e:
            raise _as_http(e) from e

    try:
        owner = await shaker_ops.resolve_pet_owner(pet_id)
    except shaker_ops.OpsError as e:
        raise _as_http(e) from e

    # BREATHING: 수동 지정이 있으면 그것, 없으면 규약에서 찾는다.
    breathing_url = (body.breathing_url or "").strip()
    bucket: str | None = None
    object_path: str | None = None

    if not breathing_url:
        located = await shaker_ops.locate_breathing(owner, pet_id)
        if not located:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "BREATHING_NOT_FOUND",
                    "message": (
                        "이 펫의 BREATHING 영상을 찾을 수 없습니다. "
                        "아직 생성되지 않았거나 규약 밖 경로에 있습니다 — "
                        "breathing_url 을 직접 지정하세요."
                    ),
                },
            )
        loc, breathing_url = located
        bucket, object_path = loc.bucket, loc.object_path

    try:
        share_id, token = await shaker_share.create_share(
            user_id=owner,                    # ⚠️ 소유자는 **고객**이다 (운영자가 아니다)
            pet_id=pet_id,
            pet_name=body.pet_name,
            breathing_url=breathing_url,
            poster_url=body.poster_url,
            ttl_days=body.ttl_days,
            breathing_bucket=bucket,
            breathing_object_path=object_path,
            created_by=ops.user_id,           # 감사 추적: 누가 만들었는가
            purpose=purpose,
            order_ref=body.order_ref,
            scene_id=body.scene_id,
            layered_asset_id=body.layered_asset_id,
        )
    except shaker_share.ShareError as e:
        raise _as_http(e) from e

    base = _public_web_base(request, purpose=purpose)
    share_url = _share_url(base, pet_id, token)

    # QR 산출물을 **발급 직후 한 번** 보관한다. 이것이 있어야 나중에 같은 QR 을
    # 다시 내려받을 수 있다 — 토큰을 복원하지도, 새 공유를 만들지도 않고.
    # 실패해도 공유 자체는 유효하므로 발급을 되돌리지 않는다(재다운로드만 불가).
    try:
        await shaker_qr_artifact.store(
            share_id=share_id, token_hash=shaker_share.hash_token(token),
            pet_id=pet_id, share_url=share_url, purpose=purpose,
        )
    except shaker_qr_artifact.QrArtifactError:
        logger.error(
            "QR 산출물 보관 실패 — share=%s. 링크는 유효하지만 재다운로드가 불가하다.",
            share_id,
        )

    logger.warning(
        "운영 Shaker 공유 발급 — ops=%s pet=%s owner=%s purpose=%s share=%s",
        ops.user_id, pet_id, owner, purpose, share_id,
    )
    return OpsShareResponse(
        share_id=share_id,
        pet_id=pet_id,
        owner_user_id=owner,
        token=token,
        share_path=f"/shaker?petId={pet_id}&share={token}",
        share_url=share_url,
        purpose=purpose,
        order_ref=body.order_ref,
    )


class OpsShareSummary(BaseModel):
    """운영 목록. **토큰이 없다** — 저장하지 않으므로 돌려줄 수 없다."""

    share_id: str
    pet_id: str
    owner_user_id: str
    pet_name: str | None = None
    purpose: str | None = None
    order_ref: str | None = None
    created_by: str | None = None
    created_at: str | None = None
    revoked_at: str | None = None
    expires_at: str | None = None
    active: bool = True


class OpsSharesResponse(BaseModel):
    shares: list[OpsShareSummary] = []


@router.get("/shares", response_model=OpsSharesResponse)
async def ops_list_shares(
    pet_id: str,
    _ops: AuthedUser = Depends(shaker_ops.require_ops),
):
    """이 펫의 공유 링크 전부. 운영자는 남의 펫도 봐야 하므로 소유자로 좁히지 않는다."""
    pid = (pet_id or "").strip()
    try:
        owner = await shaker_ops.resolve_pet_owner(pid)
        rows = await shaker_share.list_shares(user_id=owner, pet_id=pid)
    except (shaker_ops.OpsError, shaker_share.ShareError) as e:
        raise _as_http(e) from e

    return OpsSharesResponse(
        shares=[
            OpsShareSummary(
                share_id=r.share_id,
                pet_id=r.pet_id,
                owner_user_id=r.user_id,
                pet_name=r.pet_name,
                purpose=r.purpose,
                order_ref=r.order_ref,
                created_by=r.created_by,
                created_at=r.created_at,
                revoked_at=r.revoked_at,
                expires_at=r.expires_at,
                active=not r.revoked_at,
            )
            for r in rows
        ]
    )


class OpsRevokeRequest(BaseModel):
    pet_id: str


class OpsRevokeResponse(BaseModel):
    share_id: str
    revoked: bool


@router.post("/share/{share_id}/revoke", response_model=OpsRevokeResponse)
async def ops_revoke_share(
    share_id: str,
    body: OpsRevokeRequest,
    ops: AuthedUser = Depends(shaker_ops.require_ops),
):
    """
    운영자 폐기. **이미 배송된 인쇄물을 죽이는 행위다** — 로그를 크게 남긴다.

    소유자 id 를 운영자가 넣지 않는다: pet_id 로 서버가 찾는다. 폐기 자체는
    기존 revoke_share(소유자 조건 포함)를 그대로 쓴다 — 인가 규칙을 새로 만들지 않는다.
    """
    try:
        owner = await shaker_ops.resolve_pet_owner(body.pet_id)
        changed = await shaker_share.revoke_share(user_id=owner, share_id=share_id)
    except (shaker_ops.OpsError, shaker_share.ShareError) as e:
        raise _as_http(e) from e

    logger.warning(
        "운영 Shaker 공유 폐기 — ops=%s pet=%s share=%s changed=%s",
        ops.user_id, body.pet_id, share_id, changed,
    )
    return OpsRevokeResponse(share_id=share_id, revoked=changed)


# ── QR 재다운로드 (Phase 13.1) ────────────────────────────────────────────────


@router.get("/share/{share_id}/qr")
async def ops_redownload_qr(
    share_id: str,
    kind: str = "svg",
    _ops: AuthedUser = Depends(shaker_ops.require_ops),
):
    """
    **같은 QR 을 다시 내려받는다.** 발급 탭을 닫았어도 된다.

    보관된 산출물을 그대로 내보낸다:
      * 토큰을 복원하지 않는다 (여전히 해시만 저장돼 있다)
      * 새 공유를 만들지 않는다
      * **이미 인쇄된 QR 이 그대로 유효하다** — 같은 바이트이기 때문이다

    이것이 없던 시절의 유일한 경로는 재발급이었고, 그건 인쇄물 무효화를 뜻했다.
    """
    try:
        art = await shaker_qr_artifact.require(share_id)
    except shaker_qr_artifact.QrArtifactError as e:
        raise _as_http(e) from e

    k = (kind or "svg").strip().lower()
    if k == "svg":
        data = art.qr_svg.encode("utf-8")
        media, ext = "image/svg+xml", "svg"
    elif k == "png":
        if not art.qr_png:
            raise HTTPException(
                status_code=404,
                detail={"code": "QR_PNG_UNAVAILABLE", "message": "PNG 산출물이 없습니다."},
            )
        data, media, ext = art.qr_png, "image/png", "png"
    else:
        raise HTTPException(
            status_code=400,
            detail={"code": "QR_KIND_UNSUPPORTED", "message": "svg 또는 png 만 지원합니다."},
        )

    return Response(
        content=data,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{art.pet_id}-qr.{ext}"',
            "Cache-Control": "no-store",
        },
    )


# ── QR ────────────────────────────────────────────────────────────────────────


@router.get("/qr")
async def ops_render_qr(
    request: Request,
    share_url: str,
    kind: str = "svg",
    scale: int = qr_service.DEFAULT_SCALE,
    filename: str = "shaker",
    _ops: AuthedUser = Depends(shaker_ops.require_ops),
):
    """
    Shaker URL → QR 파일.

    **원문 토큰을 서버가 저장하지 않으므로** share_id 로는 QR 을 만들 수 없다.
    발급 응답에서 받은 share_url 을 그대로 넘겨야 한다. 이 제약은 의도된 것이다 —
    서버가 원문을 들고 있으면 유출 시 모든 인쇄물이 함께 털린다.

    qr_service 가 Shaker URL 이 아닌 것을 거절한다. 스토리지/영상 주소는 인코딩
    자체가 되지 않는다.
    """
    try:
        img = qr_service.render_qr(
            share_url, kind=kind, scale=scale, filename_hint=filename
        )
    except qr_service.QrError as e:
        raise _as_http(e) from e

    return Response(
        content=img.data,
        media_type=img.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{img.filename}"',
            # 인쇄용 자산이라도 공유 토큰이 들어 있다 — 캐시에 남기지 않는다.
            "Cache-Control": "no-store",
        },
    )
