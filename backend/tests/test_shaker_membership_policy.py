"""
Shaker 더블탭 = **구독 ACTIVE ∩ 자산 READY ∩ 선호 ON** (PM 확정).

Phase 6 의 런타임 적격성(behavior-library.ts `isBehaviorEligible`)과 **같은 규칙**
이어야 한다. 규칙이 갈리면 "메인 앱에서 꺼 둔 행동이 QR 로는 재생된다"는 구멍이
생기고, 사용자가 자기 설정을 신뢰할 수 없게 된다.

여기서 고정하는 것:
  * 세 조건이 모두 참일 때만 액션이 나간다 (URL 포함).
  * 하나라도 거짓/판정 불가면 **거절**하고 BREATHING 만 준다 (fail closed).
  * 자격이 없다고 해서 생성하지 않는다 — 없는 액션은 없는 채로 둔다.
  * BREATHING 은 어떤 경우에도 무료로 나간다.
"""

from __future__ import annotations

import functools
import json

import anyio
import pytest
from fastapi import FastAPI

from backend.models.hybrid_business import GeneratedMotion
from backend.routers import shaker_v1
from backend.services import behavior_preferences
from backend.services import generated_motions_service as motions_svc
from backend.services import premium_entitlement, premium_purchase
from backend.services import shaker_policy, shaker_rate_limit, shaker_share

from .conftest import ASGITestClient, follow_shaker_asset

OWNER = "member@example.com"
PET = "pet_goya"
BREATH = "https://cdn.test/goya/idle_loop.mp4"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("SHAKER_RATE_LIMIT_ENABLED", "0")
    # 기본값을 그대로 쓴다 — 이 파일은 "기본이 membership 이다"도 함께 검증한다.
    monkeypatch.delenv("SHAKER_DOUBLE_TAP_POLICY", raising=False)
    shaker_share.__reset_for_tests()
    shaker_rate_limit.__reset_for_tests()
    behavior_preferences.__reset_for_tests()
    motions_svc._MOCK_MOTIONS.clear()
    premium_purchase.__reset_for_tests()
    yield
    shaker_share.__reset_for_tests()
    shaker_rate_limit.__reset_for_tests()
    behavior_preferences.__reset_for_tests()
    motions_svc._MOCK_MOTIONS.clear()
    premium_purchase.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(shaker_v1.router, prefix="/api")
    return ASGITestClient(app)


def _sync(afn, *args, **kwargs):
    return anyio.run(functools.partial(afn, *args, **kwargs))


def _mint() -> str:
    _sid, token = _sync(
        shaker_share.create_share,
        user_id=OWNER, pet_id=PET, breathing_url=BREATH, pet_name="고야",
    )
    return token


def _ready(action: str) -> None:
    key = motions_svc._motion_key(OWNER, PET, "any", action)
    motions_svc._MOCK_MOTIONS[key] = GeneratedMotion(
        user_id=OWNER, pet_id=PET, place_id="any", action_id=action,
        video_url=f"https://cdn.test/{action.lower()}.mp4",
    )


def _member(monkeypatch, entitled: bool = True, status: str = "active") -> None:
    async def _get(_uid):
        return premium_entitlement.EntitlementState(
            entitled=entitled, status=status, enforced=True
        )

    monkeypatch.setattr(premium_entitlement, "get_entitlement", _get)


def _pref_off(action: str) -> None:
    _sync(
        behavior_preferences.set_preference,
        user_id=OWNER, pet_id=PET, action_id=action, enabled=False,
    )


def _body(client: ASGITestClient, token: str) -> dict:
    r = client.get("/api/v1/shaker/pet", params={"share": token})
    assert r.status_code == 200, r.text
    return r.json()


# ── 세 조건이 모두 참일 때만 ─────────────────────────────────────────────────


def test_member_with_ready_and_enabled_action_can_double_tap(client, monkeypatch):
    token = _mint()
    _ready("COME_CLOSER")
    _member(monkeypatch)

    body = _body(client, token)
    assert [a["id"] for a in body["actions"]] == ["COME_CLOSER"]
    assert body["double_tap_action_id"] == "COME_CLOSER"


def test_non_member_gets_no_action(client, monkeypatch):
    """**핵심 회귀**: 구독이 없으면 READY 여도 URL 조차 나가지 않는다."""
    token = _mint()
    _ready("COME_CLOSER")
    _member(monkeypatch, entitled=False, status="expired")

    body = _body(client, token)
    assert body["actions"] == []
    assert body["double_tap_action_id"] is None
    assert "come_closer" not in json.dumps(body).lower()


def test_member_with_preference_off_gets_no_action(client, monkeypatch):
    """
    **핵심 회귀**: 소유자가 끈 행동은 QR 로도 재생되지 않는다.

    메인 앱과 규칙이 갈리면 사용자가 자기 설정을 신뢰할 수 없게 된다.
    """
    token = _mint()
    _ready("COME_CLOSER")
    _member(monkeypatch)
    _pref_off("COME_CLOSER")

    body = _body(client, token)
    assert body["actions"] == []
    assert body["double_tap_action_id"] is None


def test_preference_off_removes_only_that_action(client, monkeypatch):
    """끈 것만 빠진다 — 나머지는 그대로 재생된다."""
    token = _mint()
    _ready("COME_CLOSER")
    _ready("BLINKING")
    _member(monkeypatch)
    _pref_off("COME_CLOSER")

    body = _body(client, token)
    assert [a["id"] for a in body["actions"]] == ["BLINKING"]
    assert body["double_tap_action_id"] == "BLINKING"


def test_missing_action_is_not_offered(client, monkeypatch):
    """READY 가 아니면 자격이 있어도 나가지 않는다 — 그리고 만들지도 않는다."""
    token = _mint()
    _member(monkeypatch)  # 멤버지만 자산이 없다

    body = _body(client, token)
    assert body["actions"] == []
    assert body["double_tap_action_id"] is None
    assert body["breathing_url"]  # BREATHING 은 그대로


# ── fail closed ──────────────────────────────────────────────────────────────


def test_entitlement_lookup_failure_denies(client, monkeypatch):
    """구독 조회 장애가 곧 무료 배포가 되면 안 된다."""
    token = _mint()
    _ready("COME_CLOSER")

    async def _boom(_uid):
        raise RuntimeError("구독 저장소 장애")

    monkeypatch.setattr(premium_entitlement, "get_entitlement", _boom)

    body = _body(client, token)
    assert body["actions"] == []
    assert follow_shaker_asset(client, body["breathing_url"]) == BREATH  # 무료 경험은 살아 있다


def test_preference_lookup_failure_denies(client, monkeypatch):
    """
    선호 조회 장애도 거절이다.

    끈 행동을 켠 것으로 오해하느니 아무것도 열지 않는다 — 사용자가 명시적으로
    끈 것을 장애 중에 재생하는 것이 가장 나쁜 실패다.
    """
    token = _mint()
    _ready("COME_CLOSER")
    _member(monkeypatch)

    async def _boom(*_a, **_k):
        raise behavior_preferences.PreferenceError(
            "PREFERENCES_UNAVAILABLE", "장애", status=503
        )

    monkeypatch.setattr(behavior_preferences, "get_preferences", _boom)

    body = _body(client, token)
    assert body["actions"] == []
    assert body["double_tap_action_id"] is None
    assert follow_shaker_asset(client, body["breathing_url"]) == BREATH


def test_policy_unit_requires_all_three(client):
    """순수 함수 수준에서 세 조건을 각각 떨어뜨려 본다."""
    ready = ["COME_CLOSER"]
    p = shaker_policy.POLICY_MEMBERSHIP

    ok = shaker_policy.permitted_action_ids(
        ready, owner_entitled=True, preferences={"COME_CLOSER": True}, policy=p
    )
    assert ok == ["COME_CLOSER"]

    # 구독 없음
    assert shaker_policy.permitted_action_ids(
        ready, owner_entitled=False, preferences={"COME_CLOSER": True}, policy=p
    ) == []
    # 판정 불가(None)도 거절
    assert shaker_policy.permitted_action_ids(
        ready, owner_entitled=None, preferences={"COME_CLOSER": True}, policy=p
    ) == []
    # 선호 OFF
    assert shaker_policy.permitted_action_ids(
        ready, owner_entitled=True, preferences={"COME_CLOSER": False}, policy=p
    ) == []
    # 선호 조회 실패(None)도 거절
    assert shaker_policy.permitted_action_ids(
        ready, owner_entitled=True, preferences=None, policy=p
    ) == []
    # READY 없음
    assert shaker_policy.permitted_action_ids(
        [], owner_entitled=True, preferences={"COME_CLOSER": True}, policy=p
    ) == []


def test_unsaved_preference_defaults_to_on(client, monkeypatch):
    """
    저장된 선호가 없으면 켬 — behavior_preferences.DEFAULT_ENABLED 와 같은 규칙.

    두 곳에서 기본값 판정이 달라지면 화면과 재생이 어긋난다.
    """
    assert behavior_preferences.DEFAULT_ENABLED is True
    token = _mint()
    _ready("COME_CLOSER")
    _member(monkeypatch)
    # 선호를 한 번도 저장하지 않았다.
    assert _body(client, token)["double_tap_action_id"] == "COME_CLOSER"


# ── 자격이 없어도 생성하지 않는다 ────────────────────────────────────────────


def test_ineligible_visitor_never_triggers_generation(client, monkeypatch):
    """
    **핵심 회귀**: 자격 없음 → 생성 유도가 아니라 그냥 BREATHING 이다.

    "없으면 만들어 준다"는 예전 경로(come-closer-autogen)의 발상이 여기 새어
    들어오면, 로그인도 하지 않은 방문자가 프로바이더 비용을 태우게 된다.
    """
    fired: list[str] = []

    def _explode(name: str):
        async def _boom(*_a, **_k):
            fired.append(name)
            raise AssertionError(f"{name} 이 호출됐다 — 생성 금지 위반")

        return _boom

    from backend.services import generation_queue, premium_generation

    for mod, attr in (
        (premium_purchase, "purchase"),
        (premium_generation, "submit_action"),
        (generation_queue, "enqueue_generation"),
        (generation_queue, "advance_generation_queue"),
    ):
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, _explode(f"{mod.__name__}.{attr}"))

    token = _mint()
    _member(monkeypatch, entitled=False, status="expired")

    for _ in range(3):
        body = _body(client, token)
        assert body["actions"] == []
        assert follow_shaker_asset(client, body["breathing_url"]) == BREATH
    assert fired == []


def test_breathing_is_free_regardless_of_membership(client, monkeypatch):
    """BREATHING 은 구독·선호·정책 어디에도 종속되지 않는다."""
    token = _mint()
    _ready("COME_CLOSER")

    for entitled in (True, False):
        _member(monkeypatch, entitled=entitled)
        assert follow_shaker_asset(client, _body(client, token)["breathing_url"]) == BREATH
