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
  sb = None
  if _use_db():
    try:
      sb = _supabase()
    except Exception:
      logger.exception(
        "wallet_service.get_wallet: Supabase client init failed — 인메모리 목업으로 폴백 (user_id=%s)",
        uid,
      )
  if sb:
    try:
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


class WalletUnavailableError(Exception):
  """
  지갑을 신뢰할 수 있게 갱신하지 못했다.

  **차감을 성공으로 처리하면 안 되는** 경우에만 던진다. 예전 코드는 여기서
  인메모리 목업으로 폴백했는데, 그건 목업 파이프라인에서는 편의였지만 실제
  구매 크레딧에서는 사고다: Supabase 장애 중 차감이 프로세스 메모리에만 남고,
  Render 무료 플랜이 인스턴스를 재활용하면 잔액이 통째로 되돌아간다.
  """

  def __init__(self, message: str = "지갑 서비스를 사용할 수 없습니다."):
    super().__init__(message)
    self.message = message


async def _deduct_in_memory_locked(uid: str, amount: int) -> UserWallet:
  """
  인메모리 차감. **호출자가 이미 _user_lock 을 쥐고 있다고 가정한다** —
  여기서 다시 잠그면 asyncio.Lock 은 재진입이 안 되므로 교착한다.
  """
  wallet = await get_wallet(uid, create_if_missing=True)
  assert wallet is not None
  if wallet.current_credits < amount:
    raise InsufficientCreditsError()
  wallet.current_credits -= amount
  wallet.updated_at = datetime.utcnow()
  _MOCK_WALLETS[uid] = wallet
  return wallet


def _rpc_says_insufficient(err: Exception) -> bool:
  """deduct_wallet_credits RPC 의 `raise exception 'insufficient_credits'` 판별."""
  return "insufficient_credits" in f"{err}".lower()


async def deduct_credits(user_id: str, amount: int, *, strict: bool = False) -> UserWallet:
  """
  크레딧 차감.

  strict=True (프리미엄 구매 경로):
    Supabase 의 **원자적 RPC** deduct_wallet_credits 만 쓴다. 실패하면
    WalletUnavailableError 로 **닫힌다(fail closed)** — 인메모리 폴백이 없다.

  strict=False (레거시 4코인 경로):
    기존 동작을 그대로 유지한다. 이 경로의 검증된 거동을 바꾸지 않기 위해서다.

  Raises:
    InsufficientCreditsError: 잔액 부족
    WalletUnavailableError:   strict 인데 지갑을 신뢰성 있게 갱신하지 못함
  """
  if amount <= 0:
    raise ValueError("amount must be positive")

  uid = user_id.strip()
  async with _user_lock(uid):
    if strict:
      # ── 원자적 경로 ────────────────────────────────────────────────────────
      # RPC 는 `update ... where current_credits >= p_amount` 한 문장이라
      # 읽기-쓰기 사이에 끼어들 틈이 없다. 마이그레이션에 이미 있었지만 여태
      # 아무도 호출하지 않았다(add_credits 만 자기 짝 RPC 를 쓰고 있었다).
      # 구분이 중요하다:
      #   HYBRID_USE_SUPABASE=0  → 운영자가 **명시적으로** DB 를 끈 로컬/테스트 모드.
      #                            실제 구매 크레딧이 존재하지 않으므로 인메모리로 간다.
      #   DB 를 쓰기로 해 놓고 실패 → **닫는다.** 예전처럼 조용히 인메모리로 흘리면
      #                            Supabase 장애 중 차감이 프로세스 메모리에만 남고,
      #                            인스턴스가 재활용되면 잔액이 통째로 복구된다.
      if not _use_db():
        logger.warning(
          "wallet_service: HYBRID_USE_SUPABASE=0 — 인메모리 지갑으로 차감합니다 "
          "(실구매 크레딧이 아닙니다). user_id=%s",
          uid,
        )
        # ⚠️ deduct_credits 를 다시 부르면 **같은 락을 재진입**해 교착한다
        # (_user_lock 은 asyncio.Lock 이라 재진입이 불가능하다). 락 안에서
        # 안전한 내부 헬퍼를 쓴다.
        return await _deduct_in_memory_locked(uid, amount)

      try:
        sb = _supabase()
      except Exception as e:
        raise WalletUnavailableError("Supabase 클라이언트를 만들지 못했습니다.") from e
      if not sb:
        raise WalletUnavailableError("Supabase 가 설정되지 않았습니다.")

      # 지갑 행이 없으면 RPC 의 update 가 0행이라 insufficient 로 떨어진다.
      # 신규 사용자를 위해 행만 먼저 보장한다(잔액은 건드리지 않는다).
      await get_wallet(uid, create_if_missing=True)

      try:
        r = sb.rpc("deduct_wallet_credits", {"p_user_id": uid, "p_amount": amount}).execute()
      except Exception as e:
        if _rpc_says_insufficient(e):
          raise InsufficientCreditsError() from e
        raise WalletUnavailableError(f"차감 RPC 실패: {type(e).__name__}") from e

      bal = r.data
      if isinstance(bal, list) and bal:
        bal = bal[0]
      if isinstance(bal, dict):
        bal = bal.get("current_credits", bal.get("credits_remaining"))
      if not isinstance(bal, (int, float)):
        raise WalletUnavailableError("차감 RPC 가 잔액을 돌려주지 않았습니다.")

      w = UserWallet(user_id=uid, current_credits=int(bal), updated_at=datetime.utcnow())
      _MOCK_WALLETS[uid] = w
      return w

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


async def refund_credits(user_id: str, amount: int, *, strict: bool = False) -> UserWallet:
  """
  Luma 제출 전체 실패 등 — 차감 롤백.

  예전 구현은 읽고 → 계산하고 → **무조건 덮어썼다**(CAS 없음). 환불과 차감이
  겹치면 한쪽 쓰기가 통째로 사라진다(lost update). 프리미엄은 액션마다 세션이
  따로 있고 서버가 큐를 자동 전진시켜 종료 이벤트가 잦으므로, 그 창이 실제로
  열린다. 이제 add_wallet_credits RPC(원자적 증분)를 쓴다 — add_credits 가
  이미 쓰고 있던 바로 그 함수다.

  strict=True 면 RPC 실패를 삼키지 않고 WalletUnavailableError 로 올린다.
  """
  if amount <= 0:
    if strict:
      raise ValueError("amount must be positive")
    return await get_wallet(user_id.strip(), create_if_missing=True)  # type: ignore[return-value]

  uid = user_id.strip()
  async with _user_lock(uid):
    now = datetime.utcnow()
    sb = None
    if _use_db():
      try:
        sb = _supabase()
      except Exception as e:
        if strict:
          raise WalletUnavailableError("Supabase 클라이언트를 만들지 못했습니다.") from e
        logger.exception("wallet_service.refund_credits: 클라이언트 초기화 실패 (user_id=%s)", uid)

    if sb:
      try:
        r = sb.rpc("add_wallet_credits", {"p_user_id": uid, "p_amount": amount}).execute()
        bal = r.data
        if isinstance(bal, list) and bal:
          bal = bal[0]
        if isinstance(bal, dict):
          bal = bal.get("current_credits", bal.get("credits_remaining"))
        if isinstance(bal, (int, float)):
          w = UserWallet(user_id=uid, current_credits=int(bal), updated_at=now)
          _MOCK_WALLETS[uid] = w
          return w
        if strict:
          raise WalletUnavailableError("환불 RPC 가 잔액을 돌려주지 않았습니다.")
      except WalletUnavailableError:
        raise
      except Exception as e:
        if strict:
          raise WalletUnavailableError(f"환불 RPC 실패: {type(e).__name__}") from e
        logger.exception(
          "wallet_service.refund_credits: Supabase 갱신 실패 — 인메모리 목업으로 폴백 (user_id=%s)",
          uid,
        )
    elif strict:
      raise WalletUnavailableError("Supabase 가 설정되지 않았습니다.")

    wallet = await get_wallet(uid, create_if_missing=True)
    assert wallet is not None
    wallet.current_credits = wallet.current_credits + amount
    wallet.updated_at = now
    _MOCK_WALLETS[uid] = wallet
    return wallet
