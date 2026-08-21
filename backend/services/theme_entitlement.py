"""
테마 소유권 저장소 — **"샀는가"만 답한다.**

구독과 완전히 독립이다. 이 모듈은 premium_entitlement 도 subscription_store_service
도 **import 하지 않는다** — behavior_preferences.py 와 같은 규칙이다. 그럴 수 있는
경로를 두지 않는 것이 "완전히 분리"의 실제 보장이다.

    user_subscriptions       "이번 달 회원인가"   → 만료된다
    user_theme_entitlements  "이 테마를 샀는가"   → 여기
    generated_motions        "만들어졌는가"

따라서:
  * 구독이 끊겨도 산 테마는 남는다. 이 모듈에 만료 경로가 없다(TTL 을 명시적으로
    설정한 경우만 expires_at 이 채워진다).
  * 테마를 사도 구독 상태는 한 글자도 바뀌지 않는다.
  * 무료 테마는 이 테이블에 **행이 생기지 않는다** — 살 것이 없기 때문이다.

생성하지 않는다. 프로바이더를 호출하지 않는다. 테마를 바꿔도 BREATHING 이나
프리미엄 행동은 다시 만들어지지 않는다 — 이 모듈에 그런 경로가 없다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

STATUS_OWNED = "owned"
STATUS_REVOKED = "revoked"
STATUS_REFUNDED = "refunded"


class ThemeEntitlementError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _table() -> str:
    return os.getenv("THEME_ENTITLEMENTS_TABLE", "user_theme_entitlements")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


#: DB 가 없을 때의 인메모리 저장 (로컬/테스트). key = "user|theme"
_MOCK_ENTITLEMENTS: dict[str, dict[str, Any]] = {}
#: order_id → key. 멱등성 검사를 DB 없이도 같은 의미로 재현한다.
_MOCK_ORDERS: dict[str, str] = {}


def __reset_for_tests() -> None:
    _MOCK_ENTITLEMENTS.clear()
    _MOCK_ORDERS.clear()


def _key(user_id: str, theme_key: str) -> str:
    return f"{user_id}|{theme_key}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        # 해석할 수 없는 만료 시각은 **만료로 본다**. 조용히 통과시키면 기간제
        # 소유권이 영원해진다 — fail closed.
        return _now()


@dataclass(frozen=True)
class ThemeEntitlement:
    user_id: str
    theme_key: str
    status: str
    order_id: Optional[str] = None
    provider: Optional[str] = None
    amount: Optional[int] = None
    currency: Optional[str] = None
    purchased_at: Optional[str] = None
    expires_at: Optional[str] = None

    @property
    def active(self) -> bool:
        """지금 이 테마를 쓸 수 있는가. 상태 + 만료를 함께 본다."""
        if self.status != STATUS_OWNED:
            return False
        exp = _parse_ts(self.expires_at)
        return not (exp and exp <= _now())


def _to_entitlement(row: dict[str, Any]) -> ThemeEntitlement:
    return ThemeEntitlement(
        user_id=str(row.get("user_id") or ""),
        theme_key=str(row.get("theme_key") or ""),
        status=str(row.get("status") or STATUS_OWNED),
        order_id=(row.get("order_id") or None),
        provider=(row.get("provider") or None),
        amount=(int(row["amount"]) if row.get("amount") is not None else None),
        currency=(row.get("currency") or None),
        purchased_at=(str(row["purchased_at"]) if row.get("purchased_at") else None),
        expires_at=(str(row["expires_at"]) if row.get("expires_at") else None),
    )


_SELECT = (
    "user_id, theme_key, status, order_id, provider, amount, currency, "
    "purchased_at, expires_at"
)


async def list_entitlements(user_id: str) -> list[ThemeEntitlement]:
    """이 사용자가 산 것 전부 (만료/환불 포함 — 화면이 구분해 보여 줄 수 있게)."""
    uid = (user_id or "").strip()
    if not uid:
        return []

    if _use_db() and _supabase():
        try:
            r = _supabase().table(_table()).select(_SELECT).eq("user_id", uid).execute()
            return [_to_entitlement(row) for row in (getattr(r, "data", None) or [])]
        except Exception as e:
            # 조회 실패를 "없음"으로 답하지 않는다 — 산 테마가 사라진 것처럼 보인다.
            logger.exception("테마 소유권 조회 실패 (user=%s)", uid)
            raise ThemeEntitlementError(
                "THEME_ENTITLEMENTS_UNAVAILABLE",
                "테마 소유 정보를 불러오지 못했습니다.",
                status=503,
            ) from e

    return [
        _to_entitlement(row)
        for row in _MOCK_ENTITLEMENTS.values()
        if row.get("user_id") == uid
    ]


async def owned_theme_keys(user_id: str) -> set[str]:
    """**지금 쓸 수 있는** 테마 key 집합. 만료·환불은 빠진다."""
    return {e.theme_key for e in await list_entitlements(user_id) if e.active}


async def is_owned(user_id: str, theme_key: str) -> bool:
    return theme_key in await owned_theme_keys(user_id)


async def find_by_order(order_id: str) -> Optional[ThemeEntitlement]:
    """
    이 주문으로 이미 소유권이 생겼는가 — **멱등성 검사의 읽기 쪽**.

    쓰기 쪽 보장은 DB 의 부분 unique 인덱스가 한다. 이 조회는 "두 번째 호출에
    친절한 답을 주기 위한" 것이지 경쟁 방지 수단이 아니다.
    """
    oid = (order_id or "").strip()
    if not oid:
        return None

    if _use_db() and _supabase():
        try:
            r = (
                _supabase().table(_table()).select(_SELECT)
                .eq("order_id", oid).limit(1).execute()
            )
            data = getattr(r, "data", None) or []
            return _to_entitlement(data[0]) if data else None
        except Exception as e:
            logger.exception("주문으로 테마 소유권 조회 실패 (order=%s)", oid)
            raise ThemeEntitlementError(
                "THEME_ENTITLEMENTS_UNAVAILABLE",
                "테마 소유 정보를 확인하지 못했습니다.",
                status=503,
            ) from e

    k = _MOCK_ORDERS.get(oid)
    row = _MOCK_ENTITLEMENTS.get(k) if k else None
    return _to_entitlement(row) if row else None


async def grant(
    *,
    user_id: str,
    theme_key: str,
    order_id: str,
    provider: str = "toss",
    payment_key: str | None = None,
    amount: int | None = None,
    currency: str = "KRW",
    ttl_days: int | None = None,
) -> ThemeEntitlement:
    """
    검증된 결제 → 소유권. **이 함수만 소유권을 만든다.**

    ttl_days 가 None 이면 영구다(expires_at=null). 기간제 여부는 PM 미결이고,
    호출부(theme_catalog.entitlement_ttl_days)가 설정에서 읽어 넘긴다.

    ⚠️ 결제 검증은 **호출부의 책임**이다. 이 모듈은 저장소이지 결제 게이트가
    아니다 — 그렇게 나눠야 인가 규칙이 한 곳에 모인다(shaker_share 와 같은 구조).
    """
    uid = (user_id or "").strip()
    tk = (theme_key or "").strip().lower()
    oid = (order_id or "").strip()
    if not uid or not tk:
        raise ThemeEntitlementError("THEME_GRANT_INVALID", "user_id 와 theme_key 가 필요합니다.")
    if not oid:
        raise ThemeEntitlementError("THEME_GRANT_INVALID", "order_id 가 필요합니다.")

    now = _now()
    expires = now + timedelta(days=ttl_days) if ttl_days else None
    row: dict[str, Any] = {
        "user_id": uid,
        "theme_key": tk,
        "status": STATUS_OWNED,
        "provider": provider,
        "order_id": oid,
        "payment_key": payment_key,
        "amount": amount,
        "currency": currency,
        "purchased_at": now.isoformat(),
        "expires_at": expires.isoformat() if expires else None,
    }

    if _use_db() and _supabase():
        try:
            # 복합 PK 라 upsert 하나로 신규/재구매(만료 후 다시 사기)가 모두 처리된다.
            _supabase().table(_table()).upsert(
                row, on_conflict="user_id,theme_key"
            ).execute()
        except Exception as e:
            logger.exception("테마 소유권 저장 실패 (user=%s theme=%s)", uid, tk)
            raise ThemeEntitlementError(
                "THEME_ENTITLEMENTS_UNAVAILABLE",
                "테마 소유권을 저장하지 못했습니다.",
                status=503,
            ) from e
        return _to_entitlement(row)

    _MOCK_ENTITLEMENTS[_key(uid, tk)] = row
    _MOCK_ORDERS[oid] = _key(uid, tk)
    return _to_entitlement(row)
