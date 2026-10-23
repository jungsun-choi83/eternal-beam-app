"""하이브리드 COME_CLOSER → RETURN_HOME 흐름 계약 테스트 (2026-10-22).

검증: COME_CLOSER 단일 시작 불변, RETURN_HOME 이 런별 파생 CLOSE_POSITION 을
**그대로** 시작 이미지로 쓰고 STAND_READY 에서 끝나는 START_END, 의존성/폴백 규칙,
무관 모션 불변.
"""

from __future__ import annotations

import io

import anyio
import pytest
from PIL import Image

from backend.services import motion_delivery_service as mds
from backend.services import motion_spec as ms
from backend.services import motion_video_service as mv
from backend.services import premium_motion_finalization as pmf
from backend.services import premium_run_fulfillment as prf
from backend.services import pet_generation_run_service as runs
from backend.services import supabase_assets

from .test_motion_video_generation import (
    FakeVideoProvider,
    _mock_backend,  # noqa: F401  (autouse fixture)
    _prepare_pipeline,
    conformance_ok,
    sampler_identical,
    storage,  # noqa: F401  (fixture)
)
from .test_pet_reference_sets import PET, USER


def _run(coro):
    return anyio.run(lambda: coro)


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (130, 90, 60)).save(buf, format="PNG")
    return buf.getvalue()


# ── 1. COME_CLOSER 불변 (단일 시작 I2V, 모션 레퍼런스/END 이미지 없음) ─────────
# TEMP (2026-10-22): DOG_APPROACH 모션 레퍼런스를 일시 제거 — COME_CLOSER 는 단일
# 시작 I2V 다 (시작 이미지 STAND_READY, 엔드 이미지 없음, 모션 레퍼런스 없음).
# RETURN_HOME·파생 CLOSE_POSITION 흐름은 아래 테스트들이 그대로 지킨다.
def test_come_closer_remains_single_start():
    cc = ms.MOTIONS["COME_CLOSER"]
    assert cc.preferred_video_strategy == ms.STRATEGY_I2V
    assert cc.fallback_video_strategy is None
    assert cc.motion_reference_id is None
    assert cc.motion_reference_policy == ms.REF_NONE
    assert cc.start_keyframe_role == "STAND_READY"
    assert cc.target_keyframe_role is None          # END 조건 키프레임 없음
    assert cc.requires_target_keyframe is False
    assert cc.start_from_derived is None             # START_END 복원 아님


# ── 2. RETURN_HOME 스펙: 시스템 소유 무료 + 파생 시작 START_END ───────────────
def test_return_home_spec_shape():
    rh = ms.MOTIONS["RETURN_HOME"]
    assert rh.motion_class == ms.CLASS_LOCOMOTION
    assert rh.preferred_video_strategy == ms.STRATEGY_START_END
    assert rh.start_from_derived == "close_position"
    assert rh.start_keyframe_role is None            # 역할 시작 아님
    assert rh.target_keyframe_role == "STAND_READY"  # 끝 = 홈
    assert rh.requires_target_keyframe is True
    assert "RETURN_HOME" in mds.SYSTEM_MOTIONS        # 무료 시스템
    assert "RETURN_HOME" not in pmf.PREMIUM_MOTIONS   # 상용 아님(판매 불가)


# ── 3. resolve: 파생 시작 override → 시작 키프레임이 그 파생 자산 ─────────────
def test_resolve_builds_derived_start_from_override(monkeypatch, storage):
    _prepare_pipeline(monkeypatch, storage)  # STAND_READY 승인
    derived_path = f"{USER}/{PET[4:]}/derived/close_position_run1.png"
    override = {"bucket": supabase_assets.BUCKET, "object_path": derived_path}
    contract = _run(ms.resolve_video_generation_spec(
        user_id=USER, pet_id=PET, motion_id="RETURN_HOME", start_image_override=override,
    ))
    sk = contract["start_keyframe"]
    assert sk["derived"] is True
    assert sk["raw"]["object_path"] == derived_path
    assert sk["plate"]["object_path"] == derived_path   # 하류 생성 입력도 같은 파생
    assert contract["video_strategy"] == "START_END_FRAME"
    assert contract["target_keyframe"]["role"] == "STAND_READY"


def test_resolve_without_override_raises(monkeypatch, storage):
    _prepare_pipeline(monkeypatch, storage)
    with pytest.raises(ms.MotionSpecError) as e:
        _run(ms.resolve_video_generation_spec(
            user_id=USER, pet_id=PET, motion_id="RETURN_HOME",
        ))
    assert e.value.code == "DERIVED_START_REQUIRED"


# ── 4. build: RETURN_HOME 이 **정확히 그 파생 프레임**을 시작 이미지로 쓴다 ────
def test_build_uses_exact_derived_frame_as_start(monkeypatch, storage):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    derived_path = f"{USER}/{PET[4:]}/derived/close_position_run1.png"
    derived_bytes = _png_bytes()
    storage[derived_path] = derived_bytes
    override = {"bucket": supabase_assets.BUCKET, "object_path": derived_path}
    contract = _run(ms.resolve_video_generation_spec(
        user_id=USER, pet_id=PET, motion_id="RETURN_HOME", start_image_override=override,
    ))

    def fetch(ref):
        op = getattr(ref, "object_path", None)
        if op in storage:
            return storage[op]
        return h.kf_fetch(ref)

    provider = FakeVideoProvider("kling", [_png_bytes()], end_frame=True)
    _run(mv.build_motion_video(
        user_id=USER, pet_id=PET, motion_id="RETURN_HOME",
        fetch_bytes=fetch, providers=[provider], frame_sampler=sampler_identical,
        conformance_fn=conformance_ok, precomputed_contract=contract,
    ))
    assert provider.requests, "provider 가 호출되지 않았다"
    req = provider.requests[0]
    # 시작 이미지 = 파생 프레임 바이트 **그대로**(클린 플레이트/편집 없음).
    assert req.start_image_bytes == derived_bytes
    # START_END — 끝(STAND_READY) 이미지가 실린다.
    assert req.end_image_bytes is not None


# ── 5. ensure_system_companion: 무료·override 전달 ───────────────────────────
def test_ensure_system_companion_is_free_and_threads_override(monkeypatch):
    captured = {}

    async def fake_start(**kw):
        captured.update(kw)
        from types import SimpleNamespace
        return SimpleNamespace(id="rh-run-1", status=runs.STATUS_QUEUED)

    monkeypatch.setattr(runs, "start_generation_run", fake_start)
    override = {"bucket": "b", "object_path": "p", "reference_id": "ref1"}
    rid = _run(prf.ensure_system_companion(
        user_id="u", pet_id="pet_c1", action_id="COME_CLOSER", source_run_id="cc-run", start_image_override=override,
    ))
    assert rid == "rh-run-1"
    assert captured["motion_id"] == "RETURN_HOME"
    assert captured["idempotency_key"] == "system-companion:RETURN_HOME:pet_c1:cc-run"
    assert captured["request_kind"] == runs.REQUEST_FREE_HOME
    assert captured["credits_reserved"] == 0          # 무료 (COME_CLOSER 과금과 무관)
    assert captured.get("product_key") in (None, "")  # 상용 아님
    assert captured["provider_state_extra"] == {"close_position_override": override}


def test_no_companion_for_non_come_closer(monkeypatch):
    called = {"n": 0}

    async def fake_start(**kw):
        called["n"] += 1
        from types import SimpleNamespace
        return SimpleNamespace(id="x")

    monkeypatch.setattr(runs, "start_generation_run", fake_start)
    # RETURN_HOME 자신/BREATHING 은 동반이 없다 → 동반의 동반 무한 연쇄 없음.
    assert _run(prf.ensure_system_companion(user_id="u", pet_id="p", action_id="RETURN_HOME", source_run_id="run")) is None
    assert _run(prf.ensure_system_companion(user_id="u", pet_id="p", action_id="BREATHING", source_run_id="run")) is None
    assert called["n"] == 0


# ── 6. 의존성 규칙: 종단 프레임 유무가 RETURN_HOME 큐잉을 가른다 ──────────────
def test_enqueue_only_when_terminal_frame_derived(monkeypatch):
    from types import SimpleNamespace

    enq = {"calls": []}

    async def fake_ensure(*, user_id, pet_id, action_id, source_run_id, start_image_override=None):
        assert source_run_id == "cc-run"
        enq["calls"].append(start_image_override)
        return "rh-1"

    monkeypatch.setattr(prf, "ensure_system_companion", fake_ensure)

    run = SimpleNamespace(
        motion_id="COME_CLOSER", user_id="u", pet_id="pet_c1", content_id="c1",
        selected_candidate_id="cand", motion_version_id="mv", id="cc-run",
    )

    # (a) 쓸 만한 종단 프레임 없음 → 큐잉 안 함.
    async def derive_none(_run):
        return None
    monkeypatch.setattr(runs, "_derive_close_position", derive_none)
    _run(runs._maybe_enqueue_system_companion(run))
    assert enq["calls"] == []

    # (b) 파생 성공 → override 와 함께 큐잉.
    override = {"bucket": "b", "object_path": "p", "reference_id": "r"}
    async def derive_ok(_run):
        return override
    monkeypatch.setattr(runs, "_derive_close_position", derive_ok)
    _run(runs._maybe_enqueue_system_companion(run))
    assert enq["calls"] == [override]

    # (c) COME_CLOSER 가 아니면 파생/큐잉 자체가 없다.
    enq["calls"].clear()
    _run(runs._maybe_enqueue_system_companion(
        SimpleNamespace(motion_id="BREATHING", user_id="u", pet_id="p", content_id="c1",
                        selected_candidate_id="x", motion_version_id="y", id="z")
    ))
    assert enq["calls"] == []


def test_enqueue_failure_never_raises(monkeypatch):
    """RETURN_HOME 파생/큐잉이 터져도 예외가 전파되지 않는다 — 이미 발행된
    COME_CLOSER 를 깨면 안 된다."""
    from types import SimpleNamespace

    async def derive_boom(_run):
        raise RuntimeError("boom")
    monkeypatch.setattr(runs, "_derive_close_position", derive_boom)

    run = SimpleNamespace(
        motion_id="COME_CLOSER", user_id="u", pet_id="pet_c1", content_id="c1",
        selected_candidate_id="cand", motion_version_id="mv", id="cc-run",
    )
    _run(runs._maybe_enqueue_system_companion(run))  # 예외 없이 통과 (best-effort)


# ── 7. 무관 모션 불변 ────────────────────────────────────────────────────────
def test_unrelated_motions_unchanged():
    br = ms.MOTIONS["BREATHING"]
    assert br.start_keyframe_role == "STAND_READY" and br.start_from_derived is None
    assert "BREATHING" in mds.SYSTEM_MOTIONS  # 기존 무료 홈 모션 그대로
    # COME_CLOSER 는 여전히 상용(판매) 모션이다.
    assert "COME_CLOSER" in pmf.PREMIUM_MOTIONS
