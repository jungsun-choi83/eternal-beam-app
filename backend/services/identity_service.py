"""
Supabase 계정 → Eternal Beam 안정 신원.

문제: 기존 데이터는 전부 텍스트 user_id 로 키가 잡혀 있고(지갑·생성 자산·작업·
세션·구매 원장), 그 값은 프론트가 localStorage 에 쓰던 소문자 이메일 또는 익명
`user_<base36>` 다. Supabase 의 sub 는 UUID 이므로, 신원을 sub 로 갈아치우면
기존 사용자의 크레딧과 이미 만든 영상이 전부 사라진 것처럼 보인다.

해결: **교체하지 않고 연결한다.**

    검증된 이메일이 있다  → eb_user_id = 소문자 이메일
                            (예전 auth-screen 이 쓰던 바로 그 값 → 기존 데이터가 그대로 붙는다)
    그 외                  → eb_user_id = sub
                            (물려받을 것이 없는 새 신원)

연결은 **한 번만** 정해지고 user_identity_links 에 남는다. 이후 로그인은 언제나
같은 eb_user_id 를 돌려준다.

⚠️ 익명 신원(`user_<base36>`)은 **자동 승계하지 않는다.** 소유를 증명할 방법이
없고, 그 id 는 타임스탬프 기반이라 추측도 가능하다. 자동으로 이어 주면 남의 지갑을
가져갈 수 있다. 검증된 이메일만 소유 증명으로 인정한다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _table() -> str:
    return os.getenv("IDENTITY_LINKS_TABLE", "user_identity_links")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


#: DB 가 없을 때의 인메모리 연결 (로컬/테스트).
_MOCK_LINKS: dict[str, dict[str, Any]] = {}


def __reset_for_tests() -> None:
    _MOCK_LINKS.clear()


class IdentityUnavailableError(Exception):
    """신원을 신뢰성 있게 확정하지 못했다 — 열지 말고 닫는다."""


@dataclass(frozen=True)
class ResolvedIdentity:
    user_id: str
    subject: str
    email: Optional[str]
    #: 'existing' | 'email' | 'new'
    linked_via: str


def normalize_email(email: str | None) -> Optional[str]:
    e = (email or "").strip().lower()
    return e or None


def _find_by_subject(subject: str) -> Optional[dict[str, Any]]:
    if _use_db() and _supabase():
        r = (
            _supabase()
            .table(_table())
            .select("*")
            .eq("auth_user_id", subject)
            .limit(1)
            .execute()
        )
        return (getattr(r, "data", None) or [None])[0]
    return _MOCK_LINKS.get(subject)


def _find_by_eb_user(eb_user_id: str) -> Optional[dict[str, Any]]:
    if _use_db() and _supabase():
        r = (
            _supabase()
            .table(_table())
            .select("*")
            .eq("eb_user_id", eb_user_id)
            .limit(1)
            .execute()
        )
        return (getattr(r, "data", None) or [None])[0]
    for row in _MOCK_LINKS.values():
        if row.get("eb_user_id") == eb_user_id:
            return row
    return None


def _insert_link(row: dict[str, Any]) -> bool:
    """연결을 만든다. unique 충돌이면 False (다른 요청/계정이 먼저 가져갔다)."""
    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).insert(row).execute()
        except Exception as e:
            msg = f"{e}".lower()
            if "duplicate" in msg or "unique" in msg or "23505" in msg:
                return False
            raise IdentityUnavailableError(f"신원 연결 저장 실패: {type(e).__name__}") from e
        return True

    if row["auth_user_id"] in _MOCK_LINKS:
        return False
    for existing in _MOCK_LINKS.values():
        if existing.get("eb_user_id") == row["eb_user_id"]:
            return False
    _MOCK_LINKS[row["auth_user_id"]] = row
    return True


async def resolve_identity(
    *,
    subject: str,
    email: str | None,
    email_verified: bool,
) -> ResolvedIdentity:
    """
    검증된 JWT 클레임 → 안정적인 Eternal Beam 신원.

    호출될 때마다 같은 값을 돌려준다. 첫 호출에서만 연결을 만든다.
    """
    sub = (subject or "").strip()
    if not sub:
        raise IdentityUnavailableError("토큰에 sub 가 없습니다.")

    mail = normalize_email(email)

    existing = _find_by_subject(sub)
    if existing:
        return ResolvedIdentity(
            user_id=str(existing["eb_user_id"]),
            subject=sub,
            email=mail,
            linked_via="existing",
        )

    # ── 최초 연결 ────────────────────────────────────────────────────────────
    # 검증된 이메일만 기존 신원의 소유 증명으로 인정한다. 검증되지 않은 이메일을
    # 받아 주면, 아무 주소나 적어 남의 지갑·자산을 가져갈 수 있다.
    if mail and email_verified and not _find_by_eb_user(mail):
        row = {
            "auth_user_id": sub,
            "eb_user_id": mail,
            "linked_via": "email",
            "email": mail,
        }
        if _insert_link(row):
            logger.warning("신원 연결 — sub=%s → eb_user_id=%s (검증된 이메일)", sub, mail)
            return ResolvedIdentity(user_id=mail, subject=sub, email=mail, linked_via="email")
        # 경합에서 졌다 — 다시 읽어 확정한다.
        again = _find_by_subject(sub)
        if again:
            return ResolvedIdentity(
                user_id=str(again["eb_user_id"]), subject=sub, email=mail, linked_via="existing"
            )

    # 물려받을 것이 없다 → sub 자체가 신원이다.
    row = {"auth_user_id": sub, "eb_user_id": sub, "linked_via": "new", "email": mail}
    if not _insert_link(row):
        again = _find_by_subject(sub)
        if again:
            return ResolvedIdentity(
                user_id=str(again["eb_user_id"]), subject=sub, email=mail, linked_via="existing"
            )
        raise IdentityUnavailableError("신원을 확정하지 못했습니다.")
    return ResolvedIdentity(user_id=sub, subject=sub, email=mail, linked_via="new")
