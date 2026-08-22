"""
Phase 7 — 프로바이더 비용 안전장치.

실 생성을 한 건이라도 켜기 전에 **여섯 겹**이 모두 살아 있는지 확인한다.
구독은 무제한 생성권이 아니다.

    1) GENERATION_MOCK   프로바이더 중립 차단 (luma/wan 무관)
    2) canonical 재사용   READY 는 다시 만들지 않는다
    3) 활성 작업 재사용    진행 중은 다시 제출하지 않는다
    4) 펫당 동시 상한      MAX_CONCURRENT_GENERATIONS_PER_PET
    5) 구독 게이트         만료면 제출 자체가 없다
    6) 제출 영수증         과금이 일어났다면 반드시 기록이 남는다

이 파일은 **감사(audit)** 다 — 기존 동작을 바꾸지 않고, 무너지면 알려 준다.
"""

from __future__ import annotations

import os

import anyio
import pytest

from backend.scenarios.pet_scenarios import PREMIUM_ACTIONS
from backend.services import (
    generation_queue,
    premium_generation,
    premium_purchase,
    subscription_store_service as sub_store,
    video_generation,
    wallet_service,
)
from backend.services.subscription_webhook_service import handle_subscription_webhook

USER = "cost_user"
PET = "cost_pet"
IMG = "https://example.test/cutout.png"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("SUBSCRIPTION_MOCK", "1")
    monkeypatch.delenv("PREMIUM_REQUIRES_SUBSCRIPTION", raising=False)
    premium_purchase.__reset_for_tests()
    wallet_service._MOCK_WALLETS.clear()
    sub_store._MOCK_SUBS.clear()
    sub_store._MOCK_EVENTS.clear()
    yield
    premium_purchase.__reset_for_tests()
    wallet_service._MOCK_WALLETS.clear()
    sub_store._MOCK_SUBS.clear()
    sub_store._MOCK_EVENTS.clear()


# ── 1) 프로바이더 중립 차단 ──────────────────────────────────────────────────


def test_generation_mock_defaults_off(monkeypatch: pytest.MonkeyPatch):
    """기본값이 켜져 있으면 프로덕션이 조용히 생성을 멈춘다 — 기본은 꺼짐이어야 한다."""
    monkeypatch.delenv("GENERATION_MOCK", raising=False)
    assert video_generation.generation_mock_enabled() is False


@pytest.mark.parametrize("provider", ["luma", "wan", "wan_turbo", "wan_a14b"])
def test_generation_mock_blocks_every_provider(provider, monkeypatch: pytest.MonkeyPatch):
    """
    **프로바이더 중립**이 핵심이다. 예전에는 LUMA_MOCK 이 luma 만 막았고
    wan_service 에는 목업이 아예 없어, VIDEO_PROVIDER 오타 하나로 실 호출이 나갔다.
    """
    monkeypatch.setenv("GENERATION_MOCK", "1")
    # 키를 일부러 비워 둔다 — 실제로 호출됐다면 여기서 예외가 났을 것이다.
    monkeypatch.delenv("LUMA_API_KEY", raising=False)
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("FAL_API_KEY", raising=False)

    job = anyio.run(
        lambda: video_generation.submit_generation("https://x.test/a.png", "p", provider=provider)
    )
    assert job.external_id.startswith("mock_"), "실 제출이 나갔다"


def test_generation_mock_is_checked_before_provider_dispatch():
    """차단이 프로바이더 분기보다 앞에 있어야 키 없는 환경에서도 예외가 나지 않는다."""
    import inspect

    src = inspect.getsource(video_generation.submit_generation)
    gate = src.index("generation_mock_enabled()")
    luma = src.index("PROVIDER_LUMA")
    assert gate < luma, "프로바이더 분기 뒤에서 차단하고 있다"


# ── 2~4) 재사용 · 동시 상한 ─────────────────────────────────────────────────


class _World:
    def __init__(self) -> None:
        self.ready: dict[str, str] = {}
        self.active: set[str] = set()
        self.submitted: list[str] = []


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    w = _World()

    class _M:
        def __init__(self, a, u):
            self.action_id, self.video_url = a, u

    async def list_motions(user_id, pet_id=None):
        return [_M(a, u) for a, u in w.ready.items()]

    async def list_active(user_id, pet_id=None):
        return sorted(w.active)

    async def submit(*, user_id, pet_id, action_id, pet_image_url, api_base, **kw):
        w.submitted.append(action_id)
        w.active.add(action_id)
        return premium_generation.SubmitResult(
            action_id=action_id, session_id="s", external_id="e",
            provider="mock", provider_model=None, keyframe_url=pet_image_url,
        )

    monkeypatch.setattr(premium_purchase.motions_svc, "list_motions_for_pet", list_motions)
    monkeypatch.setattr(premium_purchase.motions_svc, "list_active_action_ids_for_pet", list_active)
    monkeypatch.setattr(premium_generation, "submit_premium_action", submit)
    return w


async def _member():
    await handle_subscription_webhook({
        "store_type": "mock", "notification_type": "INITIAL_BUY", "user_id": USER,
        "plan_id": "standard_subscription", "transaction_id": "tx1",
    })


async def _gen(action):
    return await premium_purchase.purchase(
        user_id=USER, pet_id=PET, kind=premium_purchase.action_kind(action),
        pet_image_url=IMG, api_base="https://api.test",
    )


@pytest.mark.anyio
async def test_canonical_ready_is_never_regenerated(world):
    await _member()
    world.ready["BLINKING"] = "https://cdn.test/b.mp4"
    r = await _gen("BLINKING")
    assert r.submitted == [] and world.submitted == []


@pytest.mark.anyio
async def test_active_job_is_reused_not_resubmitted(world):
    await _member()
    await _gen("BLINKING")
    n = len(world.submitted)
    await _gen("BLINKING")
    assert len(world.submitted) == n, "진행 중인 작업을 다시 제출했다"


def test_per_pet_concurrency_limit_is_enforced():
    """상한을 넘기면 decide 가 거절한다 — 사용자가 아무리 눌러도 2건을 넘지 않는다."""
    cap = generation_queue.MAX_CONCURRENT_GENERATIONS_PER_PET
    assert cap >= 1
    active = list(PREMIUM_ACTIONS)[:cap]
    for action in PREMIUM_ACTIONS:
        if action in active:
            continue
        d = generation_queue.decide(
            action_id=action, ready_actions=[], active_actions=active
        )
        assert d.allowed is False, f"{action} 이 상한을 넘어 통과했다"
        assert d.reason == "at-capacity"


def test_explicit_pick_still_respects_the_concurrency_cap():
    """
    Phase 4 에서 사용자가 고른 한 건은 **우선순위**를 건너뛰게 했다.
    비용을 지키는 것은 순서가 아니라 상한이므로, 상한은 그대로여야 한다.
    """
    cap = generation_queue.MAX_CONCURRENT_GENERATIONS_PER_PET
    active = list(PREMIUM_ACTIONS)[:cap]
    target = next(a for a in PREMIUM_ACTIONS if a not in active)
    d = generation_queue.decide(
        action_id=target, ready_actions=[], active_actions=active, respect_priority=False
    )
    assert d.allowed is False, "명시적 선택이 동시 상한을 뚫었다"


# ── 5) 구독 게이트 ──────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_expired_subscription_submits_nothing(world):
    await _member()
    await handle_subscription_webhook({
        "store_type": "mock", "notification_type": "EXPIRATION", "user_id": USER,
        "plan_id": "standard_subscription", "transaction_id": "tx2",
    })
    with pytest.raises(premium_purchase.PurchaseError):
        await _gen("BLINKING")
    assert world.submitted == []


@pytest.mark.anyio
async def test_subscription_is_not_unlimited_generation(world):
    """멤버가 같은 행동을 반복 요청해도 제출은 한 번뿐이다."""
    await _member()
    for _ in range(5):
        try:
            await _gen("BLINKING")
        except premium_purchase.PurchaseError:
            pass
    assert world.submitted.count("BLINKING") == 1


# ── 6) 제출 영수증 ──────────────────────────────────────────────────────────


def test_submission_receipt_is_logged_before_db_write():
    """
    프로바이더가 수락(=과금)한 뒤 DB 쓰기가 실패해도 external_id 로 복구할 수 있어야
    한다. 영수증 로그가 register_generation_job 보다 **앞**에 있어야 성립한다.
    """
    import inspect

    src = inspect.getsource(premium_generation.submit_premium_action)
    assert src.index("log_submission_receipt") < src.index("register_generation_job")


# ── 프로덕션 설정 감사 ──────────────────────────────────────────────────────


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "SUBSCRIPTION_MOCK", "PAYMENT_MOCK", "ALLOW_INSECURE_TEST_AUTH",
        "GENERATION_MOCK", "PREMIUM_REQUIRES_SUBSCRIPTION",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "x" * 40)
    monkeypatch.setenv("SUBSCRIPTION_WEBHOOK_SECRET", "y" * 40)
    monkeypatch.setenv("SUPABASE_URL", "https://p.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k")
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "1")


def test_readiness_passes_when_fully_configured(monkeypatch: pytest.MonkeyPatch):
    from backend.services import production_readiness

    _clean_env(monkeypatch)
    r = production_readiness.audit()
    assert r.production_ready is True, r.blockers


@pytest.mark.parametrize(
    "unset", ["SUPABASE_URL", "SUBSCRIPTION_WEBHOOK_SECRET", "SUPABASE_SERVICE_ROLE_KEY"]
)
def test_missing_security_config_is_a_blocker(unset, monkeypatch: pytest.MonkeyPatch):
    from backend.services import production_readiness

    _clean_env(monkeypatch)
    monkeypatch.delenv(unset, raising=False)
    monkeypatch.delenv("VITE_SUPABASE_URL", raising=False)
    r = production_readiness.audit()
    assert r.production_ready is False, f"{unset} 가 빠졌는데 통과했다"


def test_missing_jwt_secret_is_not_a_blocker_anymore(monkeypatch: pytest.MonkeyPatch):
    """
    **핵심 회귀**: SUPABASE_JWT_SECRET 은 더 이상 인증의 전제가 아니다.

    현재 Supabase 액세스 토큰은 ES256 이라 JWKS 공개키로 검증한다. 시크릿을
    필수로 보면 "설정은 완벽한데 준비 안 됨" 이라는 틀린 신호를 주고, 실제로
    그 잘못된 전제가 ES256 토큰을 전부 막고 있었다.

    다만 레거시 HS256 토큰은 거절되므로 경고로는 남는다.
    """
    from backend.services import production_readiness

    _clean_env(monkeypatch)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    r = production_readiness.audit()

    assert r.production_ready is True, r.blockers
    assert not any("SUPABASE_JWT_SECRET" in b for b in r.blockers)
    assert any("SUPABASE_JWT_SECRET" in w for w in r.warnings), r.warnings


def test_insecure_auth_bypass_is_a_blocker(monkeypatch: pytest.MonkeyPatch):
    from backend.services import production_readiness

    _clean_env(monkeypatch)
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    assert production_readiness.audit().production_ready is False


def test_subscription_mock_is_flagged(monkeypatch: pytest.MonkeyPatch):
    """목업이 켜져 있으면 로그인한 사용자가 스스로 구독을 켤 수 있다."""
    from backend.services import production_readiness

    _clean_env(monkeypatch)
    monkeypatch.setenv("SUBSCRIPTION_MOCK", "1")
    r = production_readiness.audit()
    assert any("SUBSCRIPTION_MOCK" in w for w in r.warnings)


def test_readiness_never_leaks_secret_values(monkeypatch: pytest.MonkeyPatch):
    from backend.services import production_readiness

    _clean_env(monkeypatch)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "SUPER-SECRET-VALUE")
    blob = repr(production_readiness.audit().as_dict())
    assert "SUPER-SECRET-VALUE" not in blob, "감사 결과가 비밀값을 노출한다"


def test_readiness_audit_never_raises(monkeypatch: pytest.MonkeyPatch):
    """부팅 경로에서 도는 코드다 — 어떤 환경에서도 예외를 내면 안 된다."""
    from backend.services import production_readiness

    for k in list(os.environ):
        if k.startswith(("SUPABASE", "SUBSCRIPTION", "PAYMENT", "GENERATION", "HYBRID")):
            monkeypatch.delenv(k, raising=False)
    assert production_readiness.audit().production_ready is False
