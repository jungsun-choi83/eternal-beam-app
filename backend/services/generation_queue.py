"""
펫당 생성 큐 — **동시 제출 수 제한**.

문제: 새 펫이 들어오면 프론트가 COME_CLOSER + 아이들 이벤트 4종을 각각 ensure 해서
프로바이더 제출이 한 번에 5건 나갔다. 기존 멱등성 검사는 전부 액션 **키 단위**라
(같은 액션 중복만 막는다) 액션 사이를 세는 곳이 없었다.

여기서 정하는 것은 두 가지뿐이다:
  1) 이 펫이 지금 몇 건까지 동시에 돌 수 있는가
  2) 지금 제출해도 되는 액션이 무엇인가 (우선순위)

**재생 우선순위와 다른 개념이다.** pet-runtime-events.ts 의 priority 는 "재생 중인
것을 밀어낼 수 있는가"이고, 여기 GENERATION_ORDER 는 "어느 자산을 먼저 만들 것인가"다.
COME_CLOSER 가 양쪽에서 1순위인 것은 우연이 아니라 둘 다 사용자가 가장 먼저 마주치는
자산이기 때문이지만, 두 값은 서로를 참조하지 않는다.

순수 모듈이다(DB·네트워크 없음) — 호출부가 현재 상태를 넣어 주면 판정만 돌려준다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..scenarios.pet_scenarios import PREMIUM_ACTIONS

#: 생성 순서. 앞에 있을수록 먼저 만든다.
#: 사용자가 가장 먼저 마주치는 자산이 앞에 온다 — COME_CLOSER(더블탭)와
#: BLINKING(가장 흔한 자발적 이벤트)이 없으면 화면이 눈에 띄게 죽어 보인다.
#: PREMIUM_ACTIONS 와 **같은 집합**이어야 한다(테스트가 강제한다).
GENERATION_ORDER: tuple[str, ...] = (
    "COME_CLOSER",
    "BLINKING",
    "EAR_TWITCHING",
    "HEAD_TILTING",
    "TAIL_WAGGING",
)

#: 한 펫이 동시에 돌릴 수 있는 프로바이더 작업 수.
#: 2 인 이유: 1 이면 5종을 다 만드는 데 너무 오래 걸리고, 3 이상이면 프로바이더
#: 레이트 리밋과 비용 급증이 다시 문제가 된다. 튜닝 지점은 여기 하나다.
MAX_CONCURRENT_GENERATIONS_PER_PET = 2


@dataclass(frozen=True)
class QueueDecision:
    """제출해도 되는가. 안 된다면 왜."""

    allowed: bool
    #: 거절 사유 — "at-capacity" | "waiting-for-higher-priority" | "not-queueable"
    reason: str = ""
    #: 대기열에서 앞에 몇 건이 있는가 (allowed 면 0). 프론트 표시·로그용.
    position: int = 0


def generation_rank(action_id: str) -> int:
    """GENERATION_ORDER 안의 순서. 목록에 없으면 맨 뒤."""
    a = (action_id or "").upper()
    return GENERATION_ORDER.index(a) if a in GENERATION_ORDER else len(GENERATION_ORDER)


def pending_actions(
    ready_actions: Iterable[str],
    active_actions: Iterable[str],
) -> list[str]:
    """
    아직 만들어야 하는 액션들 — 생성 순서대로.

    ready(canonical 존재)도 active(진행 중)도 아닌 것만 남는다.
    """
    ready = {a.upper() for a in ready_actions}
    active = {a.upper() for a in active_actions}
    return [a for a in GENERATION_ORDER if a not in ready and a not in active]


def decide(
    *,
    action_id: str,
    ready_actions: Iterable[str],
    active_actions: Iterable[str],
    max_concurrent: int = MAX_CONCURRENT_GENERATIONS_PER_PET,
    respect_priority: bool = True,
) -> QueueDecision:
    """
    지금 이 액션을 프로바이더에 제출해도 되는가.

    호출 전제: 이 액션은 canonical 도 없고 진행 중도 아니다(호출부가 이미 확인).

    ── 경쟁 상태에 강한 이유 ────────────────────────────────────────────────
    단순히 "현재 active 수 < 상한" 만 보면, 5개 요청이 동시에 들어왔을 때 전부
    active=0 을 보고 통과한다. 그래서 **순서 규칙**을 함께 건다: 남은 슬롯이 N 개면
    대기 목록의 앞 N 개만 통과할 수 있다. 5개가 같은 순간에 들어와도 통과하는 것은
    COME_CLOSER 와 BLINKING 뿐 — 정확히 의도한 2건이다.

    ── respect_priority=False (사용자가 직접 고른 한 건) ────────────────────
    위 순서 규칙은 **서버가 다음에 무엇을 만들지 스스로 정할 때**의 규칙이다.
    Behavior Library 에서는 사용자가 이미 골랐다. 그때도 순서를 강요하면
    "HEAD_TILTING 생성"을 눌러도 BLINKING 이 먼저 준비되기 전까지 거절돼,
    누른 것과 다른 일이 일어난다.

    **동시 실행 상한(max_concurrent)은 그대로 적용된다** — 비용과 프로바이더
    레이트 리밋을 지키는 것은 순서가 아니라 이 상한이다. 느슨해지는 것은 순서뿐이다.
    기본값은 True 라 기존 호출자(자동 전진·dev·레거시)의 동작은 한 글자도 바뀌지 않는다.
    """
    a = (action_id or "").upper()
    if a not in GENERATION_ORDER:
        # 큐 대상이 아닌 액션(레거시 4종 등)은 여기서 판단하지 않는다.
        return QueueDecision(allowed=False, reason="not-queueable")

    active = {x.upper() for x in active_actions}
    slots = max_concurrent - len(active)
    if slots <= 0:
        pending = pending_actions(ready_actions, active)
        pos = pending.index(a) if a in pending else len(pending)
        return QueueDecision(allowed=False, reason="at-capacity", position=pos)

    pending = pending_actions(ready_actions, active)
    if a not in pending:
        # ready/active 로 이미 처리된 것 — 호출부가 앞에서 걸렀어야 한다.
        return QueueDecision(allowed=False, reason="not-queueable")

    idx = pending.index(a)
    if respect_priority and idx >= slots:
        return QueueDecision(
            allowed=False, reason="waiting-for-higher-priority", position=idx
        )
    return QueueDecision(allowed=True)


def _assert_order_matches_registry() -> None:
    """GENERATION_ORDER 와 PREMIUM_ACTIONS 가 같은 집합인지 (import 시 조기 발견)."""
    if set(GENERATION_ORDER) != set(PREMIUM_ACTIONS):
        missing = set(PREMIUM_ACTIONS) - set(GENERATION_ORDER)
        extra = set(GENERATION_ORDER) - set(PREMIUM_ACTIONS)
        raise RuntimeError(
            "GENERATION_ORDER 와 PREMIUM_ACTIONS 가 어긋났다 — "
            f"누락={sorted(missing)} 잉여={sorted(extra)}"
        )


_assert_order_matches_registry()
