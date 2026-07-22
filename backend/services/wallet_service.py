"""
UserWallet — 구독 크레딧(코인) 지갑.

- Supabase `user_wallets` 테이블 (권장)
- 없으면 프로세스 메모리 MOCK + 더미 시드
- 차감은 사용자 단위 asyncio.Lock 으로 레이스 방지
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

from ..data.dummy_business_seed import DUMMY_WALLETS
from ..models.hybrid_business import UserWallet

logger = logging.getLogger(__name__)

_INSUFFICIENT_MSG = "크레딧이 부족합니다. 구독 플랜을 업그레이드하세요."


class InsufficientCreditsError(Exception):
  def __init__(self, message: str = _INSUFFICIENT_MSG):
    super().__init__(message)
    self.message = message


_MOCK_WALLETS: dict[str, UserWallet] = {}
_USER_LOCKS: dict[str, asyncio.Lock] = {}


def _table() -> str:
  return os.getenv("USER_WALLET_TABLE", "user_wallets")


def _supabase():
  from ..models.content import _supabase_client

  return _supabase_client()


def _use_db() -> bool:
  return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _user_lock(user_id: str) -> asyncio.Lock:
  if user_id not in _USER_LOCKS:
    _USER_LOCKS[user_id] = asyncio.Lock()
  return _USER_LOCKS[user_id]


def seed_dummy_wallets() -> None:
  for row in DUMMY_WALLETS:
    uid = row["user_id"]
    if uid not in _MOCK_WALLETS:
      _MOCK_WALLETS[uid] = UserWallet(
        user_id=uid,
        current_credits=int(row["current_credits"]),
        updated_at=datetime.utcnow(),
      )


async def get_wallet(user_id: str, *, create_if_missing: bool = False) -> Optional[UserWallet]:
  uid = user_id.strip()
  if _use_db() and _supabase():
    try:
      sb = _supabase()
      r = sb.table(_table()).select("*").eq("user_id", uid).limit(1).execute()
      if r.data:
        row = r.data[0]
        return UserWallet(
          user_id=row["user_id"],
          current_credits=int(row["current_credits"]),
          updated_at=row.get("updated_at"),
        )
      if create_if_missing:
        starter = max(0, int(os.getenv("STARTER_CREDITS", "0")))
        w = UserWallet(user_id=uid, current_credits=starter, updated_at=datetime.utcnow())
        sb.table(_table()).insert(
          {
            "user_id": uid,
            "current_credits": starter,
            "updated_at": w.updated_at.isoformat(),
          }
        ).execute()
        return w
      return None
    except Exception:
      # Supabase 테이블 부재(마이그레이션 미적용)·네트워크 오류 등으로 지갑 조회가
      # 실패해도 500으로 죽지 않고 인메모리 목업으로 계속 서비스한다 (defense-in-depth).
      logger.exception(
        "wallet_service.get_wallet: Supabase 조회 실패 — 인메모리 목업으로 폴백 (user_id=%s)",
        uid,
      )

  if uid in _MOCK_WALLETS:
    return _MOCK_WALLETS[uid]
  if create_if_missing:
    starter = max(0, int(os.getenv("STARTER_CREDITS", "0")))
    w = UserWallet(user_id=uid, current_credits=starter, updated_at=datetime.utcnow())
    _MOCK_WALLETS[uid] = w
    return w
  return None


async def deduct_credits(user_id: str, amount: int) -> UserWallet:
  """
  크레딧 차감 (원자적에 가깝게).

  Returns:
    차감 후 지갑 상태

  Raises:
    InsufficientCreditsError: 잔액 부족
  """
  if amount <= 0:
    raise ValueError("amount must be positive")

  uid = user_id.strip()
  async with _user_lock(uid):
    wallet = await get_wallet(uid, create_if_missing=True)
    assert wallet is not None

    if wallet.current_credits < amount:
      raise InsufficientCreditsError()

    new_balance = wallet.current_credits - amount
    now = datetime.utcnow()

    if _use_db() and _supabase():
      try:
        sb = _supabase()
        # 낙관적 잠금: 이전 잔액과 일치할 때만 업데이트
        r = (
          sb.table(_table())
          .update({"current_credits": new_balance, "updated_at": now.isoformat()})
          .eq("user_id", uid)
          .eq("current_credits", wallet.current_credits)
          .execute()
        )
        if not r.data:
          # 동시 요청 등으로 실패 → 재조회 후 한 번 더 판단
          refreshed = await get_wallet(uid)
          if not refreshed or refreshed.current_credits < amount:
            raise InsufficientCreditsError()
          return await deduct_credits(uid, amount)
      except InsufficientCreditsError:
        raise
      except Exception:
        # DB 갱신 실패(테이블 부재 등) — 인메모리 목업으로 폴백해 요청 자체는 계속 처리.
        logger.exception(
          "wallet_service.deduct_credits: Supabase 갱신 실패 — 인메모리 목업으로 폴백 (user_id=%s)",
          uid,
        )

    wallet.current_credits = new_balance
    wallet.updated_at = now
    _MOCK_WALLETS[uid] = wallet
    return wallet


async def add_credits(user_id: str, amount: int) -> UserWallet:
  """IAP·구독 등 — 크레딧 충전 (사용자 단위 Lock)."""
  if amount <= 0:
    raise ValueError("amount must be positive")

  uid = user_id.strip()
  async with _user_lock(uid):
    wallet = await get_wallet(uid, create_if_missing=True)
    assert wallet is not None
    new_balance = wallet.current_credits + amount
    now = datetime.utcnow()

    if _use_db() and _supabase():
      try:
        sb = _supabase()
        r = sb.rpc("add_wallet_credits", {"p_user_id": uid, "p_amount": amount}).execute()
        if r.data is not None:
          bal = r.data
          if isinstance(bal, list) and bal:
            bal = bal[0]
          if isinstance(bal, (int, float)):
            new_balance = int(bal)
          elif isinstance(bal, dict) and "credits_remaining" in bal:
            new_balance = int(bal["credits_remaining"])
          else:
            sb.table(_table()).update(
              {"current_credits": new_balance, "updated_at": now.isoformat()}
            ).eq("user_id", uid).execute()
        else:
          sb.table(_table()).update(
            {"current_credits": new_balance, "updated_at": now.isoformat()}
          ).eq("user_id", uid).execute()
      except Exception:
        logger.exception(
          "wallet_service.add_credits: Supabase 갱신 실패 — 인메모리 목업으로 폴백 (user_id=%s)",
          uid,
        )

    wallet.current_credits = new_balance
    wallet.updated_at = now
    _MOCK_WALLETS[uid] = wallet
    return wallet


async def refund_credits(user_id: str, amount: int) -> UserWallet:
  """Luma 제출 전체 실패 등 — 차감 롤백."""
  uid = user_id.strip()
  async with _user_lock(uid):
    wallet = await get_wallet(uid, create_if_missing=True)
    assert wallet is not None
    new_balance = wallet.current_credits + amount
    now = datetime.utcnow()

    if _use_db() and _supabase():
      try:
        sb = _supabase()
        sb.table(_table()).update(
          {"current_credits": new_balance, "updated_at": now.isoformat()}
        ).eq("user_id", uid).execute()
      except Exception:
        logger.exception(
          "wallet_service.refund_credits: Supabase 갱신 실패 — 인메모리 목업으로 폴백 (user_id=%s)",
          uid,
        )

    wallet.current_credits = new_balance
    wallet.updated_at = now
    _MOCK_WALLETS[uid] = wallet
    return wallet
