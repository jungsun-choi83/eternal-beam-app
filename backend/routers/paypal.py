"""
/api/paypal — 프리미엄 테마 PayPal 결제 (Orders v2, 서버사이드 검증).

프론트가 클라이언트에서 "결제완료"라고 주장하는 걸 그대로 믿지 않고, 항상
capture-order에서 PayPal 서버 응답의 status == "COMPLETED"를 확인한 뒤에만
purchased_slots에 기록한다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models.paypal import (
    CapturePaypalOrderRequest,
    CapturePaypalOrderResponse,
    CreatePaypalOrderRequest,
    CreatePaypalOrderResponse,
)
from ..services import paypal_service, supabase_assets
from ..services.theme_prices import get_theme_price_usd, is_free_theme

router = APIRouter(prefix="/paypal", tags=["paypal"])


def _extract_capture_id(order: dict) -> str | None:
    try:
        purchase_units = order.get("purchase_units") or []
        if not purchase_units:
            return None
        captures = ((purchase_units[0].get("payments") or {}).get("captures")) or []
        if captures:
            return captures[0].get("id")
    except Exception:
        pass
    return None


@router.post("/create-order", response_model=CreatePaypalOrderResponse)
async def post_create_order(body: CreatePaypalOrderRequest):
    """프리미엄 테마 1건에 대한 PayPal 주문 생성. 무료 테마는 400."""
    if is_free_theme(body.theme_key):
        raise HTTPException(status_code=400, detail="무료 테마는 결제가 필요 없습니다.")

    price = get_theme_price_usd(body.theme_key)
    try:
        order = await paypal_service.create_order(
            price,
            description=f"Eternal Beam theme: {body.theme_key}"[:127],
            reference_id=f"{body.user_id}:{body.theme_key}"[:256],
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"PayPal 주문 생성 실패: {e}") from e

    order_id = order.get("id")
    if not order_id:
        raise HTTPException(status_code=502, detail=f"PayPal 주문 응답에 id가 없습니다: {order}")

    return CreatePaypalOrderResponse(
        order_id=order_id,
        amount_usd=price,
        theme_key=body.theme_key,
        status=order.get("status", ""),
    )


@router.post("/capture-order", response_model=CapturePaypalOrderResponse)
async def post_capture_order(body: CapturePaypalOrderRequest):
    """
    사용자가 PayPal 승인(approve)까지 마친 주문을 실제로 캡처(대금 확정).
    status가 COMPLETED일 때만 purchased_slots에 기록해서 테마를 잠금 해제한다.
    """
    try:
        result = await paypal_service.capture_order(body.order_id)
    except Exception as e:
        # 이미 캡처된 주문을 재호출하면 PayPal이 422를 주는 경우가 있어, 그 경우엔
        # 현재 주문 상태를 다시 조회해서 이미 COMPLETED인지 확인한다(멱등 처리).
        try:
            result = await paypal_service.get_order(body.order_id)
        except Exception:
            raise HTTPException(status_code=502, detail=f"PayPal 결제 확인 실패: {e}") from e

    status = result.get("status", "")
    payment_id = _extract_capture_id(result)
    success = status == "COMPLETED"

    message = ""
    if success:
        try:
            await supabase_assets.record_theme_purchase(
                body.user_id, body.theme_key, payment_id
            )
        except Exception as e:
            # 결제 자체는 성공 — DB 기록 실패를 결제 실패로 보이게 하지 않고 메시지로만 남김.
            message = f"결제는 완료됐지만 구매 내역 저장에 실패했습니다: {e}"

    return CapturePaypalOrderResponse(
        success=success,
        status=status,
        theme_key=body.theme_key,
        order_id=body.order_id,
        payment_id=payment_id,
        message=message,
    )
