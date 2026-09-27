"""
정본/키프레임 이미지 프로바이더 순서 — env 로만 바뀐다.

여기서 검증하는 것은 **순서 결정**뿐이다. 실 어댑터는 호출되지 않는다(레지스트리
싱글턴의 이름만 읽는다) — 테스트가 유료 생성을 일으킬 경로가 없다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services import canonical_image_providers as providers_mod

PROVIDER_ENV = (
    "CANONICAL_IMAGE_PROVIDER",
    "CANONICAL_IMAGE_FALLBACK_PROVIDER",
    "KEYFRAME_IMAGE_PROVIDER",
    "KEYFRAME_IMAGE_FALLBACK_PROVIDER",
    "CANONICAL_GENERATION_MOCK",
    "KEYFRAME_GENERATION_MOCK",
)


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch: pytest.MonkeyPatch):
    """로컬 .env 가 새어 들어와 순서 단언을 흔들지 못하게 한다."""
    for key in PROVIDER_ENV:
        monkeypatch.delenv(key, raising=False)
    yield


def _names(providers) -> list[str]:
    return [p.name for p in providers]


# ══════════════════════════════════════════════════════════════════════════
# 레지스트리 — id 는 서로 구별된다 (runway 를 gpt 로 별칭하지 않는다)
# ══════════════════════════════════════════════════════════════════════════


def test_provider_ids_stay_distinct():
    assert providers_mod.get_provider("gpt_image").name == "gpt_image"
    assert providers_mod.get_provider("runway").name == "runway"
    assert providers_mod.get_provider("mock").name == "mock"
    assert providers_mod.get_provider("gpt_image") is not providers_mod.get_provider("runway")


# ══════════════════════════════════════════════════════════════════════════
# 정본 순서
# ══════════════════════════════════════════════════════════════════════════


def test_canonical_defaults_to_gpt_primary_without_env():
    assert _names(providers_mod.resolve_providers()) == ["gpt_image", "runway"]


def test_canonical_gpt_primary_runway_fallback_from_env(monkeypatch):
    monkeypatch.setenv("CANONICAL_IMAGE_PROVIDER", "gpt_image")
    monkeypatch.setenv("CANONICAL_IMAGE_FALLBACK_PROVIDER", "runway")
    assert _names(providers_mod.resolve_providers()) == ["gpt_image", "runway"]


def test_canonical_order_flips_back_from_env(monkeypatch):
    monkeypatch.setenv("CANONICAL_IMAGE_PROVIDER", "runway")
    monkeypatch.setenv("CANONICAL_IMAGE_FALLBACK_PROVIDER", "gpt_image")
    assert _names(providers_mod.resolve_providers()) == ["runway", "gpt_image"]


# ══════════════════════════════════════════════════════════════════════════
# 키프레임 순서 — 정본과 독립
# ══════════════════════════════════════════════════════════════════════════


def test_keyframe_gpt_primary_runway_fallback_from_env(monkeypatch):
    monkeypatch.setenv("KEYFRAME_IMAGE_PROVIDER", "gpt_image")
    monkeypatch.setenv("KEYFRAME_IMAGE_FALLBACK_PROVIDER", "runway")
    assert _names(providers_mod.resolve_keyframe_providers()) == ["gpt_image", "runway"]


def test_keyframe_order_is_independent_of_canonical(monkeypatch):
    monkeypatch.setenv("CANONICAL_IMAGE_PROVIDER", "gpt_image")
    monkeypatch.setenv("CANONICAL_IMAGE_FALLBACK_PROVIDER", "runway")
    monkeypatch.setenv("KEYFRAME_IMAGE_PROVIDER", "runway")
    monkeypatch.setenv("KEYFRAME_IMAGE_FALLBACK_PROVIDER", "gpt_image")
    assert _names(providers_mod.resolve_providers()) == ["gpt_image", "runway"]
    assert _names(providers_mod.resolve_keyframe_providers()) == ["runway", "gpt_image"]


def test_keyframe_inherits_canonical_when_unset(monkeypatch):
    """키프레임 변수를 안 쓰던 배포는 동작이 그대로다."""
    monkeypatch.setenv("CANONICAL_IMAGE_PROVIDER", "gpt_image")
    monkeypatch.setenv("CANONICAL_IMAGE_FALLBACK_PROVIDER", "runway")
    assert _names(providers_mod.resolve_keyframe_providers()) == ["gpt_image", "runway"]


def test_keyframe_defaults_to_gpt_primary_without_env():
    assert _names(providers_mod.resolve_keyframe_providers()) == ["gpt_image", "runway"]


# ══════════════════════════════════════════════════════════════════════════
# 폴백 경로 / 중복 시도 / 알 수 없는 id
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("resolve", ["resolve_providers", "resolve_keyframe_providers"])
def test_same_primary_and_fallback_runs_once(monkeypatch, resolve):
    """같은 프로바이더를 두 번 태우지 않는다 — 폴백은 추가 과금이다."""
    for key in ("CANONICAL_IMAGE_PROVIDER", "KEYFRAME_IMAGE_PROVIDER"):
        monkeypatch.setenv(key, "gpt_image")
    for key in ("CANONICAL_IMAGE_FALLBACK_PROVIDER", "KEYFRAME_IMAGE_FALLBACK_PROVIDER"):
        monkeypatch.setenv(key, "gpt_image")
    assert _names(getattr(providers_mod, resolve)()) == ["gpt_image"]


@pytest.mark.parametrize("resolve", ["resolve_providers", "resolve_keyframe_providers"])
def test_unknown_primary_falls_through_without_aliasing(monkeypatch, resolve):
    for key in ("CANONICAL_IMAGE_PROVIDER", "KEYFRAME_IMAGE_PROVIDER"):
        monkeypatch.setenv(key, "not_a_provider")
    for key in ("CANONICAL_IMAGE_FALLBACK_PROVIDER", "KEYFRAME_IMAGE_FALLBACK_PROVIDER"):
        monkeypatch.setenv(key, "runway")
    assert _names(getattr(providers_mod, resolve)()) == ["runway"]


@pytest.mark.parametrize("resolve", ["resolve_providers", "resolve_keyframe_providers"])
def test_all_unknown_fails_closed(monkeypatch, resolve):
    """빈 목록 → 호출부가 과금 전 PROVIDER_NOT_CONFIGURED(503) 로 닫는다."""
    for key in PROVIDER_ENV[:4]:
        monkeypatch.setenv(key, "nope")
    assert getattr(providers_mod, resolve)() == []


# ══════════════════════════════════════════════════════════════════════════
# mock 모드
# ══════════════════════════════════════════════════════════════════════════


def test_mock_mode_overrides_both(monkeypatch):
    monkeypatch.setenv("CANONICAL_IMAGE_PROVIDER", "gpt_image")
    monkeypatch.setenv("KEYFRAME_IMAGE_PROVIDER", "gpt_image")
    monkeypatch.setenv("CANONICAL_GENERATION_MOCK", "1")
    assert _names(providers_mod.resolve_providers()) == ["mock"]
    assert _names(providers_mod.resolve_keyframe_providers()) == ["mock"]


def test_keyframe_mock_does_not_leak_into_canonical(monkeypatch):
    monkeypatch.setenv("KEYFRAME_GENERATION_MOCK", "1")
    assert _names(providers_mod.resolve_keyframe_providers()) == ["mock"]
    assert _names(providers_mod.resolve_providers()) == ["gpt_image", "runway"]


# ══════════════════════════════════════════════════════════════════════════
# 실행 경로 배선 — 어떤 리졸버가 어디에 붙었나
# ══════════════════════════════════════════════════════════════════════════


def test_keyframe_builder_uses_keyframe_resolver(monkeypatch):
    """action_keyframe_service 는 정본이 아니라 키프레임 리졸버를 부른다."""
    import inspect

    from backend.services import action_keyframe_service

    src = inspect.getsource(action_keyframe_service.build_keyframe)
    assert "resolve_keyframe_providers()" in src
    assert "canonical_image_providers.resolve_providers()" not in src


def test_generation_run_routes_by_operation(monkeypatch):
    from backend.services import durable_provider_jobs, pet_generation_run_service as runs

    calls: list[str] = []
    monkeypatch.setattr(
        runs.canonical_image_providers, "resolve_providers",
        lambda: (calls.append("canonical"), [])[1],
    )
    monkeypatch.setattr(
        runs.canonical_image_providers, "resolve_keyframe_providers",
        lambda: (calls.append("keyframe"), [])[1],
    )
    monkeypatch.setattr(runs.durable_provider_jobs, "durable_image_providers", lambda *a, **k: ["ok"])

    run = SimpleNamespace(id="run_1", user_id="u", pet_id="pet_1")
    runs._image_providers(run, durable_provider_jobs.OP_CANONICAL)
    runs._image_providers(run, durable_provider_jobs.OP_KEYFRAME)
    assert calls == ["canonical", "keyframe"]


def test_motion_video_routing_untouched_by_image_env(monkeypatch):
    """모션 영상 라우팅은 이 변수들을 읽지 않는다."""
    from backend.services import video_motion_providers as vp

    before = [p.name for p in vp.routing_for_class("BREATHING")]
    monkeypatch.setenv("CANONICAL_IMAGE_PROVIDER", "gpt_image")
    monkeypatch.setenv("KEYFRAME_IMAGE_PROVIDER", "gpt_image")
    monkeypatch.setenv("CANONICAL_IMAGE_FALLBACK_PROVIDER", "runway")
    monkeypatch.setenv("KEYFRAME_IMAGE_FALLBACK_PROVIDER", "runway")
    assert [p.name for p in vp.routing_for_class("BREATHING")] == before
