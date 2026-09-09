"""
프리미엄 행동 ON/OFF 선호 — **저장만 한다**.

Phase 5 의 범위는 여기까지다: 사용자가 켜고 끈 것을 서버에 남기고 되돌려 준다.
**재생에는 아직 연결하지 않는다** — 스케줄러는 예전 그대로 canonical 자산만 보고
동작한다. 그래서 이 모듈에는 스케줄러도, 재생도, 생성도 없다.

세 가지가 서로 독립이라는 것이 이 모듈의 핵심이다:

    generated_motions      "만들어졌는가"   (READY)
    user_subscriptions     "멤버인가"       (entitled)
    behavior_preferences   "켜 두고 싶은가" (여기)

그래서:
  * 구독이 만료돼도 선호는 **지워지지 않는다**. 이 모듈에 삭제 경로가 없다.
  * 갱신하면 예전 설정이 그대로 돌아온다 — 아무것도 복원하지 않아도 된다.
    애초에 사라진 적이 없기 때문이다.
  * 아직 만들지 않은(MISSING) 행동에도 선호를 저장할 수 있다. READY 와 선호는
    별개 상태다. UI 가 READY 에만 토글을 보여 주는 것은 표시 규칙이다.

토글은 **절대 생성을 일으키지 않는다.** 이 파일은 premium_generation 도
generation_queue 도 import 하지 않는다 — 그럴 수 있는 경로 자체를 두지 않는다.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Optional

from ..scenarios.pet_scenarios import PREMIUM_ACTIONS

logger = logging.getLogger(__name__)

#: 저장된 행이 없을 때의 값. 만든 행동은 켜져 있는 것이 기대값이다.
DEFAULT_ENABLED = True


class PreferenceError(Exception):
    """선호를 저장/조회할 수 없다. code 로 HTTP 변환을 구분한다."""

    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _table() -> str:
    return os.getenv("BEHAVIOR_PREFERENCES_TABLE", "behavior_preferences")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


#: DB 가 없을 때의 인메모리 저장 (로컬/테스트).
_MOCK_PREFS: dict[str, bool] = {}


def __reset_for_tests() -> None:
    _MOCK_PREFS.clear()


def _key(user_id: str, pet_id: str, action_id: str) -> str:
    return f"{user_id}|{pet_id}|{action_id}"


def resolve_action(raw: str | None) -> str:
    """
    행동 id 검증 → canonical 대문자 id.

    PREMIUM_ACTIONS 안에 있어야 한다. 레거시 4종(IDLE/TOUCH/VOICE/NFC)과 무료
    BREATHING 은 여기 없으므로 자동으로 거절된다 — 별도 목록을 두지 않는다.
    """
    a = (raw or "").strip().upper()
    if not a:
        raise PreferenceError("ACTION_REQUIRED", "행동 id가 필요합니다.")
    if a not in PREMIUM_ACTIONS:
        raise PreferenceError(
            "ACTION_NOT_SUPPORTED",
            f"{a} 는 선호를 설정할 수 있는 프리미엄 행동이 아닙니다.",
        )
    return a


async def get_preferences(user_id: str, pet_id: str) -> dict[str, bool]:
    """
    이 펫의 행동별 선호. **등록된 프리미엄 행동 전체**를 돌려준다.

    저장된 행이 없는 행동은 DEFAULT_ENABLED 로 채운다 — 프론트가 "없음"과 "꺼짐"을
    구분하느라 기본값 규칙을 다시 구현하지 않게 하기 위해서다. 기본값 판정은
    서버 한 곳에만 있다.
    """
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    prefs: dict[str, bool] = {a: DEFAULT_ENABLED for a in PREMIUM_ACTIONS}
    if not uid or not pid:
        return prefs

    if _use_db() and _supabase():
        try:
            r = (
                _supabase()
                .table(_table())
                .select("action_id, enabled")
                .eq("user_id", uid)
                .eq("pet_id", pid)
                .execute()
            )
            for row in getattr(r, "data", None) or []:
                action = str(row.get("action_id") or "").upper()
                if action in prefs:
                    prefs[action] = bool(row.get("enabled"))
            return prefs
        except Exception as e:
            # 조회 실패는 **기본값으로 열지 않는다**. 사용자가 꺼 둔 것이 켜진 것으로
            # 보이면 나중에 스케줄러가 붙었을 때 끈 행동이 재생된다.
            logger.exception("행동 선호 조회 실패 (user=%s pet=%s)", uid, pid)
            raise PreferenceError(
                "PREFERENCES_UNAVAILABLE",
                "행동 설정을 불러오지 못했습니다.",
                status=503,
            ) from e

    for action in PREMIUM_ACTIONS:
        k = _key(uid, pid, action)
        if k in _MOCK_PREFS:
            prefs[action] = _MOCK_PREFS[k]
    return prefs


async def set_preference(
    *, user_id: str, pet_id: str, action_id: str, enabled: bool
) -> dict[str, bool]:
    """
    선호 하나를 저장하고 **갱신된 전체 선호**를 돌려준다.

    전체를 돌려주는 이유: 프론트가 응답 하나로 화면을 그대로 다시 그릴 수 있고,
    낙관적 업데이트가 어긋났을 때 서버 값으로 수렴한다.

    생성을 일으키지 않는다. 자산 상태(READY/GENERATING/MISSING)를 읽지도 않는다 —
    선호와 자산은 별개 상태이므로 여기서 교차 검사할 것이 없다.
    """
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    if not uid or not pid:
        raise PreferenceError("PET_REQUIRED", "user_id 와 pet_id 가 필요합니다.")
    action = resolve_action(action_id)
    value = bool(enabled)

    if _use_db() and _supabase():
        row: dict[str, Any] = {
            "user_id": uid,
            "pet_id": pid,
            "action_id": action,
            "enabled": value,
            "updated_at": datetime.utcnow().isoformat(),
        }
        try:
            # 복합 PK 라 upsert 한 번이면 삽입/갱신이 모두 처리된다.
            _supabase().table(_table()).upsert(
                row, on_conflict="user_id,pet_id,action_id"
            ).execute()
        except Exception as e:
            logger.exception(
                "행동 선호 저장 실패 (user=%s pet=%s action=%s)", uid, pid, action
            )
            raise PreferenceError(
                "PREFERENCES_UNAVAILABLE",
                "행동 설정을 저장하지 못했습니다.",
                status=503,
            ) from e
        return await get_preferences(uid, pid)

    _MOCK_PREFS[_key(uid, pid, action)] = value
    return await get_preferences(uid, pid)


async def get_preference(user_id: str, pet_id: str, action_id: str) -> Optional[bool]:
    """단건 조회 — 저장된 값이 없으면 None (기본값을 씌우지 않는다)."""
    action = resolve_action(action_id)
    prefs = await get_preferences(user_id, pet_id)
    return prefs.get(action)
