"""
Phase 5 — 행동 ON/OFF 선호.

세 상태가 서로 독립이라는 것이 전부다:

    READY        "만들어졌는가"   generated_motions
    ENTITLED     "멤버인가"       user_subscriptions
    PREFERENCE   "켜 두고 싶은가" behavior_preferences

고정하는 것:
  * 선호는 서버에 사용자/펫/행동 단위로 남는다.
  * **구독 만료가 선호를 지우지 않는다.** 갱신하면 그대로 돌아온다.
  * 토글은 **아무것도 생성하지 않는다**.
  * 남의 펫 설정은 바꿀 수 없다.
  * 레거시·무료·오타 행동 id 는 거절된다.
  * READY 가 아니어도 선호는 저장된다 (별개 상태이므로).
"""

from __future__ import annotations

import datetime

import jwt
import pytest
from fastapi import FastAPI

from backend.scenarios.pet_scenarios import ACTION_ORDER, PREMIUM_ACTIONS
from backend.routers import premium_v1, subscription_v1
from backend.services import (
    behavior_preferences as prefs_svc,
    identity_service,
    premium_generation,
    premium_purchase,
    subscription_store_service as sub_store,
    wallet_service,
)

from .conftest import ASGITestClient

SECRET = "test-jwt-secret-value-long-enough-32b"
EMAIL = "prefs@example.com"
SUB = "cccccccc-cccc-cccc-cccc-cccccccccccc"
OTHER_SUB = "dddddddd-dddd-dddd-dddd-dddddddddddd"
OTHER_EMAIL = "other@example.com"
PET = "prefs_pet"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("SUBSCRIPTION_MOCK", "1")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.delenv("ALLOW_INSECURE_TEST_AUTH", raising=False)
    monkeypatch.delenv("PREMIUM_REQUIRES_SUBSCRIPTION", raising=False)
    for mod in (prefs_svc, identity_service, premium_purchase):
        getattr(mod, "__reset_for_tests", lambda: None)()
    wallet_service._MOCK_WALLETS.clear()
    sub_store._MOCK_SUBS.clear()
    sub_store._MOCK_EVENTS.clear()
    yield
    for mod in (prefs_svc, identity_service, premium_purchase):
        getattr(mod, "__reset_for_tests", lambda: None)()
    wallet_service._MOCK_WALLETS.clear()
    sub_store._MOCK_SUBS.clear()
    sub_store._MOCK_EVENTS.clear()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> ASGITestClient:
    # 생성이 절대 일어나지 않는지 검사하기 위해 제출 계층을 감시한다.
    submitted: list[str] = []

    async def spy_submit(*, action_id, **kw):
        submitted.append(action_id)
        raise premium_generation.PremiumSubmitError("no provider in tests", stage="test")

    monkeypatch.setattr(premium_generation, "submit_premium_action", spy_submit)

    app = FastAPI()
    app.include_router(premium_v1.router, prefix="/api")
    app.include_router(subscription_v1.router, prefix="/api")
    c = ASGITestClient(app)
    c.submitted = submitted  # type: ignore[attr-defined]
    return c


def _auth(sub: str = SUB, email: str = EMAIL) -> dict[str, str]:
    tok = jwt.encode(
        {
            "sub": sub, "email": email, "email_verified": True, "aud": "authenticated",
            "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
        },
        SECRET, algorithm="HS256",
    )
    return {"Authorization": f"Bearer {tok}"}


def _subscribe(c: ASGITestClient, headers, tx="t1"):
    return c.post(
        "/api/v1/subscription/webhook",
        json={"notification_type": "INITIAL_BUY", "plan_id": "standard_subscription",
              "transaction_id": tx},
        headers=headers,
    )


def _sub_event(c: ASGITestClient, headers, event, tx):
    return c.post(
        "/api/v1/subscription/webhook",
        json={"notification_type": event, "plan_id": "standard_subscription",
              "transaction_id": tx},
        headers=headers,
    )


def _set(c: ASGITestClient, action: str, enabled: bool, *, headers=None, pet: str = PET):
    return c.put(
        "/api/v1/pet/premium/preference",
        json={"pet_id": pet, "action_id": action, "enabled": enabled},
        headers=headers or _auth(),
    )


def _prefs(c: ASGITestClient, *, headers=None, pet: str = PET) -> dict:
    r = c.get(f"/api/v1/pet/premium/assets?pet_id={pet}", headers=headers or _auth())
    assert r.status_code == 200, r.text
    return r.json()["preferences"]


# ── 기본값 ───────────────────────────────────────────────────────────────────


def test_all_premium_behaviors_default_to_on(client):
    p = _prefs(client)
    assert set(p) == set(PREMIUM_ACTIONS), "등록된 행동 전체가 나와야 한다"
    assert all(p.values()), "만든 행동의 기본값은 켬이다"


def test_legacy_and_free_behaviors_are_absent(client):
    p = _prefs(client)
    for excluded in (*ACTION_ORDER, "BREATHING"):
        assert excluded not in p, f"{excluded} 가 선호 목록에 섞였다"


# ── 지속성 ───────────────────────────────────────────────────────────────────


def test_preference_persists_across_requests(client):
    r = _set(client, "BLINKING", False)
    assert r.status_code == 200, r.text
    assert r.json()["preferences"]["BLINKING"] is False

    assert _prefs(client)["BLINKING"] is False, "다음 조회에서 값이 사라졌다"


def test_toggling_one_behavior_does_not_disturb_others(client):
    _set(client, "HEAD_TILTING", False)
    p = _prefs(client)
    assert p["HEAD_TILTING"] is False
    for other in PREMIUM_ACTIONS:
        if other != "HEAD_TILTING":
            assert p[other] is True, f"{other} 가 함께 바뀌었다"


def test_preference_can_be_toggled_back_on(client):
    _set(client, "TAIL_WAGGING", False)
    _set(client, "TAIL_WAGGING", True)
    assert _prefs(client)["TAIL_WAGGING"] is True


def test_repeated_writes_are_idempotent(client):
    for _ in range(3):
        r = _set(client, "COME_CLOSER", False)
        assert r.status_code == 200
    assert _prefs(client)["COME_CLOSER"] is False


def test_preferences_are_per_pet(client):
    _set(client, "BLINKING", False, pet="pet_a")
    assert _prefs(client, pet="pet_a")["BLINKING"] is False
    assert _prefs(client, pet="pet_b")["BLINKING"] is True, "선호가 펫 사이로 샜다"


# ── READY 와 선호는 별개 ─────────────────────────────────────────────────────


def test_preference_is_storable_for_a_behavior_that_is_not_ready(client):
    """READY 와 선호는 별개 상태다 — 아직 만들지 않았어도 저장된다."""
    body = client.get(f"/api/v1/pet/premium/assets?pet_id={PET}", headers=_auth()).json()
    assert "BLINKING" in body["missing"], "전제: 아직 만들지 않았다"

    r = _set(client, "BLINKING", False)
    assert r.status_code == 200
    assert _prefs(client)["BLINKING"] is False


def test_preference_does_not_change_asset_state(client):
    before = client.get(f"/api/v1/pet/premium/assets?pet_id={PET}", headers=_auth()).json()
    _set(client, "EAR_TWITCHING", False)
    after = client.get(f"/api/v1/pet/premium/assets?pet_id={PET}", headers=_auth()).json()

    for field in ("ready", "generating", "missing"):
        assert before[field] == after[field], f"토글이 {field} 를 바꿨다"


# ── 토글은 생성하지 않는다 ───────────────────────────────────────────────────


def test_toggling_never_submits_a_generation(client):
    _subscribe(client, _auth())
    for action in PREMIUM_ACTIONS:
        _set(client, action, False)
        _set(client, action, True)

    assert client.submitted == [], "토글이 생성을 제출했다"


def test_preference_module_cannot_reach_generation():
    """
    구조적 보장 — 생성 모듈을 import 하지 않으므로 부를 수 있는 경로가 없다.

    문자열 검색이 아니라 **실제 import 문**을 본다: 독스트링이 "generation_queue 를
    import 하지 않는다"라고 설명하는 것까지 위반으로 잡으면 안 된다.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(prefs_svc))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module)
            imported.update(a.name for a in node.names)

    for forbidden in ("premium_generation", "generation_queue", "video_generation",
                      "submit_generation", "premium_purchase"):
        assert forbidden not in imported, f"선호 모듈이 {forbidden} 을 import 한다"


# ── 만료 / 갱신 보존 ─────────────────────────────────────────────────────────


def test_expiry_does_not_delete_preferences(client):
    _subscribe(client, _auth())
    _set(client, "BLINKING", False)
    _set(client, "TAIL_WAGGING", False)

    _sub_event(client, _auth(), "EXPIRATION", "t2")

    p = _prefs(client)
    assert p["BLINKING"] is False, "만료가 선호를 지웠다"
    assert p["TAIL_WAGGING"] is False
    assert p["HEAD_TILTING"] is True


def test_renewal_restores_the_same_preferences(client):
    _subscribe(client, _auth())
    _set(client, "EAR_TWITCHING", False)
    _set(client, "COME_CLOSER", False)
    before = _prefs(client)

    _sub_event(client, _auth(), "EXPIRATION", "t2")
    _sub_event(client, _auth(), "RENEWAL", "t3")

    body = client.get(f"/api/v1/pet/premium/assets?pet_id={PET}", headers=_auth()).json()
    assert body["entitled"] is True, "전제: 갱신으로 다시 멤버가 됐다"
    assert body["preferences"] == before, "갱신 후 선호가 달라졌다"


def test_preferences_are_editable_while_expired(client):
    """
    만료 중에도 설정은 고칠 수 있다 — 선호는 결제 대상이 아니라 사용자 데이터다.
    (생성은 여전히 막힌다: test_expired_member_cannot_generate 참고.)
    """
    _subscribe(client, _auth())
    _sub_event(client, _auth(), "EXPIRATION", "t2")

    r = _set(client, "BLINKING", False)
    assert r.status_code == 200, r.text
    assert _prefs(client)["BLINKING"] is False


def test_preferences_readable_without_any_subscription(client):
    """구독한 적 없는 사용자도 자기 설정을 읽고 쓸 수 있다."""
    assert _set(client, "BLINKING", False).status_code == 200
    assert _prefs(client)["BLINKING"] is False


# ── 소유권 ───────────────────────────────────────────────────────────────────


def test_preference_requires_authentication(client):
    r = client.put(
        "/api/v1/pet/premium/preference",
        json={"pet_id": PET, "action_id": "BLINKING", "enabled": False},
    )
    assert r.status_code == 401


def test_cannot_change_another_users_pet_preferences(client):
    # 첫 사용자가 이 펫을 선점한다.
    assert _set(client, "BLINKING", False).status_code == 200

    r = _set(client, "BLINKING", True, headers=_auth(OTHER_SUB, OTHER_EMAIL))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "PET_NOT_OWNED"
    assert _prefs(client)["BLINKING"] is False, "남이 내 설정을 바꿨다"


def test_preferences_do_not_leak_between_users(client):
    _set(client, "BLINKING", False, pet="pet_mine")
    other = _auth(OTHER_SUB, OTHER_EMAIL)
    assert _prefs(client, headers=other, pet="pet_theirs")["BLINKING"] is True


# ── 잘못된 행동 id ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad", ["", "   ", "NOPE", "BREATHING", "IDLE", "TOUCH", "VOICE", "NFC", "ACTION:BLINKING"]
)
def test_invalid_behavior_ids_are_rejected(client, bad):
    r = _set(client, bad, False)
    assert r.status_code == 400, f"{bad!r} 가 통과했다"
    assert r.json()["detail"]["code"] in ("ACTION_REQUIRED", "ACTION_NOT_SUPPORTED")


def test_rejected_id_stores_nothing(client):
    _set(client, "NOPE", False)
    assert _prefs(client) == {a: True for a in PREMIUM_ACTIONS}


def test_behavior_id_is_case_insensitive_but_stored_canonically(client):
    r = _set(client, "blinking", False)
    assert r.status_code == 200
    assert r.json()["preferences"]["BLINKING"] is False


def test_resolve_action_rejects_non_premium_directly():
    for bad in ("IDLE", "TOUCH", "VOICE", "NFC", "BREATHING", "", None):
        with pytest.raises(prefs_svc.PreferenceError):
            prefs_svc.resolve_action(bad)
    for good in PREMIUM_ACTIONS:
        assert prefs_svc.resolve_action(good.lower()) == good


# ── 조회 실패는 기본값으로 열지 않는다 ───────────────────────────────────────


@pytest.mark.anyio
async def test_lookup_failure_does_not_silently_default_to_on(monkeypatch):
    """
    꺼 둔 설정이 조회 실패로 "켬"이 되면, 나중에 스케줄러가 붙었을 때 끈 행동이
    재생된다. 실패는 실패로 올린다.
    """
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "1")

    class _Boom:
        def table(self, *_a, **_k):
            raise RuntimeError("supabase down")

    monkeypatch.setattr(prefs_svc, "_supabase", lambda: _Boom())

    with pytest.raises(prefs_svc.PreferenceError) as e:
        await prefs_svc.get_preferences("u", "p")
    assert e.value.status == 503
