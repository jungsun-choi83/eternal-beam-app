"""
정본 펫 빌더 (Phase 4) 계약 테스트.

프로바이더는 전부 가짜다 — 유닛 테스트에서 실 결제 호출은 절대 없다.
QA/선택/버전/근거 로직은 실제 코드로 검증한다 (합성 이미지 + 주입된 VLM 결과).
"""

from __future__ import annotations

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import assets as assets_router
from backend.routers import canonical_v1
from backend.services import canonical_image_providers as providers_mod
from backend.services import canonical_pet_service as svc
from backend.services import canonical_qa
from backend.services import durable_provider_jobs
from backend.services import pet_identity_service as ids
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry, vlm_identity
from backend.services.canonical_image_providers import (
    CanonicalImageProvider,
    CanonicalImageResult,
    CanonicalProviderError,
)

from .conftest import ASGITestClient, make_jpeg_bytes
from .test_pet_identity_profile import make_pet_cutout_png, make_striped_cutout_png
from .test_pet_reference_sets import PET, USER, Harness, cls


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    # 합성 이미지(200×150)가 실사 해상도 게이트에 걸리지 않도록 낮춘다.
    monkeypatch.setenv("CANONICAL_QA_MIN_RESOLUTION", "100")
    for m in (refs, pet_registry, ids, sets, svc):
        m.__reset_for_tests()
    yield
    for m in (refs, pet_registry, ids, sets, svc):
        m.__reset_for_tests()


@pytest.fixture
def uploads(monkeypatch) -> list[str]:
    from backend.services import supabase_assets

    paths: list[str] = []

    async def fake_upload(path, data, content_type):
        paths.append(path)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    return paths


def _run(coro):
    return anyio.run(lambda: coro)


VLM_QA_OK = {
    "same_pet": "yes",
    "same_pet_confidence": "high",
    "anatomy_plausible": "yes",
    "single_pet": "yes",
    "human_present": "no",
    "accessories_present": "no",
    "background_neutral": "yes",
    "pose_neutral": "yes",
    "full_body_visible": "yes",
    "major_occlusion": "no",
    "identity_notes": "",
    "source": vlm_identity.VLM_CANONICAL_QA_VERSION,
    "model": "test-stub",
}


def install_vlm_qa(monkeypatch, result):
    monkeypatch.setattr(
        vlm_identity, "qa_canonical_image", lambda candidate, references, candidate_mime="image/png": result
    )


class FakeProvider(CanonicalImageProvider):
    """결정된 이미지 시퀀스를 돌려주는 가짜 프로바이더. 호출 수를 센다."""

    def __init__(self, name: str, images: list | None = None, model: str = "fake-1"):
        self.name = name
        self._images = list(images or [])
        self._model = model
        self.calls = 0

    def available(self) -> bool:
        return True

    def model_name(self) -> str:
        return self._model

    def generate(self, references, prompt, output_spec, metadata):
        self.calls += 1
        if not self._images:
            raise CanonicalProviderError("PROVIDER_FAILED", "no more fake images")
        item = self._images.pop(0)
        if isinstance(item, Exception):
            raise item
        return CanonicalImageResult(
            image_bytes=item,
            provider=self.name,
            model=self._model,
            external_job_id=f"{self.name}-job-{self.calls}",
        )


def _seed_three_ref_pet(monkeypatch) -> Harness:
    """FACE + FULL_BODY + 3Q 커버리지의 펫 (Phase 3 하네스 재사용)."""
    h = Harness()
    h.seed(cutout=make_pet_cutout_png(), classification=cls(view="FRONT", face_visible="yes"))
    h.seed(
        cutout=make_pet_cutout_png(),
        classification=cls(view="LEFT", full_body_visible="yes", tail_visible="yes"),
    )
    h.seed(
        cutout=make_pet_cutout_png(),
        classification=cls(view="FRONT_RIGHT_3Q", full_body_visible="yes"),
    )
    h.install_vlm(monkeypatch)
    return h


def _build(h: Harness, providers, **kw):
    return _run(
        svc.build_canonical(
            user_id=USER,
            pet_id=PET,
            fetch_bytes=h.fetch,
            providers=providers,
            cutout_fn=lambda raw: raw,  # 가짜 프로바이더가 RGBA PNG 를 내므로 그대로 누끼
            **kw,
        )
    )


GOOD = make_pet_cutout_png  # 시드 누끼와 같은 코트 → 신원 시그니처 일치


def test_durable_builder_resumes_one_building_version(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
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
        _build(h, [provider])
    rows = _run(svc._version_rows(PET))
    assert len(rows) == 1 and rows[0]["status"] == svc.STATUS_BUILDING

    completed = _build(h, [provider])
    assert completed.status == svc.STATUS_COMPLETE
    assert len(_run(svc._version_rows(PET))) == 1
    assert len(completed.candidates) == 1


# ══════════════════════════════════════════════════════════════════════════
# 입력 요건 / 프로바이더 구성
# ══════════════════════════════════════════════════════════════════════════


def test_reference_set_is_required(uploads):
    with pytest.raises(svc.CanonicalPetError) as e:
        _run(svc.build_canonical(user_id=USER, pet_id=PET, providers=[FakeProvider("runway", [GOOD()])]))
    assert e.value.code == "NO_ORIGINAL_REFERENCES"


def test_unconfigured_providers_fail_closed_before_any_row(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)

    class Unavailable(CanonicalImageProvider):
        name = "runway"

        def available(self):
            return False

    with pytest.raises(svc.CanonicalPetError) as e:
        _build(h, [Unavailable()])
    assert e.value.code == "PROVIDER_NOT_CONFIGURED" and e.value.status == 503
    assert _run(svc._version_rows(PET)) == []  # 버전 행도, 과금도 없다


def test_provider_registry_and_mock_mode(monkeypatch):
    assert providers_mod.get_provider("runway").name == "runway"
    assert providers_mod.get_provider("gpt_image").name == "gpt_image"
    monkeypatch.setenv("CANONICAL_GENERATION_MOCK", "1")
    resolved = providers_mod.resolve_providers()
    assert [p.name for p in resolved] == ["mock"]


# ══════════════════════════════════════════════════════════════════════════
# 성공 경로 / 레퍼런스 선택 / 프롬프트
# ══════════════════════════════════════════════════════════════════════════


def test_primary_success_with_three_complementary_references(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    primary = FakeProvider("runway", [GOOD(), GOOD(), GOOD()])
    fallback = FakeProvider("gpt_image", [GOOD()])

    v = _build(h, [primary, fallback])

    assert v.status == svc.STATUS_COMPLETE
    assert v.version == 1
    roles = [p["role"] for p in v.output_spec["input_references"]]
    assert roles == ["PRIMARY_FACE", "PRIMARY_FULL_BODY", "PRIMARY_3Q"]
    assert len(v.input_reference_ids) == 3
    assert fallback.calls == 0  # PRIMARY 가 통과하면 FALLBACK 은 호출되지 않는다
    # 점진적 조기 중단: stop_after_passes 기본 1 — 첫 PASS 에서 즉시 멈춘다.
    assert primary.calls == 1
    sel = next(c for c in v.candidates if c.selected)
    assert sel.provider == "runway" and sel.decision == "PASS"
    assert sel.qa_result["identity_similarity"] is not None
    assert v.qa_summary["canonical_confidence"] == "normal"


def test_one_reference_limited_case_lowers_confidence(uploads, monkeypatch):
    h = Harness()
    h.seed(cutout=make_pet_cutout_png(), classification=cls(view="FRONT", face_visible="yes"))
    h.install_vlm(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)

    v = _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])
    assert v.status == svc.STATUS_COMPLETE
    assert len(set(v.input_reference_ids)) == 1
    assert v.qa_summary["canonical_confidence"] == "low"  # 없는 증거를 지어내지 않는다


def test_prompt_uses_known_traits_and_never_invents_unknowns(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    v = _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])

    prompt = v.prompt or ""
    assert v.prompt_version == "canonical-prompt-v1"
    # Phase 2 가 실측한 코트 색은 제약으로 들어간다.
    assert "brown" in prompt.lower()
    # UNKNOWN 특성은 문장이 되지 않는다 (귀 모양은 semantic unknown 상태다).
    assert "unknown" not in prompt.lower()
    assert "Ear shape" not in prompt
    # 정면 3/4·중립 배경·펫 단독 사양이 들어 있다.
    assert "three-quarter" in prompt
    assert "No human" in prompt


def test_no_theme_vocabulary_in_prompt(uploads, monkeypatch):
    from backend.services.theme_catalog import ALL_THEME_KEYS

    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    v = _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])
    prompt = (v.prompt or "").lower()
    for key in ALL_THEME_KEYS:
        assert key.replace("_", " ") not in prompt
        assert key not in prompt


# ══════════════════════════════════════════════════════════════════════════
# 폴백 / 실패 구분 / 상한
# ══════════════════════════════════════════════════════════════════════════


def test_primary_provider_failure_falls_back(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    err = CanonicalProviderError("PROVIDER_FAILED", "boom")
    primary = FakeProvider("runway", [err, err, err])
    fallback = FakeProvider("gpt_image", [GOOD(), GOOD()])

    v = _build(h, [primary, fallback])
    assert v.status == svc.STATUS_COMPLETE
    sel = next(c for c in v.candidates if c.selected)
    assert sel.provider == "gpt_image"
    # 프로바이더 실패는 ERROR 로 기록된다 — QA 실패(FAIL)와 구분된다.
    errors = [c for c in v.candidates if c.decision == "ERROR"]
    assert len(errors) == 3 and all(c.error for c in errors)


def test_primary_qa_failure_falls_back(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    # 전혀 다른 코트의 이미지 → 신원 시그니처/코트 계열 FAIL.
    primary = FakeProvider("runway", [make_striped_cutout_png(), make_striped_cutout_png(), make_striped_cutout_png()])
    fallback = FakeProvider("gpt_image", [GOOD()])

    v = _build(h, [primary, fallback])
    assert v.status == svc.STATUS_COMPLETE
    assert next(c for c in v.candidates if c.selected).provider == "gpt_image"
    assert all(c.decision == "FAIL" for c in v.candidates if c.provider == "runway")


def test_candidate_limits_are_enforced_and_configurable(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    # VLM QA 없음 → 후보는 최대 REVIEW → PASS 0 → 상한까지 시도 후 폴백도 상한까지.
    install_vlm_qa(monkeypatch, None)
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "2")
    monkeypatch.setenv("CANONICAL_MAX_FALLBACK", "1")
    primary = FakeProvider("runway", [GOOD()] * 10)
    fallback = FakeProvider("gpt_image", [GOOD()] * 10)

    v = _build(h, [primary, fallback])
    assert primary.calls == 2 and fallback.calls == 1
    assert v.qa_summary["candidate_count"] == 3


def test_review_status_without_vlm_confirmation(uploads, monkeypatch):
    """합성 임계값만으로는 절대 자동 승인되지 않는다 — VLM 확언 없으면 REVIEW."""
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, None)

    v = _build(h, [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])])
    assert v.status == svc.STATUS_REVIEW
    assert v.selected_candidate_id is None
    assert all(c.decision == "REVIEW" for c in v.candidates)
    # 선택되지 않았으므로 generated 대장 기록도 없다.
    ledger = _run(refs.list_references(user_id=USER, pet_id=PET))
    assert not any(r.role == refs.ROLE_GENERATED for r in ledger)


# ══════════════════════════════════════════════════════════════════════════
# 저장 / 근거 / 버전 / 결정론
# ══════════════════════════════════════════════════════════════════════════


def test_candidates_persist_raw_and_cutout(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    v = _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])

    for c in v.candidates:
        assert c.raw_object_path and "_raw.png" in c.raw_object_path
        assert c.cutout_object_path and "_cutout.png" in c.cutout_object_path
        assert c.raw_object_path in uploads and c.cutout_object_path in uploads
    # raw(증거)와 cutout(파생)은 서로 다른 객체다 — raw 는 파괴되지 않는다.
    assert all("canonical/v1/" in p for p in uploads if "canonical" in p)


def test_provenance_chain_to_originals(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    v = _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])

    refset = _run(sets.get_set(user_id=USER, pet_id=PET, version=v.reference_set_version))
    ledger = _run(refs.list_references(user_id=USER, pet_id=PET))
    original_ids = {r.id for r in ledger if r.role == refs.ROLE_ORIGINAL}

    assert set(v.input_reference_ids) <= set(refset.source_reference_ids) <= original_ids
    generated = [r for r in ledger if r.role == refs.ROLE_GENERATED]
    assert {r.derived_kind for r in generated} == {"canonical_raw", "canonical_cutout"}
    for g in generated:
        assert g.diagnostics["canonical_version_id"] == v.id
        assert set(g.diagnostics["input_reference_ids"]) <= original_ids


def test_generated_role_never_becomes_original_evidence(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])

    before_originals = {r.id for r in _run(refs.list_references(user_id=USER, pet_id=PET)) if r.role == refs.ROLE_ORIGINAL}
    # 강제 재빌드된 레퍼런스 세트도 생성물을 근거로 삼지 않는다.
    refset = _run(sets.build_reference_set(user_id=USER, pet_id=PET, fetch_bytes=h.fetch, skip_if_unchanged=False))
    assert set(refset.source_reference_ids) == before_originals
    for item in refset.items:
        assert item["reference_id"] in before_originals


def test_canonical_versioning_is_immutable(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    v1 = _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])
    assert v1.version == 1

    # 새 원본 → 새 레퍼런스 세트 → 새 정본 버전. V1 은 그대로 남는다.
    h.seed(cutout=make_pet_cutout_png(), classification=cls(view="RIGHT", full_body_visible="yes"))
    v2 = _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])
    assert v2.version == 2 and v2.reference_set_version == 2

    old = _run(svc.get_canonical(user_id=USER, pet_id=PET, version=1))
    assert old.id == v1.id and old.reference_set_version == 1
    assert old.selected_candidate_id == v1.selected_candidate_id


def test_idempotent_build_does_not_repay(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    primary = FakeProvider("runway", [GOOD()] * 10)

    first = _build(h, [primary])
    calls_after_first = primary.calls
    second = _build(h, [primary])

    assert second.deduplicated is True and second.version == first.version
    assert primary.calls == calls_after_first  # 중복 과금 없음


def test_deterministic_ranking_given_fixed_qa(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    monkeypatch.setenv("CANONICAL_STOP_AFTER_PASSES", "3")

    a = _build(h, [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])], skip_if_unchanged=False)
    b = _build(h, [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])], skip_if_unchanged=False)

    sa = next(c for c in a.candidates if c.selected)
    sb = next(c for c in b.candidates if c.selected)
    assert (sa.provider, sa.attempt, sa.decision) == (sb.provider, sb.attempt, sb.decision)
    assert a.qa_summary["decisions"] == b.qa_summary["decisions"]


def test_ownership_isolation(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    with pytest.raises(svc.CanonicalPetError) as e:
        _run(svc.build_canonical(user_id="mallory@test", pet_id=PET, providers=[FakeProvider("runway", [GOOD()])]))
    assert e.value.code == "PET_NOT_OWNED"

    install_vlm_qa(monkeypatch, VLM_QA_OK)
    _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])
    with pytest.raises(svc.CanonicalPetError):
        _run(svc.get_canonical(user_id="mallory@test", pet_id=PET))


# ══════════════════════════════════════════════════════════════════════════
# 온보딩 보존 / 평가 하네스 / 라우터
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def assets_client(uploads) -> ASGITestClient:
    app = FastAPI()
    app.include_router(assets_router.router, prefix="/api")
    return ASGITestClient(app)


def test_canonical_failure_does_not_break_onboarding(assets_client, uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    err = CanonicalProviderError("PROVIDER_FAILED", "provider down")
    v = _build(h, [FakeProvider("runway", [err, err, err])])
    assert v.status == svc.STATUS_FAILED  # 정본 실패는 정본에만 머문다

    res = assets_client.post(
        "/api/assets/original",
        files={"file": ("dog.jpg", make_jpeg_bytes(64, 64), "image/jpeg")},
        data={"user_id": USER, "content_id": "cid1"},
    )
    assert res.status_code == 200


def test_evaluation_harness_records_and_summarizes(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    v = _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])
    sel = next(c for c in v.candidates if c.selected)

    _run(
        svc.record_evaluation(
            user_id=USER, pet_id=PET, canonical_version_id=v.id, candidate_id=sel.id,
            scores={"face_identity": 9, "markings": 8, "body_proportions": 9,
                    "tail_ears_paws": 7, "anatomy": 9, "overall_same_pet": 9},
            verdict="PASS", notes="looks like the same dog",
        )
    )
    summary = _run(svc.evaluation_summary(user_id=USER))
    assert summary["providers"]["runway"]["count"] == 1
    assert summary["providers"]["runway"]["mean_scores"]["overall_same_pet"] == 9.0
    assert summary["providers"]["runway"]["verdicts"]["PASS"] == 1

    with pytest.raises(svc.CanonicalPetError):
        _run(
            svc.record_evaluation(
                user_id=USER, pet_id=PET, canonical_version_id=v.id, candidate_id=sel.id,
                scores={"anatomy": 99}, verdict="PASS",
            )
        )


AUTH = {"Authorization": "Bearer test:alice@test"}


@pytest.fixture
def canonical_client(monkeypatch) -> ASGITestClient:
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    app = FastAPI()
    app.include_router(canonical_v1.router, prefix="/api")
    return ASGITestClient(app)


def test_router_build_get_and_review(canonical_client, uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    monkeypatch.setattr(ids, "_default_fetch_bytes", h.fetch)
    monkeypatch.setattr(
        providers_mod, "resolve_providers", lambda: [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])]
    )
    monkeypatch.setattr(svc, "_default_cutout_fn", lambda raw: raw)

    res = canonical_client.post(f"/api/v1/pet/canonical/{PET}/build", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "complete" and body["version"] == 1
    assert body["selected_candidate_id"]

    res = canonical_client.get(f"/api/v1/pet/canonical/{PET}", headers=AUTH)
    assert res.status_code == 200
    assert res.json()["candidates"]

    res = canonical_client.get(f"/api/v1/pet/canonical/{PET}/review", headers=AUTH)
    assert res.status_code == 200
    review = res.json()
    assert len(review["references"]) == 3
    assert review["references"][0]["role"] == "PRIMARY_FACE"
    assert review["candidates"][0]["qa_result"]["decision"] in ("PASS", "REVIEW", "FAIL")

    res = canonical_client.get(
        f"/api/v1/pet/canonical/{PET}", headers={"Authorization": "Bearer test:mallory@test"}
    )
    assert res.status_code == 403


def test_router_404_without_versions(canonical_client, uploads, monkeypatch):
    _seed_three_ref_pet(monkeypatch)
    res = canonical_client.get(f"/api/v1/pet/canonical/{PET}", headers=AUTH)
    assert res.status_code == 404
