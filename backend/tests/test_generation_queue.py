"""
펫당 생성 큐 — 동시 제출 수 제한.

막으려는 것: 새 펫이 들어오면 프론트가 COME_CLOSER + 아이들 이벤트 4종을 각각
ensure 해서 프로바이더 제출이 **한 번에 5건** 나갔다. 기존 멱등성 검사는 전부
액션 키 단위(같은 액션 중복만 차단)라 액션 사이를 세는 곳이 없었다.

프로바이더는 호출하지 않는다 — 판정 로직과 펫 단위 집계만 검사한다.
"""

from __future__ import annotations

import asyncio

from backend.scenarios.pet_scenarios import ACTION_ORDER, PREMIUM_ACTIONS
from backend.services import generated_motions_service as motions_svc
from backend.services.generation_queue import (
    GENERATION_ORDER,
    MAX_CONCURRENT_GENERATIONS_PER_PET,
    decide,
    generation_rank,
    pending_actions,
)

ALL = list(GENERATION_ORDER)


def _allowed(action: str, ready=(), active=()) -> bool:
    return decide(action_id=action, ready_actions=ready, active_actions=active).allowed


def _who_may_submit(ready=(), active=()) -> list[str]:
    """지금 이 순간 제출이 허용되는 액션들."""
    return [a for a in ALL if _allowed(a, ready, active)]


# ── 1) 다섯 건이 비어 있어도 동시 제출은 2건뿐 ────────────────────────────────


def test_five_missing_assets_allow_only_two_submissions():
    allowed = _who_may_submit(ready=[], active=[])
    assert len(allowed) == MAX_CONCURRENT_GENERATIONS_PER_PET, allowed
    assert allowed == ["COME_CLOSER", "BLINKING"]


def test_limit_holds_even_when_all_five_arrive_simultaneously():
    """
    경쟁 상태 방어. 5개 요청이 같은 순간에 들어오면 전부 active=0 을 본다.
    "현재 active < 상한" 만 검사했다면 5건 모두 통과했을 것이다 — 순서 규칙이
    남은 슬롯 수만큼만 통과시키므로 결과가 같은 2건으로 고정된다.
    """
    simultaneous = [decide(action_id=a, ready_actions=[], active_actions=[]) for a in ALL]
    assert sum(1 for d in simultaneous if d.allowed) == MAX_CONCURRENT_GENERATIONS_PER_PET


# ── 2) 한 건이 끝나면 다음이 들어간다 ────────────────────────────────────────


def test_completion_starts_the_next_queued_job():
    # COME_CLOSER 완료, BLINKING 진행 중 → 다음은 EAR_TWITCHING 하나만.
    allowed = _who_may_submit(ready=["COME_CLOSER"], active=["BLINKING"])
    assert allowed == ["EAR_TWITCHING"]

    # 둘 다 끝났다 → 다음 두 건.
    allowed = _who_may_submit(ready=["COME_CLOSER", "BLINKING"], active=[])
    assert allowed == ["EAR_TWITCHING", "HEAD_TILTING"]


def test_queue_drains_in_order_to_completion():
    """전부 완료될 때까지 돌려 보면 순서대로 정확히 한 번씩 나간다."""
    ready: list[str] = []
    active: list[str] = []
    submitted: list[str] = []
    for _ in range(20):  # 유한 루프 — 무한 대기 회귀도 같이 잡는다
        batch = _who_may_submit(ready, active)
        if not batch:
            break
        assert len(batch) + len(active) <= MAX_CONCURRENT_GENERATIONS_PER_PET
        active.extend(batch)
        submitted.extend(batch)
        # 가장 오래된 것 하나가 완료된다
        done = active.pop(0)
        ready.append(done)
    # 루프는 "더 제출할 게 없을 때" 끝나므로 마지막 작업들이 아직 active 에 남아 있다.
    ready.extend(active)
    assert submitted == ALL, submitted
    assert sorted(ready) == sorted(ALL)
    # 각 액션은 정확히 한 번만 제출됐다 — 중복 제출 회귀 가드.
    assert len(submitted) == len(set(submitted))


# ── 3) 실패해도 큐가 막히지 않는다 ───────────────────────────────────────────


def test_failed_job_does_not_block_the_queue():
    """
    실패는 terminal 이라 active 에서 빠진다(ready 에도 없다). 그러면 그 액션은
    다시 대기열 맨 앞이 되고, 뒤 작업들도 계속 진행된다 — 영구 차단이 없다.
    """
    # COME_CLOSER 가 실패 → ready 도 active 도 아니다.
    allowed = _who_may_submit(ready=[], active=["BLINKING"])
    # 실패한 COME_CLOSER 가 재시도 후보로 남고, 슬롯이 1개뿐이라 그것만 통과.
    assert allowed == ["COME_CLOSER"]

    # COME_CLOSER 를 영영 못 만들더라도(예: 재시도 소진) 나머지는 흐른다:
    # ready 에 넣지 않고 active 로 점유만 시켜 보면 뒤가 이어진다.
    allowed = _who_may_submit(ready=["BLINKING"], active=["COME_CLOSER"])
    assert allowed == ["EAR_TWITCHING"]


# ── 4) 이미 ready 인 자산은 건너뛴다 ─────────────────────────────────────────


def test_ready_assets_are_skipped():
    assert not _allowed("COME_CLOSER", ready=["COME_CLOSER"], active=[])
    assert pending_actions(["COME_CLOSER", "BLINKING"], []) == [
        "EAR_TWITCHING",
        "HEAD_TILTING",
        "TAIL_WAGGING",
    ]


def test_all_ready_means_nothing_to_submit():
    assert _who_may_submit(ready=ALL, active=[]) == []
    assert pending_actions(ALL, []) == []


# ── 5) 진행 중인 것은 중복 제출되지 않는다 ───────────────────────────────────


def test_active_action_is_not_resubmitted():
    assert not _allowed("BLINKING", ready=[], active=["BLINKING"])


def test_at_capacity_blocks_everything():
    d = decide(
        action_id="EAR_TWITCHING",
        ready_actions=[],
        active_actions=["COME_CLOSER", "BLINKING"],
    )
    assert not d.allowed
    assert d.reason == "at-capacity"


# ── 8) COME_CLOSER 가 생성 1순위 ─────────────────────────────────────────────


def test_come_closer_is_first_in_generation_order():
    assert GENERATION_ORDER[0] == "COME_CLOSER"
    assert generation_rank("COME_CLOSER") == 0
    for other in ALL[1:]:
        assert generation_rank(other) > generation_rank("COME_CLOSER")


def test_generation_order_covers_exactly_the_premium_actions():
    """레지스트리에 액션을 추가하고 큐 순서를 빠뜨리면 여기서 잡힌다."""
    assert set(GENERATION_ORDER) == set(PREMIUM_ACTIONS)


def test_legacy_actions_are_not_queueable():
    """레거시 4종은 이 경로로 오지 않는다 — 와도 제출되지 않는다."""
    for legacy in ACTION_ORDER:
        d = decide(action_id=legacy, ready_actions=[], active_actions=[])
        assert not d.allowed
        assert d.reason == "not-queueable"


# ── 10) ACTION_ORDER 불변 ────────────────────────────────────────────────────


def test_action_order_unchanged():
    assert ACTION_ORDER == ("IDLE", "TOUCH", "VOICE", "NFC")


# ── 7) 펫마다 독립적인 큐 (실제 집계 함수로 검증) ────────────────────────────


def test_active_jobs_are_counted_per_pet():
    """
    generation_queue 는 넘겨받은 상태만 본다 — 그 상태를 만드는 집계가
    펫 단위로 갈라지는지는 여기서 확인한다. (DB 없이 목 저장소로 돈다.)
    """
    motions_svc._MOCK_JOBS.clear()
    try:
        asyncio.run(
            motions_svc.register_generation_job(
                "s1", "u1", "petA", "any", "COME_CLOSER", "ext-a1"
            )
        )
        asyncio.run(
            motions_svc.register_generation_job(
                "s2", "u1", "petB", "any", "BLINKING", "ext-b1"
            )
        )
        a = asyncio.run(motions_svc.list_active_action_ids_for_pet("u1", "petA"))
        b = asyncio.run(motions_svc.list_active_action_ids_for_pet("u1", "petB"))
        assert a == ["COME_CLOSER"], a
        assert b == ["BLINKING"], b

        # petA 가 한 건 쓰고 있어도 petB 는 자기 슬롯을 온전히 갖는다.
        assert _who_may_submit(ready=[], active=b) == ["COME_CLOSER"]
    finally:
        motions_svc._MOCK_JOBS.clear()


def test_terminal_jobs_do_not_occupy_a_slot():
    """완료/실패한 작업이 슬롯을 계속 점유하면 큐가 영구히 막힌다."""
    from backend.models.hybrid_business import MotionJobStatus

    motions_svc._MOCK_JOBS.clear()
    try:
        asyncio.run(
            motions_svc.register_generation_job(
                "s1", "u2", "petC", "any", "COME_CLOSER", "ext-c1"
            )
        )
        assert asyncio.run(motions_svc.list_active_action_ids_for_pet("u2", "petC"))
        motions_svc._MOCK_JOBS["ext-c1"].status = MotionJobStatus.failed
        assert asyncio.run(motions_svc.list_active_action_ids_for_pet("u2", "petC")) == []
    finally:
        motions_svc._MOCK_JOBS.clear()


# ── 회귀: 레거시 4코인 작업이 프리미엄 슬롯을 잡아먹으면 안 된다 ──────────────


def test_legacy_jobs_do_not_consume_premium_queue_slots():
    """
    실제로 난 버그: "BREATHING 에서 멈추고 아무것도 안 나온다".

    4코인 세트(IDLE/TOUCH/VOICE/NFC)를 만든 펫은 논터미널 작업이 4건 있는데,
    그걸 프리미엄 큐가 같이 세는 바람에 상한(2)을 영구히 넘겨 COME_CLOSER 와
    아이들 이벤트가 전부 at-capacity 로 막혔다. 두 파이프라인은 동시성을 각자
    관리해야 한다.
    """
    motions_svc._MOCK_JOBS.clear()
    try:
        for a in ACTION_ORDER:  # IDLE / TOUCH / VOICE / NFC
            asyncio.run(
                motions_svc.register_generation_job(
                    f"s-{a}", "u9", "pet9", "01_snow_forest", a, f"ext-{a}"
                )
            )
        active = asyncio.run(motions_svc.list_active_action_ids_for_pet("u9", "pet9"))
        assert active == [], f"레거시 작업이 큐 슬롯을 차지한다: {active}"

        # 프리미엄은 정상적으로 2건까지 나갈 수 있어야 한다.
        allowed = _who_may_submit(ready=[], active=active)
        assert allowed == ["COME_CLOSER", "BLINKING"], allowed
    finally:
        motions_svc._MOCK_JOBS.clear()


def test_premium_jobs_still_consume_slots():
    """반대 방향 — 필터가 과해서 프리미엄까지 안 세면 상한이 무의미해진다."""
    motions_svc._MOCK_JOBS.clear()
    try:
        for a in ("COME_CLOSER", "BLINKING"):
            asyncio.run(
                motions_svc.register_generation_job(f"s-{a}", "u9", "pet9", "any", a, f"x-{a}")
            )
        active = asyncio.run(motions_svc.list_active_action_ids_for_pet("u9", "pet9"))
        assert sorted(active) == ["BLINKING", "COME_CLOSER"]
        assert _who_may_submit(ready=[], active=active) == [], "상한이 걸리지 않았다"
    finally:
        motions_svc._MOCK_JOBS.clear()


def test_mixed_legacy_and_premium_counts_only_premium():
    motions_svc._MOCK_JOBS.clear()
    try:
        for a in ACTION_ORDER:
            asyncio.run(
                motions_svc.register_generation_job(
                    f"L-{a}", "u9", "pet9", "01_snow_forest", a, f"L-{a}"
                )
            )
        asyncio.run(
            motions_svc.register_generation_job("P1", "u9", "pet9", "any", "COME_CLOSER", "P1")
        )
        active = asyncio.run(motions_svc.list_active_action_ids_for_pet("u9", "pet9"))
        assert active == ["COME_CLOSER"], active
        # 슬롯 하나 남았으니 다음 우선순위 하나만 통과.
        assert _who_may_submit(ready=[], active=active) == ["BLINKING"]
    finally:
        motions_svc._MOCK_JOBS.clear()
