"""
/api/v1/payment — 인앱 결제(IAP) 영수증 검증 및 크레딧 충전
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..data.iap_products import IAP_PRODUCTS
from ..models.payment import VerifyAndChargeRequest, VerifyAndChargeResponse
from ..services.iap_charge_service import PaymentVerificationError, verify_and_charge
from ..services.wallet_service import WalletUnavailableError

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
  except WalletUnavailableError as e:
    # 지갑을 DB 로 확정하지 못했다 → 충전을 성공으로 보고하지 않는다.
    #
    # 503 인 이유: 영수증은 유효하고 고객 잘못도 아니다. 같은 영수증으로 다시
    # 시도하면 되고, 그때 이중 충전은 payment_history.receipt_fingerprint 의
    # unique 인덱스가 막는다. 400 이면 앱이 "영수증이 잘못됐다"로 처리해
    # 재시도를 포기한다 — 고객은 돈을 내고 크레딧을 못 받은 채로 남는다.
    raise HTTPException(
      status_code=503,
      detail={
        "code": "WALLET_UNAVAILABLE",
        "message": "지갑을 갱신하지 못했습니다. 잠시 후 다시 시도해 주세요. "
                   "같은 영수증으로 재시도해도 이중 충전되지 않습니다.",
        "credits_added": 0,
      },
    ) from e
  except ValueError as e:
    raise HTTPException(status_code=400, detail=str(e)) from e
