"""
generated_motions.action_id CHECK 제약 ↔ 파이썬 레지스트리 동기화 가드.

막으려는 실패: 새 액션/아이들 이벤트를 파이썬(PREMIUM_ACTIONS)에만 등록하고
마이그레이션을 잊는 것. 그러면 제출·생성은 전부 성공하고 **승격 시점의 INSERT 만**
CHECK 위반으로 죽는다 — 웹훅 백그라운드 경로라 사용자에게는 "생성이 끝났는데
영상이 안 나온다"로만 보인다. 가장 늦게, 가장 조용히 터지는 종류의 버그다.

DB 에 붙지 않는다. 마이그레이션 파일에 선언된 값 집합만 읽어서 비교한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.scenarios.pet_scenarios import ACTION_ORDER, PREMIUM_ACTIONS

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "supabase" / "migrations"

#: 이 제약을 정의하는 구문. 인라인 CHECK(테이블 생성 시)와 명명된 제약 둘 다 잡는다.
_NAMED_CHECK = re.compile(
    r"add\s+constraint\s+generated_motions_action_id_check\s*"
    r"check\s*\(\s*action_id\s+in\s*\((?P<values>[^)]*)\)\s*\)",
    re.IGNORECASE | re.DOTALL,
)


def _parse_values(raw: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"'([^']+)'", raw)}


def _effective_allowed_values() -> tuple[str, set[str]]:
    """
    마이그레이션을 파일명 순으로 적용했을 때 **최종적으로** 살아남는 허용 집합.

    제약은 여러 번 drop/add 되므로, 마지막으로 정의한 파일이 이긴다.
    """
    latest_file = None
    latest_values: set[str] = set()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        for match in _NAMED_CHECK.finditer(text):
            latest_file = path.name
            latest_values = _parse_values(match.group("values"))
    assert latest_file is not None, "generated_motions_action_id_check 정의를 찾지 못했다"
    return latest_file, latest_values


def test_constraint_definition_exists():
    name, values = _effective_allowed_values()
    assert values, f"{name} 에서 허용 값을 파싱하지 못했다"


def test_legacy_four_actions_still_allowed():
    """ACTION_ORDER 4종은 어떤 변경에서도 살아남아야 한다 — 4코인/device sync 계약."""
    _, values = _effective_allowed_values()
    for action in ACTION_ORDER:
        assert action in values, f"레거시 액션 {action} 이 제약에서 빠졌다"


def test_previously_allowed_values_preserved():
    """
    제약을 **넓히기만** 해야 한다. 과거 마이그레이션이 한 번이라도 허용한 값이
    최종 집합에서 빠지면, 이미 저장된 행이 있는 DB 에서 ALTER 자체가 실패한다.
    """
    _, effective = _effective_allowed_values()
    ever_allowed: set[str] = set()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        for match in _NAMED_CHECK.finditer(path.read_text(encoding="utf-8")):
            ever_allowed |= _parse_values(match.group("values"))
    missing = ever_allowed - effective
    assert not missing, f"예전에 허용됐던 값이 제거됐다: {sorted(missing)}"


def test_every_premium_action_is_allowed_by_the_constraint():
    """
    PREMIUM_ACTIONS(= PET_ACTIONS + IDLE_EVENTS)는 전부 generated_motions 에
    저장된다. 파이썬에만 등록하고 마이그레이션을 빠뜨리면 여기서 잡힌다.
    """
    name, values = _effective_allowed_values()
    for action in PREMIUM_ACTIONS:
        assert action in values, (
            f"{action} 이 PREMIUM_ACTIONS 에는 있는데 {name} 의 CHECK 에는 없다 — "
            f"마이그레이션을 추가해라. 안 그러면 승격 INSERT 가 조용히 실패한다."
        )


def test_blinking_is_allowed():
    """Phase 1A — 첫 아이들 이벤트."""
    _, values = _effective_allowed_values()
    assert "BLINKING" in values


def test_constraint_does_not_leak_unknown_actions():
    """
    제약이 파이썬이 모르는 값을 허용하고 있지 않은지 — 반대 방향 드리프트.
    (실패해도 데이터 손상은 아니지만, 지운 액션이 DB 에만 남았다는 신호다.)
    """
    _, values = _effective_allowed_values()
    known = set(ACTION_ORDER) | set(PREMIUM_ACTIONS)
    unknown = values - known
    assert not unknown, f"CHECK 는 허용하는데 파이썬은 모르는 값: {sorted(unknown)}"


def test_ear_twitching_is_allowed():
    """Phase 2 — 두 번째 아이들 이벤트."""
    _, values = _effective_allowed_values()
    assert "EAR_TWITCHING" in values
