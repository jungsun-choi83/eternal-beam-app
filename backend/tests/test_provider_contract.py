"""
프로바이더 계약 (Runway promptText ≤ 1000자 — 라이브 검증) 테스트.

계약 위반은 QA 실패가 아니다: 재시도를 소모하지 않고, 폴백을 태우지 않으며,
과금 호출 전에 로컬에서 차단된다.
"""

from __future__ import annotations

import pytest

from backend.services import action_keyframe_service as kf
from backend.services import action_keyframe_spec as kf_spec
from backend.services import canonical_pet_service as canon
from backend.services import canonical_prompt as cp
from backend.services import pet_identity_service as ids
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry
from backend.services.canonical_image_providers import (
    CanonicalProviderError,
    RunwayImageProvider,
)

from .test_canonical_pet_builder import (
    GOOD,
    VLM_QA_OK,
    FakeProvider,
    _build,
    _seed_three_ref_pet,
    install_vlm_qa,
)
from .test_pet_reference_sets import PET, USER

RICH_VISUAL = {
    "coat": {
        "status": "measured",
        "dominant_colors": [
            {"name": "black", "fraction": 0.6},
            {"name": "tan", "fraction": 0.3},
        ],
    },
    "semantic_traits": {
        "status": "vlm",
        "traits": {
            "species": "dog",
            "ears": {"shape": "erect", "color_markings": "unknown"},
            "coat": {
                "marking_distribution": "black covers the head, neck, back and sides; "
                "tan on cheeks, chest, lower legs and underside " * 5,  # 길게
                "length": "medium",
            },
        },
    },
}


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
def uploads(monkeypatch) -> list[str]:
    from backend.services import supabase_assets

    paths: list[str] = []

    async def fake_upload(path, data, content_type):
        paths.append(path)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    return paths


# ── 컴팩트 프롬프트 ─────────────────────────────────────────────────────────


def test_compact_canonical_prompt_fits_1000_and_keeps_high_confidence_traits():
    full = cp.build_canonical_prompt(
        visual_identity=RICH_VISUAL, structural_identity={}, reference_roles=["PRIMARY_FACE"]
    )
    assert len(full) > 1000  # 전체 내부 프롬프트는 실제로 상한을 넘는다 (라이브 재현)

    compact = cp.build_compact_canonical_prompt(visual_identity=RICH_VISUAL, max_chars=1000)
    assert len(compact) <= 1000
    assert "Coat colors: black, tan." in compact  # 고신뢰 실측 특성 유지
    assert "The pet is a dog." in compact
    assert "unknown" not in compact.lower()       # UNKNOWN 발명 금지
    assert "exact same pet" in compact            # 신원은 레퍼런스 이미지가 정본


def test_compact_keyframe_prompt_fits_1000():
    spec = kf_spec.KEYFRAME_ROLES["LIE"]
    compact = kf_spec.build_compact_keyframe_prompt(spec, RICH_VISUAL, max_chars=1000)
    assert len(compact) <= 1000
    assert "Requested pose:" in compact and "lying down naturally" in compact
    assert "unknown" not in compact.lower()


def test_compact_drops_whole_trait_lines_never_mid_sentence():
    tight = cp.build_compact_canonical_prompt(visual_identity=RICH_VISUAL, max_chars=600)
    assert len(tight) <= 600  # 특성 줄이 전부 떨어져 나가고 베이스만 남는다
    assert tight.endswith((".",))  # 문장 중간 절단 없음


# ── Runway 어댑터 로컬 검증 (과금 전) ───────────────────────────────────────


def test_runway_local_guard_blocks_oversized_prompt_without_http(monkeypatch):
    monkeypatch.delenv("RUNWAY_API_KEY", raising=False)  # 키조차 필요 없다 — 순수 로컬 검증
    with pytest.raises(CanonicalProviderError) as e:
        RunwayImageProvider().generate([], "x" * 1001, {}, {})
    assert e.value.code == "PROVIDER_CONTRACT"

    monkeypatch.setenv("RUNWAY_MAX_PROMPT_CHARS", "2000")  # 계약은 env 로 조정 가능
    with pytest.raises(CanonicalProviderError) as e2:
        RunwayImageProvider().generate([], "x" * 1001, {}, {})
    assert e2.value.code == "PROVIDER_NOT_CONFIGURED"  # 이제 길이는 통과, 키 검증에서 멈춤


# ── 빌더 통합 ───────────────────────────────────────────────────────────────


def _inject_rich_profile(monkeypatch):
    """프로필의 visual_identity 를 라이브급으로 부풀려 full 프롬프트가 1000자를 넘게 한다."""
    original = ids.build_identity_profile

    async def patched(**kwargs):
        p = await original(**kwargs)
        merged = {**p.visual_identity, **RICH_VISUAL}
        object.__setattr__(p, "visual_identity", merged)
        # 저장된 행에도 반영 (get_profile 재조회 대비)
        for row in ids._MOCK_PROFILES:
            if row["pet_id"] == p.pet_id and row["version"] == p.version:
                row["visual_identity"] = merged
        return p

    monkeypatch.setattr(ids, "build_identity_profile", patched)


def test_limited_provider_receives_compact_prompt(uploads, monkeypatch):
    _inject_rich_profile(monkeypatch)
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)

    class Recording(FakeProvider):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.prompts: list[str] = []

        def generate(self, references, prompt, output_spec, metadata):
            self.prompts.append(prompt)
            return super().generate(references, prompt, output_spec, metadata)

    primary = Recording("runway", [GOOD(), GOOD()])
    primary.max_prompt_chars = 1000

    v = _build(h, [primary])
    assert v.status == canon.STATUS_COMPLETE
    assert all(len(p) <= 1000 for p in primary.prompts)  # 상한 준수
    sel = next(c for c in v.candidates if c.selected)
    assert sel.generation_metadata["prompt_variant"] == "compact"
    assert sel.generation_metadata["prompt_chars"] <= 1000
    # 버전 행의 전체 내부 프롬프트는 그대로 보존된다 (기록용).
    assert len(v.prompt) > 1000


def test_contract_violation_consumes_no_attempts_and_no_fallback(uploads, monkeypatch):
    _inject_rich_profile(monkeypatch)
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)

    primary = FakeProvider("runway", [GOOD()] * 5)
    primary.max_prompt_chars = 100  # 컴팩트로도 불가능한 상한
    fallback = FakeProvider("gpt_image", [GOOD()] * 5)

    v = _build(h, [primary, fallback])
    assert primary.calls == 0            # 과금 호출 0회 — 로컬에서 차단
    assert fallback.calls == 0           # 계약 위반만으로 폴백을 태우지 않는다
    assert v.status == canon.STATUS_FAILED
    assert "contract violation" in (v.selection_reason or "")
    errors = [c for c in v.candidates if c.decision == "ERROR"]
    assert len(errors) == 1 and errors[0].attempt == 0  # 시도 소모 없음, 감사 기록 1건


def test_adapter_contract_error_stops_repeats(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    err = CanonicalProviderError("PROVIDER_CONTRACT", "promptText too long")
    primary = FakeProvider("runway", [err, GOOD(), GOOD()])
    fallback = FakeProvider("gpt_image", [GOOD()])

    v = _build(h, [primary, fallback])
    assert primary.calls == 1            # 같은 초과 프롬프트로 반복하지 않는다
    assert fallback.calls == 0           # 폴백 금지
    assert v.status == canon.STATUS_FAILED


def test_keyframe_builder_uses_compact_for_limited_provider(uploads, monkeypatch):
    from .test_action_keyframes import VLM_KF_OK, _build_kf, _prepare_canonical, install_kf_vlm

    _inject_rich_profile(monkeypatch)
    # storage dict 픽스처가 필요 — uploads 대신 로컬 구성
    from backend.services import supabase_assets

    store: dict[str, bytes] = {}

    async def fake_upload(path, data, content_type):
        store[path] = bytes(data)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    h, _ = _prepare_canonical(monkeypatch, store)
    install_kf_vlm(monkeypatch, VLM_KF_OK)

    provider = FakeProvider("runway", [GOOD(), GOOD()])
    provider.max_prompt_chars = 1000
    k = _build_kf(h, [provider])
    assert k.status == kf.STATUS_COMPLETE
    sel = next(c for c in k.candidates if c.selected)
    assert sel.generation_metadata["prompt_variant"] == "compact"
    assert sel.generation_metadata["prompt_chars"] <= 1000
