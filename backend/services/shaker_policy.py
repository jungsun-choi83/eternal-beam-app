"""
Shaker 더블탭 **상업 정책** — PM 미결 사항을 격리하는 단 하나의 자리.

핸드오프의 열린 결정(Phase 10.4):

    A. 언제나 무료인 Shaker 전용 액션
    B. 자산이 이미 있으면 COME_CLOSER
    C. 멤버십 종속

**PM 이 C(멤버십 종속)를 선택했다.** 기본값이 `membership` 이다.

── 확정된 규칙 ────────────────────────────────────────────────────────────────
Phase 6 의 런타임 적격성(behavior-library.ts `isBehaviorEligible`)과 **똑같다**:

    구독 entitled  ∩  자산 READY  ∩  선호 ON

Shaker 라고 다른 규칙을 쓰지 않는 것이 핵심이다. 규칙이 갈리면 "메인 앱에서는
꺼 둔 행동이 QR 로는 재생된다" 같은 구멍이 생기고, 그건 사용자가 자기 설정을
신뢰할 수 없게 만든다. 소유자가 끈 행동은 어디서도 재생되지 않는다.

세 조건 중 하나라도 **판정할 수 없으면 거절한다**(fail closed). 구독 조회 장애나
선호 조회 장애가 곧 무료 배포가 되면 안 된다.

나머지 세 정책(disabled / free / ready-only)은 되돌릴 수 있도록 남겨 둔다 —
환경변수 한 줄로 바뀌고, 재생 코드는 어느 쪽이든 그대로다.

BREATHING 은 이 파일의 대상이 **아니다.** 언제나 무료이며 정책과 무관하게 돈다.
자격이 없으면 액션이 목록에서 빠질 뿐, 화면은 BREATHING 을 계속 재생한다.
"""

from __future__ import annotations

import os
from typing import Iterable, Optional

#: 액션 후보 중 더블탭에 쓸 것을 고르는 우선순위.
#: COME_CLOSER 가 먼저인 이유는 그것만이 런타임에 **등록된**(재생 가능한) 액션이기
#: 때문이다 — lib/pet-runtime-events.ts 의 RUNTIME_EVENTS 참고. 나머지 자발적
#: 행동은 선언만 돼 있어 트리거해도 거절된다.
_PREFERRED_ORDER: tuple[str, ...] = ("COME_CLOSER",)

#: 고를 수 있는 값들. 문자열을 그대로 노출하는 이유는 운영자가 /readiness 나
#: 로그에서 "지금 어느 정책인가"를 한눈에 보게 하기 위해서다.
POLICY_DISABLED = "disabled"        # 아무 액션도 노출하지 않는다 (되돌리기용)
POLICY_FREE = "free"                # A — READY 인 액션이면 무조건 허용
POLICY_READY_ONLY = "ready-only"    # B — COME_CLOSER 가 READY 일 때만 허용
POLICY_MEMBERSHIP = "membership"    # C — 구독 ∩ READY ∩ 선호 ON  ← **현재 기본값**

_VALID_POLICIES = frozenset(
    {POLICY_DISABLED, POLICY_FREE, POLICY_READY_ONLY, POLICY_MEMBERSHIP}
)

#: PM 확정값. 환경변수가 없거나 잘못됐을 때 여기로 떨어진다.
DEFAULT_POLICY = POLICY_MEMBERSHIP

_ENV = "SHAKER_DOUBLE_TAP_POLICY"


def current_policy() -> str:
    """
    설정된 정책. 알 수 없는 값은 **기본값(membership)으로 떨어진다.**

    membership 은 세 조건을 모두 요구하므로, 오타로 여기 떨어져도 자격 없는
    방문자에게 무언가가 열리는 일은 없다 — 되돌아가는 자리가 안전한 자리다.
    """
    raw = (os.getenv(_ENV) or "").strip().lower()
    return raw if raw in _VALID_POLICIES else DEFAULT_POLICY


def requires_owner_entitlement(policy: str | None = None) -> bool:
    """
    이 정책이 소유자의 구독 상태를 필요로 하는가.

    라우터가 이것을 먼저 묻는 이유: 필요 없을 때는 구독 테이블을 **아예 조회하지
    않기** 위해서다. disabled / A / B 경로에서는 공개 엔드포인트가 구독 데이터를
    건드릴 일이 없고, 건드리지 않으면 새어 나갈 수도 없다.
    """
    return (policy or current_policy()) == POLICY_MEMBERSHIP


def requires_preferences(policy: str | None = None) -> bool:
    """
    이 정책이 소유자의 ON/OFF 선호를 필요로 하는가.

    entitlement 와 **따로** 묻는다. 둘은 다른 테이블이고 따로 실패할 수 있어서,
    한 번에 묶으면 어느 쪽이 없어서 거절됐는지 로그로 구분할 수 없다.
    """
    return (policy or current_policy()) == POLICY_MEMBERSHIP


def permitted_action_ids(
    ready_action_ids: Iterable[str],
    *,
    owner_entitled: Optional[bool] = None,
    preferences: Optional[dict[str, bool]] = None,
    policy: str | None = None,
) -> list[str]:
    """
    Shaker 가 **재생해도 되는** 액션 id 목록 (정렬됨).

    공개 응답의 `actions` 가 이 결과다. 정책이 허용하지 않는 액션은 목록에서
    빠지고, 따라서 **URL 도 나가지 않는다**. "노출은 하되 재생만 막는다"로
    만들면 URL 이 이미 손에 들어간 뒤라 막은 것이 아니게 된다.

    기본(membership)에서 세 조건이 모두 필요하다:

        owner_entitled=True  ∩  ready_action_ids 에 포함  ∩  preferences[id] != False

    owner_entitled 나 preferences 가 None 이면 **판정 불가로 보고 거절한다.**
    조회 장애가 곧 무료 배포가 되면 안 된다.
    """
    pol = policy or current_policy()
    ready = sorted({str(a).strip().upper() for a in ready_action_ids if str(a).strip()})
    if not ready or pol == POLICY_DISABLED:
        return []

    if pol == POLICY_MEMBERSHIP:
        if not owner_entitled:
            return []
        if preferences is None:
            # 선호를 읽지 못했다. 끈 행동을 켠 것으로 오해하느니 아무것도 열지 않는다.
            return []
        # 저장된 값이 없는 행동은 기본 켬 — behavior_preferences.DEFAULT_ENABLED 와
        # 같은 규칙이다. 기본값 판정을 두 곳에서 다르게 하면 화면과 재생이 어긋난다.
        return [a for a in ready if preferences.get(a, True)]

    if pol == POLICY_READY_ONLY:
        return ["COME_CLOSER"] if "COME_CLOSER" in ready else []
    return ready


def select_double_tap_action(
    ready_action_ids: Iterable[str],
    *,
    owner_entitled: Optional[bool] = None,
    preferences: Optional[dict[str, bool]] = None,
    policy: str | None = None,
) -> Optional[str]:
    """
    더블탭으로 재생할 액션 id — 없으면 None.

    입력은 **이미 READY 인 것만** 들어온다. 이 함수는 생성 가능 여부를 판단하지
    않으며, 판단할 수단도 없다(자산 상태를 만들어 내지 못한다). Shaker 가 절대
    생성하지 않는다는 보장은 호출부가 READY 목록만 넘기는 것으로 성립한다.

    허용 목록에서 고르므로 정책 판정이 **한 곳**(permitted_action_ids)에만 있다.
    예전에는 두 함수가 각자 판정해서, 한쪽만 고치면 "목록에는 없는데 더블탭은
    되는" 상태가 만들어질 수 있었다.
    """
    permitted = permitted_action_ids(
        ready_action_ids,
        owner_entitled=owner_entitled,
        preferences=preferences,
        policy=policy,
    )
    if not permitted:
        return None

    for candidate in _PREFERRED_ORDER:
        if candidate in permitted:
            return candidate
    return permitted[0]
