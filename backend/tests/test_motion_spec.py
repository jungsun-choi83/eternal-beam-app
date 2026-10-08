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
from backend.services import pet_morphology_service as morph
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry
from types import SimpleNamespace

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
    for m in (refs, pet_registry, ids, morph, sets, canon, kf):
        m.__reset_for_tests()
    yield
    for m in (refs, pet_registry, ids, morph, sets, canon, kf):
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


def test_authoritative_registry_requirements_exist_for_each_motion():
    for mid, m in ms.MOTIONS.items():
        assert m.motion_type
        req = m.requirements
        assert req["registry_contract_version"] == ms.MOTION_REGISTRY_CONTRACT_VERSION
        assert req["roles"]["required_start_roles"] == [m.start_keyframe_role]
        if m.requires_target_keyframe:
            assert req["roles"]["required_end_roles"] == [m.target_keyframe_role]
        else:
            assert req["roles"]["required_end_roles"] == []
        assert req["duration"]["range_sec"] == list(m.duration_range_sec)
        assert req["loopability"]["loopable"] is m.loopable
        assert req["interruptibility"]["interruptible"] is m.interruptible
        assert req["morphology"]["constraint_mode"] == "structural_only"
        assert "breed_specific_logic" in req["morphology"]["forbidden_logic"]
        assert "qa" in req and "motion_specific" in req["qa"], mid


def test_provider_capability_requirements_are_backward_compatible():
    # START_END_FRAME 는 end-frame capability 가 필수, 강등 없음.
    lie_down = ms.MOTIONS["LIE_DOWN"].requirements["provider_capabilities"]
    assert "supports_end_frame" in lie_down["required_all"]
    assert lie_down["degrade_allowed"] is False
    assert lie_down["degrade_to_strategy"] is None

    # motion-ref 선호 모션은 소비 capability 를 선호로 요구하고 I2V 폴백을 유지.
    run = ms.MOTIONS["RUN"].requirements["provider_capabilities"]
    assert "supports_motion_reference" in run["preferred_any"]
    assert run["degrade_allowed"] is True
    assert run["degrade_to_strategy"] == ms.STRATEGY_I2V


def test_registry_morphology_requirements_include_match_fields_and_confidence_floor():
    run = ms.MOTIONS["RUN"].requirements["morphology"]
    assert run["confidence_floor"] == "medium"
    assert "body_build_class" in run["match_fields"]

    pet_head = ms.MOTIONS["PET_HEAD"].requirements["morphology"]
    assert pet_head["match_fields"] == [
        "head_proportion_class",
        "muzzle_proportion_class",
        "ear_form",
    ]


def test_registry_qa_structural_domain_declares_morphology_consistency_check():
    run_qa = ms.MOTIONS["RUN"].requirements["qa"]
    structural = run_qa["structural_anatomy"]
    required = structural["required_checks"]
    assert "vlm_anatomy" in required
    assert "structural_morphology_consistency" in required
    cfg = structural["morphology_consistency"]
    assert cfg["check"] == "structural_morphology_consistency"
    assert "body_length_class" in cfg["compare_fields"]
    assert cfg["use_sampled_frames"] is True


@pytest.mark.parametrize(
    ("motion_id", "expected_domains", "absent_domains"),
    [
        (
            "BREATHING",
            {
                "identity_continuity", "temporal_stability", "motion_correctness",
                "global_motion_integrity", "composition_integrity",
                "severe_anatomy", "loop_usability",
            },
            {"structural_continuity", "direction_travel", "interaction_correctness"},
        ),
        (
            "LIE_DOWN",
            {"identity_continuity", "start_state", "target_pose", "motion_correctness", "anatomy", "structural_continuity"},
            {"direction_travel", "interaction_correctness"},
        ),
        (
            "RUN",
            {"identity_continuity", "motion_correctness", "direction_travel", "limb_joint_integrity", "structural_continuity"},
            {"target_pose", "interaction_correctness"},
        ),
        (
            "PET_HEAD",
            {"identity_continuity", "interaction_correctness", "anatomy", "structural_continuity", "human_hand_policy"},
            {"target_pose", "direction_travel"},
        ),
    ],
)
def test_registry_owns_business_qa_domains_by_motion_class(
    motion_id, expected_domains, absent_domains
):
    contract = ms.MOTIONS[motion_id].requirements["qa"]["business"]

    assert contract["version"] == ms.MOTION_QA_CONTRACT_VERSION
    assert contract["motion_class"] == ms.MOTIONS[motion_id].motion_class
    assert expected_domains <= set(contract["domains"])
    assert absent_domains.isdisjoint(contract["domains"])


def test_registry_owns_motion_check_authority_and_applicability():
    micro = ms.MOTIONS["BREATHING"].requirements["qa"]["business"]
    transition = ms.MOTIONS["LIE_DOWN"].requirements["qa"]["business"]
    locomotion = ms.MOTIONS["RUN"].requirements["qa"]["business"]
    interaction = ms.MOTIONS["PET_HEAD"].requirements["qa"]["business"]

    assert micro["structural_required"] is False
    assert micro["check_authority"]["temporal_stability"] == "QUALITY_ADVISORY"
    assert micro["check_authority"]["loop_return"] == "QUALITY_ADVISORY"
    assert micro["authority_profile"] == "breathing-v2"
    assert micro["domains"]["motion_correctness"]["required_checks"] == [
        "breathing_motion_correctness"
    ]
    assert micro["check_authority"]["vlm_motion"] == "DIAGNOSTIC_ONLY"
    assert micro["check_authority"]["temporal_breathing"] == "DIAGNOSTIC_ONLY"
    assert micro["check_authority"]["breathing_global_motion_integrity"] == "QUALITY_ADVISORY"
    assert (
        micro["check_authority"]["breathing_catastrophic_motion_integrity"]
        == "INTEGRITY_HARD"
    )
    assert transition["check_authority"]["reaches_target_pose"] == "INTEGRITY_HARD"
    assert locomotion["check_authority"]["vlm_direction_travel"] == "INTEGRITY_HARD"
    assert interaction["check_authority"]["vlm_human_hand_policy"] == "INTEGRITY_HARD"
    assert interaction["domains"]["human_hand_policy"]["allow_generated_hand"] is True

    # Runtime repeatability and QA endpoint return are distinct registry facts.
    # BLINKING is not an indefinitely loopable state, but it must return to the
    # start pose and therefore still receives loop-return QA.
    blinking = ms.MOTIONS["BLINKING"].requirements["qa"]["business"]
    assert ms.MOTIONS["BLINKING"].loopable is False
    assert blinking["loop_return_required"] is True
    assert blinking["domains"]["loop_usability"]["required_checks"] == ["loop_return"]


@pytest.mark.parametrize(
    "motion_id",
    ["PET_HEAD", "LOOK_UP", "COME_CLOSER", "LIE_DOWN"],
)
def test_registry_pose_dependent_morphology_is_advisory_for_every_motion_class(
    motion_id,
):
    structural = ms.MOTIONS[motion_id].requirements["qa"]["structural_anatomy"]
    cfg = structural["morphology_consistency"]

    assert cfg["pose_dependent_fields"] == [
        "body_length_class",
        "leg_length_class",
        "body_build_class",
    ]
    assert cfg["pose_dependent_policy"] == "review_never_fail"

    if motion_id != "LOOK_UP":
        assert "structural_morphology_consistency" in structural["required_checks"]


def test_motion_snapshot_includes_authoritative_registry_fields():
    snap = ms.motion_snapshot(ms.MOTIONS["BREATHING"])
    assert snap["registry_contract_version"] == ms.MOTION_REGISTRY_CONTRACT_VERSION
    assert snap["motion_type"] == ms.MOTIONS["BREATHING"].motion_type
    assert snap["requirements"]["qa"]["motion_specific"]["required_checks"]


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
    # v16: 그 하나가 STAND_READY(HOME) 다 — 홈/아이들/서기 액션 전부.
    assert len(ms.motions_for_keyframe_role("STAND_READY")) >= 10
    lie_users = ms.motions_for_keyframe_role("LIE")
    assert {"LIE_IDLE", "LIE_DOWN", "STAND_UP", "FALL_ASLEEP"} <= set(lie_users)


def test_transitions_declare_explicit_start_target_pairs():
    pairs = {
        "LIE_DOWN": ("STAND_READY", "LIE"),  # Phase 4: 눕기는 선 자세에서 시작
        "STAND_UP": ("LIE", "STAND_READY"),  # v16: 홈(STAND_READY)으로 복귀
        "FALL_ASLEEP": ("LIE", "SLEEP"),
        "WAKE_UP": ("SLEEP", "LIE"),
    }
    for mid, (start, target) in pairs.items():
        spec = ms.MOTIONS[mid]
        assert spec.motion_class == ms.CLASS_TRANSITION
        assert (spec.start_keyframe_role, spec.target_keyframe_role) == (start, target)
        assert spec.requires_target_keyframe is True


def test_sit_stand_bridges_repair_seated_home_seam():
    """앉은 홈 ↔ STAND_READY 브리지 (2026-09-10, Phase 4 이음매 수리).

    NEUTRAL_IDLE 은 정본 자세를 물려받아 앉아 있을 수 있다 — STAND_READY 시작
    모션으로의 진입/복귀는 이 두 전이가 잇는다. START_END 라 양 끝이 실제
    키프레임 이미지와 일치한다(포즈 일치 하드 컷). 수요 기반: 서 있는 홈 펫은
    생성할 이유가 없다.
    """
    pairs = {
        "SIT_TO_STAND": ("NEUTRAL_IDLE", "STAND_READY"),
        "STAND_TO_SIT": ("STAND_READY", "NEUTRAL_IDLE"),
    }
    for mid, (start, target) in pairs.items():
        spec = ms.MOTIONS[mid]
        assert spec.motion_class == ms.CLASS_TRANSITION
        assert (spec.start_keyframe_role, spec.target_keyframe_role) == (start, target)
        assert spec.requires_target_keyframe is True
        assert spec.preferred_video_strategy == ms.STRATEGY_START_END
        assert spec.loopable is False
    # STAND_UP 은 브리지가 필요 없다 — 끝 프레임이 곧 홈 키프레임 이미지라
    # 눕기→홈 복귀는 구성상 이음매가 없다. v16 부터 홈은 STAND_READY 다:
    # 목표가 홈 역할과 어긋나면 없던 이음매가 생긴다.
    assert (
        ms.MOTIONS["STAND_UP"].target_keyframe_role
        == ms.MOTIONS["BREATHING"].start_keyframe_role
        == "STAND_READY"
    )


def test_interaction_does_not_require_human_in_keyframe():
    spec = ms.MOTIONS["PET_HEAD"]
    assert spec.motion_class == ms.CLASS_INTERACTION
    assert spec.start_keyframe_role == "STAND_READY"
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


def _prepare_keyframes(monkeypatch, storage, roles=("STAND_READY",)):
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
    assert breath["start_keyframe"]["keyframe_id"] == built["STAND_READY"].id
    assert blink["start_keyframe"]["keyframe_id"] == built["STAND_READY"].id
    assert breath["start_keyframe"]["raw"]["object_path"]
    assert breath["canonical_version_id"] == canonical.id
    inherited = breath["start_keyframe"]["approved_qa_evidence"]
    assert inherited["valid"] is True
    assert inherited["source_stage"] == "KEYFRAME"
    assert {"identity", "pose", "morphology_profile", "reference_set"} <= set(
        inherited["domains"]
    )


HOME_FAMILY = (
    "BREATHING", "BLINKING", "EAR_TWITCHING", "HEAD_TILTING", "TAIL_WAGGING",
    "LOOK_UP", "PET_HEAD",
)


def test_home_family_and_standing_actions_start_from_stand_ready():
    """v16: HOME = STAND_READY. 홈·아이들·서기 액션이 전부 같은 역할에서 시작한다."""
    for mid in HOME_FAMILY + ("COME_CLOSER", "LIE_DOWN", "RUN", "WALK"):
        assert ms.MOTIONS[mid].start_keyframe_role == "STAND_READY", mid
    # 런타임 모션 중 NEUTRAL_IDLE 에서 시작/종료하는 것은 휴면 중인 앉기 전이뿐이다.
    assert set(ms.motions_for_keyframe_role("NEUTRAL_IDLE")) == {
        "SIT_TO_STAND", "STAND_TO_SIT", "SIT_DOWN",
    }
    assert ms.MOTIONS["BREATHING"].description == "Calm, natural resting breathing only."
    assert ms.MOTION_SPEC_VERSION == "motion-spec-v16"


def test_one_stand_ready_keyframe_serves_home_idles_and_come_closer(storage, monkeypatch):
    """홈/아이들/COME_CLOSER 가 **하나의** STAND_READY 키프레임을 공유한다 —
    모션마다 서기 스틸을 따로 만들지 않고, NEUTRAL_IDLE 은 필요 없다."""
    h, canonical, built = _prepare_keyframes(monkeypatch, storage, roles=("STAND_READY",))
    home = built["STAND_READY"]

    for mid in HOME_FAMILY + ("COME_CLOSER",):
        contract = _resolve(mid)
        assert contract["start_keyframe"]["role"] == "STAND_READY", mid
        assert contract["start_keyframe"]["keyframe_id"] == home.id, mid
        assert contract["start_keyframe"]["version"] == home.version, mid
        assert contract["canonical_version_id"] == canonical.id, mid

    # 다시 빌드를 요청해도 같은 버전이 재사용된다 — 유료 호출 0, 새 행 0.
    provider = FakeProvider("runway", [GOOD(), GOOD(), GOOD()])
    again = _build_kf(h, [provider], role="STAND_READY")
    assert again.id == home.id and again.version == home.version
    assert provider.calls == 0
    rows = _run(kf.list_keyframes(user_id=USER, pet_id=PET))
    assert [k.keyframe_role for k in rows] == ["STAND_READY"]


def test_transition_resolves_start_and_target(storage, monkeypatch):
    _, _, built = _prepare_keyframes(monkeypatch, storage, roles=("STAND_READY", "LIE"))

    spec = _resolve("LIE_DOWN")
    assert spec["motion_class"] == "TRANSITION"
    assert spec["video_strategy"] == ms.STRATEGY_START_END
    assert spec["start_keyframe"]["role"] == "STAND_READY"
    assert spec["target_keyframe"]["role"] == "LIE"
    assert spec["target_keyframe"]["keyframe_id"] == built["LIE"].id
    assert spec["loopable"] is False


def test_transition_missing_target_fails_safely(storage, monkeypatch):
    _prepare_keyframes(monkeypatch, storage, roles=("STAND_READY",))  # 시작만, 목표(LIE) 없음
    with pytest.raises(ms.MotionSpecError) as e:
        _resolve("LIE_DOWN")
    assert e.value.code == "TARGET_KEYFRAME_REQUIRED" and e.value.status == 409


def test_missing_start_keyframe_fails_safely(storage, monkeypatch):
    _prepare_canonical(monkeypatch, storage)  # 키프레임 없음
    with pytest.raises(ms.MotionSpecError) as e:
        _resolve("BREATHING")
    assert e.value.code == "KEYFRAME_REQUIRED" and e.value.status == 409


def test_advisory_review_keyframe_is_business_deliverable(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, None)  # VLM 없음 → legacy REVIEW evidence
    k = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])], role="STAND_READY")
    assert k.status == kf.STATUS_COMPLETE
    assert k.candidates[0].decision == "REVIEW"
    assert k.candidates[0].qa_result["business_qa"]["delivery_action"] == "DELIVER_WITH_ADVISORY"

    spec = _resolve("BREATHING")
    assert spec["start_keyframe"]["keyframe_id"] == k.id


def test_locomotion_falls_back_with_warning(storage, monkeypatch):
    _prepare_keyframes(monkeypatch, storage, roles=("STAND_READY",))  # Phase 4: 이동은 서기 시작
    spec = _resolve("COME_CLOSER")
    # 라이브러리 미해석 시 v1 형태 유지 (Phase 6.6: resolution 표시 추가).
    mr = spec["motion_reference"]
    assert mr["id"] == "DOG_APPROACH" and mr["policy"] == "preferred" and mr["asset"] is None
    assert mr["resolution"] == "unresolved"
    assert spec["video_strategy"] == ms.STRATEGY_I2V  # 호환 레퍼런스 없음 → 폴백
    assert any("DOG_APPROACH" in w for w in spec["warnings"])
    assert spec["pet_motion_profile"]["profile_version"]  # Phase 6.6 프로필 동봉


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
    assert a["registry_contract_version"] == ms.MOTION_REGISTRY_CONTRACT_VERSION
    assert a["start_keyframe"]["version"] == 1


def test_resolver_exposes_registry_requirements_without_changing_strategy(storage, monkeypatch):
    _prepare_keyframes(monkeypatch, storage, roles=("STAND_READY",))
    spec = _resolve("COME_CLOSER")
    assert spec["motion_type"] == ms.MOTIONS["COME_CLOSER"].motion_type
    assert spec["requirements"]["provider_capabilities"]["degrade_to_strategy"] == ms.STRATEGY_I2V
    # 기존 동작 보존: 레퍼런스 미해석 시 폴백 전략은 그대로 I2V.
    assert spec["video_strategy"] == ms.STRATEGY_I2V


def test_resolver_uses_pinned_morphology_profile_when_available(storage, monkeypatch):
    _prepare_keyframes(monkeypatch, storage, roles=("STAND_READY",))

    async def fake_get_canonical(**kwargs):
        return SimpleNamespace(reference_set_version=7)

    async def fake_get_set(**kwargs):
        return SimpleNamespace(morphology_profile_version=9)

    async def fake_get_profile(**kwargs):
        return SimpleNamespace(
            id="morph-id",
            version=9,
            profile={
                "traits": {
                    "species": {"status": "fused", "class": "DOG", "confidence": "high"},
                    "body_size": {"status": "fused", "class": "medium_in_frame", "confidence": "high"},
                    "torso_proportion": {"status": "fused", "class": "standard", "confidence": "high"},
                    "leg_proportion": {"status": "fused", "class": "long", "confidence": "high"},
                }
            },
        )

    monkeypatch.setattr(
        __import__("backend.services.canonical_pet_service", fromlist=["x"]),
        "get_canonical",
        fake_get_canonical,
    )
    monkeypatch.setattr(
        __import__("backend.services.pet_reference_set_service", fromlist=["x"]),
        "get_set",
        fake_get_set,
    )
    monkeypatch.setattr(morph, "get_profile", fake_get_profile)

    spec = _resolve("RUN")
    p = spec["pet_motion_profile"]
    assert p["morphology_profile_version"] == 9
    # 프레임 점유율(medium_in_frame)은 실제 체급이 아니다 — 체급으로 승격하지
    # 않고 UNKNOWN 을 유지한다 (실측 소스 없음).
    assert p["body_size_class"] == "UNKNOWN"
    assert "body_size" not in p["sources"]
    assert p["leg_length_class"] == "LONG"
    # 융합 형태 프로필의 몸통 비율이 정본 — 단일 레퍼런스 신원 측정이 덮어쓰지
    # 않는다.
    assert p["body_length_class"] == "STANDARD"
    assert p["sources"]["body_length"] == "morphology_profile:high"
    # body_size_class 는 구조 매칭 축에서도 빠진다.
    assert "body_size_class" not in spec["requirements"]["morphology"]["match_fields"]


def test_unavailable_pinned_morphology_does_not_fall_back_to_latest(storage, monkeypatch):
    """
    핀된 형태 프로필을 못 읽으면 **UNKNOWN** 이다 — 최신으로 대신하지 않는다.

    예전에는 조용히 `get_profile(version=None)` 로 떨어졌다. 같은 키프레임을 같은
    계약으로 다시 돌려도 그 사이 프로필이 다시 빌드돼 있으면 다른 몸으로
    생성됐고, 기록에는 "핀됨" 이라고 남았다.
    """
    _prepare_keyframes(monkeypatch, storage, roles=("STAND_READY",))

    async def fake_get_canonical(**kwargs):
        return SimpleNamespace(reference_set_version=7)

    async def fake_get_set(**kwargs):
        return SimpleNamespace(morphology_profile_version=9)

    asked: list = []

    async def fake_get_profile(**kwargs):
        version = kwargs.get("version")
        asked.append(version)
        if version == 9:
            return None  # 핀된 버전이 사라졌다 (정리/마이그레이션)
        # 최신 프로필은 존재한다 — 예전 코드가 조용히 집어 가던 값.
        return SimpleNamespace(
            id="morph-latest",
            version=12,
            profile={
                "traits": {
                    "species": {"status": "fused", "class": "DOG", "confidence": "high"},
                    "leg_proportion": {"status": "fused", "class": "short", "confidence": "high"},
                }
            },
        )

    monkeypatch.setattr(
        __import__("backend.services.canonical_pet_service", fromlist=["x"]),
        "get_canonical",
        fake_get_canonical,
    )
    monkeypatch.setattr(
        __import__("backend.services.pet_reference_set_service", fromlist=["x"]),
        "get_set",
        fake_get_set,
    )
    monkeypatch.setattr(morph, "get_profile", fake_get_profile)

    spec = _resolve("RUN")
    p = spec["pet_motion_profile"]

    # 핀 버전만 물었고, 최신은 **묻지 않았다**.
    assert asked == [9], asked
    assert p.get("morphology_profile_version") in (None, 9)
    # 최신 프로필의 값(SHORT)이 새어 들어오지 않았다.
    assert p["leg_length_class"] != "SHORT"
    # 강등은 조용하지 않다.
    assert any("pinned morphology profile v9 unavailable" in w for w in spec["warnings"]), spec["warnings"]


def test_unpinned_lineage_still_reads_the_latest_morphology_profile(storage, monkeypatch):
    """핀이 **선언되지 않은** 계보(핀 이전 자산)는 예전처럼 최신을 본다."""
    _prepare_keyframes(monkeypatch, storage, roles=("STAND_READY",))

    async def fake_get_canonical(**kwargs):
        return SimpleNamespace(reference_set_version=7)

    async def fake_get_set(**kwargs):
        return SimpleNamespace(morphology_profile_version=None)

    asked: list = []

    async def fake_get_profile(**kwargs):
        asked.append(kwargs.get("version"))
        return SimpleNamespace(
            id="morph-latest",
            version=12,
            profile={
                "traits": {
                    "species": {"status": "fused", "class": "DOG", "confidence": "high"},
                    "leg_proportion": {"status": "fused", "class": "long", "confidence": "high"},
                }
            },
        )

    monkeypatch.setattr(
        __import__("backend.services.canonical_pet_service", fromlist=["x"]),
        "get_canonical",
        fake_get_canonical,
    )
    monkeypatch.setattr(
        __import__("backend.services.pet_reference_set_service", fromlist=["x"]),
        "get_set",
        fake_get_set,
    )
    monkeypatch.setattr(morph, "get_profile", fake_get_profile)

    spec = _resolve("RUN")
    assert asked == [None], asked
    assert spec["pet_motion_profile"]["leg_length_class"] == "LONG"
    assert not any("pinned morphology" in w for w in spec["warnings"])


def test_identity_body_length_is_fallback_when_morphology_has_none(storage, monkeypatch):
    """형태 프로필이 몸통 비율을 못 주면 그때만 신원 구조 측정으로 폴백한다."""
    _prepare_keyframes(monkeypatch, storage, roles=("STAND_READY",))

    async def fake_get_profile(**kwargs):
        return SimpleNamespace(
            id="morph-id",
            version=3,
            profile={
                "traits": {
                    "torso_proportion": {
                        "status": "unknown",
                        "reason": "insufficient_cross_reference_evidence",
                    },
                }
            },
        )

    monkeypatch.setattr(morph, "get_profile", fake_get_profile)

    spec = _resolve("RUN")
    p = spec["pet_motion_profile"]
    assert p["body_length_class"] in ("COMPACT", "STANDARD", "LONG")
    assert p["sources"]["body_length"] == "measured"


def test_resolver_makes_no_provider_calls(storage, monkeypatch):
    from backend.services import canonical_image_providers

    _prepare_keyframes(monkeypatch, storage)

    def boom():
        raise AssertionError("리졸버가 프로바이더를 건드렸다")

    monkeypatch.setattr(canonical_image_providers, "resolve_providers", boom)
    monkeypatch.setattr(canonical_image_providers, "resolve_keyframe_providers", boom)
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
    _prepare_keyframes(monkeypatch, storage, roles=("STAND_READY", "LIE"))
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
