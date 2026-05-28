"""
PaymentHistory — 영수증 중복 방지 · 감사 로그.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

from ..models.payment import PaymentHistoryRow


def _table() -> str:
  return os.getenv("PAYMENT_HISTORY_TABLE", "payment_history")


def _supabase():
  from ..models.content import _supabase_client

  return _supabase_client()


def _use_db() -> bool:
  return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


_MOCK_HISTORY: dict[str, PaymentHistoryRow] = {}
_MOCK_BY_TX: dict[str, PaymentHistoryRow] = {}


async def find_success_by_fingerprint(fingerprint: str) -> Optional[PaymentHistoryRow]:
  fp = fingerprint.strip()
  if _use_db() and _supabase():
    sb = _supabase()
    r = (
      sb.table(_table())
      .select("*")
      .eq("receipt_fingerprint", fp)
      .eq("status", "success")
      .limit(1)
      .execute()
    )
    if r.data:
      return _row_from_db(r.data[0])
    return None
  return _MOCK_HISTORY.get(fp)


async def find_success_by_transaction(
  store_type: str, transaction_id: str
) -> Optional[PaymentHistoryRow]:
  if not transaction_id:
    return None
  st = store_type.strip().lower()
  tx = transaction_id.strip()
  key = f"{st}:{tx}"
  if _use_db() and _supabase():
    sb = _supabase()
    r = (
      sb.table(_table())
      .select("*")
      .eq("store_type", st)
      .eq("transaction_id", tx)
      .eq("status", "success")
      .limit(1)
      .execute()
    )
    if r.data:
      return _row_from_db(r.data[0])
    return None
  return _MOCK_BY_TX.get(key)


async def insert_failed(
  *,
  user_id: str,
  product_id: str,
  store_type: str,
  receipt_fingerprint: str,
  amount_krw: int,
  credits_added: int,
  error_message: str,
  transaction_id: Optional[str] = None,
  raw_meta: Optional[dict[str, Any]] = None,
) -> PaymentHistoryRow:
  row = PaymentHistoryRow(
    user_id=user_id,
    product_id=product_id,
    store_type=store_type,
    receipt_fingerprint=receipt_fingerprint,
    transaction_id=transaction_id,
    amount_krw=amount_krw,
    credits_added=credits_added,
    status="failed",
    error_message=error_message,
    raw_receipt_meta=raw_meta,
    created_at=datetime.utcnow(),
  )
  if _use_db() and _supabase():
    sb = _supabase()
    payload = {
      "user_id": user_id,
      "product_id": product_id,
      "store_type": store_type,
      "receipt_fingerprint": receipt_fingerprint,
      "transaction_id": transaction_id,
      "amount_krw": amount_krw,
      "credits_added": credits_added,
      "status": "failed",
      "error_message": error_message,
      "raw_receipt_meta": raw_meta,
    }
    r = sb.table(_table()).insert(payload).execute()
    if r.data:
      return _row_from_db(r.data[0])
  return row


async def process_charge_via_rpc(
  *,
  user_id: str,
  product_id: str,
  store_type: str,
  receipt_fingerprint: str,
  transaction_id: str,
  amount_krw: int,
  credits_added: int,
  raw_meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
  """
  Supabase RPC `process_iap_charge` — 결제 이력 + 지갑 충전 단일 트랜잭션.
  """
  sb = _supabase()
  if not sb:
    raise RuntimeError("Supabase not configured")

  r = sb.rpc(
    "process_iap_charge",
    {
      "p_user_id": user_id,
      "p_product_id": product_id,
      "p_store_type": store_type,
      "p_receipt_fingerprint": receipt_fingerprint,
      "p_transaction_id": transaction_id,
      "p_amount_krw": amount_krw,
      "p_credits_added": credits_added,
      "p_raw_meta": raw_meta or {},
    },
  ).execute()

  if getattr(r, "data", None) is None:
    raise RuntimeError("process_iap_charge returned no data")

  data = r.data
  if isinstance(data, list) and data:
    data = data[0]
  return dict(data) if isinstance(data, dict) else {"raw": data}


async def process_charge_mock(
  *,
  user_id: str,
  product_id: str,
  store_type: str,
  receipt_fingerprint: str,
  transaction_id: str,
  amount_krw: int,
  credits_added: int,
  raw_meta: Optional[dict[str, Any]] = None,
) -> tuple[int, int]:
  """메모리 MOCK: (payment_id, credits_remaining)."""
  from .wallet_service import get_wallet

  if receipt_fingerprint in _MOCK_HISTORY:
    existing = _MOCK_HISTORY[receipt_fingerprint]
    if existing.status == "success" and existing.id is not None:
      w = await get_wallet(user_id, create_if_missing=True)
      return existing.id, w.current_credits if w else 0

  tx_key = f"{store_type}:{transaction_id}"
  if transaction_id and tx_key in _MOCK_BY_TX:
    existing = _MOCK_BY_TX[tx_key]
    w = await get_wallet(user_id, create_if_missing=True)
    return existing.id or 0, w.current_credits if w else 0

  wallet = await add_credits(user_id, credits_added)
  pay_id = len(_MOCK_HISTORY) + 1
  row = PaymentHistoryRow(
    id=pay_id,
    user_id=user_id,
    product_id=product_id,
    store_type=store_type,
    receipt_fingerprint=receipt_fingerprint,
    transaction_id=transaction_id,
    amount_krw=amount_krw,
    credits_added=credits_added,
    status="success",
    raw_receipt_meta=raw_meta,
    created_at=datetime.utcnow(),
  )
  _MOCK_HISTORY[receipt_fingerprint] = row
  if transaction_id:
    _MOCK_BY_TX[tx_key] = row
  return pay_id, wallet.current_credits


def _row_from_db(row: dict) -> PaymentHistoryRow:
  return PaymentHistoryRow(
    id=int(row["id"]) if row.get("id") is not None else None,
    user_id=row["user_id"],
    product_id=row["product_id"],
    store_type=row["store_type"],
    receipt_fingerprint=row["receipt_fingerprint"],
    transaction_id=row.get("transaction_id"),
    amount_krw=int(row["amount_krw"]),
    credits_added=int(row["credits_added"]),
    status=row["status"],
    error_message=row.get("error_message"),
    raw_receipt_meta=row.get("raw_receipt_meta"),
    created_at=row.get("created_at"),
  )
