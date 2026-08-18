"""
Apple App Store / Google Play 영수증 검증.

- PAYMENT_MOCK=1: 개발용 (receipt 해시로 transaction_id 생성)
- 프로덕션: APPLE_SHARED_SECRET, GOOGLE_PACKAGE_NAME + 서비스 계정
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any, Literal, Optional

import asyncio

import requests

StoreType = Literal["apple", "google"]


@dataclass
class VerifiedReceipt:
  valid: bool
  transaction_id: str
  product_id: str
  store_type: StoreType
  raw_meta: dict[str, Any]
  error: Optional[str] = None


def _mock_enabled() -> bool:
  return os.getenv("PAYMENT_MOCK", "0").strip().lower() in ("1", "true", "yes")


#: 공개 별칭 — 호출부가 "지금 목업 모드인가"를 물어볼 수 있게 한다.
#: (iap_charge_service 가 테스트 전용 상품을 실 검증으로 보내지 않으려고 쓴다.)
def mock_enabled() -> bool:
  return _mock_enabled()


def _receipt_fingerprint(store_type: str, receipt_data: str) -> str:
  payload = f"{store_type}:{receipt_data.strip()}"
  return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _verify_mock(
  receipt_data: str,
  store_type: StoreType,
  expected_product_id: str,
) -> VerifiedReceipt:
  """개발·스테이징: 영수증 문자열이 비어 있지 않으면 유효 처리."""
  rid = receipt_data.strip()
  if len(rid) < 8:
    return VerifiedReceipt(
      valid=False,
      transaction_id="",
      product_id=expected_product_id,
      store_type=store_type,
      raw_meta={},
      error="receipt_data too short",
    )
  tx = f"mock_{store_type}_{hashlib.sha256(rid.encode()).hexdigest()[:24]}"
  return VerifiedReceipt(
    valid=True,
    transaction_id=tx,
    product_id=expected_product_id,
    store_type=store_type,
    raw_meta={"mode": "mock", "fingerprint": _receipt_fingerprint(store_type, rid)},
  )


async def _verify_apple(receipt_data: str, expected_product_id: str) -> VerifiedReceipt:
  secret = (os.getenv("APPLE_SHARED_SECRET") or "").strip()
  if not secret:
    if _mock_enabled():
      return await _verify_mock(receipt_data, "apple", expected_product_id)
    return VerifiedReceipt(
      valid=False,
      transaction_id="",
      product_id=expected_product_id,
      store_type="apple",
      raw_meta={},
      error="APPLE_SHARED_SECRET not configured",
    )

  use_sandbox = os.getenv("APPLE_USE_SANDBOX", "1").strip().lower() in ("1", "true", "yes")
  url = (
    "https://sandbox.itunes.apple.com/verifyReceipt"
    if use_sandbox
    else "https://buy.itunes.apple.com/verifyReceipt"
  )

  body = {
    "receipt-data": receipt_data.strip(),
    "password": secret,
    "exclude-old-transactions": True,
  }

  def _post() -> dict:
    r = requests.post(url, json=body, timeout=30)
    r.raise_for_status()
    return r.json()

  data = await asyncio.to_thread(_post)
  status = int(data.get("status", -1))
  # 21007: sandbox receipt sent to production → retry sandbox
  if status == 21007 and not use_sandbox:

    def _post_sandbox() -> dict:
      r = requests.post(
        "https://sandbox.itunes.apple.com/verifyReceipt",
        json=body,
        timeout=30,
      )
      r.raise_for_status()
      return r.json()

    data = await asyncio.to_thread(_post_sandbox)
    status = int(data.get("status", -1))

  if status != 0:
    return VerifiedReceipt(
      valid=False,
      transaction_id="",
      product_id=expected_product_id,
      store_type="apple",
      raw_meta={"apple_status": status},
      error=f"Apple verifyReceipt status={status}",
    )

  latest = (data.get("latest_receipt_info") or data.get("receipt", {}).get("in_app") or [])
  if isinstance(latest, dict):
    latest = [latest]
  if not latest:
    return VerifiedReceipt(
      valid=False,
      transaction_id="",
      product_id=expected_product_id,
      store_type="apple",
      raw_meta=data,
      error="no in_app purchases in receipt",
    )

  item = latest[-1] if isinstance(latest, list) else latest
  tx_id = str(item.get("transaction_id") or item.get("original_transaction_id") or "")
  prod = str(item.get("product_id") or expected_product_id)

  if prod != expected_product_id:
    return VerifiedReceipt(
      valid=False,
      transaction_id=tx_id,
      product_id=prod,
      store_type="apple",
      raw_meta={"item": item},
      error=f"product_id mismatch: {prod}",
    )

  return VerifiedReceipt(
    valid=True,
    transaction_id=tx_id,
    product_id=prod,
    store_type="apple",
    raw_meta={"apple_status": status, "item": item},
  )


async def _verify_google(receipt_data: str, expected_product_id: str) -> VerifiedReceipt:
  """
  Google Play: 클라이언트 purchaseToken 전달.

  전체 Google Play Developer API 연동은 서비스 계정 JSON 필요.
  미설정 시 PAYMENT_MOCK=1 또는 GOOGLE_VERIFY_URL 프록시 사용.
  """
  package = (os.getenv("GOOGLE_PACKAGE_NAME") or "").strip()
  if not package:
    if _mock_enabled():
      return await _verify_mock(receipt_data, "google", expected_product_id)
    return VerifiedReceipt(
      valid=False,
      transaction_id="",
      product_id=expected_product_id,
      store_type="google",
      raw_meta={},
      error="GOOGLE_PACKAGE_NAME not configured",
    )

  # 선택: 자체 검증 프록시 (Cloud Function 등)
  proxy = (os.getenv("GOOGLE_VERIFY_URL") or "").strip()
  token = receipt_data.strip()
  if proxy:

    def _post_google() -> dict:
      r = requests.post(
        proxy,
        json={
          "packageName": package,
          "productId": expected_product_id,
          "purchaseToken": token,
        },
        timeout=30,
      )
      r.raise_for_status()
      return r.json()

    data = await asyncio.to_thread(_post_google)
    if not data.get("valid"):
      return VerifiedReceipt(
        valid=False,
        transaction_id="",
        product_id=expected_product_id,
        store_type="google",
        raw_meta=data,
        error=data.get("error", "google verification failed"),
      )
    return VerifiedReceipt(
      valid=True,
      transaction_id=str(data.get("orderId") or data.get("transaction_id") or token[:32]),
      product_id=str(data.get("productId") or expected_product_id),
      store_type="google",
      raw_meta=data,
    )

  if _mock_enabled():
    return await _verify_mock(receipt_data, "google", expected_product_id)

  return VerifiedReceipt(
    valid=False,
    transaction_id="",
    product_id=expected_product_id,
    store_type="google",
    raw_meta={},
    error="Configure GOOGLE_VERIFY_URL or PAYMENT_MOCK=1",
  )


async def verify_store_receipt(
  *,
  receipt_data: str,
  store_type: StoreType,
  expected_product_id: str,
) -> VerifiedReceipt:
  st = store_type.lower().strip()
  if st == "apple":
    return await _verify_apple(receipt_data, expected_product_id)
  if st == "google":
    return await _verify_google(receipt_data, expected_product_id)
  return VerifiedReceipt(
    valid=False,
    transaction_id="",
    product_id=expected_product_id,
    store_type="apple",
    raw_meta={},
    error=f"invalid store_type: {store_type}",
  )


def receipt_fingerprint(store_type: str, receipt_data: str) -> str:
  return _receipt_fingerprint(store_type, receipt_data)
