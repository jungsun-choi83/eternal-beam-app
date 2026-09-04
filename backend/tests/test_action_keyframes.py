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
    # 현재 런타임 행동은 전부 NEUTRAL_IDLE 로 흡수된다 (다대일 재사용).
    for aid in ("BREATHING", "BLINKING", "COME_CLOSER", "TOUCH", "IDLE_BREATH"):
        assert spec_mod.role_for_action(aid) == "NEUTRAL_IDLE"
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
