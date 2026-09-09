"""
/api/v1/ops/partners — 제휴처 등록 · QR 발급 (Phase 16). **운영 전용.**

    GET   /                       파트너 + 코드 목록 (운영 화면 한 장)
    POST  /                       파트너 등록 (원하면 첫 QR 까지)
    PATCH /{partner_id}           파트너 켜기/끄기 · 이름 · 정산 비율
    POST  /codes                  QR 코드 추가 발급
    PATCH /codes/{code}           코드 켜기/끄기

Phase 10·13 과 **같은 allowlist**(SHAKER_OPS_USER_IDS)를 쓴다 — 운영 자격을 두 벌
만들면 하나가 갱신되고 다른 하나가 잊힌다.

── 이 라우터가 하지 않는 것 ────────────────────────────────────────────────
정산을 실행하지 않는다. 송금하지 않는다. 인보이스를 만들지 않는다.
편지·주문·펫을 건드리지 않는다. 생성하지 않는다. 과금하지 않는다.
**귀속을 만들지 않는다** — 귀속은 고객이 QR 로 들어올 때 Soul Trace 가 정한다.

── 왜 여기서 Soul Trace DB 를 직접 읽지 않는가 ─────────────────────────────
partners/partner_codes 는 Soul Trace 프로젝트 소유이고 두 프로젝트는 DB 를
공유하지 않는다. 이 라우터는 운영자를 **인가**하고, 실제 조작은 검증된 S2S
경로로 넘긴다(services/partner_admin.py).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from ..auth import AuthedUser
from ..services import partner_admin, qr_service, shaker_ops

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/ops/partners", tags=["partner-ops"])


def _http(e: Exception) -> HTTPException:
    return HTTPException(
        status_code=getattr(e, "status", 400),
        detail={"code": getattr(e, "code", "ERROR"), "message": getattr(e, "message", str(e))},
    )


class CodeOut(BaseModel):
    code: str
    #: 'living' | 'memorial' | None. Soul Trace LetterMode 와 같은 값이다.
    track: str | None = None
    active: bool = True
    created_at: str | None = None


class PartnerOut(BaseModel):
    partner_id: str
    partner_type: str
    partner_name: str
    #: 0..1 (0.15 = 15%). 주문 시점에 스냅샷되어 정산 근거가 된다.
    share_rate: float = 0.0
    active: bool = True
    created_at: str | None = None
    codes: list[CodeOut] = []


class PartnersResponse(BaseModel):
    partners: list[PartnerOut] = []


class CreatePartnerRequest(BaseModel):
    partner_name: str
    partner_type: str
    #: 0..1. 15% 는 0.15 다 — 15 를 넣으면 거절된다.
    share_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    active: bool = True
    #: 등록과 동시에 첫 QR 을 뽑고 싶을 때. 없으면 파트너만 만든다.
    initial_track: str | None = None


class UpdatePartnerRequest(BaseModel):
    active: bool | None = None
    partner_name: str | None = None
    share_rate: float | None = Field(default=None, ge=0.0, le=1.0)


class IssueCodeRequest(BaseModel):
    partner_id: str
    #: 없으면 고객이 첫 화면에서 직접 고른다(기존 동작).
    track: str | None = None


class UpdateCodeRequest(BaseModel):
    active: bool


def _code_out(c: partner_admin.PartnerCode) -> CodeOut:
    return CodeOut(code=c.code, track=c.track, active=c.active, created_at=c.created_at)


def _partner_out(p: partner_admin.Partner) -> PartnerOut:
    return PartnerOut(
        partner_id=p.partner_id,
        partner_type=p.partner_type,
        partner_name=p.partner_name,
        share_rate=p.share_rate,
        active=p.active,
        created_at=p.created_at,
        codes=[_code_out(c) for c in p.codes],
    )


@router.get("", response_model=PartnersResponse)
async def list_partners(user: AuthedUser = Depends(shaker_ops.require_ops)):
    try:
        rows = await partner_admin.list_partners()
    except partner_admin.PartnerAdminError as e:
        raise _http(e) from e
    return PartnersResponse(partners=[_partner_out(p) for p in rows])


@router.post("", response_model=PartnerOut, status_code=201)
async def create_partner(
    body: CreatePartnerRequest,
    user: AuthedUser = Depends(shaker_ops.require_ops),
):
    """
    파트너 등록. **partner_id 는 서버가 만든다** — 요청에 그 자리가 없다.

    브라우저가 고를 수 있으면 남의 병원 id 를 적어 정산을 가로챌 수 있다.
    """
    try:
        partner = await partner_admin.create_partner(
            partner_name=body.partner_name,
            partner_type=body.partner_type,
            share_rate=body.share_rate,
            active=body.active,
            initial_track=body.initial_track,
        )
    except partner_admin.PartnerAdminError as e:
        raise _http(e) from e
    logger.warning("[partner-ops] 파트너 등록 — by=%s id=%s", user.user_id, partner.partner_id)
    return _partner_out(partner)


@router.patch("/codes/{code}", response_model=CodeOut)
async def update_code(
    code: str,
    body: UpdateCodeRequest,
    user: AuthedUser = Depends(shaker_ops.require_ops),
):
    """
    코드 켜기/끄기. 끄면 **새 귀속만** 멈춘다 — 과거 편지·주문은 그대로다.

    track 은 바꿀 수 없다. 벽에 붙은 종이의 의미를 나중에 바꾸면, 장례식장 QR 이
    어느 날부터 living 편지를 만든다. 갈래를 바꾸려면 새 코드를 발급한다.
    """
    try:
        row = await partner_admin.set_code_active(code=code, active=body.active)
    except partner_admin.PartnerAdminError as e:
        raise _http(e) from e
    logger.warning(
        "[partner-ops] 코드 %s — active=%s by=%s", row.code, row.active, user.user_id
    )
    return _code_out(row)


@router.get("/codes/{code}/qr")
async def render_code_qr(
    code: str,
    kind: str = "svg",
    scale: int = qr_service.DEFAULT_SCALE,
    _ops: AuthedUser = Depends(shaker_ops.require_ops),
):
    """
    코드 → QR 파일. **주소는 서버가 만든다** — 요청이 URL 을 고르지 않는다.

    고를 수 있으면 운영 화면의 실수 하나로 엉뚱한 곳을 가리킨 QR 이 벽에 붙는다.
    인쇄된 QR 은 회수할 수 없다.

    svg 가 기본이다 — 인쇄용이라 벡터여야 어떤 크기로 뽑아도 모듈이 선명하다.
    png 는 화면 미리보기용이다.
    """
    try:
        img = qr_service.render_partner_qr(
            code, kind=kind, scale=scale, filename_hint=f"partner-{code}"
        )
    except qr_service.QrError as e:
        raise _http(e) from e

    return Response(
        content=img.data,
        media_type=img.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{img.filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/codes", response_model=CodeOut, status_code=201)
async def issue_code(
    body: IssueCodeRequest,
    user: AuthedUser = Depends(shaker_ops.require_ops),
):
    """
    QR 코드 추가 발급. 한 파트너가 지점·캠페인·갈래별로 여러 코드를 갖는다.

    코드 문자열은 Soul Trace 의 createPartnerCode() 가 만든다 — 무작위 96비트.
    읽을 수 있는 코드를 허용하면 남의 코드를 추측해 정산을 훔칠 수 있다.
    """
    try:
        row = await partner_admin.issue_code(partner_id=body.partner_id, track=body.track)
    except partner_admin.PartnerAdminError as e:
        raise _http(e) from e
    logger.warning(
        "[partner-ops] 코드 발급 — partner=%s track=%s by=%s",
        body.partner_id,
        row.track or "(none)",
        user.user_id,
    )
    return _code_out(row)


@router.patch("/{partner_id}", response_model=PartnerOut)
async def update_partner(
    partner_id: str,
    body: UpdatePartnerRequest,
    user: AuthedUser = Depends(shaker_ops.require_ops),
):
    """
    파트너 켜기/끄기 · 이름 · 정산 비율.

    ⚠️ 비율을 바꿔도 **과거 주문은 움직이지 않는다** — physical_orders 가 주문
    시점 비율을 들고 있다. 여기서 바꾸는 값은 앞으로의 주문에만 적용된다.
    """
    try:
        partner = await partner_admin.update_partner(
            partner_id=partner_id,
            active=body.active,
            partner_name=body.partner_name,
            share_rate=body.share_rate,
        )
    except partner_admin.PartnerAdminError as e:
        raise _http(e) from e
    logger.warning("[partner-ops] 파트너 수정 — id=%s by=%s", partner_id, user.user_id)
    return _partner_out(partner)
