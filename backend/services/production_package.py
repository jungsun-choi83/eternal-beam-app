"""
생산 패키지 — **입력 스냅샷**을 만들고 그것으로 인쇄물을 렌더한다.

    PAID 주문 + canonical petId + Soul Trace 편지 + 사진 + 기존 Shaker 공유
      → 생산 패키지 (A5 편지 PDF · 사진 카드 · QR 카드 · 메타데이터)

── 만들지 않는 것 ──────────────────────────────────────────────────────────
편지를 만들지 않는다(Soul Trace 스냅샷을 읽는다). 펫을 만들지 않는다.
**Shaker 공유를 새로 만들지 않는다** — 주문에 붙은 것을 쓰고, 없으면 거절한다.
생성(WAN/Luma)이나 프리미엄 행동을 부르지 않는다: 그런 import 가 없다.

── 왜 파일을 저장하지 않는가 ────────────────────────────────────────────────
렌더링이 결정적이라 같은 입력이면 같은 바이트가 나온다. 파일을 저장하면 스토리지
수명(서명 URL 만료)을 또 관리해야 하는데 그 문제로 이미 두 번 데였다. 입력만
남기면 언제든 동일한 인쇄물을 다시 뽑을 수 있고 새 만료 표면이 생기지 않는다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from . import (
    physical_order,
    physical_product,
    print_render,
    qr_service,
    shaker_qr_artifact,
    soul_trace_letter,
)

logger = logging.getLogger(__name__)


class ProductionError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _table() -> str:
    return os.getenv("PRODUCTION_PACKAGES_TABLE", "production_packages")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


_MOCK_PACKAGES: dict[str, dict[str, Any]] = {}


def __reset_for_tests() -> None:
    _MOCK_PACKAGES.clear()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ProductionPackage:
    order_id: str
    user_id: str
    pet_id: str
    product_type: str
    soul_trace_letter_id: str
    #: 산출물로 준비된 경우 None — 토큰은 복원되지 않는다.
    qr_share_url: Optional[str] = None
    qr_source: str = "url"
    shaker_share_id: Optional[str] = None
    photo_image_url: Optional[str] = None
    recipient_name: Optional[str] = None
    recipient_phone: Optional[str] = None
    postal_code: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    built_at: Optional[str] = None


_SELECT = (
    "order_id, user_id, pet_id, product_type, soul_trace_letter_id, qr_share_url, "
    "qr_source, shaker_share_id, photo_image_url, recipient_name, recipient_phone, "
    "postal_code, address_line1, address_line2, built_at"
)


def _to_pkg(row: dict[str, Any]) -> ProductionPackage:
    return ProductionPackage(
        order_id=str(row.get("order_id") or ""),
        user_id=str(row.get("user_id") or ""),
        pet_id=str(row.get("pet_id") or ""),
        product_type=str(row.get("product_type") or ""),
        soul_trace_letter_id=str(row.get("soul_trace_letter_id") or ""),
        qr_share_url=(row.get("qr_share_url") or None),
        qr_source=str(row.get("qr_source") or "url"),
        shaker_share_id=(row.get("shaker_share_id") or None),
        photo_image_url=(row.get("photo_image_url") or None),
        recipient_name=(row.get("recipient_name") or None),
        recipient_phone=(row.get("recipient_phone") or None),
        postal_code=(row.get("postal_code") or None),
        address_line1=(row.get("address_line1") or None),
        address_line2=(row.get("address_line2") or None),
        built_at=(str(row["built_at"]) if row.get("built_at") else None),
    )


async def get_package(order_id: str) -> Optional[ProductionPackage]:
    oid = (order_id or "").strip()
    if not oid:
        return None

    if _use_db() and _supabase():
        try:
            r = _supabase().table(_table()).select(_SELECT).eq("order_id", oid).limit(1).execute()
            data = getattr(r, "data", None) or []
            return _to_pkg(data[0]) if data else None
        except Exception as e:
            logger.exception("생산 패키지 조회 실패 (order=%s)", oid)
            raise ProductionError(
                "PACKAGE_STORE_UNAVAILABLE", "생산 패키지를 확인하지 못했습니다.", status=503
            ) from e

    row = _MOCK_PACKAGES.get(oid)
    return _to_pkg(row) if row else None


async def _resolve_qr(
    order: physical_order.PhysicalOrder, supplied: str | None
) -> tuple[Optional[str], Optional[str], str]:
    """
    (QR URL 또는 None, share_id, 출처).

    우선순위:
      1. 운영이 넘긴 URL — Shaker URL 인지 검증한다.
      2. 주문에 붙은 공유의 **보관된 산출물** (Phase 13.1) — URL 을 복원하지 않고
         저장된 QR 바이트를 그대로 쓴다. **이미 인쇄된 QR 과 같은 바이트다.**
      3. 둘 다 없으면 거절 — 여기서 새 공유를 발급하면 "펫 경험 중복 금지"가 깨진다.

    2번이 Phase 13.1 이 푼 문제다. 예전에는 발급 탭을 닫으면 생산 준비가 막혔고,
    남는 경로는 재발급(= 인쇄물 무효화)뿐이었다.
    """
    url = (supplied or "").strip()
    if url:
        try:
            qr_service.assert_shaker_url(url)
        except qr_service.QrError as e:
            raise ProductionError(e.code, e.message, status=e.status) from e
        return url, order.shaker_share_id, "url"

    if order.shaker_share_id:
        art = await shaker_qr_artifact.get(order.shaker_share_id)
        if art:
            return None, order.shaker_share_id, "artifact"
        raise ProductionError(
            "QR_URL_REQUIRED",
            (
                "이 주문에는 Shaker 공유가 붙어 있지만 QR 산출물도 토큰도 없습니다. "
                "운영 콘솔에서 URL 을 확인해 함께 보내 주세요 "
                "(새 공유를 만들면 펫 경험이 중복됩니다)."
            ),
            status=409,
        )

    raise ProductionError(
        "QR_SHARE_MISSING",
        "이 펫의 Shaker 공유가 없습니다. 운영 콘솔에서 먼저 발급한 뒤 URL 을 보내 주세요.",
        status=409,
    )


async def prepare(
    *, order_id: str, qr_share_url: str | None = None, photo_image_url: str | None = None
) -> ProductionPackage:
    """
    생산 준비 — 입력을 확정해 스냅샷한다. **멱등이다.**

    이미 패키지가 있으면 **그대로 돌려준다** — 다시 만들지 않고, QR 도 다시
    발급하지 않으며, 편지도 다시 읽어 덮어쓰지 않는다. 인쇄가 이미 시작됐을 수
    있으므로 입력이 조용히 바뀌면 안 된다.

    **PAID 주문만** 생산에 들어간다.
    """
    oid = (order_id or "").strip()
    if not oid:
        raise ProductionError("ORDER_REQUIRED", "order_id 가 필요합니다.")

    existing = await get_package(oid)
    if existing:
        return existing

    try:
        order = await physical_order.get(oid)
    except physical_order.OrderError as e:
        raise ProductionError(e.code, e.message, status=e.status) from e
    if not order:
        raise ProductionError("ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.", status=404)

    # **결제되지 않은 주문은 생산에 들어갈 수 없다.** 돈을 받기 전에 인쇄하면
    # 취소 시 그대로 손실이다.
    if not order.paid:
        raise ProductionError(
            "ORDER_NOT_PAID", "결제된 주문만 생산할 수 있습니다.", status=409
        )

    if not order.soul_trace_letter_id:
        raise ProductionError(
            "LETTER_MISSING", "이 주문에 연결된 Soul Trace 편지가 없습니다.", status=409
        )
    # 편지가 실제로 존재하고 본문이 있는지 확인한다 — 없는 편지를 인쇄할 수 없다.
    try:
        letter = await soul_trace_letter.get_letter(order.soul_trace_letter_id)
    except soul_trace_letter.LetterError as e:
        raise ProductionError(e.code, e.message, status=e.status) from e
    if not letter or not (letter.letter_body or "").strip():
        raise ProductionError(
            "LETTER_BODY_EMPTY",
            "편지 본문이 비어 있습니다. Soul Trace 편지를 다시 연결해 주세요.",
            status=409,
        )

    qr_url, share_id, qr_source = await _resolve_qr(order, qr_share_url)

    row: dict[str, Any] = {
        "order_id": oid,
        "user_id": order.user_id,
        "pet_id": order.pet_id,
        "product_type": order.product_type,
        "soul_trace_letter_id": order.soul_trace_letter_id,
        "qr_share_url": qr_url,
        "qr_source": qr_source,
        "shaker_share_id": share_id,
        "photo_image_url": (photo_image_url or "").strip() or None,
        # 수령인 스냅샷 — 주소가 나중에 바뀌어도 인쇄물은 흔들리지 않는다.
        "recipient_name": order.recipient_name,
        "recipient_phone": order.recipient_phone,
        "postal_code": order.postal_code,
        "address_line1": order.address_line1,
        "address_line2": order.address_line2,
        "built_at": _now().isoformat(),
    }

    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).upsert(row, on_conflict="order_id").execute()
        except Exception as e:
            logger.exception("생산 패키지 저장 실패 (order=%s)", oid)
            raise ProductionError(
                "PACKAGE_STORE_UNAVAILABLE", "생산 패키지를 저장하지 못했습니다.", status=503
            ) from e
    else:
        _MOCK_PACKAGES[oid] = row

    logger.warning(
        "생산 패키지 준비 — order=%s pet=%s letter=%s share=%s",
        oid, order.pet_id, order.soul_trace_letter_id, share_id,
    )
    return _to_pkg(row)


# ── 렌더링 ────────────────────────────────────────────────────────────────────


async def _fetch_bytes(url: str) -> Optional[bytes]:
    try:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.get(url)
            res.raise_for_status()
            return res.content
    except Exception:
        logger.warning("생산용 이미지 다운로드 실패 — %s", url)
        return None


def manifest(pkg: ProductionPackage) -> dict[str, Any]:
    """
    인쇄소·운영이 보는 구성표. **개인정보를 포함하므로 운영 전용이다.**

    파일 목록은 제품 구성(physical_product)에서 나온다 — 여기서 하드코딩하면
    구성이 바뀔 때 두 곳이 어긋난다.
    """
    product = physical_product.get_product(pkg.product_type)
    files = ["letter_pdf"]
    if "photo_card" in product.contents:
        files.append("photo_card")
    if "qr_memory_card" in product.contents:
        files.append("qr_card")

    return {
        "order_id": pkg.order_id,
        "product_type": pkg.product_type,
        "pet_id": pkg.pet_id,
        "soul_trace_letter_id": pkg.soul_trace_letter_id,
        "qr_share_url": pkg.qr_share_url,
        "qr_source": pkg.qr_source,
        "shaker_share_id": pkg.shaker_share_id,
        "files": files,
        "packaging": list(product.contents),
        "card_size_mm": [print_render.CARD_W_MM, print_render.CARD_H_MM],
        "card_dpi": print_render.CARD_DPI,
        "letter_page_size": "A5",
        "font_embedded": print_render.font_is_embedded(),
        "recipient": {
            "name": pkg.recipient_name,
            "phone": pkg.recipient_phone,
            "postal_code": pkg.postal_code,
            "address_line1": pkg.address_line1,
            "address_line2": pkg.address_line2,
        },
        "built_at": pkg.built_at,
    }


async def _qr_png_for(pkg: ProductionPackage) -> Optional[bytes]:
    """
    이 패키지의 QR 이미지 — 보관된 산출물이 있으면 그것.

    산출물을 쓰면 **이미 인쇄된 QR 과 같은 바이트**가 나온다. URL 로 다시 렌더해도
    보통 같겠지만, 렌더 옵션이 바뀌면 달라질 수 있고 인쇄물은 그 차이를 되돌릴 수
    없다. 원본이 있으면 원본을 쓴다.
    """
    if not pkg.shaker_share_id:
        return None
    try:
        art = await shaker_qr_artifact.get(pkg.shaker_share_id)
    except shaker_qr_artifact.QrArtifactError:
        return None
    return art.qr_png if art else None


async def render_file(pkg: ProductionPackage, kind: str) -> print_render.RenderedFile:
    """
    구성 파일 하나를 렌더한다. **결정적** — 같은 패키지면 같은 바이트다.
    """
    k = (kind or "").strip().lower()
    product = physical_product.get_product(pkg.product_type)

    if k == "letter_pdf":
        letter = await soul_trace_letter.get_letter(pkg.soul_trace_letter_id)
        if not letter:
            raise ProductionError("LETTER_MISSING", "편지를 찾을 수 없습니다.", status=409)
        try:
            data = print_render.render_letter_pdf(
                print_render.LetterContent(
                    body=letter.letter_body or "",
                    child_name=letter.child_name,
                    kicker=letter.letter_kicker,
                ),
                order_id=pkg.order_id,
                qr_url=pkg.qr_share_url,
                qr_png=await _qr_png_for(pkg),
            )
        except print_render.PrintRenderError as e:
            raise ProductionError(e.code, e.message, status=e.status) from e
        return print_render.RenderedFile(
            kind=k, filename=f"{pkg.order_id}-letter-a5.pdf",
            content_type="application/pdf", data=data,
        )

    if k == "qr_card":
        if "qr_memory_card" not in product.contents:
            raise ProductionError(
                "FILE_NOT_IN_PRODUCT", "이 제품에는 QR 카드가 없습니다.", status=404
            )
        letter = await soul_trace_letter.get_letter(pkg.soul_trace_letter_id)
        try:
            data = print_render.render_qr_card_png(
                pkg.qr_share_url,
                pet_name=(letter.child_name if letter else None),
                qr_png=await _qr_png_for(pkg),
            )
        except print_render.PrintRenderError as e:
            raise ProductionError(e.code, e.message, status=e.status) from e
        return print_render.RenderedFile(
            kind=k, filename=f"{pkg.order_id}-qr-card-85x55.png",
            content_type="image/png", data=data,
        )

    if k == "photo_card":
        if "photo_card" not in product.contents:
            raise ProductionError(
                "FILE_NOT_IN_PRODUCT", "이 제품에는 사진 카드가 없습니다.", status=404
            )
        if not pkg.photo_image_url:
            raise ProductionError(
                "PHOTO_MISSING",
                "사진 카드용 이미지가 없습니다. 생산 준비 시 photo_image_url 을 지정하세요.",
                status=409,
            )
        raw = await _fetch_bytes(pkg.photo_image_url)
        if not raw:
            raise ProductionError(
                "PHOTO_UNREACHABLE", "사진 이미지를 내려받지 못했습니다.", status=502
            )
        letter = await soul_trace_letter.get_letter(pkg.soul_trace_letter_id)
        try:
            data = print_render.render_photo_card_png(
                raw, pet_name=(letter.child_name if letter else None)
            )
        except print_render.PrintRenderError as e:
            raise ProductionError(e.code, e.message, status=e.status) from e
        return print_render.RenderedFile(
            kind=k, filename=f"{pkg.order_id}-photo-card-85x55.png",
            content_type="image/png", data=data,
        )

    raise ProductionError("FILE_UNKNOWN", f"{kind} 는 알 수 없는 구성 파일입니다.", status=404)


async def render_zip(pkg: ProductionPackage) -> print_render.RenderedFile:
    """
    전체 패키지 ZIP — 인쇄소에 그대로 넘길 한 덩어리.

    구성 파일 하나가 실패해도 **ZIP 을 만들지 않는다.** 반쪽짜리 패키지가
    인쇄소로 넘어가면 무엇이 빠졌는지 아무도 모른 채 생산이 시작된다.
    """
    import io
    import json
    import zipfile

    m = manifest(pkg)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for kind in m["files"]:
            f = await render_file(pkg, kind)
            z.writestr(f.filename, f.data)
        z.writestr(
            f"{pkg.order_id}-manifest.json",
            json.dumps(m, ensure_ascii=False, indent=2),
        )
    return print_render.RenderedFile(
        kind="package", filename=f"{pkg.order_id}-production.zip",
        content_type="application/zip", data=buf.getvalue(),
    )
