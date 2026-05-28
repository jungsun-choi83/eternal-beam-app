"""
/api/v1/payment — 인앱 결제(IAP) 영수증 검증 및 크레딧 충전
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..data.iap_products import IAP_PRODUCTS
from ..models.payment import VerifyAndChargeRequest, VerifyAndChargeResponse
from ..services.iap_charge_service import PaymentVerificationError, verify_and_charge

router = APIRouter(prefix="/v1/payment", tags=["payment-v1"])


@router.get("/products")
async def list_iap_products():
  """앱·QA용 상품 목록."""
  return {
    "products": [
      {
        "product_id": p.product_id,
        "price_krw": p.price_krw,
        "credits": p.credits,
        "display_name": p.display_name,
      }
      for p in IAP_PRODUCTS.values()
    ]
  }


@router.post("/verify-and-charge", response_model=VerifyAndChargeResponse)
async def post_verify_and_charge(body: VerifyAndChargeRequest):
  """
  Apple / Google 인앱 결제 영수증 검증 후 지갑에 크레딧 충전.

  - **credit_pack_4**: 4,900 KRW → +4 credits
  - 동일 영수증 재전송 시 `idempotent_replay: true` (중복 충전 없음)
  """
  try:
    return await verify_and_charge(
      user_id=body.user_id,
      receipt_data=body.receipt_data,
      store_type=body.store_type,
      product_id=body.product_id,
    )
  except PaymentVerificationError as e:
    raise HTTPException(status_code=400, detail=e.message) from e
  except ValueError as e:
    raise HTTPException(status_code=400, detail=str(e)) from e
