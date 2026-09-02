"""
모션 스펙 + Phase 6 계약 리졸버 (Phase 5.1) 테스트.

리졸버는 읽기 전용이다 — 이미지/영상 프로바이더 호출이 없음을 명시적으로 검증한다.
"""

from __future__ import annotations

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import keyframes_v1
from backend.scenarios.pet_scenarios import ACTION_ORDER, IDLE_EVENTS, PET_ACTIONS
from backend.services import action_keyframe_service as kf
from backend.services import action_keyframe_spec as kf_spec
from backend.services import canonical_pet_service as canon
from backend.services import motion_spec as ms
from backend.services import pet_identity_service as ids
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry

from .conftest import ASGITestClient
from .test_canonical_pet_builder import GOOD, FakeProvider
from .test_action_keyframes import (
    VLM_KF_OK,
    _build_kf,
    _prepare_canonical,
    install_kf_vlm,
)
from .test_pet_reference_sets import PET, USER


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    monkeypatch.setenv("CANONICAL_QA_MIN_RESOLUTION", "100")
    for m in (refs, pet_registry, ids, sets, canon, kf):
        m.__reset_for_tests()
    yield
    for m in (refs, pet_registry, ids, sets, canon, kf):
        m.__reset_for_tests()


@pytest.fixture
def storage(monkeypatch) -> dict[str, bytes]:
    from backend.services import supabase_assets

    store: dict[str, bytes] = {}

    async def fake_upload(path, data, content_type):
        store[path] = bytes(data)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    return store


def _run(coro):
    return anyio.run(lambda: coro)


def _resolve(motion_id: str):
    return _run(
        ms.resolve_video_generation_spec(user_id=USER, pet_id=PET, motion_id=motion_id)
    )


# ══════════════════════════════════════════════════════════════════════════
# 레지스트리 무결성
# ══════════════════════════════════════════════════════════════════════════


def test_every_motion_has_exactly_one_valid_class():
    for m in ms.MOTIONS.values():
        assert m.motion_class in ms.MOTION_CLASSES
    assert len(ms.MOTION_ORDER) == len(set(ms.MOTION_ORDER))  # 중복 모션 id 없음


def test_triggers_are_not_motions_and_resolve_to_motions():
    assert set(ms.TRIGGERS) & set(ms.MOTIONS) == set()
    # 레거시 트리거 전부가 해석된다 — TOUCH/VOICE/NFC/IDLE 은 몸의 움직임이 아니다.
    assert set(ms.TRIGGERS) == set(ACTION_ORDER)
    for trigger, motion in ms.TRIGGERS.items():
        assert motion in ms.MOTIONS
    assert ms.motion_for_trigger("TOUCH") == "PET_HEAD"
    assert ms.motion_for_trigger("IDLE") == "BREATHING"


def test_existing_runtime_registry_integrity():
    # 기존 런타임 모션은 전부, 정확히 같은 id 로 등록돼 있다.
    for aid in tuple(IDLE_EVENTS) + tuple(PET_ACTIONS) + ("BREATHING",):
        assert aid in ms.MOTIONS, aid
    # Phase 5 키프레임 매핑과 모순되지 않는다: 기존 모션의 시작 역할은
    # role_for_action 이 말하는 역할과 같다.
    for aid in tuple(IDLE_EVENTS) + tuple(PET_ACTIONS) + ("BREATHING",):
        assert ms.MOTIONS[aid].start_keyframe_role == kf_spec.role_for_action(aid)


def test_all_roles_exist_and_keyframes_are_reused():
    for m in ms.MOTIONS.values():
        assert m.start_keyframe_role in kf_spec.KEYFRAME_ROLES
        if m.target_keyframe_role:
            assert m.target_keyframe_role in kf_spec.KEYFRAME_ROLES
    # 하나의 키프레임이 여러 모션을 감당한다 — 불필요한 스틸 생성 방지.
    assert len(ms.motions_for_keyframe_role("NEUTRAL_IDLE")) >= 5
    lie_users = ms.motions_for_keyframe_role("LIE")
    assert {"LIE_IDLE", "LIE_DOWN", "STAND_UP", "FALL_ASLEEP"} <= set(lie_users)


def test_transitions_declare_explicit_start_target_pairs():
    pairs = {
        "LIE_DOWN": ("NEUTRAL_IDLE", "LIE"),
        "STAND_UP": ("LIE", "NEUTRAL_IDLE"),
        "FALL_ASLEEP": ("LIE", "SLEEP"),
        "WAKE_UP": ("SLEEP", "LIE"),
    }
    for mid, (start, target) in pairs.items():
        spec = ms.MOTIONS[mid]
        assert spec.motion_class == ms.CLASS_TRANSITION
        assert (spec.start_keyframe_role, spec.target_keyframe_role) == (start, target)
        assert spec.requires_target_keyframe is True


def test_interaction_does_not_require_human_in_keyframe():
    spec = ms.MOTIONS["PET_HEAD"]
    assert spec.motion_class == ms.CLASS_INTERACTION
    assert spec.start_keyframe_role == "NEUTRAL_IDLE"
    assert spec.video_compat["requires_human_in_keyframe"] is False


def test_locomotion_exposes_motion_reference_metadata():
    for mid in ("COME_CLOSER", "RUN"):
        spec = ms.MOTIONS[mid]
        assert spec.motion_class == ms.CLASS_LOCOMOTION
        assert spec.motion_reference_id
        assert spec.motion_reference_policy == ms.REF_PREFERRED
        assert spec.fallback_video_strategy == ms.STRATEGY_I2V


# ══════════════════════════════════════════════════════════════════════════
# 리졸버 — Phase 6 계약
# ══════════════════════════════════════════════════════════════════════════


def _prepare_keyframes(monkeypatch, storage, roles=("NEUTRAL_IDLE",)):
    h, canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    built = {}
    for role in roles:
        built[role] = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])], role=role)
        assert built[role].status == kf.STATUS_COMPLETE
    return h, canonical, built


def test_micro_resolves_single_reusable_keyframe(storage, monkeypatch):
    _, canonical, built = _prepare_keyframes(monkeypatch, storage)

    breath = _resolve("BREATHING")
    blink = _resolve("BLINKING")

    assert breath["contract_version"] == ms.PHASE6_CONTRACT_VERSION
    assert breath["motion_class"] == "MICRO"
    assert breath["video_strategy"] == ms.STRATEGY_I2V
    assert breath["target_keyframe"] is None
    assert breath["loopable"] is True and blink["loopable"] is False
    # 두 모션이 **같은** 키프레임을 재사용한다.
    assert breath["start_keyframe"]["keyframe_id"] == built["NEUTRAL_IDLE"].id
    assert blink["start_keyframe"]["keyframe_id"] == built["NEUTRAL_IDLE"].id
    assert breath["start_keyframe"]["raw"]["object_path"]
    assert breath["canonical_version_id"] == canonical.id


def test_transition_resolves_start_and_target(storage, monkeypatch):
    _, _, built = _prepare_keyframes(monkeypatch, storage, roles=("NEUTRAL_IDLE", "LIE"))

    spec = _resolve("LIE_DOWN")
    assert spec["motion_class"] == "TRANSITION"
    assert spec["video_strategy"] == ms.STRATEGY_START_END
    assert spec["start_keyframe"]["role"] == "NEUTRAL_IDLE"
    assert spec["target_keyframe"]["role"] == "LIE"
    assert spec["target_keyframe"]["keyframe_id"] == built["LIE"].id
    assert spec["loopable"] is False


def test_transition_missing_target_fails_safely(storage, monkeypatch):
    _prepare_keyframes(monkeypatch, storage)  # NEUTRAL_IDLE 만
    with pytest.raises(ms.MotionSpecError) as e:
        _resolve("LIE_DOWN")
    assert e.value.code == "TARGET_KEYFRAME_REQUIRED" and e.value.status == 409


def test_missing_start_keyframe_fails_safely(storage, monkeypatch):
    _prepare_canonical(monkeypatch, storage)  # 키프레임 없음
    with pytest.raises(ms.MotionSpecError) as e:
        _resolve("BREATHING")
    assert e.value.code == "KEYFRAME_REQUIRED" and e.value.status == 409


def test_review_keyframe_is_not_silently_used(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, None)  # VLM 없음 → 키프레임 REVIEW
    k = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])])
    assert k.status == kf.STATUS_REVIEW

    with pytest.raises(ms.MotionSpecError) as e:
        _resolve("BREATHING")
    assert e.value.code == "KEYFRAME_REQUIRED"


def test_locomotion_falls_back_with_warning(storage, monkeypatch):
    _prepare_keyframes(monkeypatch, storage)
    spec = _resolve("COME_CLOSER")
    assert spec["motion_reference"] == {"id": "DOG_APPROACH", "policy": "preferred", "asset": None}
    assert spec["video_strategy"] == ms.STRATEGY_I2V  # 라이브러리 없음 → 폴백
    assert any("DOG_APPROACH" in w for w in spec["warnings"])


def test_unknown_motion_rejected(storage, monkeypatch):
    _prepare_keyframes(monkeypatch, storage)
    with pytest.raises(ms.MotionSpecError) as e:
        _resolve("MOONWALK")
    assert e.value.code == "UNKNOWN_MOTION" and e.value.status == 422


def test_resolver_is_deterministic_and_versioned(storage, monkeypatch):
    _prepare_keyframes(monkeypatch, storage)
    a = _resolve("BREATHING")
    b = _resolve("BREATHING")
    assert a == b
    assert a["motion_spec_version"] == ms.MOTION_SPEC_VERSION
    assert a["start_keyframe"]["version"] == 1


def test_resolver_makes_no_provider_calls(storage, monkeypatch):
    from backend.services import canonical_image_providers

    _prepare_keyframes(monkeypatch, storage)

    def boom():
        raise AssertionError("리졸버가 프로바이더를 건드렸다")

    monkeypatch.setattr(canonical_image_providers, "resolve_providers", boom)
    spec = _resolve("BREATHING")
    assert spec["motion_id"] == "BREATHING"


def test_ownership_isolation(storage, monkeypatch):
    _prepare_keyframes(monkeypatch, storage)
    with pytest.raises(ms.MotionSpecError) as e:
        _run(
            ms.resolve_video_generation_spec(
                user_id="mallory@test", pet_id=PET, motion_id="BREATHING"
            )
        )
    assert e.value.code == "PET_NOT_OWNED"


# ══════════════════════════════════════════════════════════════════════════
# 라우터
# ══════════════════════════════════════════════════════════════════════════


AUTH = {"Authorization": "Bearer test:alice@test"}


@pytest.fixture
def client(monkeypatch) -> ASGITestClient:
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    app = FastAPI()
    app.include_router(keyframes_v1.router, prefix="/api")
    return ASGITestClient(app)


def test_router_lists_motions_and_triggers(client):
    res = client.get("/api/v1/pet/keyframes/motions", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["motion_spec_version"] == ms.MOTION_SPEC_VERSION
    ids_ = [m["motion_id"] for m in body["motions"]]
    assert ids_ == list(ms.MOTION_ORDER)
    assert body["triggers"]["VOICE"] == "LOOK_UP"


def test_router_resolves_spec(client, storage, monkeypatch):
    _prepare_keyframes(monkeypatch, storage, roles=("NEUTRAL_IDLE", "LIE"))
    res = client.get(f"/api/v1/pet/keyframes/{PET}/motions/LIE_DOWN/spec", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["video_strategy"] == "START_END_FRAME"
    assert body["target_keyframe"]["role"] == "LIE"

    res = client.get(f"/api/v1/pet/keyframes/{PET}/motions/WAKE_UP/spec", headers=AUTH)
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "KEYFRAME_REQUIRED"

    res = client.get(f"/api/v1/pet/keyframes/{PET}/motions/MOONWALK/spec", headers=AUTH)
    assert res.status_code == 422
