"""
IAP 검증 + 크레딧 충전 오케스트레이션.

① 스토어 영수증 검증
② 중복 영수증 확인 (idempotent)
③ PaymentHistory + UserWallet (트랜잭션)
"""

from __future__ import annotations

import asyncio
from typing import Literal

from ..data.iap_products import get_product
from ..models.payment import VerifyAndChargeResponse
from . import payment_history_service as pay_hist
from .iap_verification_service import (
  StoreType,
  receipt_fingerprint,
  verify_store_receipt,
)
from .wallet_service import get_wallet

# 동일 user_id 동시 결제 직렬화
_CHARGE_LOCKS: dict[str, asyncio.Lock] = {}


def _user_charge_lock(user_id: str) -> asyncio.Lock:
  uid = user_id.strip()
  if uid not in _CHARGE_LOCKS:
    _CHARGE_LOCKS[uid] = asyncio.Lock()
  return _CHARGE_LOCKS[uid]


class PaymentVerificationError(Exception):
  def __init__(self, message: str):
    super().__init__(message)
    self.message = message


async def verify_and_charge(
  *,
  user_id: str,
  receipt_data: str,
  store_type: StoreType,
  product_id: str = "credit_pack_4",
) -> VerifyAndChargeResponse:
  uid = user_id.strip()
  if not uid:
    raise ValueError("user_id is required")

  product = get_product(product_id)

  # 테스트 전용 상품(0원)은 **실제 스토어 검증 경로로 보내지 않는다.**
  #
  # 예전에는 그대로 흘러가서, PAYMENT_MOCK 이 꺼진 환경에서
  # "GOOGLE_PACKAGE_NAME not configured" 라는 엉뚱한 400 이 났다 — 설정에서
  # "테스트 크레딧 추가"를 눌렀는데 Google Play 설정 오류가 나오니 원인을 찾기
  # 어려웠다. 이 상품들은 어느 스토어에도 존재하지 않으므로 실 검증은 성공할 수
  # 없고, 실패한다면 원인은 언제나 "목업이 꺼져 있다" 하나뿐이다.
  from ..data.iap_products import TEST_ONLY_PRODUCT_IDS
  from .iap_verification_service import mock_enabled

  if product.product_id in TEST_ONLY_PRODUCT_IDS and not mock_enabled():
    raise PaymentVerificationError(
      f"{product.product_id} 는 테스트 전용 상품입니다. "
      "PAYMENT_MOCK=1 이 설정된 환경에서만 사용할 수 있습니다."
    )

  st: Literal["apple", "google"] = store_type  # type: ignore[assignment]
  fp = receipt_fingerprint(st, receipt_data)

  async with _user_charge_lock(uid):
    # ②-a 이미 성공한 영수증 → 재플레이 (충전 없음)
    prior = await pay_hist.find_success_by_fingerprint(fp)
    if prior and prior.status == "success":
      w = await get_wallet(uid, create_if_missing=True)
      return VerifyAndChargeResponse(
        success=True,
        user_id=uid,
        product_id=product.product_id,
        amount_krw=product.price_krw,
        credits_added=0,
        credits_remaining=w.current_credits if w else 0,
        payment_id=prior.id,
        transaction_id=prior.transaction_id,
        store_type=st,
        status="success",
        idempotent_replay=True,
        message="이미 처리된 영수증입니다. 잔액은 변경되지 않았습니다.",
      )

    # ① 스토어 검증
    verified = await verify_store_receipt(
      receipt_data=receipt_data,
      store_type=st,
      expected_product_id=product.product_id,
    )

    if not verified.valid:
      await pay_hist.insert_failed(
        user_id=uid,
        product_id=product.product_id,
        store_type=st,
        receipt_fingerprint=fp,
        amount_krw=product.price_krw,
        credits_added=product.credits,
        error_message=verified.error or "invalid receipt",
        transaction_id=verified.transaction_id or None,
        raw_meta=verified.raw_meta,
      )
      raise PaymentVerificationError(verified.error or "영수증 검증에 실패했습니다.")

    # ②-b 동일 transaction_id
    if verified.transaction_id:
      prior_tx = await pay_hist.find_success_by_transaction(st, verified.transaction_id)
      if prior_tx:
        w = await get_wallet(uid, create_if_missing=True)
        return VerifyAndChargeResponse(
          success=True,
          user_id=uid,
          product_id=product.product_id,
          amount_krw=product.price_krw,
          credits_added=0,
          credits_remaining=w.current_credits if w else 0,
          payment_id=prior_tx.id,
          transaction_id=verified.transaction_id,
          store_type=st,
          status="success",
          idempotent_replay=True,
          message="이미 처리된 거래 ID입니다.",
        )

    # ③ DB 트랜잭션 충전
    from .payment_history_service import _supabase, _use_db

    try:
      if _use_db() and _supabase():
        result = await pay_hist.process_charge_via_rpc(
          user_id=uid,
          product_id=product.product_id,
          store_type=st,
          receipt_fingerprint=fp,
          transaction_id=verified.transaction_id,
          amount_krw=product.price_krw,
          credits_added=product.credits,
          raw_meta=verified.raw_meta,
        )
        pay_id = int(result.get("payment_id", 0))
        remaining = int(result.get("credits_remaining", 0))
      else:
        pay_id, remaining = await pay_hist.process_charge_mock(
          user_id=uid,
          product_id=product.product_id,
          store_type=st,
          receipt_fingerprint=fp,
          transaction_id=verified.transaction_id,
          amount_krw=product.price_krw,
          credits_added=product.credits,
          raw_meta=verified.raw_meta,
        )
    except Exception as e:
      err = str(e).lower()
      if "duplicate_receipt" in err or "unique" in err:
        prior = await pay_hist.find_success_by_fingerprint(fp)
        w = await get_wallet(uid, create_if_missing=True)
        return VerifyAndChargeResponse(
          success=True,
          user_id=uid,
          product_id=product.product_id,
          amount_krw=product.price_krw,
          credits_added=0,
          credits_remaining=w.current_credits if w else 0,
          payment_id=prior.id if prior else None,
          transaction_id=verified.transaction_id,
          store_type=st,
          status="success",
          idempotent_replay=True,
          message="동시 요청으로 이미 충전되었습니다.",
        )
      raise

    return VerifyAndChargeResponse(
      success=True,
      user_id=uid,
      product_id=product.product_id,
      amount_krw=product.price_krw,
      credits_added=product.credits,
      credits_remaining=remaining,
      payment_id=pay_id,
      transaction_id=verified.transaction_id,
      store_type=st,
      status="success",
      idempotent_replay=False,
      message="크레딧이 충전되었습니다.",
    )
