"""
/api/v1/ops/production — 인쇄 생산 파이프라인 (Phase 13). **판매자/운영 전용.**

    GET  /{order_id}                 주문 + 패키지 상태 (Ops 화면 한 장)
    POST /{order_id}/prepare         생산 준비 (입력 스냅샷) → production READY
    POST /{order_id}/photo           사진 카드 원본 지정/교체 (메모리 박스)
    GET  /{order_id}/package         구성표(manifest)
    GET  /{order_id}/file/{kind}     구성 파일 미리보기/내려받기
    GET  /{order_id}/download        전체 패키지 ZIP
    POST /{order_id}/start           READY → IN_PRODUCTION
    POST /{order_id}/produced        IN_PRODUCTION → PRODUCED
    POST /{order_id}/tracking        송장 등록
    POST /{order_id}/ship            PRODUCED → SHIPPED (송장 필요)
    POST /{order_id}/delivered       SHIPPED → DELIVERED

Phase 10 과 **같은 allowlist**(SHAKER_OPS_USER_IDS)를 쓴다 — 운영 자격을 두 벌
만들면 하나가 갱신되고 다른 하나가 잊힌다.

── 이 라우터가 하지 않는 것 ────────────────────────────────────────────────
편지를 만들지 않는다. 펫을 만들지 않는다. **Shaker 공유를 새로 발급하지 않는다.**
생성(WAN/Luma)·프리미엄 행동·구독·테마·크레딧을 건드리지 않는다.
결제되지 않은 주문은 생산에 들어갈 수 없다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ..auth import AuthedUser
from ..services import physical_order, production_package, shaker_ops

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/ops/production", tags=["production-ops"])


def _http(e: Exception) -> HTTPException:
    return HTTPException(
        status_code=getattr(e, "status", 400),
        detail={"code": getattr(e, "code", "ERROR"), "message": getattr(e, "message", str(e))},
    )


class OrderStateOut(BaseModel):
    """Ops 화면 한 장에 필요한 전부."""

    order_id: str
    user_id: str
    pet_id: str
    soul_trace_letter_id: str | None = None
    product_type: str
    amount: int
    payment_status: str
    production_status: str
    shipping_status: str
    tracking_number: str | None = None
    shaker_share_id: str | None = None
    #: 생산 패키지가 준비됐는가 (파일을 뽑을 수 있는가).
    package_ready: bool = False
    #: 준비됐을 때 뽑을 수 있는 파일들.
    files: list[str] = []
    recipient_name: str | None = None
    address_line1: str | None = None

    # ── 운영이 "이 주문을 만들어도 되는가"를 한 장에서 판단하기 위한 값들 ──────
    # 예전에는 letter_id / pet_id 같은 **식별자만** 보였다. 식별자로는 편지가
    # 맞는지, BREATHING 이 실제로 있는지, QR 이 인쇄 가능한지 알 수 없어서
    # 운영이 매번 다른 화면을 열어 확인해야 했다.
    #: 편지 미리보기 — 아이 이름과 발췌. **본문은 싣지 않는다**(인쇄용이다).
    letter_child_name: str | None = None
    letter_excerpt: str | None = None
    #: BREATHING 이 실제로 존재하는가. 없으면 QR 을 찍어도 열리지 않는다.
    breathing_ready: bool = False
    #: 인쇄될 QR 주소. 산출물로 준비된 경우 None 이고 qr_artifact_stored 가 True 다.
    qr_share_url: str | None = None
    #: 재인쇄 가능 여부 — 보관된 QR 산출물이 있으면 같은 바이트로 다시 뽑는다.
    qr_artifact_stored: bool = False

    # ── MEMORY BOX 구성품 (Phase 17) ──────────────────────────────────────────
    #: 사진 카드 원본이 확정됐는가. False 면 사진 카드도 패키지 ZIP 도 만들 수 없다.
    photo_ready: bool = False
    #: 확정된 원본 주소(운영이 눈으로 확인하고 필요하면 교체한다).
    photo_image_url: str | None = None
    #: 구성품이지만 아직 패키지에 넣을 수 없는 것 — 예: 문구 미승인 메시지 카드.
    #: 비어 있으면 완전한 패키지다.
    pending_files: list[dict[str, str]] = []

    # ── 파트너 귀속 (Phase 15) ────────────────────────────────────────────────
    # 주문 시점 스냅샷이다. 전부 None 이면 **직접 유입**이며 그것도 정상이다.
    partner_id: str | None = None
    partner_type: str | None = None
    partner_name: str | None = None
    #: 정산 근거 스냅샷 — 주문 시점 값이며 파트너의 현재 값과 다를 수 있다.
    partner_code: str | None = None
    partner_track: str | None = None
    partner_share_rate: float | None = None


async def _breathing_ready(pet_id: str) -> bool:
    """
    BREATHING 이 등록돼 있는가. **조회 실패를 '있음'으로 읽지 않는다.**

    여기서 낙관적으로 True 를 주면 운영이 "준비됨"을 보고 인쇄를 넘기고,
    고객은 열리지 않는 QR 이 찍힌 종이를 받는다.
    """
    try:
        from ..services import pet_registry

        pet = await pet_registry.get(pet_id)
    except Exception:  # noqa: BLE001
        return False
    return bool(pet and pet.breathing_object_path)


async def _qr_artifact_stored(share_id: str | None) -> bool:
    if not share_id:
        return False
    try:
        from ..services import shaker_qr_artifact

        return bool(await shaker_qr_artifact.get(share_id))
    except Exception:  # noqa: BLE001
        return False


async def _letter_preview(letter_id: str | None) -> tuple[str | None, str | None]:
    """(아이 이름, 발췌). 본문은 돌려주지 않는다 — 화면용이 아니라 인쇄용이다."""
    if not letter_id:
        return None, None
    try:
        from ..services import soul_trace_letter

        letter = await soul_trace_letter.get_letter(letter_id)
    except Exception:  # noqa: BLE001
        return None, None
    if not letter:
        return None, None
    return letter.child_name, letter.letter_excerpt


async def _state(order: physical_order.PhysicalOrder) -> OrderStateOut:
    pkg = await production_package.get_package(order.order_id)
    m = production_package.manifest(pkg) if pkg else {}
    files = m.get("files") or []
    pending = m.get("pending_files") or []
    share_id = order.shaker_share_id or (pkg.shaker_share_id if pkg else None)
    child_name, excerpt = await _letter_preview(order.soul_trace_letter_id)
    return OrderStateOut(
        order_id=order.order_id, user_id=order.user_id, pet_id=order.pet_id,
        soul_trace_letter_id=order.soul_trace_letter_id,
        product_type=order.product_type, amount=order.amount,
        payment_status=order.payment_status,
        production_status=order.production_status,
        shipping_status=order.shipping_status,
        tracking_number=order.tracking_number,
        shaker_share_id=share_id,
        package_ready=bool(pkg), files=files,
        recipient_name=order.recipient_name, address_line1=order.address_line1,
        letter_child_name=child_name, letter_excerpt=excerpt,
        breathing_ready=await _breathing_ready(order.pet_id),
        qr_share_url=(pkg.qr_share_url if pkg else None),
        qr_artifact_stored=await _qr_artifact_stored(share_id),
        photo_ready=bool(pkg and pkg.photo_image_url),
        photo_image_url=(pkg.photo_image_url if pkg else None),
        pending_files=pending,
        partner_id=order.partner_id,
        partner_type=order.partner_type,
        partner_name=order.partner_name,
        partner_code=order.partner_code,
        partner_track=order.partner_track,
        partner_share_rate=order.partner_share_rate,
    )


async def _load(order_id: str) -> physical_order.PhysicalOrder:
    try:
        order = await physical_order.get(order_id)
    except physical_order.OrderError as e:
        raise _http(e) from e
    if not order:
        raise HTTPException(
            status_code=404,
            detail={"code": "ORDER_NOT_FOUND", "message": "주문을 찾을 수 없습니다."},
        )
    return order


@router.get("/{order_id}", response_model=OrderStateOut)
async def get_state(order_id: str, _ops: AuthedUser = Depends(shaker_ops.require_ops)):
    return await _state(await _load(order_id))


class PrepareRequest(BaseModel):
    """
    ⚠️ qr_share_url 은 **기존** Shaker 공유의 URL 이다. 여기서 새 공유를 만들지
    않는다 — 만들면 펫 경험이 중복된다. 운영 콘솔(/ops/shaker)에서 확인한다.
    """

    qr_share_url: str | None = None
    #: 사진 카드 원본(메모리 박스). 보통 Shaker 공유의 포스터를 쓴다.
    photo_image_url: str | None = None


class AttachPhotoRequest(BaseModel):
    """사진 카드에 쓸 원본 이미지 주소."""

    photo_image_url: str


@router.post("/{order_id}/photo", response_model=OrderStateOut)
async def attach_photo(
    order_id: str,
    body: AttachPhotoRequest,
    ops: AuthedUser = Depends(shaker_ops.require_ops),
):
    """
    사진 카드 원본을 지정하거나 교체한다 (메모리 박스 전용).

    ── 왜 prepare 로는 안 되는가 ────────────────────────────────────────────
    prepare 는 "이미 있으면 그대로 돌려준다"가 계약이다(인쇄가 시작된 뒤 입력이
    조용히 바뀌면 안 된다). 그래서 자동 완결이 사진을 찾지 못한 채 패키지를
    만들면, 운영이 나중에 prepare 로 사진을 넘겨도 **아무 일도 일어나지 않았다.**
    사진을 붙일 방법이 없어 메모리 박스 패키지 ZIP 이 영영 만들어지지 않는다.

    여기서 바꾸는 것은 **사진 한 칸뿐**이다. 편지·QR·공유·수령인은 인쇄물의
    정체성이므로 건드리지 않는다. 생산이 시작된 뒤에는 거절한다.
    """
    try:
        await production_package.attach_photo(
            order_id=order_id, photo_image_url=body.photo_image_url
        )
    except production_package.ProductionError as e:
        raise _http(e) from e

    logger.warning("[ops] 사진 카드 원본 지정 — order=%s by=%s", order_id, ops.user_id)
    return await _state(await _load(order_id))


@router.post("/{order_id}/prepare", response_model=OrderStateOut)
async def prepare(
    order_id: str,
    body: PrepareRequest,
    ops: AuthedUser = Depends(shaker_ops.require_ops),
):
    """
    생산 준비 — 입력을 확정해 스냅샷하고 production 을 READY 로 올린다.

    **멱등이다**: 이미 준비된 주문은 그대로 돌려주며 QR 도 편지도 다시 만들지 않는다.
    인쇄가 이미 시작됐을 수 있으므로 입력이 조용히 바뀌면 안 된다.
    """
    try:
        await production_package.prepare(
            order_id=order_id,
            qr_share_url=body.qr_share_url,
            photo_image_url=body.photo_image_url,
        )
    except production_package.ProductionError as e:
        raise _http(e) from e

    try:
        order = await physical_order.advance_production(
            order_id=order_id, to=physical_order.PRODUCTION_READY
        )
    except physical_order.OrderError as e:
        # 이미 READY 를 지나 IN_PRODUCTION 이면 전이가 거절되지만, 패키지는 있다.
        # 그 경우는 오류가 아니라 현재 상태를 그대로 보여 준다.
        if e.code != "PRODUCTION_TRANSITION_INVALID":
            raise _http(e) from e
        order = await _load(order_id)

    logger.warning("생산 준비 완료 — ops=%s order=%s", ops.user_id, order_id)
    return await _state(order)


@router.get("/{order_id}/package")
async def get_manifest(order_id: str, _ops: AuthedUser = Depends(shaker_ops.require_ops)):
    """구성표. 인쇄소에 넘길 사양과 수령인 정보가 들어 있다(운영 전용)."""
    pkg = await production_package.get_package(order_id)
    if not pkg:
        raise HTTPException(
            status_code=409,
            detail={"code": "PACKAGE_NOT_READY", "message": "생산 준비가 필요합니다."},
        )
    return production_package.manifest(pkg)


@router.get("/{order_id}/file/{kind}")
async def get_file(
    order_id: str, kind: str, _ops: AuthedUser = Depends(shaker_ops.require_ops)
):
    """
    구성 파일 하나 — 미리보기와 내려받기가 같은 경로다.

    inline 으로 내보내 브라우저가 바로 보여 준다(PDF/PNG). 인쇄 전에 눈으로
    확인하는 것이 이 단계에서 가장 값싼 검증이다.
    """
    pkg = await production_package.get_package(order_id)
    if not pkg:
        raise HTTPException(
            status_code=409,
            detail={"code": "PACKAGE_NOT_READY", "message": "생산 준비가 필요합니다."},
        )
    try:
        f = await production_package.render_file(pkg, kind)
    except production_package.ProductionError as e:
        raise _http(e) from e

    return Response(
        content=f.data,
        media_type=f.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{f.filename}"',
            # 수령인 정보가 담긴 인쇄물이다 — 캐시에 남기지 않는다.
            "Cache-Control": "no-store",
        },
    )


@router.get("/{order_id}/download")
async def download_package(
    order_id: str, _ops: AuthedUser = Depends(shaker_ops.require_ops)
):
    """전체 패키지 ZIP. 파일 하나라도 실패하면 ZIP 을 만들지 않는다."""
    pkg = await production_package.get_package(order_id)
    if not pkg:
        raise HTTPException(
            status_code=409,
            detail={"code": "PACKAGE_NOT_READY", "message": "생산 준비가 필요합니다."},
        )
    try:
        z = await production_package.render_zip(pkg)
    except production_package.ProductionError as e:
        raise _http(e) from e

    return Response(
        content=z.data,
        media_type=z.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{z.filename}"',
            "Cache-Control": "no-store",
        },
    )


async def _advance(order_id: str, to: str) -> OrderStateOut:
    try:
        order = await physical_order.advance_production(order_id=order_id, to=to)
    except physical_order.OrderError as e:
        raise _http(e) from e
    return await _state(order)


@router.post("/{order_id}/start", response_model=OrderStateOut)
async def start_production(order_id: str, _ops: AuthedUser = Depends(shaker_ops.require_ops)):
    """READY → IN_PRODUCTION. 패키지가 없으면 시작할 수 없다."""
    if not await production_package.get_package(order_id):
        raise HTTPException(
            status_code=409,
            detail={"code": "PACKAGE_NOT_READY", "message": "생산 준비가 필요합니다."},
        )
    return await _advance(order_id, physical_order.PRODUCTION_IN_PRODUCTION)


@router.post("/{order_id}/produced", response_model=OrderStateOut)
async def mark_produced(order_id: str, _ops: AuthedUser = Depends(shaker_ops.require_ops)):
    """IN_PRODUCTION → PRODUCED."""
    return await _advance(order_id, physical_order.PRODUCTION_PRODUCED)


class TrackingRequest(BaseModel):
    tracking_number: str


@router.post("/{order_id}/tracking", response_model=OrderStateOut)
async def add_tracking(
    order_id: str, body: TrackingRequest, _ops: AuthedUser = Depends(shaker_ops.require_ops)
):
    """송장 등록. 발송 처리와 **분리**돼 있다 — 먼저 등록하고 나중에 보낼 수 있다."""
    try:
        order = await physical_order.set_tracking(
            order_id=order_id, tracking_number=body.tracking_number
        )
    except physical_order.OrderError as e:
        raise _http(e) from e
    return await _state(order)


@router.post("/{order_id}/ship", response_model=OrderStateOut)
async def mark_shipped(order_id: str, _ops: AuthedUser = Depends(shaker_ops.require_ops)):
    """PRODUCED → SHIPPED. **송장이 없으면 거절한다.**"""
    try:
        order = await physical_order.advance_shipping(
            order_id=order_id, to=physical_order.SHIPPING_SHIPPED
        )
    except physical_order.OrderError as e:
        raise _http(e) from e
    return await _state(order)


@router.post("/{order_id}/delivered", response_model=OrderStateOut)
async def mark_delivered(order_id: str, _ops: AuthedUser = Depends(shaker_ops.require_ops)):
    """SHIPPED → DELIVERED."""
    try:
        order = await physical_order.advance_shipping(
            order_id=order_id, to=physical_order.SHIPPING_DELIVERED
        )
    except physical_order.OrderError as e:
        raise _http(e) from e
    return await _state(order)
