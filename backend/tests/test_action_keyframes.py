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
from backend.services import business_qa
from backend.services import canonical_image_providers as providers_mod
from backend.services import canonical_pet_service as canon
from backend.services import canonical_qa
from backend.services import durable_provider_jobs
from backend.services import pet_identity_service as ids
from backend.services import pet_morphology_service as morph
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
    "source": vlm_identity.VLM_KEYFRAME_QA_VERSION,
}


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    monkeypatch.delenv("KEYFRAME_ALLOW_REVIEW_CANONICAL", raising=False)
    monkeypatch.setenv("CANONICAL_QA_MIN_RESOLUTION", "100")
    for m in (refs, pet_registry, ids, morph, sets, canon, kf):
        m.__reset_for_tests()
    yield
    for m in (refs, pet_registry, ids, morph, sets, canon, kf):
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
        lambda candidate, references, *, required_pose, required_visibility=(), candidate_mime="image/png", **kwargs: result,
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


def test_keyframe_storage_failure_recovers_same_candidate_no_resubmission(storage, monkeypatch):
    """유료 키프레임 생성 후 raw 저장만 실패해도 같은 후보가 재사용된다 — 재과금 없음."""
    h, _canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "2")
    monkeypatch.setenv("CANONICAL_STOP_AFTER_PASSES", "1")

    from backend.services import supabase_assets

    upload_calls = {"n": 0}

    async def flaky_upload(path, data, content_type):
        upload_calls["n"] += 1
        if upload_calls["n"] == 1:
            raise RuntimeError("storage down")
        storage[path] = bytes(data)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", flaky_upload)

    class DurableRunway(FakeProvider):
        durable_execution = True

        def __init__(self, name, image):
            super().__init__(name, [])
            self._image = image
            self.submissions = 0
            self._job_ids: dict[int, str] = {}

        def generate(self, references, prompt, output_spec, metadata):
            self.calls += 1
            attempt = metadata.get("attempt")
            if attempt not in self._job_ids:
                self.submissions += 1
                self._job_ids[attempt] = f"{self.name}-job-{attempt}"
            return CanonicalImageResult(
                image_bytes=self._image, provider=self.name, model=self.model_name(),
                external_job_id=self._job_ids[attempt],
            )

    provider = DurableRunway("runway", GOOD())

    with pytest.raises(durable_provider_jobs.ProviderRecoveryRequired):
        _build_kf(h, [provider])

    assert provider.calls == 1
    assert provider.submissions == 1
    rows = _run(kf._keyframe_rows(PET, "NEUTRAL_IDLE"))
    assert len(rows) == 1 and rows[0]["status"] == kf.STATUS_BUILDING
    cands = _run(kf._candidate_rows(str(rows[0]["id"])))
    assert len(cands) == 1
    assert cands[0]["error"] == "RAW_STORE_FAILED"
    assert cands[0]["external_job_id"] == "runway-job-1"
    assert not cands[0].get("raw_object_path")

    completed = _build_kf(h, [provider])
    assert completed.status == kf.STATUS_COMPLETE
    assert provider.calls == 2
    assert provider.submissions == 1  # 재제출 없음
    assert len(completed.candidates) == 1
    assert completed.candidates[0].external_job_id == "runway-job-1"


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


def test_legacy_review_canonical_is_selected_by_business_contract(storage, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, None)  # legacy decision remains REVIEW

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
    selected = next(candidate for candidate in canonical.candidates if candidate.selected)
    assert selected.decision == "REVIEW"
    assert selected.qa_result["business_qa"]["delivery_action"] == "DELIVER"

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

    # 신원 앵커: 첫 레퍼런스는 항상 정본 **클린 플레이트**다 — 고객 원본에서
    # 재발명하지 않고, 그림자/배경이 구워진 raw 를 먹이지도 않는다.
    first_ref = provider.seen_references[0][0]
    anchor = next(c for c in canonical.candidates if c.selected)
    assert first_ref.role == "CANONICAL"
    assert first_ref.reference_id == f"canonical:{anchor.id}"
    assert anchor.plate_object_path and anchor.plate_object_path in storage
    assert first_ref.data == storage[anchor.plate_object_path]
    assert first_ref.data != storage[anchor.raw_object_path]  # raw 는 생성에 안 간다
    # raw 는 파괴되지 않는다 — 증거로 그대로 남는다.
    assert storage[anchor.raw_object_path]
    sent_input = (
        next(c for c in k.candidates if c.selected).generation_metadata["canonical_input"]
    )
    assert sent_input["kind"] == "clean_plate"
    assert sent_input["object_path"] == anchor.plate_object_path
    selected_qa = next(c for c in k.candidates if c.selected).qa_result
    reuse = selected_qa["qa_evidence_reuse"]
    assert reuse["summary"]["inherited"] >= 3
    assert any(
        source["source_stage"] == "CANONICAL" and source["valid"] is True
        for source in reuse["inherited_sources"]
    )
    # 보조 신뢰 레퍼런스는 최대 2장.
    assert len(provider.seen_references[0]) <= 3

    sel = next(c for c in k.candidates if c.selected)
    assert sel.input_canonical_candidate_id == anchor.id
    assert sel.qa_result["decision"] == "PASS"
    assert sel.qa_result["pose"]["matches"] == "yes"


def test_legacy_canonical_without_plate_is_backfilled_from_its_cutout(storage, monkeypatch):
    """플레이트 이전에 만들어진 정본: 프로바이더 재호출 없이 누끼에서 채운다."""
    from backend.services import clean_plate_service as cp

    h, canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    anchor = next(c for c in canonical.candidates if c.selected)

    # 레거시 행 재현 — 플레이트 컬럼만 지운다 (raw/cutout 은 그대로).
    for row in canon._MOCK_CANDIDATES:
        row["plate_bucket"] = None
        row["plate_object_path"] = None
    storage.pop(anchor.plate_object_path, None)

    provider = RecordingProvider("runway", [GOOD(), GOOD(), GOOD()])
    k = _build_kf(h, [provider])
    assert k.status == kf.STATUS_COMPLETE

    backfilled = cp.plate_object_path(anchor.raw_object_path)
    assert backfilled in storage                       # 지연 백필됨
    assert provider.seen_references[0][0].data == storage[backfilled]
    # 백필된 플레이트도 대장에 남는다 — 근거 없는 생성 입력은 없다.
    ledger = _run(refs.list_references(user_id=USER, pet_id=PET))
    assert any(r.object_path == backfilled for r in ledger)


def test_keyframe_fails_closed_when_no_plate_can_be_built(storage, monkeypatch):
    """누끼조차 없으면 raw 로 **조용히 새지 않는다** — 그게 그림자의 경로였다."""
    h, canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)

    for row in canon._MOCK_CANDIDATES:
        row["plate_bucket"] = row["plate_object_path"] = None
        row["cutout_bucket"] = row["cutout_object_path"] = None

    provider = RecordingProvider("runway", [GOOD(), GOOD(), GOOD()])
    with pytest.raises(kf.ActionKeyframeError) as e:
        _build_kf(h, [provider])
    assert e.value.code == "CANONICAL_PLATE_UNAVAILABLE"
    assert e.value.status == 503
    assert provider.calls == 0  # 과금 호출 0회


def test_explicit_opt_out_falls_back_to_raw_and_says_so(storage, monkeypatch):
    """CLEAN_PLATE_REQUIRED=0 은 명시적 탈출구다 — 계보에 raw 라고 적힌다."""
    h, canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    monkeypatch.setenv("CLEAN_PLATE_REQUIRED", "0")

    anchor = next(c for c in canonical.candidates if c.selected)
    for row in canon._MOCK_CANDIDATES:
        row["plate_bucket"] = row["plate_object_path"] = None
        row["cutout_bucket"] = row["cutout_object_path"] = None

    provider = RecordingProvider("runway", [GOOD(), GOOD(), GOOD()])
    k = _build_kf(h, [provider])
    assert provider.seen_references[0][0].data == storage[anchor.raw_object_path]
    sent = next(c for c in k.candidates if c.selected).generation_metadata["canonical_input"]
    assert sent["kind"] == "raw" and sent["fallback_reason"]


def test_prompt_contains_pose_and_traits_never_unknowns_or_themes(storage, monkeypatch):
    from backend.services.theme_catalog import ALL_THEME_KEYS

    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    k = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD()])], role="LIE")

    prompt = k.prompt or ""
    assert k.prompt_version == "keyframe-prompt-v2"
    # 그림자 금지는 프롬프트의 계약이다 (알파 억제와 짝을 이룬다).
    assert "No contact shadow under the pet" in prompt
    assert "no cast shadow on the background" in prompt
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
    assert spec_mod.KEYFRAME_SPEC_VERSION == "keyframe-spec-v3"
    # 신원 앵커 소스는 raw 가 아니라 클린 플레이트다 (spec-v3).
    assert spec.preferred_canonical_source == "clean_plate"
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
# Canonical → NEUTRAL_IDLE 재사용 (BREATHING 전용, allow_canonical_reuse)
# ══════════════════════════════════════════════════════════════════════════


class ExplodingProvider(FakeProvider):
    """generate() 가 호출되면 즉시 실패한다 — 재사용 경로에서는 절대 불려선 안 된다."""

    def generate(self, references, prompt, output_spec, metadata):
        raise AssertionError("keyframe provider must not be called when Canonical reuse is eligible")


def test_eligible_canonical_reuse_skips_keyframe_provider_call(storage, monkeypatch):
    h, canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    anchor = next(c for c in canonical.candidates if c.selected)

    k = _build_kf(h, [ExplodingProvider("runway")], allow_canonical_reuse=True)

    assert k.status == kf.STATUS_COMPLETE
    assert len(k.candidates) == 1
    sel = k.candidates[0]
    assert sel.selected is True
    assert sel.provider == kf.CANONICAL_REUSE_PROVIDER
    assert sel.decision == canonical_qa.PASS
    assert sel.qa_result["decision"] == canonical_qa.PASS
    assert sel.input_canonical_candidate_id == anchor.id
    assert sel.generation_metadata["reused_from_canonical"] is True
    assert sel.generation_metadata["generated"] is False
    assert k.selected_candidate_id == sel.id


def test_alias_keyframe_is_complete_pass_with_correct_lineage(storage, monkeypatch):
    """별칭 후보는 새 업로드 없이 Canonical 의 raw/cutout/plate 객체를 그대로 가리킨다.

    대장(pet_reference_service)에는 새 keyframe_* 행이 추가되지 않는다 — 물리적으로
    새 자산이 하나도 없기 때문이다(canonical 빌드가 이미 그 정확한 object_path 로
    canonical_raw/cutout/plate 를 기록해 뒀고, record_generated 는 object_path 로
    멱등하다). 진짜 키프레임 계보는 candidate 행 자체(input_canonical_candidate_id +
    generation_metadata.reused_from_canonical)와 keyframe 행(canonical_version_id/
    canonical_version, selected_candidate_id)이 담당하며, 둘 다 COMPLETE/PASS 로
    정확히 채워진다."""
    h, canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    anchor = next(c for c in canonical.candidates if c.selected)
    ledger_before = {r.id for r in _run(refs.list_references(user_id=USER, pet_id=PET))}
    keys_before = set(storage)

    k = _build_kf(h, [ExplodingProvider("runway")], allow_canonical_reuse=True)
    sel = k.candidates[0]

    # 키프레임 행 자체 — COMPLETE/PASS + 정확한 canonical 계보.
    assert k.status == kf.STATUS_COMPLETE
    assert k.canonical_version_id == canonical.id
    assert k.canonical_version == canonical.version
    assert k.selected_candidate_id == sel.id

    # 별칭 — 새 스토리지 객체 없이 Canonical 의 raw/cutout/plate 를 그대로 가리킨다.
    assert sel.raw_object_path == anchor.raw_object_path
    assert sel.cutout_object_path == anchor.cutout_object_path
    assert sel.plate_object_path == anchor.plate_object_path
    assert set(storage) == keys_before  # 새 스토리지 객체 0개 — 재업로드 없음

    # 대장에도 새 행이 생기지 않는다 — 별칭이지 새 생성물이 아니다.
    ledger_after = {r.id for r in _run(refs.list_references(user_id=USER, pet_id=PET))}
    assert ledger_after == ledger_before
    # 대신 canonical 자신의 기존 대장 행이 그대로 그 object_path 들의 근거다.
    ledger = _run(refs.list_references(user_id=USER, pet_id=PET))
    canonical_generated = {r.object_path: r for r in ledger if r.role == refs.ROLE_GENERATED}
    assert canonical_generated[sel.raw_object_path].derived_kind == "canonical_raw"
    assert canonical_generated[sel.cutout_object_path].derived_kind == "canonical_cutout"
    assert canonical_generated[sel.plate_object_path].derived_kind == "canonical_plate"


def test_unsuitable_canonical_falls_back_to_keyframe_generation(storage, monkeypatch):
    """Canonical 자체가 NEUTRAL_IDLE 계약을 못 만족하면(포즈 불일치) 재사용을 포기하고
    평소 키프레임 생성 경로로 폴백한다 — Canonical PASS 만으로는 부족하다."""
    h, _canonical = _prepare_canonical(monkeypatch, storage)

    # 첫 VLM 호출 = 재사용 적격성 판정(Canonical 자체) — 포즈 불일치로 거절한다.
    # 이후 호출(실제 생성된 후보들)은 정상 확언 — 두 경로가 완전히 분리돼 있음을
    # 함께 증명한다: 재사용은 실패하지만 평소 생성은 그대로 성공한다.
    seen = {"n": 0}

    def vlm_stub(candidate, references, *, required_pose, required_visibility=(), candidate_mime="image/png", **kwargs):
        seen["n"] += 1
        if seen["n"] == 1:
            return {**VLM_KF_OK, "pose_matches": "no"}
        return VLM_KF_OK

    monkeypatch.setattr(vlm_identity, "qa_action_keyframe", vlm_stub)
    provider = RecordingProvider("runway", [GOOD(), GOOD()])

    k = _build_kf(h, [provider], allow_canonical_reuse=True)

    assert provider.calls >= 1  # 재사용이 거절되고 실제 생성으로 폴백했다
    assert k.status == kf.STATUS_COMPLETE
    sel = next(c for c in k.candidates if c.selected)
    assert sel.provider == "runway"
    assert sel.provider != kf.CANONICAL_REUSE_PROVIDER


def test_uncertain_canonical_reuse_keeps_review_evidence_without_paid_fallback(storage, monkeypatch):
    """VLM unknown remains REVIEW evidence but cannot independently spend again."""
    h, _canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, None)  # VLM 비활성/무응답 — 모든 판정이 unknown
    provider = RecordingProvider("runway", [GOOD(), GOOD(), GOOD()])

    k = _build_kf(h, [provider], allow_canonical_reuse=True)

    assert provider.calls == 0
    assert k.status == kf.STATUS_COMPLETE
    selected = next(candidate for candidate in k.candidates if candidate.selected)
    assert selected.provider == kf.CANONICAL_REUSE_PROVIDER
    assert selected.decision == "REVIEW"
    assert selected.qa_result["business_qa"]["retry_action"] == "STOP"
    assert k.qa_summary["decisions"]["REVIEW"] == 1
    assert k.qa_summary["decisions"]["PASS"] == 0


def test_canonical_reuse_only_applies_to_neutral_idle_role(storage, monkeypatch):
    """다른 역할(LIE 등)은 allow_canonical_reuse=True 를 넘겨도 평소와 똑같이
    동작한다 — Canonical 자체가 그 포즈(엎드림 등)를 보여줄 수 없으므로 대상이 아니다."""
    h, _canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    provider = RecordingProvider("runway", [GOOD(), GOOD()])

    k = _build_kf(h, [provider], role="LIE", allow_canonical_reuse=True)

    assert provider.calls >= 1
    assert k.status == kf.STATUS_COMPLETE
    assert all(c.provider != kf.CANONICAL_REUSE_PROVIDER for c in k.candidates)


def test_canonical_reuse_off_by_default(storage, monkeypatch):
    """allow_canonical_reuse 기본값은 False — 기존 호출부(라우터 등)는 아무 것도
    바뀌지 않는다."""
    h, _canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    provider = RecordingProvider("runway", [GOOD(), GOOD()])

    k = _build_kf(h, [provider])  # allow_canonical_reuse 인자 없음

    assert provider.calls >= 1
    assert all(c.provider != kf.CANONICAL_REUSE_PROVIDER for c in k.candidates)


# ══════════════════════════════════════════════════════════════════════════
# Canonical → NEUTRAL_IDLE 재사용 — 레이턴시 최적화 (중복 제거) 계약
# ══════════════════════════════════════════════════════════════════════════


def test_canonical_reuse_fetches_each_asset_at_most_once(storage, monkeypatch):
    """
    요구 3: 같은 객체(정본 raw/누끼)는 재사용 경로 안에서 두 번 다운로드되지
    않는다. 레거시(플레이트 없음) 정본으로 clean_plate_service.ensure_plate 의
    백필 분기(누끼 재다운로드 위험 지점)를 강제로 태운다.
    """
    from backend.services import clean_plate_service as cp

    h, canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    anchor = next(c for c in canonical.candidates if c.selected)

    for row in canon._MOCK_CANDIDATES:
        row["plate_bucket"] = None
        row["plate_object_path"] = None
    storage.pop(anchor.plate_object_path, None)

    fetch_counts: dict[str, int] = {}
    real_fetch = h.kf_fetch

    def counting_fetch(ref):
        fetch_counts[ref.object_path] = fetch_counts.get(ref.object_path, 0) + 1
        return real_fetch(ref)

    h.kf_fetch = counting_fetch

    k = _build_kf(h, [ExplodingProvider("runway")], allow_canonical_reuse=True)

    assert k.status == kf.STATUS_COMPLETE
    assert k.candidates[0].provider == kf.CANONICAL_REUSE_PROVIDER
    assert fetch_counts.get(anchor.raw_object_path) == 1
    assert fetch_counts.get(anchor.cutout_object_path) == 1
    # 백필된 플레이트도 대장에 남는다 — 백필 자체는 그대로 일어난다.
    backfilled = cp.plate_object_path(anchor.raw_object_path)
    assert backfilled in storage


def test_canonical_reuse_never_signs_provider_urls(storage, monkeypatch):
    """
    요구: 재사용이 성공하면 프로바이더 전용 준비물(signed URL)은 절대 만들지
    않는다 — sign_url_fn 이 한 번도 불리지 않아야 한다.
    """
    h, _canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)

    sign_calls = {"n": 0}

    def counting_sign(ref):
        sign_calls["n"] += 1
        return f"https://signed.test/{ref.object_path}"

    k = _run(
        kf.build_keyframe(
            user_id=USER, pet_id=PET, keyframe_role="NEUTRAL_IDLE",
            fetch_bytes=h.kf_fetch, providers=[ExplodingProvider("runway")],
            cutout_fn=lambda raw: raw, sign_url_fn=counting_sign,
            allow_canonical_reuse=True,
        )
    )

    assert k.status == kf.STATUS_COMPLETE
    assert k.candidates[0].provider == kf.CANONICAL_REUSE_PROVIDER
    assert sign_calls["n"] == 0, "재사용 성공 경로는 signed URL 을 만들지 않아야 한다"


def test_canonical_reuse_queries_keyframe_rows_once(storage, monkeypatch):
    """요구: 같은 멱등/재개 판정에 쓰는 _keyframe_rows 조회가 한 번으로 줄었다."""
    h, _canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)

    calls = {"n": 0}
    real_rows = kf._keyframe_rows

    async def counting_rows(pet_id, role=None):
        calls["n"] += 1
        return await real_rows(pet_id, role)

    monkeypatch.setattr(kf, "_keyframe_rows", counting_rows)

    k = _build_kf(h, [ExplodingProvider("runway")], allow_canonical_reuse=True)

    assert k.status == kf.STATUS_COMPLETE
    assert calls["n"] == 1


def test_canonical_reuse_retry_is_idempotent_no_second_candidate(storage, monkeypatch):
    """요구 6: 같은 상태에서 다시 부르면(재시작/재시도 흉내) 새 재사용 후보를
    또 만들지 않고 이미 완료된 버전을 그대로 돌려준다 — 프로바이더도 다시
    부르지 않는다."""
    h, canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    anchor = next(c for c in canonical.candidates if c.selected)

    first = _build_kf(h, [ExplodingProvider("runway")], allow_canonical_reuse=True)
    assert first.status == kf.STATUS_COMPLETE
    assert len(first.candidates) == 1

    second = _build_kf(h, [ExplodingProvider("runway")], allow_canonical_reuse=True)

    assert second.deduplicated is True
    assert second.id == first.id
    assert second.version == first.version == 1
    assert len(second.candidates) == 1
    assert second.candidates[0].id == first.candidates[0].id
    assert second.candidates[0].input_canonical_candidate_id == anchor.id
    # 정확히 한 버전, 한 후보만 저장돼 있다 — 재시도가 중복을 남기지 않았다.
    assert len(_run(kf._keyframe_rows(PET, "NEUTRAL_IDLE"))) == 1
    assert len(_run(kf._candidate_rows(first.id))) == 1


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


def test_without_vlm_keyframe_keeps_legacy_review_but_delivers(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, None)
    k = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])])
    assert k.status == kf.STATUS_COMPLETE
    assert k.selected_candidate_id is not None
    assert all(c.decision == "REVIEW" for c in k.candidates)
    assert k.candidates[0].qa_result["business_qa"]["delivery_action"] == "DELIVER_WITH_ADVISORY"


def test_identity_support_failure_does_not_trigger_fallback(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    primary = FakeProvider("runway", [make_striped_cutout_png()] * 3)
    fallback = FakeProvider("gpt_image", [GOOD()])

    k = _build_kf(h, [primary, fallback])
    assert k.status == kf.STATUS_COMPLETE
    sel = next(c for c in k.candidates if c.selected)
    assert sel.provider == "runway"
    assert primary.calls == 1 and fallback.calls == 0
    assert sel.decision == "FAIL"
    assert sel.qa_result["business_qa"]["retry_action"] == "STOP"


def _advisory_pattern_profile():
    """핵심 신원 검사는 전부 PASS 인데 coat_pattern 만 계열 일치(REVIEW)인 프로필."""
    from .test_canonical_qa import _seed_strict_profile, _set_pattern

    profile, sig, cutout = _seed_strict_profile()
    _set_pattern(profile, "golden|tan|golden", confidence="high", supports=2)
    return profile, sig, cutout


def _attach_keyframe_business(qa: dict, *, attempt: int = 1) -> dict:
    business_qa.attach_business_result(
        qa,
        attempt_number=attempt,
        request_kind="KEYFRAME",
    )
    return qa["business_qa"]


def test_advisory_coat_pattern_review_does_not_block_keyframe_pass(storage):
    """정본 QA 가 자문으로 통과시킨 coat_pattern REVIEW 를 키프레임이 다시
    재집계해서 REVIEW 로 끌어내리면 안 된다 — 핵심 전부 PASS + 포즈 PASS 다."""
    from backend.services import canonical_qa

    profile, sig, cutout = _advisory_pattern_profile()
    qa = kf.evaluate_keyframe_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        canonical_signature=sig,
        reference_signatures=[],
        spec=spec_mod.KEYFRAME_ROLES["NEUTRAL_IDLE"],
        vlm_qa=VLM_KF_OK,
    )
    assert [qa["checks"][k] for k in canonical_qa.CORE_CHECKS] == [canonical_qa.PASS] * len(
        canonical_qa.CORE_CHECKS
    )
    assert qa["checks"]["pose"] == canonical_qa.PASS
    # 자문 검사값과 이유는 그대로 남는다 — PASS 로 고쳐 쓰지 않는다.
    assert qa["checks"]["coat_pattern"] == canonical_qa.REVIEW
    assert "coat_pattern_family_equivalent_not_exact" in qa["reasons"]
    assert qa["identity_decision"] == canonical_qa.PASS
    assert qa["decision"] == canonical_qa.PASS


def test_pose_review_still_holds_keyframe_at_review(storage):
    """신원이 PASS 여도 포즈가 PASS 가 아니면 자동 승인은 없다."""
    from backend.services import canonical_qa

    profile, sig, cutout = _advisory_pattern_profile()
    qa = kf.evaluate_keyframe_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        canonical_signature=sig,
        reference_signatures=[],
        spec=spec_mod.KEYFRAME_ROLES["NEUTRAL_IDLE"],
        vlm_qa={**VLM_KF_OK, "required_regions_visible": "no"},
    )
    assert qa["identity_decision"] == canonical_qa.PASS
    assert qa["checks"]["pose"] == canonical_qa.REVIEW
    assert qa["decision"] == canonical_qa.REVIEW


def test_pose_fail_blocks_even_when_identity_passes(storage):
    from backend.services import canonical_qa

    profile, sig, cutout = _advisory_pattern_profile()
    qa = kf.evaluate_keyframe_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        canonical_signature=sig,
        reference_signatures=[],
        spec=spec_mod.KEYFRAME_ROLES["NEUTRAL_IDLE"],
        vlm_qa={**VLM_KF_OK, "pose_matches": "no"},
    )
    assert qa["identity_decision"] == canonical_qa.PASS
    assert qa["decision"] == canonical_qa.FAIL


@pytest.mark.parametrize(
    "gains,lift",
    [
        ((1.35, 1.35, 1.35), 22),
        ((0.72, 1.08, 1.32), 12),
    ],
)
def test_same_pet_enhanced_color_or_lighting_is_business_deliverable(storage, gains, lift):
    from .test_canonical_qa import _presentation_variant

    profile, sig, cutout = _advisory_pattern_profile()
    enhanced = _presentation_variant(cutout, gains=gains, lift=lift)
    qa = kf.evaluate_keyframe_candidate(
        cutout_rgba=ids.load_rgba(enhanced),
        profile=profile,
        canonical_signature=sig,
        reference_signatures=[],
        spec=spec_mod.KEYFRAME_ROLES["NEUTRAL_IDLE"],
        vlm_qa={**VLM_KF_OK, "presentation_difference_only": "yes"},
    )
    result = _attach_keyframe_business(qa)

    assert qa["identity_reference"]["primary"] == "approved_canonical"
    assert result["authority_profile"] == business_qa.KEYFRAME_IDENTITY_AUTHORITY_VERSION
    assert result["integrity_status"] == "PASS"
    assert business_qa.is_deliverable(qa) is True
    assert result["retry_action"] == "STOP"


def test_minor_morphology_variation_is_supporting_and_does_not_regenerate(storage):
    profile, sig, cutout = _advisory_pattern_profile()
    rgba = ids.load_rgba(cutout)
    candidate_ar = ids.analyze_structural_identity(rgba)["silhouette"]["bbox_aspect_ratio"]
    profile.structural_identity["silhouette"]["bbox_aspect_ratio"] = candidate_ar / 2.0
    qa = kf.evaluate_keyframe_candidate(
        cutout_rgba=rgba,
        profile=profile,
        canonical_signature=sig,
        reference_signatures=[],
        spec=spec_mod.KEYFRAME_ROLES["NEUTRAL_IDLE"],
        vlm_qa=VLM_KF_OK,
    )
    result = _attach_keyframe_business(qa)

    assert qa["checks"]["structure"] == canonical_qa.REVIEW
    assert result["authority_evidence"]["IDENTITY_SUPPORT"]["structure"] == "REVIEW"
    assert result["integrity_status"] == "PASS"
    assert result["retry_action"] == "STOP"


def test_visibility_uncertainty_is_advisory_and_not_a_pose_hard_fail(storage):
    profile, sig, cutout = _advisory_pattern_profile()
    qa = kf.evaluate_keyframe_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        canonical_signature=sig,
        reference_signatures=[],
        spec=spec_mod.KEYFRAME_ROLES["NEUTRAL_IDLE"],
        vlm_qa={**VLM_KF_OK, "required_regions_visible": "unknown"},
    )
    result = _attach_keyframe_business(qa)

    # Legacy evidence remains unchanged while vNext separates visibility.
    assert qa["checks"]["pose"] == canonical_qa.PASS
    assert qa["business_domains"]["visibility"] == canonical_qa.REVIEW
    assert qa["vlm"]["required_regions_visible"] == "unknown"
    assert result["authority_evidence"]["QUALITY_ADVISORY"]["keyframe_visibility"] == "REVIEW"
    assert result["integrity_status"] == "PASS"
    assert result["retry_action"] == "STOP"


def test_high_confidence_wrong_pose_is_business_hard_failure(storage):
    profile, sig, cutout = _advisory_pattern_profile()
    qa = kf.evaluate_keyframe_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        canonical_signature=sig,
        reference_signatures=[],
        spec=spec_mod.KEYFRAME_ROLES["NEUTRAL_IDLE"],
        vlm_qa={**VLM_KF_OK, "pose_matches": "no", "pose_confidence": "high"},
    )
    result = _attach_keyframe_business(qa)

    assert qa["business_domains"]["pose_correctness"] == canonical_qa.FAIL
    assert result["authority_evidence"]["INTEGRITY_HARD"]["keyframe_pose_integrity"] == "FAIL"
    assert result["retry_action"] == "REGENERATE"


def test_high_confidence_wrong_pet_is_business_hard_failure(storage):
    profile, sig, cutout = _advisory_pattern_profile()
    qa = kf.evaluate_keyframe_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        canonical_signature=sig,
        reference_signatures=[],
        spec=spec_mod.KEYFRAME_ROLES["NEUTRAL_IDLE"],
        vlm_qa={**VLM_KF_OK, "same_pet": "no", "same_pet_confidence": "high"},
    )
    result = _attach_keyframe_business(qa)

    assert result["authority_evidence"]["INTEGRITY_HARD"]["vlm_same_pet"] == "FAIL"
    assert result["retry_action"] == "REGENERATE"


def test_severe_anatomy_issue_is_business_hard_failure(storage):
    profile, sig, cutout = _advisory_pattern_profile()
    qa = kf.evaluate_keyframe_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        canonical_signature=sig,
        reference_signatures=[],
        spec=spec_mod.KEYFRAME_ROLES["NEUTRAL_IDLE"],
        vlm_qa={**VLM_KF_OK, "anatomy_plausible": "no"},
    )
    result = _attach_keyframe_business(qa)

    assert qa["business_domains"]["anatomy"] == canonical_qa.FAIL
    assert result["authority_evidence"]["INTEGRITY_HARD"]["vlm_anatomy"] == "FAIL"
    assert result["retry_action"] == "REGENERATE"


@pytest.mark.parametrize(
    "contamination",
    [
        {"single_pet": "no"},
        {"human_present": "yes"},
    ],
)
def test_duplicate_pet_or_human_contamination_is_business_hard_failure(storage, contamination):
    profile, sig, cutout = _advisory_pattern_profile()
    qa = kf.evaluate_keyframe_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        canonical_signature=sig,
        reference_signatures=[],
        spec=spec_mod.KEYFRAME_ROLES["NEUTRAL_IDLE"],
        vlm_qa={**VLM_KF_OK, **contamination},
    )
    result = _attach_keyframe_business(qa)

    assert result["authority_evidence"]["INTEGRITY_HARD"]["vlm_composition"] == "FAIL"
    assert result["retry_action"] == "REGENERATE"


def test_strong_keyframe_face_contradiction_requires_strong_evidence(storage):
    profile, sig, cutout = _advisory_pattern_profile()
    contradiction = {**VLM_KF_OK, "face_head_consistent": "no"}

    weak = kf.evaluate_keyframe_candidate(
        cutout_rgba=ids.load_rgba(cutout), profile=profile,
        canonical_signature=sig, reference_signatures=[],
        spec=spec_mod.KEYFRAME_ROLES["NEUTRAL_IDLE"], vlm_qa=contradiction,
    )
    weak_result = _attach_keyframe_business(weak)
    assert weak["business_signals"]["keyframe_face_head_identity"] == canonical_qa.REVIEW
    assert weak_result["retry_action"] == "STOP"

    profile.visual_identity["same_individual_gate"]["strict_lineage_reference_count"] = 2
    strong = kf.evaluate_keyframe_candidate(
        cutout_rgba=ids.load_rgba(cutout), profile=profile,
        canonical_signature=sig, reference_signatures=[],
        spec=spec_mod.KEYFRAME_ROLES["NEUTRAL_IDLE"], vlm_qa=contradiction,
    )
    strong_result = _attach_keyframe_business(strong)
    assert strong["business_signals"]["keyframe_face_head_identity"] == canonical_qa.FAIL
    assert strong_result["retry_action"] == "REGENERATE"


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
    first = k.candidates[0].qa_result["business_qa"]
    assert first["delivery_action"] in ("DELIVER", "DELIVER_WITH_ADVISORY")
    assert first["retry_action"] == "STOP"
    assert first["authority_profile"] == business_qa.KEYFRAME_IDENTITY_AUTHORITY_VERSION

    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "1")
    install_kf_vlm(monkeypatch, None)  # legacy REVIEW → business delivery, no extra spend
    primary = FakeProvider("runway", [GOOD()] * 10)
    fallback = FakeProvider("gpt_image", [GOOD()] * 10)
    k2 = _build_kf(h, [primary, fallback], role="LOOK_UP")
    assert primary.calls == 1 and fallback.calls == 0


def test_candidates_persist_raw_and_cutout(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    k = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD()])])
    for c in k.candidates:
        assert c.raw_object_path and "/keyframes/neutral_idle/v1/" in c.raw_object_path
        assert c.cutout_object_path and c.cutout_object_path in storage
        assert c.raw_object_path in storage
        # 클린 플레이트는 raw/cutout 과 **별개 객체**다 — 셋 다 남는다.
        assert c.plate_object_path and c.plate_object_path.endswith("_plate.png")
        assert c.plate_object_path in storage
        assert len({c.raw_object_path, c.cutout_object_path, c.plate_object_path}) == 3


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
    assert {r.derived_kind for r in kf_generated} == {
        "keyframe_raw", "keyframe_cutout", "keyframe_plate",
    }
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
        providers_mod,
        "resolve_keyframe_providers",
        lambda: [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])],
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


# ══════════════════════════════════════════════════════════════════════════
# REVIEW 회복 — QA 재실행 (프로바이더 재호출 없음)
# ══════════════════════════════════════════════════════════════════════════


def test_qa_rerun_reuses_existing_candidate_and_flips_review_to_pass(storage, monkeypatch):
    """QA 규칙이 업데이트되면(버전 상승) REVIEW 키프레임을 재구매 없이 재판정한다."""
    h, _canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, None)  # 포즈 VLM 확언 없음 → REVIEW
    primary = FakeProvider("runway", [GOOD()])

    k = _build_kf(h, [primary])
    assert k.status == kf.STATUS_COMPLETE
    assert k.candidates[0].decision == "REVIEW"
    calls_after_build = primary.calls
    stale_candidate_id = k.candidates[0].id

    # QA 규칙 튜닝을 흉내낸다 (버전 상승) — 그리고 이번엔 VLM 이 포즈를 확언한다.
    monkeypatch.setattr(kf, "KEYFRAME_QA_VERSION", "keyframe-qa-v999-test")
    install_kf_vlm(monkeypatch, VLM_KF_OK)

    updated = _run(
        kf.reevaluate_keyframe_candidate(
            user_id=USER,
            pet_id=PET,
            keyframe_id=k.id,
            candidate_id=stale_candidate_id,
            fetch_bytes=h.kf_fetch,
            cutout_fn=lambda raw: raw,
        )
    )

    assert updated.status == kf.STATUS_COMPLETE
    assert updated.selected_candidate_id == stale_candidate_id
    assert next(c for c in updated.candidates if c.id == stale_candidate_id).decision == "PASS"
    # No new provider (image generation) call — this is a pure re-judgment.
    assert primary.calls == calls_after_build


def test_qa_rerun_is_idempotent_and_makes_no_provider_call(storage, monkeypatch):
    """같은 QA 버전으로 다시 부르면 아무 것도 다시 계산하지 않는다 (deduplicated)."""
    h, _canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    primary = FakeProvider("runway", [GOOD()])

    k = _build_kf(h, [primary])
    assert k.status == kf.STATUS_COMPLETE
    calls_after_build = primary.calls

    def fail_if_called(*args, **kwargs):
        raise AssertionError("reevaluate_keyframe_candidate must never call an image provider")

    monkeypatch.setattr(providers_mod, "resolve_keyframe_providers", fail_if_called)

    again = _run(
        kf.reevaluate_keyframe_candidate(
            user_id=USER,
            pet_id=PET,
            keyframe_id=k.id,
            candidate_id=k.selected_candidate_id,
            fetch_bytes=h.kf_fetch,
            cutout_fn=lambda raw: raw,
        )
    )
    assert again.deduplicated is True
    assert again.status == kf.STATUS_COMPLETE
    assert primary.calls == calls_after_build
