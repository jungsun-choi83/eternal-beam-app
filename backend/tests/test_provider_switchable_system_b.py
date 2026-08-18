"""
System B 프로바이더 전환 아키텍처 (Phase 12).

핵심 계약:
  * 아무 설정도 하지 않으면 **luma** — 기존 배포와 동작이 동일해야 한다.
  * fal/Wan 웹훅 본문이 Luma 와 **같은 완료 경로**로 정규화되어야 한다.
  * 알 수 없는 프로바이더는 제출 전에 hard error.
  * /luma-webhook 은 별칭으로 살아 있어야 한다 (이미 등록된 콜백 URL 보호).

유료 API 는 호출하지 않는다.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.services import video_generation as vg
from backend.services.video_generation import (
    DEFAULT_PROVIDER,
    PROVIDER_LUMA,
    PROVIDER_WAN_A14B,
    PROVIDER_WAN_TURBO,
    GenerationOutcome,
    SubmittedJob,
    UnknownVideoProviderError,
    normalize_provider,
    normalize_webhook,
    resolve_action_provider,
    submit_generation,
)

ACTIONS = ("IDLE", "TOUCH", "VOICE", "NFC")
_ENV = (
    "VIDEO_PROVIDER",
    "VIDEO_PROVIDER_ACTION",
    "VIDEO_PROVIDER_IDLE",
    "VIDEO_PROVIDER_TOUCH",
    "VIDEO_PROVIDER_VOICE",
    "VIDEO_PROVIDER_NFC",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)


# ── 기본값: 설정이 없으면 전부 luma ─────────────────────────────────────────


@pytest.mark.parametrize("action", ACTIONS)
def test_default_provider_is_luma(action: str):
    assert resolve_action_provider(action) == PROVIDER_LUMA
    assert DEFAULT_PROVIDER == PROVIDER_LUMA


def test_blank_env_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("VIDEO_PROVIDER_ACTION", "   ")
    assert resolve_action_provider("TOUCH") == PROVIDER_LUMA


# ── 우선순위: 액션별 > ACTION > 전역 ────────────────────────────────────────


def test_global_video_provider_applies_when_nothing_more_specific(monkeypatch):
    monkeypatch.setenv("VIDEO_PROVIDER", "wan_turbo")
    assert resolve_action_provider("VOICE") == PROVIDER_WAN_TURBO


def test_action_scope_overrides_global(monkeypatch):
    monkeypatch.setenv("VIDEO_PROVIDER", "luma")
    monkeypatch.setenv("VIDEO_PROVIDER_ACTION", "wan_turbo")
    assert resolve_action_provider("VOICE") == PROVIDER_WAN_TURBO


def test_per_action_overrides_action_scope(monkeypatch):
    monkeypatch.setenv("VIDEO_PROVIDER_ACTION", "wan_turbo")
    monkeypatch.setenv("VIDEO_PROVIDER_NFC", "luma")
    assert resolve_action_provider("NFC") == PROVIDER_LUMA
    assert resolve_action_provider("TOUCH") == PROVIDER_WAN_TURBO


def test_mixed_per_action_matrix(monkeypatch):
    monkeypatch.setenv("VIDEO_PROVIDER_TOUCH", "wan_turbo")
    monkeypatch.setenv("VIDEO_PROVIDER_VOICE", "wan_a14b")
    monkeypatch.setenv("VIDEO_PROVIDER_NFC", "luma")
    assert resolve_action_provider("TOUCH") == PROVIDER_WAN_TURBO
    assert resolve_action_provider("VOICE") == PROVIDER_WAN_A14B
    assert resolve_action_provider("NFC") == PROVIDER_LUMA
    assert resolve_action_provider("IDLE") == PROVIDER_LUMA  # 미설정 → 기본값


# ── 별칭 / 알 수 없는 값 ────────────────────────────────────────────────────


def test_wan_alias_maps_to_turbo(monkeypatch):
    monkeypatch.setenv("VIDEO_PROVIDER_ACTION", "wan")
    assert resolve_action_provider("TOUCH") == PROVIDER_WAN_TURBO
    assert normalize_provider("wan") == PROVIDER_WAN_TURBO


@pytest.mark.parametrize("bad", ["wanna", "WAN2", "openai", "ray2", "wan-turbo"])
def test_unknown_provider_hard_errors(monkeypatch, bad: str):
    monkeypatch.setenv("VIDEO_PROVIDER_ACTION", bad)
    with pytest.raises(UnknownVideoProviderError):
        resolve_action_provider("TOUCH")


def test_unknown_provider_error_lists_valid_options(monkeypatch):
    monkeypatch.setenv("VIDEO_PROVIDER_ACTION", "nope")
    with pytest.raises(UnknownVideoProviderError) as ei:
        resolve_action_provider("TOUCH")
    msg = str(ei.value)
    for p in ("luma", "wan_turbo", "wan_a14b"):
        assert p in msg


# ── submit_generation: 각 프로바이더로 올바르게 디스패치 ────────────────────


def test_submit_dispatches_to_luma_with_callback(monkeypatch):
    seen: dict = {}

    async def fake_luma(image_url, prompt=None, model=None, resolution=None, callback_url=None):
        seen.update(image_url=image_url, prompt=prompt, model=model,
                    resolution=resolution, callback_url=callback_url)
        return "luma-gen-123"

    import backend.services.luma_service as ls

    monkeypatch.setattr(ls, "create_generation", fake_luma)
    job = asyncio.run(
        submit_generation("http://img", "P", provider="luma", callback_url="http://hook")
    )
    assert isinstance(job, SubmittedJob)
    assert job.provider == PROVIDER_LUMA
    assert job.external_id == "luma-gen-123"
    assert seen["callback_url"] == "http://hook"
    assert job.poll_url is None, "luma 는 push 완료 — 폴링 URL 이 없다"


def test_submit_dispatches_to_fal_with_webhook(monkeypatch):
    seen: dict = {}

    class _Sub:
        request_id = "fal-req-9"
        status_url = "http://q/status"
        response_url = "http://q/result"

    async def fake_wan(image_url, prompt, *, model=None, resolution=None, webhook_url=None):
        seen.update(model=model, webhook_url=webhook_url)
        return _Sub()

    import backend.services.wan_service as ws

    monkeypatch.setattr(ws, "create_generation", fake_wan)
    job = asyncio.run(
        submit_generation("http://img", "P", provider="wan_turbo", callback_url="http://hook")
    )
    assert job.provider == PROVIDER_WAN_TURBO
    assert job.external_id == "fal-req-9"
    assert seen["webhook_url"] == "http://hook", "fal_webhook 으로 전달돼야 한다"
    assert "turbo" in seen["model"]
    assert job.poll_url == "http://q/status"


def test_wan_a14b_uses_non_turbo_model(monkeypatch):
    seen: dict = {}

    class _Sub:
        request_id = "r"
        status_url = "s"
        response_url = "x"

    async def fake_wan(image_url, prompt, *, model=None, resolution=None, webhook_url=None):
        seen["model"] = model
        return _Sub()

    import backend.services.wan_service as ws

    monkeypatch.setattr(ws, "create_generation", fake_wan)
    asyncio.run(submit_generation("i", "p", provider="wan_a14b"))
    assert seen["model"].endswith("/image-to-video"), "a14b 는 turbo 가 아니어야 한다"
    assert "turbo" not in seen["model"]


def test_submit_rejects_unknown_provider():
    with pytest.raises(UnknownVideoProviderError):
        asyncio.run(submit_generation("i", "p", provider="bogus"))


# ── 웹훅 정규화: Luma ───────────────────────────────────────────────────────


def test_luma_completed_normalizes():
    o = normalize_webhook(
        {"id": "abc", "state": "completed", "assets": {"video": "https://v/a.mp4"}}
    )
    assert o == GenerationOutcome(
        provider=PROVIDER_LUMA, external_id="abc", state="completed",
        video_url="https://v/a.mp4", error=None
    )
    assert o.is_completed and not o.is_failed


def test_luma_failed_normalizes():
    o = normalize_webhook({"id": "abc", "state": "failed", "failure_reason": "moderation"})
    assert o.state == "failed" and o.error == "moderation" and o.is_failed


def test_luma_completed_without_video_is_pending():
    o = normalize_webhook({"id": "abc", "state": "completed", "assets": {}})
    assert o.state == "pending"


def test_luma_dreaming_is_pending():
    assert normalize_webhook({"id": "abc", "state": "dreaming"}).state == "pending"


# ── 웹훅 정규화: fal / Wan ──────────────────────────────────────────────────


def test_fal_completed_normalizes_to_same_shape():
    o = normalize_webhook(
        {
            "request_id": "fal-1",
            "status": "OK",
            "payload": {"video": {"url": "https://v/b.mp4"}},
            "error": None,
        }
    )
    assert o.external_id == "fal-1"
    assert o.state == "completed"
    assert o.video_url == "https://v/b.mp4"
    assert o.error is None
    assert o.is_completed


def test_fal_error_normalizes():
    o = normalize_webhook({"request_id": "fal-2", "status": "ERROR", "error": "boom"})
    assert o.state == "failed" and o.error == "boom" and o.is_failed


def test_fal_in_progress_is_pending():
    o = normalize_webhook({"request_id": "fal-3", "status": "IN_PROGRESS"})
    assert o.state == "pending"


def test_fal_ok_without_video_is_pending():
    o = normalize_webhook({"request_id": "fal-4", "status": "OK", "payload": {}})
    assert o.state == "pending"


def test_luma_and_fal_produce_identical_downstream_fields():
    """두 프로바이더의 완료 본문이 같은 (state, video_url) 로 수렴해야 한다."""
    luma = normalize_webhook({"id": "x", "state": "completed", "assets": {"video": "u"}})
    fal = normalize_webhook(
        {"request_id": "x", "status": "OK", "payload": {"video": {"url": "u"}}}
    )
    assert (luma.state, luma.video_url) == (fal.state, fal.video_url)
    assert luma.external_id == fal.external_id


# ── 구분 불가 / 잘못된 본문 ─────────────────────────────────────────────────


@pytest.mark.parametrize("body", [{}, {"foo": "bar"}, {"state": "completed"}, [], "str", None])
def test_unrecognised_body_returns_none(body):
    assert normalize_webhook(body) is None


def test_discrimination_does_not_guess_from_id_format():
    """UUID 처럼 보여도 request_id 키가 있으면 fal 로 판정해야 한다."""
    o = normalize_webhook(
        {"request_id": "6c70fb46-3dfb-4aed-9f6b-0b38dcc2a72c", "status": "OK",
         "payload": {"video": {"url": "u"}}}
    )
    assert o.provider == PROVIDER_WAN_TURBO


# ── credit_luma_batch 가 중립 제출을 쓰는가 ─────────────────────────────────


def test_batch_uses_neutral_submit_and_records_provider(monkeypatch):
    from backend.services import credit_luma_batch as batch

    recorded: list[dict] = []

    async def fake_submit(image_url, prompt, *, provider, callback_url=None, **kw):
        return SubmittedJob(provider=provider, external_id=f"id-{provider}", model="M")

    async def fake_register(session_id, user_id, pet_id, place_key, action_id, external_id,
                            *, provider="luma", provider_model=None):
        recorded.append({"action": action_id, "provider": provider,
                         "external_id": external_id, "model": provider_model})

    monkeypatch.setattr(batch, "submit_generation", fake_submit)
    monkeypatch.setattr(batch.motions_svc, "register_generation_job", fake_register)
    monkeypatch.setenv("VIDEO_PROVIDER_VOICE", "wan_turbo")

    ok, errors = asyncio.run(
        batch.submit_place_motion_set(
            session_id="s", user_id="u", pet_id="p", place_key="01_snow_forest",
            pet_image_url="http://img", webhook_base_url="http://hook",
        )
    )
    assert ok == 4 and errors == []
    by_action = {r["action"]: r for r in recorded}
    assert by_action["VOICE"]["provider"] == "wan_turbo"
    for a in ("IDLE", "TOUCH", "NFC"):
        assert by_action[a]["provider"] == "luma", f"{a} 는 기본값 luma 여야 한다"
    assert all(r["model"] == "M" for r in recorded)


def test_batch_defaults_all_four_to_luma(monkeypatch):
    from backend.services import credit_luma_batch as batch

    seen: list[str] = []

    async def fake_submit(image_url, prompt, *, provider, callback_url=None, **kw):
        seen.append(provider)
        return SubmittedJob(provider=provider, external_id="i", model="m")

    async def fake_register(*a, **k):
        return None

    monkeypatch.setattr(batch, "submit_generation", fake_submit)
    monkeypatch.setattr(batch.motions_svc, "register_generation_job", fake_register)
    asyncio.run(
        batch.submit_place_motion_set(
            session_id="s", user_id="u", pet_id="p", place_key="01_snow_forest",
            pet_image_url="http://img", webhook_base_url="http://hook",
        )
    )
    assert seen == ["luma"] * 4, "설정이 없으면 기존과 100% 동일해야 한다"


# ── 하위호환 ────────────────────────────────────────────────────────────────


def test_register_luma_job_alias_still_exists():
    from backend.services import generated_motions_service as gms

    assert hasattr(gms, "register_luma_job")
    assert hasattr(gms, "register_generation_job")


def test_both_webhook_routes_registered():
    from backend.routers.pet_v1 import router

    paths = {r.path for r in router.routes}
    assert "/v1/pet/generation-webhook" in paths
    assert "/v1/pet/luma-webhook" in paths, "기존 콜백 URL 이 살아 있어야 한다"


def test_system_a_entry_point_unchanged():
    """create_generation_and_get_video_url 은 이 작업에서 건드리지 않았다."""
    assert callable(vg.create_generation_and_get_video_url)
    assert callable(vg.get_video_provider)
    assert callable(vg.is_luma)


# ── 신규 제출은 중립 엔드포인트를 쓴다 (별칭은 수신만 담당) ──────────────────


def test_new_submissions_target_the_neutral_webhook():
    """
    fal/Luma 모두 새 제출은 /generation-webhook 으로 콜백해야 한다.
    /luma-webhook 은 이미 등록된 예전 콜백을 받아 주기만 하는 별칭이다.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "routers" / "pet_v1.py"
    body = src.read_text(encoding="utf-8")
    assert 'webhook_url = f"{api_base}/api/v1/pet/generation-webhook"' in body
    # 제출 URL 조립에 legacy 경로가 남아 있으면 안 된다.
    assert 'webhook_url = f"{api_base}/api/v1/pet/luma-webhook"' not in body


def test_response_webhook_path_points_at_neutral_endpoint():
    from pathlib import Path

    svc = Path(__file__).resolve().parents[1] / "services" / "credit_generation_service.py"
    body = svc.read_text(encoding="utf-8")
    assert body.count('webhook_path="/api/v1/pet/generation-webhook"') == 2
    assert '"/api/v1/pet/luma-webhook"' not in body


def test_legacy_alias_route_still_accepts_callbacks():
    from backend.routers.pet_v1 import router

    paths = {r.path for r in router.routes}
    assert "/v1/pet/luma-webhook" in paths
    assert "/v1/pet/generation-webhook" in paths


# ── 개발 전용 액션 셀렉터 (DEV_ACTION_SUBSET) ────────────────────────────────


def test_action_subset_defaults_to_full_production_set(monkeypatch):
    from backend.scenarios.pet_scenarios import ACTION_ORDER
    from backend.services.credit_luma_batch import resolve_submit_actions

    monkeypatch.delenv("DEV_ACTION_SUBSET", raising=False)
    assert resolve_submit_actions() == ACTION_ORDER == ("IDLE", "TOUCH", "VOICE", "NFC")


@pytest.mark.parametrize("value", ["", "   ", None])
def test_blank_subset_is_production_default(monkeypatch, value):
    from backend.scenarios.pet_scenarios import ACTION_ORDER
    from backend.services.credit_luma_batch import resolve_submit_actions

    if value is None:
        monkeypatch.delenv("DEV_ACTION_SUBSET", raising=False)
    else:
        monkeypatch.setenv("DEV_ACTION_SUBSET", value)
    assert resolve_submit_actions() == ACTION_ORDER


def test_subset_narrows_submission(monkeypatch):
    from backend.services.credit_luma_batch import resolve_submit_actions

    monkeypatch.setenv("DEV_ACTION_SUBSET", "TOUCH")
    assert resolve_submit_actions() == ("TOUCH",)


def test_subset_preserves_action_order(monkeypatch):
    from backend.services.credit_luma_batch import resolve_submit_actions

    monkeypatch.setenv("DEV_ACTION_SUBSET", "nfc, idle")
    assert resolve_submit_actions() == ("IDLE", "NFC"), "ACTION_ORDER 순서를 유지해야 한다"


def test_unknown_values_do_not_break_and_fall_back(monkeypatch):
    from backend.scenarios.pet_scenarios import ACTION_ORDER
    from backend.services.credit_luma_batch import resolve_submit_actions

    monkeypatch.setenv("DEV_ACTION_SUBSET", "BOGUS,NOPE")
    assert resolve_submit_actions() == ACTION_ORDER, "유효값이 없으면 프로덕션 기본값"


def test_subset_never_changes_billing(monkeypatch):
    """핵심 안전 계약: 제출을 줄여도 과금은 그대로 4코인."""
    from backend.services.credit_luma_batch import credit_cost, resolve_submit_actions

    before = credit_cost()
    monkeypatch.setenv("DEV_ACTION_SUBSET", "TOUCH")
    assert resolve_submit_actions() == ("TOUCH",)
    assert credit_cost() == before == 4


def test_subset_logs_loudly_when_active(monkeypatch, caplog):
    import logging

    from backend.services.credit_luma_batch import resolve_submit_actions

    monkeypatch.setenv("DEV_ACTION_SUBSET", "TOUCH")
    with caplog.at_level(logging.WARNING):
        resolve_submit_actions()
    msg = caplog.text
    assert "TEST ISOLATION MODE" in msg
    assert "TOUCH" in msg


def test_no_log_when_disabled(monkeypatch, caplog):
    import logging

    from backend.services.credit_luma_batch import resolve_submit_actions

    monkeypatch.delenv("DEV_ACTION_SUBSET", raising=False)
    with caplog.at_level(logging.WARNING):
        resolve_submit_actions()
    assert "TEST ISOLATION MODE" not in caplog.text
