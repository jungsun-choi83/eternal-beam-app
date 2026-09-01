"""
UserWallet — 구독 크레딧(코인) 지갑.

- Supabase `user_wallets` 테이블 (권장)
- 차감·충전은 Supabase 의 **원자적 RPC** 로만 한다
- 사용자 단위 asyncio.Lock 으로 같은 프로세스 안의 레이스를 좁힌다

── 인메모리 지갑은 언제 쓰이는가 (Phase 1 에서 좁혔다) ────────────────────────

**HYBRID_USE_SUPABASE=0 일 때만.** 그것은 운영자가 "이 환경에는 실구매 크레딧이
없다"고 명시적으로 선언한 것이고(로컬 개발·유닛 테스트), 그때 인메모리는 폴백이
아니라 정답이다.

DB 를 쓰기로 해 놓고 실패한 경우에는 **인메모리로 흘리지 않는다.** 예전에는
흘렸고, 그것이 이번 감사에서 나온 가장 나쁜 결함이다:

    환불이 Supabase 장애 중에 인메모리로만 적용된다
      → 응답은 성공 (잔액이 늘어난 것처럼 보인다)
      → 구매 원장에는 이미 "환불됨" 도장이 찍혀 있다
      → Render 가 인스턴스를 재활용한다 (무료·starter 플랜에서는 일상이다)
      → 메모리가 사라진다. 크레딧은 **영구히 증발한다.**
      → 원장은 환불됐다고 말하므로 아무도 이 사실을 발견하지 못한다

그래서 규칙은 하나다: **DB 장애 ≠ 성공한 크레딧 연산.** 돈이 움직이는 연산은
DB 가 확인해 준 경우에만 성공을 반환하고, 그러지 못하면 WalletUnavailableError 로
닫는다. 요청이 실패하는 것은 복구할 수 있지만, 사라진 크레딧은 복구할 수 없다.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..data.dummy_business_seed import DUMMY_WALLETS
from ..models.hybrid_business import UserWallet
from . import credit_ledger as ledger

logger = logging.getLogger(__name__)

_INSUFFICIENT_MSG = "크레딧이 부족합니다. 구독 플랜을 업그레이드하세요."


class InsufficientCreditsError(Exception):
  def __init__(self, message: str = _INSUFFICIENT_MSG):
    super().__init__(message)
    self.message = message


class WalletUnavailableError(Exception):
  """
  지갑을 **DB 로 확정하지 못했다.**

  성공으로 처리하면 안 되는 모든 경우에 던진다. 예전에는 여기서 인메모리 목업으로
  폴백했는데, 그건 목업 파이프라인에서는 편의였지만 실제 크레딧에서는 사고다:
  장애 중의 변경이 프로세스 메모리에만 남고, 인스턴스가 재활용되면 통째로 사라진다.

  ⚠️ 호출부는 이 예외를 **삼키면 안 된다.** 삼키는 순간 이 클래스가 존재하는
  이유가 없어진다. 사용자에게는 "잠시 후 다시 시도" 계열(503)로 보여 주고,
  선점한 원장 행이 있다면 그대로 **남겨 둔다** — 지우면 이미 차감된 고객이
  다시 사게 된다.
  """

  def __init__(self, message: str = "지갑 서비스를 사용할 수 없습니다."):
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


def _memory_mode() -> bool:
  """
  운영자가 **명시적으로** DB 를 끈 모드 (HYBRID_USE_SUPABASE=0).

  인메모리 지갑이 정답인 유일한 경우다. 이 환경에는 실구매 크레딧이 존재하지
  않는다고 운영자가 선언한 것이므로, 잃을 돈도 없다.

  이 함수와 "DB 를 쓰기로 했는데 실패했다"를 구분하는 것이 Phase 1 의 핵심이다.
  예전 코드는 둘을 같은 `except` 안에서 똑같이 처리했다.
  """
  return not _use_db()


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


async def get_wallet(
  user_id: str, *, create_if_missing: bool = False, strict: bool = False
) -> Optional[UserWallet]:
  """
  지갑 조회.

  strict=False (기본): 조회 실패 시 인메모리로 폴백한다. **읽기 전용 표시 경로**
    (설정 화면의 잔액 등)를 위한 동작이며, 예전과 같다. 표시가 잠깐 틀리는 것은
    돈을 잃는 것이 아니다.

  strict=True: 돈이 움직이는 연산 안에서 부르는 경우. 폴백하지 않고
    WalletUnavailableError 로 닫는다.

    왜 필요한가: 예전에는 strict 차감 직전의 "행 보장" 호출이 조용히 폴백해
    인메모리 지갑을 만들어 냈다. 그러면 이어지는 RPC 는 DB 에 행이 없어
    insufficient_credits 로 떨어지고, 사용자는 **잔액이 충분한데도**
    "크레딧이 부족합니다"를 본다. 원인은 잔액이 아니라 DB 장애였다.
  """
  uid = user_id.strip()
  sb = None
  if _use_db():
    try:
      sb = _supabase()
    except Exception as e:
      if strict:
        raise WalletUnavailableError("Supabase 클라이언트를 만들지 못했습니다.") from e
      logger.exception(
        "wallet_service.get_wallet: Supabase client init failed — 인메모리 목업으로 폴백 (user_id=%s)",
        uid,
      )
    if strict and not sb:
      raise WalletUnavailableError("Supabase 가 설정되지 않았습니다.")
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
        # ── 지갑 생성은 **원장을 남긴다** (Phase 2) ─────────────────────────
        # 예전에는 여기서 STARTER_CREDITS 로 직접 insert 했다. 그러면 잔액 4 짜리
        # 지갑의 원장 합계가 0 이 되어, 첫 사용자부터 불변식이 깨진다.
        #
        # wallet_ensure 가 행 보장과 가입 보너스 지급(+원장 기록)을 한 트랜잭션으로
        # 처리하고, 보너스는 사용자당 한 번뿐이다(키가 'starter:<uid>').
        starter = max(0, int(os.getenv("STARTER_CREDITS", "0")))
        r2 = sb.rpc("wallet_ensure", {"p_user_id": uid, "p_starter": starter}).execute()
        bal = _balance_from_rpc(r2.data)
        if bal is None:
          # 잔액을 못 받았으면 지갑 상태를 모른다. 표시용 조회(strict=False)에서는
          # 예전처럼 넘어가되, 돈이 걸린 경로(strict=True)에서는 닫는다.
          if strict:
            raise WalletUnavailableError("지갑 생성 RPC 가 잔액을 돌려주지 않았습니다.")
          bal = starter
        return UserWallet(user_id=uid, current_credits=bal, updated_at=datetime.utcnow())
      return None
    except Exception as e:
      if strict:
        raise WalletUnavailableError(f"지갑 조회 실패: {type(e).__name__}") from e
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
    w = UserWallet(user_id=uid, current_credits=0, updated_at=datetime.utcnow())
    _MOCK_WALLETS[uid] = w
    # DB 경로와 같다: 보너스는 원장을 통해 들어오고, 사용자당 한 번뿐이다.
    if starter > 0:
      entry = ledger.LedgerEntry(
        user_id=uid,
        delta=starter,
        balance_after=starter,
        reason=ledger.REASON_STARTER_BONUS,
        idempotency_key=ledger.starter_key(uid),
        ref_type="user_wallets",
        ref_id=uid,
      )
      _, replayed = ledger.record_mock(entry)
      if not replayed:
        w.current_credits = starter
    return w
  return None


@dataclass(frozen=True)
class _LedgerFields:
  """
  한 번의 지갑 움직임에 붙는 회계 정보.

  묶어서 다니는 이유: 인자가 여섯 개가 되면 호출부에서 순서가 뒤바뀌고, 그중 하나가
  reason 과 idempotency_key 처럼 **둘 다 문자열**이면 타입 검사도 잡지 못한다.
  """

  reason: str
  idempotency_key: str
  product_key: Optional[str] = None
  unit_price: Optional[int] = None
  ref_type: Optional[str] = None
  ref_id: Optional[str] = None

  def rpc_params(self) -> dict[str, object]:
    """RPC 인자 이름은 마이그레이션의 파라미터 이름과 1:1 이다."""
    return {
      "p_reason": self.reason,
      "p_idempotency_key": self.idempotency_key,
      "p_product_key": self.product_key,
      "p_unit_price": self.unit_price,
      "p_ref_type": self.ref_type,
      "p_ref_id": self.ref_id,
    }


def _fields(
  *,
  reason: str,
  idempotency_key: Optional[str],
  product_key: Optional[str],
  unit_price: Optional[int],
  ref_type: Optional[str],
  ref_id: Optional[str],
  auto_prefix: str,
) -> _LedgerFields:
  """
  회계 정보를 조립하고 **부호 규칙을 미리 검사**한다.

  DB CHECK 가 최종 방어선이지만, 여기서 걸리면 스택 트레이스가 호출부를 가리킨다.
  DB 에서만 걸리면 오류 메시지가 제약 이름 하나뿐이다.
  """
  if reason not in ledger.ALL_REASONS:
    raise ValueError(f"알 수 없는 원장 사유: {reason!r}")
  return _LedgerFields(
    reason=reason,
    idempotency_key=(idempotency_key or ledger.auto_key(auto_prefix)),
    product_key=product_key,
    unit_price=unit_price,
    ref_type=ref_type,
    ref_id=ref_id,
  )


async def _apply_in_memory_locked(uid: str, delta: int, entry: _LedgerFields) -> UserWallet:
  """
  인메모리 지갑 변경 + 원장 기록. **호출자가 이미 _user_lock 을 쥐고 있다고
  가정한다** — 여기서 다시 잠그면 asyncio.Lock 은 재진입이 안 되므로 교착한다.

  충전·환불·차감이 같은 함수를 쓴다. 셋의 차이는 부호와 사유뿐이고, 구현을 세 벌
  두면 그중 하나만 원장을 빠뜨리는 날이 온다 — 그게 정확히 이번 Phase 가 없애려는
  종류의 결함이다.

  DB 경로와 **같은 순서**로 판정한다: 재플레이 → 잔액 검사 → 적용 → 기록.
  """
  # ① 재플레이. 키가 이미 있으면 아무것도 적용하지 않는다 (DB 의 unique 와 같은 판정).
  prior = ledger._MOCK_BY_KEY.get(entry.idempotency_key)
  if prior is not None:
    wallet = await get_wallet(uid, create_if_missing=True)
    assert wallet is not None
    return wallet

  wallet = await get_wallet(uid, create_if_missing=True)
  assert wallet is not None

  # ② 잔액 검사 — 차감이 잔액을 넘지 못한다.
  if delta < 0 and wallet.current_credits < -delta:
    raise InsufficientCreditsError()

  # ③ 적용 + ④ 기록. 단일 프로세스라 이 둘 사이에 끼어들 것이 없다.
  wallet.current_credits += delta
  wallet.updated_at = datetime.utcnow()
  _MOCK_WALLETS[uid] = wallet
  ledger.record_mock(
    ledger.LedgerEntry(
      user_id=uid,
      delta=delta,
      balance_after=wallet.current_credits,
      reason=entry.reason,
      idempotency_key=entry.idempotency_key,
      product_key=entry.product_key,
      unit_price=entry.unit_price,
      ref_type=entry.ref_type,
      ref_id=entry.ref_id,
    )
  )
  return wallet


def _rpc_says_insufficient(err: Exception) -> bool:
  """deduct_wallet_credits RPC 의 `raise exception 'insufficient_credits'` 판별."""
  return "insufficient_credits" in f"{err}".lower()


def _balance_from_rpc(data: object) -> Optional[int]:
  """
  RPC 응답에서 잔액을 뽑는다. 못 뽑으면 None → 호출부가 fail closed 한다.

  모양이 여러 가지인 이유: supabase-py 버전에 따라 스칼라 반환이 값 그대로 오기도,
  1행 리스트로 오기도, 컬럼명이 붙은 dict 로 오기도 한다. **모르는 모양을 0 이나
  기존 잔액으로 추측하지 않는 것**이 요점이다 — 추측한 값을 응답에 실으면 화면에
  틀린 잔액이 뜨고, 사용자는 그것을 근거로 다음 행동을 결정한다.
  """
  bal = data
  if isinstance(bal, list) and bal:
    bal = bal[0]
  if isinstance(bal, dict):
    bal = bal.get("current_credits", bal.get("credits_remaining"))
  if isinstance(bal, bool):  # bool 은 int 의 하위형이다 — 잔액일 리 없다
    return None
  return int(bal) if isinstance(bal, (int, float)) else None


async def deduct_credits(
  user_id: str,
  amount: int,
  *,
  strict: bool = False,
  reason: str = ledger.REASON_ADMIN_ADJUSTMENT,
  idempotency_key: Optional[str] = None,
  product_key: Optional[str] = None,
  unit_price: Optional[int] = None,
  ref_type: Optional[str] = None,
  ref_id: Optional[str] = None,
) -> UserWallet:
  """
  크레딧 차감.

  **어느 모드든 DB 장애에서는 실패한다** (Phase 1) 그리고 **모든 차감은 원장에
  기록된다** (Phase 2).

  ── strict 인자는 이제 아무것도 가르지 않는다 ─────────────────────────────
  예전에는 두 경로가 있었다: 원자적 RPC(strict=True)와 읽기-후-낙관적잠금
  UPDATE(strict=False, 레거시 4코인). 후자는 지갑 표에 **직접** 썼기 때문에 원장을
  남길 수 없었다 — 즉 "모든 움직임이 기록된다"를 구조적으로 깨는 경로였다.

  RPC 는 레거시 경로가 하던 일을 전부 더 안전하게 한다(조건부 UPDATE 한 문장,
  초과 인출 불가, 원장 동시 기록). 그래서 둘을 합쳤다. 함께 사라진 것들:

    * 낙관적 잠금이 빗나갔을 때의 재귀 — 예전에는 **락 안에서** 재귀해 교착했다
    * 그 재귀의 재시도 상한 관리
    * 지갑 표 직접 쓰기

  strict 인자는 호출부 호환을 위해 남기지만 동작에 영향을 주지 않는다.

  인메모리 차감은 **HYBRID_USE_SUPABASE=0** 일 때만 일어난다(_memory_mode).

  Raises:
    InsufficientCreditsError: 잔액 부족
    WalletUnavailableError:   지갑을 DB 로 확정하지 못함
    ValueError:               금액이 0 이하이거나, 차감에 쓸 수 없는 사유
  """
  if amount <= 0:
    raise ValueError("amount must be positive")

  fields = _fields(
    reason=reason,
    idempotency_key=idempotency_key,
    product_key=product_key,
    unit_price=unit_price,
    ref_type=ref_type,
    ref_id=ref_id,
    auto_prefix="deduct",
  )
  if not ledger.direction_ok(fields.reason, -amount):
    raise ValueError(f"차감에 쓸 수 없는 사유: {fields.reason!r}")

  uid = user_id.strip()
  async with _user_lock(uid):
    if _memory_mode():
      logger.warning(
        "wallet_service: HYBRID_USE_SUPABASE=0 — 인메모리 지갑으로 차감합니다 "
        "(실구매 크레딧이 아닙니다). user_id=%s",
        uid,
      )
      return await _apply_in_memory_locked(uid, -amount, fields)

    # ── 여기부터는 DB 가 정본이다. 폴백 없음. ──────────────────────────────
    try:
      sb = _supabase()
    except Exception as e:
      raise WalletUnavailableError("Supabase 클라이언트를 만들지 못했습니다.") from e
    if not sb:
      raise WalletUnavailableError("Supabase 가 설정되지 않았습니다.")

    # 지갑 행이 없으면 RPC 의 조건부 update 가 0행이라 insufficient 로 떨어진다.
    # 신규 사용자를 위해 행만 먼저 보장한다(가입 보너스가 있으면 그것도 함께).
    #
    # strict=True 로 보장하는 이유: 이 조회가 조용히 인메모리로 폴백하면 DB 에는
    # 행이 없는 채로 RPC 가 돌아 "크레딧 부족"이 되고, 잔액이 충분한 사용자가
    # 원인 불명의 402 를 받는다.
    await get_wallet(uid, create_if_missing=True, strict=True)

    try:
      r = sb.rpc(
        "deduct_wallet_credits",
        {"p_user_id": uid, "p_amount": amount, **fields.rpc_params()},
      ).execute()
    except Exception as e:
      if _rpc_says_insufficient(e):
        raise InsufficientCreditsError() from e
      raise WalletUnavailableError(f"차감 RPC 실패: {type(e).__name__}") from e

    bal = _balance_from_rpc(r.data)
    if bal is None:
      raise WalletUnavailableError("차감 RPC 가 잔액을 돌려주지 않았습니다.")

    w = UserWallet(user_id=uid, current_credits=bal, updated_at=datetime.utcnow())
    _MOCK_WALLETS[uid] = w
    return w


async def add_credits(
  user_id: str,
  amount: int,
  *,
  reason: str = ledger.REASON_ADMIN_ADJUSTMENT,
  idempotency_key: Optional[str] = None,
  product_key: Optional[str] = None,
  unit_price: Optional[int] = None,
  ref_type: Optional[str] = None,
  ref_id: Optional[str] = None,
) -> UserWallet:
  """
  IAP·구독 등 — 크레딧 충전. **DB 가 확인해 준 경우에만 성공한다.**

  ── 무엇이 바뀌었나 (Phase 1) ──────────────────────────────────────────────
  예전에는 RPC 가 실패하면 로그만 남기고 인메모리 잔액을 올린 뒤 **성공을
  반환**했다. 그 응답은 IAP 영수증 처리·구독 갱신의 성공 신호로 쓰인다:

      Supabase 장애 중 충전 요청
        → RPC 실패, 인메모리로 +N
        → 200 OK, credits_remaining 이 늘어난 채로 응답
        → payment_history 에는 성공 행이 남는다 (또는 남지 않는다)
        → 인스턴스 재활용 → 인메모리 소멸
        → 고객은 돈을 냈고, 크레딧은 없고, 영수증은 이미 소비됐다

  이제는 닫는다. 실패한 충전은 재시도할 수 있지만, "성공했다고 기록된 충전"은
  되찾을 방법이 없다.

  ⚠️ 멱등성은 **idempotency_key 를 넘기는 호출부의 책임**이다 (Phase 2). 키가 없으면
  자동 생성되어 재플레이를 막지 못한다 — 예전과 같은 수준이다. 다만 이제는 기록이
  남으므로 이중 충전이 원장에 두 줄로 드러난다. 예전에는 잔액만 늘고 흔적이 없었다.
  기존 방어(payment_history.receipt_fingerprint, subscription_webhook_events.
  event_fingerprint)는 그대로이고, 이제 그 값들이 그대로 멱등 키가 된다.
  """
  if amount <= 0:
    raise ValueError("amount must be positive")

  fields = _fields(
    reason=reason,
    idempotency_key=idempotency_key,
    product_key=product_key,
    unit_price=unit_price,
    ref_type=ref_type,
    ref_id=ref_id,
    auto_prefix="topup",
  )
  if not ledger.direction_ok(fields.reason, amount):
    raise ValueError(f"충전에 쓸 수 없는 사유: {fields.reason!r}")

  uid = user_id.strip()
  async with _user_lock(uid):
    if _memory_mode():
      logger.warning(
        "wallet_service: HYBRID_USE_SUPABASE=0 — 인메모리 지갑에 충전합니다 "
        "(실구매 크레딧이 아닙니다). user_id=%s",
        uid,
      )
      return await _apply_in_memory_locked(uid, amount, fields)

    try:
      sb = _supabase()
    except Exception as e:
      raise WalletUnavailableError("Supabase 클라이언트를 만들지 못했습니다.") from e
    if not sb:
      raise WalletUnavailableError("Supabase 가 설정되지 않았습니다.")

    try:
      r = sb.rpc(
        "add_wallet_credits",
        {"p_user_id": uid, "p_amount": amount, **fields.rpc_params()},
      ).execute()
    except Exception as e:
      raise WalletUnavailableError(f"충전 RPC 실패: {type(e).__name__}") from e

    # RPC 가 잔액을 돌려주지 않았다면 충전됐는지 알 수 없다.
    # 예전에는 이때 직접 UPDATE 로 덮어썼는데, 그 UPDATE 는 **읽은 값 기준**이라
    # 동시 차감을 통째로 지운다(lost update). 모르면 모른다고 하는 편이 낫다 —
    # 호출부가 재시도하면 멱등 키가 이중 충전을 막아 준다.
    bal = _balance_from_rpc(r.data)
    if bal is None:
      raise WalletUnavailableError("충전 RPC 가 잔액을 돌려주지 않았습니다.")

    w = UserWallet(user_id=uid, current_credits=bal, updated_at=datetime.utcnow())
    _MOCK_WALLETS[uid] = w
    return w


async def refund_credits(
  user_id: str,
  amount: int,
  *,
  reason: str = ledger.REASON_REFUND,
  idempotency_key: Optional[str] = None,
  product_key: Optional[str] = None,
  unit_price: Optional[int] = None,
  ref_type: Optional[str] = None,
  ref_id: Optional[str] = None,
) -> UserWallet:
  """
  생성 실패 등 — 차감 롤백. **DB 가 확인해 준 경우에만 성공한다.**

  원자적 증분 RPC(add_wallet_credits)를 쓴다. 예전 구현은 읽고 → 계산하고 →
  무조건 덮어썼고(CAS 없음), 환불과 차감이 겹치면 한쪽 쓰기가 통째로 사라졌다.

  ── 왜 strict 인자를 없앴나 (Phase 1) ──────────────────────────────────────
  예전에는 `strict` 기본값이 False 였고, **호출부 다섯 곳이 전부 비-strict** 였다.
  즉 실전에서 이 함수는 언제나 "실패해도 성공을 반환하는" 모드로 돌았다. 환불에서
  그것은 감사에서 나온 최악의 시나리오 그 자체다:

      원장에 '환불됨' 도장 → 지갑 증분은 인메모리에만 → 인스턴스 재활용
        → 크레딧 영구 소멸. 원장이 환불됐다고 말하므로 아무도 모른다.

  "느슨한 환불"에는 정당한 용례가 없다. 인자를 남겨 두면 다음 호출부가 또 False 를
  넘기게 되므로 선택지 자체를 없앤다. 인메모리 모드(HYBRID_USE_SUPABASE=0)는
  여전히 인메모리로 동작한다 — 그건 폴백이 아니라 그 환경의 정답이다.

  ⚠️ 호출부 계약: 이 함수가 던지면 **환불 표시(refunded_at 등)를 되돌려야 한다.**
  표시만 남고 크레딧이 돌아가지 않은 상태가 바로 고객이 크레딧을 잃는 경로다.

  Raises:
    WalletUnavailableError: 환불을 DB 로 확정하지 못함
  """
  if amount <= 0:
    # 되돌릴 것이 없다. 조회는 표시용이므로 비-strict 로 충분하다.
    return await get_wallet(user_id.strip(), create_if_missing=True)  # type: ignore[return-value]

  fields = _fields(
    reason=reason,
    idempotency_key=idempotency_key,
    product_key=product_key,
    unit_price=unit_price,
    ref_type=ref_type,
    ref_id=ref_id,
    auto_prefix="refund",
  )
  if not ledger.direction_ok(fields.reason, amount):
    raise ValueError(f"환불에 쓸 수 없는 사유: {fields.reason!r}")

  uid = user_id.strip()
  async with _user_lock(uid):
    if _memory_mode():
      logger.warning(
        "wallet_service: HYBRID_USE_SUPABASE=0 — 인메모리 지갑에 환불합니다 "
        "(실구매 크레딧이 아닙니다). user_id=%s",
        uid,
      )
      return await _apply_in_memory_locked(uid, amount, fields)

    try:
      sb = _supabase()
    except Exception as e:
      raise WalletUnavailableError("Supabase 클라이언트를 만들지 못했습니다.") from e
    if not sb:
      raise WalletUnavailableError("Supabase 가 설정되지 않았습니다.")

    try:
      r = sb.rpc(
        "add_wallet_credits",
        {"p_user_id": uid, "p_amount": amount, **fields.rpc_params()},
      ).execute()
    except Exception as e:
      raise WalletUnavailableError(f"환불 RPC 실패: {type(e).__name__}") from e

    bal = _balance_from_rpc(r.data)
    if bal is None:
      raise WalletUnavailableError("환불 RPC 가 잔액을 돌려주지 않았습니다.")

    w = UserWallet(user_id=uid, current_credits=bal, updated_at=datetime.utcnow())
    _MOCK_WALLETS[uid] = w
    return w
