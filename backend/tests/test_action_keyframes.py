"""
액션 키프레임 빌더 (Phase 5) 계약 테스트.

프로바이더는 전부 가짜 — 실 결제 호출 없음. 스펙/QA/선택/버전/근거는 실제 코드.
"""

from __future__ import annotations

import os

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import keyframes_v1
from backend.scenarios.pet_scenarios import ACTION_ORDER, IDLE_EVENTS, PET_ACTIONS
from backend.services import action_keyframe_service as kf
from backend.services import action_keyframe_spec as spec_mod
from backend.services import canonical_pet_service as canon
from backend.services import durable_provider_jobs
from backend.services import pet_identity_service as ids
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry, vlm_identity
from backend.services.luma_idle_templates import IDLE_TEMPLATE_ORDER
from backend.services.canonical_image_providers import CanonicalImageResult

from .conftest import ASGITestClient
from .test_pet_identity_profile import make_pet_cutout_png, make_striped_cutout_png
from .test_canonical_pet_builder import (
    GOOD,
    VLM_QA_OK,
    FakeProvider,
    _seed_three_ref_pet,
    install_vlm_qa,
)
from .test_pet_reference_sets import PET, USER

VLM_KF_OK = {
    **VLM_QA_OK,
    "pose_matches": "yes",
    "pose_confidence": "high",
    "body_orientation_ok": "yes",
    "required_regions_visible": "yes",
    "source": "vlm-keyframe-qa-v1",
}


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    monkeypatch.delenv("KEYFRAME_ALLOW_REVIEW_CANONICAL", raising=False)
    monkeypatch.setenv("CANONICAL_QA_MIN_RESOLUTION", "100")
    for m in (refs, pet_registry, ids, sets, canon, kf):
        m.__reset_for_tests()
    yield
    for m in (refs, pet_registry, ids, sets, canon, kf):
        m.__reset_for_tests()


@pytest.fixture
def storage(monkeypatch) -> dict[str, bytes]:
    """경로 → 바이트. 정본 raw 를 키프레임 빌드가 다시 읽을 수 있어야 한다."""
    from backend.services import supabase_assets

    store: dict[str, bytes] = {}

    async def fake_upload(path, data, content_type):
        store[path] = bytes(data)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    return store


def _run(coro):
    return anyio.run(lambda: coro)


def install_kf_vlm(monkeypatch, result):
    monkeypatch.setattr(
        vlm_identity,
        "qa_action_keyframe",
        lambda candidate, references, *, required_pose, required_visibility=(), candidate_mime="image/png": result,
    )


def _prepare_canonical(monkeypatch, storage):
    """레퍼런스 → 정본 complete 까지 준비. (harness, canonical) 반환."""
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)

    def fetch(ref):
        return h.bytes_by_path.get(ref.object_path) or storage.get(ref.object_path)

    h.kf_fetch = fetch
    canonical = _run(
        canon.build_canonical(
            user_id=USER, pet_id=PET, fetch_bytes=fetch,
            providers=[FakeProvider("runway", [GOOD(), GOOD(), GOOD()])],
            cutout_fn=lambda raw: raw,
        )
    )
    assert canonical.status == canon.STATUS_COMPLETE
    return h, canonical


def _build_kf(h, providers, role="NEUTRAL_IDLE", **kw):
    return _run(
        kf.build_keyframe(
            user_id=USER, pet_id=PET, keyframe_role=role,
            fetch_bytes=h.kf_fetch, providers=providers,
            cutout_fn=lambda raw: raw, **kw,
        )
    )


class RecordingProvider(FakeProvider):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.seen_references: list[list] = []
        self.seen_prompts: list[str] = []

    def generate(self, references, prompt, output_spec, metadata):
        self.seen_references.append(list(references))
        self.seen_prompts.append(prompt)
        return super().generate(references, prompt, output_spec, metadata)


def test_durable_keyframe_resumes_one_building_version(storage, monkeypatch):
    h, _canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "1")
    monkeypatch.setenv("CANONICAL_STOP_AFTER_PASSES", "1")

    class YieldOnce(FakeProvider):
        durable_execution = True

        def generate(self, references, prompt, output_spec, metadata):
            self.calls += 1
            if self.calls == 1:
                raise durable_provider_jobs.ProviderWorkPending("operation-1", "PENDING")
            return CanonicalImageResult(
                image_bytes=GOOD(), provider=self.name, model=self.model_name(),
                external_job_id="job-1",
            )

    provider = YieldOnce("runway")
    with pytest.raises(durable_provider_jobs.ProviderWorkPending):
        _build_kf(h, [provider])
    assert len(_run(kf._keyframe_rows(PET, "NEUTRAL_IDLE"))) == 1

    completed = _build_kf(h, [provider])
    assert completed.status == kf.STATUS_COMPLETE
    assert len(_run(kf._keyframe_rows(PET, "NEUTRAL_IDLE"))) == 1
    assert len(completed.candidates) == 1


# ══════════════════════════════════════════════════════════════════════════
# 레지스트리 — 네 번째 명명 체계 금지
# ══════════════════════════════════════════════════════════════════════════


def test_roles_map_only_existing_action_ids():
    known = set(ACTION_ORDER) | set(IDLE_EVENTS) | set(PET_ACTIONS) | set(IDLE_TEMPLATE_ORDER) | {
        spec_mod.BREATHING_HOME_STATE
    }
    seen: set[str] = set()
    for role in spec_mod.KEYFRAME_ROLE_ORDER:
        spec = spec_mod.KEYFRAME_ROLES[role]
        for aid in spec.supported_action_ids:
            assert aid in known, f"{aid} 는 기존 레지스트리에 없다 — 새 액션 id 금지"
            assert aid not in seen, f"{aid} 가 두 역할에 매핑됐다"
            seen.add(aid)
    # 중립 시작 행동은 NEUTRAL_IDLE 로 흡수된다 (다대일 재사용).
    for aid in ("BREATHING", "BLINKING", "TOUCH", "IDLE_BREATH", "PET_HEAD", "LOOK_UP"):
        assert spec_mod.role_for_action(aid) == "NEUTRAL_IDLE"
    # Phase 4: 서기 시작(이동/눕기 전이)은 STAND_READY, LIE 시작은 LIE.
    for aid in ("COME_CLOSER", "LIE_DOWN"):
        assert spec_mod.role_for_action(aid) == "STAND_READY"
    for aid in ("LIE_IDLE", "STAND_UP"):
        assert spec_mod.role_for_action(aid) == "LIE"
    assert spec_mod.role_for_action("NOT_AN_ACTION") is None


def test_breathing_home_state_matches_ts_registry():
    ts = os.path.join(os.path.dirname(__file__), "..", "..", "src", "lib", "pet-runtime-events.ts")
    with open(ts, encoding="utf-8") as f:
        content = f.read()
    assert f'IDLE_HOME_STATE = "{spec_mod.BREATHING_HOME_STATE}"' in content


# ══════════════════════════════════════════════════════════════════════════
# 정본 요구 / REVIEW 정책
# ══════════════════════════════════════════════════════════════════════════


def test_canonical_is_required(storage, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    h.kf_fetch = h.fetch
    with pytest.raises(kf.ActionKeyframeError) as e:
        _build_kf(h, [FakeProvider("runway", [GOOD()])])
    assert e.value.code == "CANONICAL_REQUIRED" and e.value.status == 409


def test_review_canonical_rejected_unless_policy_allows(storage, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, None)  # VLM 확언 없음 → 정본은 REVIEW 에 머문다

    def fetch(ref):
        return h.bytes_by_path.get(ref.object_path) or storage.get(ref.object_path)

    h.kf_fetch = fetch
    canonical = _run(
        canon.build_canonical(
            user_id=USER, pet_id=PET, fetch_bytes=fetch,
            providers=[FakeProvider("runway", [GOOD(), GOOD(), GOOD()])],
            cutout_fn=lambda raw: raw,
        )
    )
    assert canonical.status == canon.STATUS_REVIEW

    with pytest.raises(kf.ActionKeyframeError) as e:
        _build_kf(h, [FakeProvider("runway", [GOOD()])])
    assert e.value.code == "CANONICAL_NOT_APPROVED"

    # 명시적 정책으로만 허용된다 — 조용한 사용은 없다.
    monkeypatch.setenv("KEYFRAME_ALLOW_REVIEW_CANONICAL", "1")
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    built = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD()])])
    assert built.status == kf.STATUS_COMPLETE


# ══════════════════════════════════════════════════════════════════════════
# 성공 경로 / 신원 앵커 / 프롬프트
# ══════════════════════════════════════════════════════════════════════════


def test_build_neutral_idle_with_canonical_anchor(storage, monkeypatch):
    h, canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    provider = RecordingProvider("runway", [GOOD(), GOOD(), GOOD()])

    k = _build_kf(h, [provider])
    assert k.status == kf.STATUS_COMPLETE and k.version == 1
    assert k.canonical_version_id == canonical.id
    assert k.canonical_version == canonical.version

    # 신원 앵커: 첫 레퍼런스는 항상 정본 raw 다 — 고객 원본에서 재발명하지 않는다.
    first_ref = provider.seen_references[0][0]
    anchor = next(c for c in canonical.candidates if c.selected)
    assert first_ref.role == "CANONICAL"
    assert first_ref.reference_id == f"canonical:{anchor.id}"
    assert first_ref.data == storage[anchor.raw_object_path]
    # 보조 신뢰 레퍼런스는 최대 2장.
    assert len(provider.seen_references[0]) <= 3

    sel = next(c for c in k.candidates if c.selected)
    assert sel.input_canonical_candidate_id == anchor.id
    assert sel.qa_result["decision"] == "PASS"
    assert sel.qa_result["pose"]["matches"] == "yes"


def test_prompt_contains_pose_and_traits_never_unknowns_or_themes(storage, monkeypatch):
    from backend.services.theme_catalog import ALL_THEME_KEYS

    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    k = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD()])], role="LIE")

    prompt = k.prompt or ""
    assert k.prompt_version == "keyframe-prompt-v1"
    assert "Requested pose:" in prompt and "lying down naturally" in prompt
    assert "Change only" in prompt  # 최소 변형 원칙
    assert "brown" in prompt.lower()  # 실측 코트 색 제약
    assert "unknown" not in prompt.lower()
    assert "No beds" in prompt  # 환경 오브젝트 금지
    for key in ALL_THEME_KEYS:
        assert key not in prompt.lower() and key.replace("_", " ") not in prompt.lower()


def test_spec_snapshot_recorded_on_keyframe(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    k = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD()])])
    assert k.spec["spec_version"] == spec_mod.KEYFRAME_SPEC_VERSION
    assert "BREATHING" in k.spec["supported_action_ids"]
    assert k.spec["video_compat"]["loopable_base"] is True


def test_neutral_idle_preserves_canonical_posture_not_a_pose_choice():
    """spec-v2: NEUTRAL_IDLE 은 홈/기준 포즈다 — 앉기/서기를 **다시 고르지 않는다**.

    이전 문구 "sitting or standing pose" 는 이미지 모델에게 포즈 선택권을 줬고
    앉기로 강하게 쏠렸다. 이제 정본(Canonical)의 기존 자세를 그대로 물려받는다:
    정본이 서 있으면 서 있고, 앉아 있으면 앉아 있다.
    """
    spec = spec_mod.KEYFRAME_ROLES["NEUTRAL_IDLE"]
    pose = spec.required_pose
    # 포즈 선택 문구 금지 — 이 문구가 앉기 쏠림의 원인이었다.
    assert "sitting or standing pose" not in pose
    assert "standing or sitting pose" not in pose
    # 정본 자세 유지가 명시된다 — 양방향 모두.
    assert "existing body posture" in pose
    assert "canonical reference image" in pose
    assert "stays standing" in pose and "stays sitting" in pose
    # 중립 홈 특성은 유지된다 (몸 이완 / 머리 수평 / 눈 뜸 / 입 이완).
    for kept in ("body relaxed", "head level", "eyes open", "mouth relaxed"):
        assert kept in pose, kept
    # 가시성·역할 계약은 그대로다 — 문구만 바뀌었다.
    assert spec.required_visibility == ("face", "full_body", "ears", "front_paws")
    assert spec.video_compat["loopable_base"] is True
    # 버전 범프 — 재사용 게이트(analyzer_versions)가 이 값을 비교하므로, 안
    # 올리면 앉기로 쏠린 기존 NEUTRAL_IDLE 키프레임이 영원히 재사용된다.
    assert spec_mod.KEYFRAME_SPEC_VERSION == "keyframe-spec-v2"
    # 두 프롬프트 빌더 모두에 실제로 실린다 (컴팩트는 Runway 1000자 계약 유지).
    prompt = spec_mod.build_keyframe_prompt(spec, {})
    assert "keeps the pet's existing body posture" in prompt
    compact = spec_mod.build_compact_keyframe_prompt(spec, {})
    assert "keeps the pet's existing body posture" in compact
    assert len(compact) <= 1000
    # 다른 역할의 문구는 건드리지 않았다.
    assert "lying down naturally" in spec_mod.KEYFRAME_ROLES["LIE"].required_pose


def test_phase4_four_pose_roles_and_lazy_generation_contract():
    """Phase 4: 포즈 역할 4종 + 지연 생성 계약 (스펙 수준).

    신규 펫의 최소 생성물은 Canonical + NEUTRAL_IDLE 이다 — 기본 모션
    (BREATHING/아이들 4종/PET_HEAD/LOOK_UP)이 전부 NEUTRAL_IDLE 시작이고 목표
    키프레임이 없기 때문에, 런 오케스트레이션(KEYFRAMES 스테이지는 스펙이
    가리키는 역할만 만든다)이 다른 역할을 만들 이유가 없다. STAND_READY/LIE/
    SLEEP 은 그 역할을 시작(또는 목표)으로 요구하는 모션이 요청될 때만 생긴다.
    """
    from backend.services import motion_spec as ms

    # 기계적 포즈 역할 4종이 1급으로 존재하고 순서가 결정론적이다.
    assert spec_mod.KEYFRAME_ROLE_ORDER[:4] == ("NEUTRAL_IDLE", "STAND_READY", "LIE", "SLEEP")
    for role in spec_mod.KEYFRAME_ROLE_ORDER:
        assert role in spec_mod.KEYFRAME_ROLES

    # STAND_READY 는 명시적 서기다 — NEUTRAL_IDLE 과 달리 자세를 물려받지 않는다.
    stand = spec_mod.KEYFRAME_ROLES["STAND_READY"].required_pose
    assert "standing upright" in stand and "all four" in stand
    assert "canonical reference" not in stand

    # SLEEP 은 LIE 와 다른 신체 구성 — 눈 감김 + 머리 내림 vs 머리 들고 깨어 있음.
    assert "eyes fully closed" in spec_mod.KEYFRAME_ROLES["SLEEP"].required_pose
    assert "head resting down" in spec_mod.KEYFRAME_ROLES["SLEEP"].required_pose
    assert "head upright and awake" in spec_mod.KEYFRAME_ROLES["LIE"].required_pose

    # 신규 펫 기본 모션은 NEUTRAL_IDLE 하나만 요구한다 — 지연 생성의 근거.
    basic = ("BREATHING", "BLINKING", "EAR_TWITCHING", "HEAD_TILTING",
             "TAIL_WAGGING", "PET_HEAD", "LOOK_UP")
    for mid in basic:
        m = ms.MOTIONS[mid]
        assert m.start_keyframe_role == "NEUTRAL_IDLE", mid
        assert not m.requires_target_keyframe and m.target_keyframe_role is None, mid

    # 역할별 수요 모션 — 해당 모션이 요청될 때만 그 역할이 필요해진다.
    assert {"COME_CLOSER", "RUN", "WALK", "LIE_DOWN"} <= set(
        ms.motions_for_keyframe_role("STAND_READY"))
    assert {"LIE_IDLE", "STAND_UP", "FALL_ASLEEP"} <= set(
        ms.motions_for_keyframe_role("LIE"))
    assert {"SLEEP_BREATH", "FALL_ASLEEP", "WAKE_UP"} <= set(
        ms.motions_for_keyframe_role("SLEEP"))


def test_stand_ready_builds_on_demand_with_role_scoped_storage(storage, monkeypatch):
    """STAND_READY 는 다른 역할과 같은 빌더/정책으로 생성되고, 저장 경로가
    역할별로 갈라지며(keyframes/stand_ready/v1/), 역할당 승인 이미지는 1장이다."""
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    provider = FakeProvider("runway", [GOOD()] * 10)
    k = _build_kf(h, [provider], role="STAND_READY")

    assert k.status == kf.STATUS_COMPLETE
    assert k.keyframe_role == "STAND_READY"
    assert provider.calls == 1  # 후보 정책 불변: 첫 QA PASS 에서 즉시 멈춘다
    # 역할당 승인 이미지 1장 — 선택은 정확히 하나다.
    assert k.selected_candidate_id
    assert sum(1 for c in k.candidates if c.selected) == 1
    # 저장 경로가 역할별로 물질화된다.
    sel = next(c for c in k.candidates if c.selected)
    assert "/keyframes/stand_ready/v1/" in (sel.raw_object_path or "")


# ══════════════════════════════════════════════════════════════════════════
# QA — 포즈 / 구조 / VLM 없음
# ══════════════════════════════════════════════════════════════════════════


def test_pose_failure_fails_candidate(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, {**VLM_KF_OK, "pose_matches": "no"})
    fallback = FakeProvider("gpt_image", [GOOD()])
    k = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD(), GOOD()]), fallback])

    runway_cands = [c for c in k.candidates if c.provider == "runway"]
    assert all(c.decision == "FAIL" for c in runway_cands)
    assert all("pose_not_achieved" in c.qa_result["reasons"] for c in runway_cands)
    # 포즈 실패 → 폴백도 시도되지만 같은 스텁이라 결국 review/fail 로 남는다.
    assert k.status in (kf.STATUS_FAILED, kf.STATUS_REVIEW)


def test_pose_changing_role_uses_vlm_anatomy_for_structure(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    k = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD()])], role="LIE")

    sel = next(c for c in k.candidates if c.selected)
    checks = sel.qa_result["checks"]
    assert checks["structure"] == "PASS"
    assert "structure_via_vlm_anatomy" in sel.qa_result["reasons"]
    assert "structure_comparison_skipped_pose_change" in sel.qa_result["reasons"]


def test_without_vlm_keyframe_is_review_only(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, None)
    k = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])])
    assert k.status == kf.STATUS_REVIEW
    assert k.selected_candidate_id is None
    assert all(c.decision == "REVIEW" for c in k.candidates)


def test_identity_failure_triggers_fallback(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    primary = FakeProvider("runway", [make_striped_cutout_png()] * 3)
    fallback = FakeProvider("gpt_image", [GOOD()])

    k = _build_kf(h, [primary, fallback])
    assert k.status == kf.STATUS_COMPLETE
    sel = next(c for c in k.candidates if c.selected)
    assert sel.provider == "gpt_image"
    assert all(c.decision == "FAIL" for c in k.candidates if c.provider == "runway")


def test_provider_error_distinct_from_qa_fail_and_falls_back(storage, monkeypatch):
    from backend.services.canonical_image_providers import CanonicalProviderError

    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    err = CanonicalProviderError("PROVIDER_FAILED", "boom")
    k = _build_kf(h, [FakeProvider("runway", [err, err, err]), FakeProvider("gpt_image", [GOOD(), GOOD()])])

    assert k.status == kf.STATUS_COMPLETE
    errors = [c for c in k.candidates if c.provider == "runway" and c.decision == "ERROR"]
    assert len(errors) == 3 and all(c.error for c in errors)
    assert next(c for c in k.candidates if c.selected).provider == "gpt_image"


# ══════════════════════════════════════════════════════════════════════════
# 후보 정책 / 결정론 / 버전 / 근거
# ══════════════════════════════════════════════════════════════════════════


def test_early_stop_and_limits(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    provider = FakeProvider("runway", [GOOD()] * 10)
    k = _build_kf(h, [provider])
    assert provider.calls == 1  # 점진적 조기 중단: 첫 PASS 에서 즉시 멈춘다
    assert k.qa_summary["candidate_count"] == 1

    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "1")
    install_kf_vlm(monkeypatch, None)  # PASS 없음 → 상한까지만
    primary = FakeProvider("runway", [GOOD()] * 10)
    fallback = FakeProvider("gpt_image", [GOOD()] * 10)
    k2 = _build_kf(h, [primary, fallback], role="LOOK_UP")
    assert primary.calls == 1 and fallback.calls == 2  # max_fallback 기본 2


def test_candidates_persist_raw_and_cutout(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    k = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD()])])
    for c in k.candidates:
        assert c.raw_object_path and "/keyframes/neutral_idle/v1/" in c.raw_object_path
        assert c.cutout_object_path and c.cutout_object_path in storage
        assert c.raw_object_path in storage


def test_deterministic_ranking(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    monkeypatch.setenv("CANONICAL_STOP_AFTER_PASSES", "3")
    a = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])], skip_if_unchanged=False)
    b = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])], skip_if_unchanged=False)
    sa = next(c for c in a.candidates if c.selected)
    sb = next(c for c in b.candidates if c.selected)
    assert (sa.provider, sa.attempt, sa.decision) == (sb.provider, sb.attempt, sb.decision)


def test_versioning_immutable_and_idempotent(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    provider = FakeProvider("runway", [GOOD()] * 10)

    v1 = _build_kf(h, [provider])
    calls = provider.calls
    again = _build_kf(h, [provider])
    assert again.deduplicated is True and again.version == 1
    assert provider.calls == calls  # 중복 과금 없음

    v2 = _build_kf(h, [provider], skip_if_unchanged=False)
    assert v2.version == 2
    old = _run(kf.get_keyframe(user_id=USER, pet_id=PET, keyframe_role="NEUTRAL_IDLE", version=1))
    assert old.id == v1.id and old.selected_candidate_id == v1.selected_candidate_id


def test_provenance_chain_and_generated_role(storage, monkeypatch):
    h, canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    k = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD()])])

    ledger = _run(refs.list_references(user_id=USER, pet_id=PET))
    kf_generated = [r for r in ledger if r.role == refs.ROLE_GENERATED and (r.derived_kind or "").startswith("keyframe")]
    assert {r.derived_kind for r in kf_generated} == {"keyframe_raw", "keyframe_cutout"}
    for g in kf_generated:
        assert g.diagnostics["keyframe_id"] == k.id
        assert g.diagnostics["canonical_version_id"] == canonical.id
    # 키프레임 → 정본 → 레퍼런스 세트 → 원본 사슬.
    assert k.canonical_version_id == canonical.id
    assert canonical.reference_set_version == 1
    # 생성물은 절대 원본 증거가 되지 않는다.
    originals = {r.id for r in ledger if r.role == refs.ROLE_ORIGINAL}
    assert not any(g.id in originals for g in kf_generated)


def test_ownership_isolation(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    with pytest.raises(kf.ActionKeyframeError) as e:
        _run(
            kf.build_keyframe(
                user_id="mallory@test", pet_id=PET, keyframe_role="NEUTRAL_IDLE",
                providers=[FakeProvider("runway", [GOOD()])],
            )
        )
    assert e.value.code == "PET_NOT_OWNED"

    _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD()])])
    with pytest.raises(kf.ActionKeyframeError):
        _run(kf.get_keyframe(user_id="mallory@test", pet_id=PET, keyframe_role="NEUTRAL_IDLE"))


def test_unknown_role_rejected(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    with pytest.raises(kf.ActionKeyframeError) as e:
        _build_kf(h, [FakeProvider("runway", [GOOD()])], role="DANCE_BATTLE")
    assert e.value.code == "UNKNOWN_KEYFRAME_ROLE"


# ══════════════════════════════════════════════════════════════════════════
# 라우터 / 평가
# ══════════════════════════════════════════════════════════════════════════


AUTH = {"Authorization": "Bearer test:alice@test"}


@pytest.fixture
def kf_client(monkeypatch) -> ASGITestClient:
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    app = FastAPI()
    app.include_router(keyframes_v1.router, prefix="/api")
    return ASGITestClient(app)


def test_router_roles_build_get(kf_client, storage, monkeypatch):
    from backend.services import canonical_image_providers as providers_mod

    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    monkeypatch.setattr(ids, "_default_fetch_bytes", h.kf_fetch)
    monkeypatch.setattr(
        providers_mod, "resolve_providers", lambda: [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])]
    )
    monkeypatch.setattr(canon, "_default_cutout_fn", lambda raw: raw)

    res = kf_client.get("/api/v1/pet/keyframes/roles", headers=AUTH)
    assert res.status_code == 200
    roles = [r["role"] for r in res.json()["roles"]]
    assert roles == list(spec_mod.KEYFRAME_ROLE_ORDER)

    res = kf_client.post(
        f"/api/v1/pet/keyframes/{PET}/build",
        json={"keyframe_role": "NEUTRAL_IDLE"},
        headers=AUTH,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "complete" and body["selected_candidate_id"]

    res = kf_client.get(f"/api/v1/pet/keyframes/{PET}", headers=AUTH)
    assert res.status_code == 200
    assert res.json()["keyframes"][0]["keyframe_role"] == "NEUTRAL_IDLE"

    res = kf_client.get(f"/api/v1/pet/keyframes/{PET}/NEUTRAL_IDLE", headers=AUTH)
    assert res.status_code == 200
    assert res.json()["candidates"]

    res = kf_client.get(f"/api/v1/pet/keyframes/{PET}/LIE", headers=AUTH)
    assert res.status_code == 404


def test_keyframe_evaluation_extends_phase4_harness(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    k = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD()])])
    sel = next(c for c in k.candidates if c.selected)

    _run(
        kf.record_keyframe_evaluation(
            user_id=USER, pet_id=PET, keyframe_id=k.id, candidate_id=sel.id,
            scores={"face_identity": 9, "markings": 8, "body_proportions": 8,
                    "pose_correctness": 9, "anatomy": 9, "phase6_suitability": 8},
            verdict="PASS",
        )
    )
    summary = _run(canon.evaluation_summary(user_id=USER))
    assert summary["providers"]["runway"]["count"] == 1
    assert summary["providers"]["runway"]["mean_scores"]["pose_correctness"] == 9.0
