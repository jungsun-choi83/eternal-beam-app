"""
정본 프롬프트 v3 계약 테스트 — 사진별 기여 가이드 + 개체 신원 사실.

유료 호출은 없다 (가짜 프로바이더, 주입된 VLM 결과). 검증 대상:
  * 1장 / 2–3장, 다른 각도, 불완전한 VLM 근거 — 아는 것만 말하고 unknown 은 침묵.
  * 공급 순서 == 프롬프트의 "Reference N" 순서 == 프로바이더 입력 순서.
  * 실측 코트 색·영역 분포·VLM 무늬/고유 특징·형태 프로필의 문자열 관측이
    실제 프로바이더 프롬프트까지 도달한다. 수치 형태 클래스·품종은 도달하지 않는다.
  * Runway 1000자 계약: 컴팩트 v3 가 상한 안에서 중요도 순으로 줄을 떨어뜨린다.
  * 키프레임이 공유하는 v2 헬퍼의 출력은 그대로다.
"""

from __future__ import annotations

from types import SimpleNamespace

import anyio
import pytest

from backend.services import canonical_pet_service as svc
from backend.services import canonical_prompt as cp
from backend.services import canonical_reference_selector as selector
from backend.services import durable_provider_jobs
from backend.services import pet_identity_service as ids
from backend.services import pet_morphology_service as morph
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry, vlm_identity

from .test_canonical_pet_builder import VLM_QA_OK, FakeProvider, distinct_view_cutout, install_vlm_qa
from .test_pet_identity_profile import make_pet_cutout_png
from .test_pet_reference_sets import PET, USER, VIS_KEYS, Harness, cls


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    monkeypatch.setenv("CANONICAL_QA_MIN_RESOLUTION", "100")
    for m in (refs, pet_registry, ids, morph, sets, svc):
        m.__reset_for_tests()
    durable_provider_jobs.__reset_for_tests()
    yield
    for m in (refs, pet_registry, ids, morph, sets, svc):
        m.__reset_for_tests()
    durable_provider_jobs.__reset_for_tests()


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


# ══════════════════════════════════════════════════════════════════════════
# 합성 입력
# ══════════════════════════════════════════════════════════════════════════


def _analysis(view="UNKNOWN", vconf="high", **visible) -> dict:
    vis = {k: "unknown" for k in VIS_KEYS}
    vis.update(visible)
    return {
        "eligibility": {"usable_for_identity": True, "reasons": []},
        "quality": {"base_quality": 0.8},
        "consistency": {"label": "CONSISTENT"},
        "classification": {"view_label": view, "view_confidence": vconf, "visibility": vis},
    }


def _refset(analysis: dict[str, dict]) -> SimpleNamespace:
    return SimpleNamespace(
        source_reference_ids=sorted(analysis),
        reference_analysis=analysis,
        morphology_profile_version=1,
    )


THREE_VIEWS = {
    "face": _analysis(view="FRONT", face_visible="yes", ears_visible="yes"),
    "body": _analysis(view="LEFT", full_body_visible="yes", tail_visible="yes", paws_visible="yes"),
    "q3": _analysis(view="FRONT_RIGHT_3Q", full_body_visible="yes"),
}
THREE_PICKS = [
    {"reference_id": "face", "role": "PRIMARY_FACE"},
    {"reference_id": "body", "role": "PRIMARY_FULL_BODY"},
    {"reference_id": "q3", "role": "PRIMARY_3Q"},
]


def _fused(value: str, confidence: str = "high") -> dict:
    return {"status": "fused", "value": value, "confidence": confidence, "support_reference_ids": ["a", "b"]}


RICH_VISUAL_IDENTITY = {
    "status": "measured",
    "coat": {
        "status": "measured",
        "dominant_colors": [{"name": "brown", "fraction": 0.6}, {"name": "white", "fraction": 0.25}],
        "secondary_colors": [{"name": "black", "fraction": 0.1}],
        "palette": [],
    },
    "coat_pattern": _fused("brown|white|brown"),
    "facial_markings": _fused("white blaze from forehead to nose"),
    "body_markings": _fused("white chest patch / dark saddle over the back"),
    "distinctive_features": {
        "status": "fused",
        "items": [
            {"value": "notch in the left ear", "confidence": "medium", "support_reference_ids": ["a"]},
            {"value": "small white spot on the right hind paw", "confidence": "low", "support_reference_ids": ["b"]},
        ],
    },
    "semantic_traits": {
        "status": "vlm",
        "traits": {
            "species": "dog",
            "ears": {"shape": "floppy"},
            "coat": {"marking_distribution": "saddle pattern", "length": "medium"},
        },
    },
}


def _vlm_traits(**overrides) -> dict:
    """VLM 시맨틱 스키마와 같은 모양의 한 장짜리 관측치."""
    base = {
        "species": "dog",
        "breed_estimate": "Golden Retriever",
        "breed_confidence": "high",
        "face": {"muzzle_color": "black", "facial_markings": "white blaze from forehead to nose"},
        "eyes": {"color": "amber", "surrounding_markings": "unknown"},
        "ears": {"shape": "floppy", "color_markings": "darker brown than the body"},
        "coat": {
            "dominant_colors": ["golden red"],
            "secondary_colors": ["white"],
            "length": "medium",
            "texture": "unknown",
            "marking_distribution": "saddle pattern",
        },
        "body": {"chest_markings": "white chest patch", "torso_markings": "dark saddle over the back"},
        "paws": {"colors_markings": "white socks on the front paws"},
        "tail": {"appearance": "long, plumed", "tip_marking": "white tip"},
        "unique_features": ["notch in the left ear"],
    }
    base.update(overrides)
    return base


def _morphology(
    observations: dict[str, dict],
    *,
    tail_form: str | None = "long, plumed",
    ear_form: str | None = "floppy",
) -> SimpleNamespace:
    traits = {
        # 수치 클래스 — 전부 프롬프트에 **나타나면 안 된다**.
        "body_size": {"status": "fused", "class": "large_in_frame", "confidence": "high", "measures": "frame_occupancy"},
        "body_build": {"status": "fused", "class": "stocky", "confidence": "high", "evidence": "measured"},
        "torso_proportion": {"status": "fused", "class": "long", "confidence": "high", "evidence": "measured"},
        "leg_proportion": {"status": "fused", "class": "short", "confidence": "medium", "evidence": "heuristic"},
        "head_proportion": {"status": "fused", "class": "large", "confidence": "medium", "evidence": "heuristic"},
        "muzzle_proportion": {"status": "fused", "class": "short", "confidence": "medium", "evidence": "heuristic"},
        "ear_form": (_fused(ear_form) if ear_form else {"status": "unknown", "reason": "x"}),
        "tail_form": (_fused(tail_form) if tail_form else {"status": "unknown", "reason": "x"}),
    }
    return SimpleNamespace(
        version=1,
        profile={
            "status": "fused",
            "traits": traits,
            "metadata": {"breed_estimate": _fused("Golden Retriever")},
        },
        reference_observations={
            rid: {"reference_id": rid, "semantic_traits": {"status": "present", "traits": t}}
            for rid, t in observations.items()
        },
    )


# ══════════════════════════════════════════════════════════════════════════
# 가이드: 1장 / 여러 장 / 순서 / 불완전한 근거
# ══════════════════════════════════════════════════════════════════════════


def test_single_photo_without_vlm_evidence_is_honest():
    refset = _refset({"only": _analysis()})  # VLM 꺼짐: 뷰 UNKNOWN, 가시성 unknown
    picks = [{"reference_id": "only", "role": selector.ROLE_ONLY_AVAILABLE}]
    guide = selector.reference_guide(refset, picks)

    assert guide == [
        {
            "reference_id": "only",
            "role": "ONLY_AVAILABLE",
            "headline": "the only reference photo",
            "view": None,
            "view_label": None,
            "facets": [],
            "caveats": [],
            "evidence": "none",
            "contributes": [selector._CONTRIB_GENERIC],
        }
    ]
    prompt = cp.build_canonical_prompt(
        visual_identity={}, structural_identity={}, reference_roles=["ONLY_AVAILABLE"], reference_guide=guide
    )
    assert "Reference 1 — the only reference photo: authority for every identity detail it clearly shows." in prompt
    assert "Only one photo is available" in prompt
    assert "Reference 2" not in prompt
    assert "unknown" not in prompt.lower()
    assert "Verified identity facts" not in prompt  # 아는 사실이 없으면 헤더도 없다
    # v2 에서 이어지는 계약 문구.
    assert "three-quarter" in prompt and "No human" in prompt
    assert "No contact shadow under the pet" in prompt and "no cast shadow on the background" in prompt


def test_multi_photo_guide_follows_supplied_order_and_evidence():
    refset = _refset(THREE_VIEWS)
    guide = selector.reference_guide(refset, THREE_PICKS)
    assert [g["reference_id"] for g in guide] == ["face", "body", "q3"]

    face, body, q3 = guide
    assert face["view"] == "front view" and face["evidence"] == "vlm"
    assert face["contributes"] == [selector._CONTRIB_FACE, selector._CONTRIB_EARS]
    assert body["view"] == "left side"
    assert body["contributes"] == [
        selector._CONTRIB_BODY,
        selector._CONTRIB_COAT_DISTRIBUTION,
        selector._CONTRIB_GROOMING,
        selector._CONTRIB_SIDE.format(side="left"),
        selector._CONTRIB_TAIL,
        selector._CONTRIB_PAWS,
    ]
    assert q3["view"] == "front-right three-quarter view"
    # 3/4 뷰는 얼굴도 보여 준다 (v3.1: FACE_VIEWS) — 얼굴·귀 권위가 앞에 붙는다.
    assert q3["contributes"] == [
        selector._CONTRIB_FACE, selector._CONTRIB_EARS,
        selector._CONTRIB_BODY, selector._CONTRIB_COAT_DISTRIBUTION, selector._CONTRIB_GROOMING, selector._CONTRIB_3Q,
    ]

    prompt = cp.build_canonical_prompt(
        visual_identity={},
        structural_identity={},
        reference_roles=[p["role"] for p in THREE_PICKS],
        reference_guide=guide,
    )
    lines = prompt.splitlines()
    r1 = next(l for l in lines if l.startswith("Reference 1 — "))
    r2 = next(l for l in lines if l.startswith("Reference 2 — "))
    r3 = next(l for l in lines if l.startswith("Reference 3 — "))
    assert r1.startswith("Reference 1 — face reference (front view): authority for facial identity")
    assert "body proportions" in r2 and "tail shape" in r2 and "left side of the body" in r2
    assert r3.startswith("Reference 3 — three-quarter reference (front-right three-quarter view)")
    assert lines.index(r1) < lines.index(r2) < lines.index(r3)
    assert "do not let one photo override another" in prompt
    # 역할 요약 줄(구 계약)도 같은 순서다.
    assert "The supplied references show, in order: face, full body, 3q." in prompt


def test_guide_order_is_the_pick_order_not_a_fixed_role_order():
    """선택기가 전신 사진을 먼저 내놓으면 프롬프트의 Reference 1 도 전신이다."""
    refset = _refset(THREE_VIEWS)
    reversed_picks = list(reversed(THREE_PICKS))
    guide = selector.reference_guide(refset, reversed_picks)
    prompt = cp.build_canonical_prompt(
        visual_identity={}, structural_identity={}, reference_roles=[p["role"] for p in reversed_picks], reference_guide=guide
    )
    assert "Reference 1 — three-quarter reference" in prompt
    assert "Reference 3 — face reference" in prompt


def test_incomplete_vlm_evidence_only_claims_known_facets():
    refset = _refset(
        {
            "tail_only": _analysis(view="UNKNOWN", vconf="low", tail_visible="yes"),
            "nothing": _analysis(view="UNKNOWN", vconf="low"),
            "low_conf_view": _analysis(view="LEFT", vconf="low"),
        }
    )
    picks = [
        {"reference_id": "tail_only", "role": "SUPPORT_1"},
        {"reference_id": "nothing", "role": "SUPPORT_2"},
        {"reference_id": "low_conf_view", "role": "SUPPORT_3"},
    ]
    guide = selector.reference_guide(refset, picks)
    assert guide[0]["view"] is None and guide[0]["contributes"] == [selector._CONTRIB_TAIL]
    assert guide[1]["evidence"] == "none" and guide[1]["contributes"] == [selector._CONTRIB_GENERIC]
    # 저신뢰 뷰는 뷰로 치지 않는다 — 없는 "왼쪽" 권위를 지어내지 않는다.
    assert guide[2]["view"] is None and guide[2]["contributes"] == [selector._CONTRIB_GENERIC]

    prompt = cp.build_canonical_prompt(
        visual_identity={}, structural_identity={}, reference_roles=[p["role"] for p in picks], reference_guide=guide
    )
    assert "Reference 1 — supporting reference: authority for tail shape, length and carriage." in prompt
    assert "left side" not in prompt
    assert "unknown" not in prompt.lower()


def test_roles_only_call_still_builds_a_minimal_guide():
    """구 호출 계약(reference_guide 없음) — 역할 이름만으로 최소 가이드."""
    prompt = cp.build_canonical_prompt(
        visual_identity={}, structural_identity={}, reference_roles=["PRIMARY_FACE", "SUPPORT_1"]
    )
    assert "Reference 1 — face reference: authority for every identity detail it clearly shows." in prompt
    assert "Reference 2 — supporting reference: authority for every identity detail it clearly shows." in prompt


# ══════════════════════════════════════════════════════════════════════════
# 개체 신원 사실
# ══════════════════════════════════════════════════════════════════════════


def test_identity_facts_reach_prompt_and_unknowns_stay_silent():
    morphology = _morphology(
        {
            "a": _vlm_traits(),
            "b": _vlm_traits(paws={"colors_markings": "black paws"}),  # 발: 1:1 동률 → 침묵
            "c": _vlm_traits(eyes={"color": "unknown", "surrounding_markings": "unknown"},
                             paws={"colors_markings": "unknown"}),
        }
    )
    prompt = cp.build_canonical_prompt(
        visual_identity=RICH_VISUAL_IDENTITY,
        structural_identity={"silhouette": {"bbox_aspect_ratio": 1.7}},
        reference_roles=["PRIMARY_FACE"],
        morphology_profile=morphology,
    )
    facts = prompt.split("Verified identity facts for this individual (from its own photos):", 1)[1]

    # 사진에서 본 색 이름이 앞서고(v3.1), 실측 팔레트는 괄호 안 참고값이다. 코트가 첫 사실이다.
    assert facts.strip().startswith("Coat as seen in the photos: golden red, white. Keep this exact tone and its lightness")
    assert "(Measured pixel palette: brown, white; black.)" in facts
    assert "regions read as brown, white, brown" in facts
    # VLM 이 실제 사진에서 본 무늬/고유 특징.
    assert "Facial markings seen in the photos: white blaze from forehead to nose." in facts
    # v3.1: 가슴/등·옆구리/분포는 라벨을 붙여 따로 적는다.
    assert "Chest markings: white chest patch." in facts
    assert "Back and side markings: dark saddle over the back." in facts
    assert "Coat pattern across the body: saddle pattern." in facts
    assert "Distinctive features to keep exactly: notch in the left ear; small white spot on the right hind paw." in facts
    # 형태 프로필의 레퍼런스별 관측 — 다수결 (amber 2:0, black 3:0).
    assert "Eye color: amber." in facts
    assert "Muzzle: black." in facts
    assert "Ear shape: floppy." in facts
    assert "Ear hair and markings: darker brown than the body." in facts
    assert "Tail: long, plumed." in facts
    assert "Tail tip: white tip." in facts
    assert "Coat length: medium." in facts
    assert "The pet is a dog." in facts
    # 근거 없는 동률(발: 가이드가 없다) / 전부 unknown(질감) → 문장 없음.
    assert "Paws:" not in facts and "Coat texture:" not in facts
    # 수치 형태 클래스·품종·구조 수치는 절대 **사실 문장**이 되지 않는다. (베이스
    # 템플릿은 "torso length"/"frame" 같은 일반어를 쓰므로 사실 구간만 본다.)
    for banned in ("stocky", "torso", "large_in_frame", "frame", "short", "large", "1.7", "aspect"):
        assert banned not in facts, banned
    assert "Golden Retriever" not in prompt and "retriever" not in prompt.lower()
    assert "unknown" not in prompt.lower()


def test_identity_facts_with_no_evidence_produce_no_section():
    assert cp.identity_fact_lines({}, None) == []
    assert cp.identity_fact_lines({"coat": {"status": "unknown"}, "coat_pattern": {"status": "unknown"}}, None) == []
    # 형태 프로필만 있고 문자열 관측이 전부 unknown 이면 같다 — 수치 클래스만으로는
    # 어떤 문장도 나오지 않는다.
    assert cp.identity_fact_lines({}, _morphology({}, tail_form=None, ear_form=None)) == []
    # 형태 프로필의 융합 귀/꼬리 형태(VLM 관측)만 있으면 그 둘만 나온다 — 융합 꼬리
    # 형태는 한 장 지지일 수 있어 길이 단어("long")는 믿지 않는다 (v3.1).
    assert cp.identity_fact_lines({}, _morphology({})) == [
        "Ear shape: floppy.",
        "Tail: plumed. Keep its length and carriage exactly as in the photos.",
    ]


def test_single_tone_coat_pattern_reads_as_one_tone():
    vi = {"coat_pattern": _fused("black|black|black")}
    assert cp.identity_fact_lines(vi, None) == ["The coat reads as one black tone across the body."]


def test_marking_distribution_is_a_fallback_only_when_body_markings_unknown():
    vi = {"semantic_traits": {"status": "vlm", "traits": {"coat": {"marking_distribution": "saddle"}}}}
    assert "Markings: saddle." in cp.identity_fact_lines(vi, None)
    vi["body_markings"] = _fused("white chest")
    lines = cp.identity_fact_lines(vi, None)
    assert "Body markings seen in the photos: white chest." in lines and "Markings: saddle." not in lines


def test_keyframe_shared_helpers_are_unchanged_from_v2():
    """키프레임 프롬프트는 이 변경의 범위 밖 — 공유 헬퍼의 v2 출력이 그대로여야 한다."""
    assert cp.confident_trait_lines(RICH_VISUAL_IDENTITY) == [
        "The coat's dominant colors are brown, white, as in the references.",
        "Ear shape: floppy.",
        "Markings: saddle pattern.",
        "Coat length: medium.",
        "The pet is a dog.",
    ]
    assert cp._compact_trait_lines(RICH_VISUAL_IDENTITY) == [
        "Coat colors: brown, white.",
        "The pet is a dog.",
        "Floppy ears.",
    ]
    assert cp._with_tone("x {background_tone} y", None) == "x light-gray y"


# ══════════════════════════════════════════════════════════════════════════
# 컴팩트 (Runway ≤ 1000자)
# ══════════════════════════════════════════════════════════════════════════


def test_compact_prompt_respects_runway_limit_and_priority():
    guide = selector.reference_guide(_refset(THREE_VIEWS), THREE_PICKS)
    # 관측은 **공급된 사진**(가이드의 id)에만 붙는다 — 다른 id 의 관측은 쓰이지 않는다.
    morphology = _morphology({"face": _vlm_traits(), "zzz_not_supplied": _vlm_traits(eyes={"color": "blue", "surrounding_markings": "unknown"})})
    compact = cp.build_compact_canonical_prompt(
        visual_identity=RICH_VISUAL_IDENTITY, reference_guide=guide, morphology_profile=morphology
    )
    assert len(compact) <= 1000
    # 1순위: 사진 가이드. 2순위: 코트 색. 3순위: 종.
    assert "Photo 1: face (front); Photo 2: full body (left side), tail, paws; Photo 3: 3/4 view (front-right 3/4)." in compact
    assert "Each photo rules what it shows." in compact
    assert "Coat colors: golden red, white." in compact
    assert "The pet is a dog." in compact
    assert compact.index("Photo 1") < compact.index("Coat colors:") < compact.index("The pet is a dog.")
    assert "Measured pixel palette" not in compact  # 긴 꼬리 절은 컴팩트에서 빠진다
    assert "blue" not in compact  # 공급되지 않은 사진의 관측은 쓰이지 않는다
    assert "exact same pet" in compact  # 신원은 레퍼런스 이미지가 정본 (provider-contract 계약)
    assert "unknown" not in compact.lower()
    assert "Golden Retriever" not in compact and "stocky" not in compact

    # 줄은 뒤에서부터 떨어진다 — 상한을 마지막 줄만큼 못 미치게 두면 그 줄만 통째로
    # 빠진다 (상한은 실제 길이에서 계산한다: 베이스 문구가 바뀌어도 뜻은 같다).
    last_line = " " + cp.compact_identity_lines(RICH_VISUAL_IDENTITY, morphology, guide)[-1]
    while not compact.endswith(last_line):  # 상한 때문에 이미 빠진 줄이면 그 앞 줄
        lines = cp.compact_identity_lines(RICH_VISUAL_IDENTITY, morphology, guide)
        last_line = " " + lines[lines.index(last_line.strip()) - 1]
    limit = len(compact) - 1
    tighter = cp.build_compact_canonical_prompt(
        visual_identity=RICH_VISUAL_IDENTITY, reference_guide=guide, morphology_profile=morphology, max_chars=limit
    )
    assert len(tighter) <= limit
    assert tighter == compact[: -len(last_line)]  # 앞 줄 그대로, 마지막 줄만 통째로 빠짐
    assert "Photo 1: face" in tighter and "Coat colors: golden red, white." in tighter

    base_only = cp.build_compact_canonical_prompt(
        visual_identity=RICH_VISUAL_IDENTITY, reference_guide=guide, max_chars=len(cp._COMPACT_BASE) + 5
    )
    assert base_only == cp._COMPACT_BASE
    with pytest.raises(ValueError):
        cp.build_compact_canonical_prompt(visual_identity={}, max_chars=100)


def test_compact_base_leaves_room_for_identity_lines():
    # 가이드(3장) + 종 + 코트 색 한 줄은 어떤 톤에서도 상한 안에 들어가야 한다.
    assert len(cp._COMPACT_BASE) <= 800
    guide = selector.reference_guide(_refset(THREE_VIEWS), THREE_PICKS)
    vi = {"coat": RICH_VISUAL_IDENTITY["coat"], "semantic_traits": RICH_VISUAL_IDENTITY["semantic_traits"]}
    compact = cp.build_compact_canonical_prompt(visual_identity=vi, reference_guide=guide, background_tone="charcoal-gray")
    assert "Photo 1: face" in compact and "The pet is a dog." in compact and "Coat colors: brown, white" in compact
    assert "charcoal-gray background" in compact


def test_long_free_text_facts_are_dropped_not_truncated_in_compact():
    vi = {"facial_markings": _fused("a " * 80)}  # 160자 — 잘라서 거짓 문장을 만들지 않는다
    assert cp.compact_identity_lines(vi, None) == []
    assert "a a a" not in cp.build_compact_canonical_prompt(visual_identity=vi)


# ══════════════════════════════════════════════════════════════════════════
# 통합: build_canonical → 프로바이더가 실제로 받는 프롬프트/입력
# ══════════════════════════════════════════════════════════════════════════


class PromptRecordingProvider(FakeProvider):
    def __init__(self, images, *, max_prompt_chars=None, name="fake"):
        super().__init__(name, images)
        self.seen_prompts: list[str] = []
        self.seen_refs: list[list[tuple[str, str]]] = []
        if max_prompt_chars is not None:
            self.max_prompt_chars = max_prompt_chars

    def generate(self, references, prompt, output_spec, metadata):
        self.seen_prompts.append(prompt)
        self.seen_refs.append([(r.reference_id, r.role) for r in references])
        return super().generate(references, prompt, output_spec, metadata)


def _build(h: Harness, provider, **kw):
    return _run(
        svc.build_canonical(
            user_id=USER, pet_id=PET, fetch_bytes=h.fetch, providers=[provider],
            cutout_fn=lambda raw: raw, **kw,
        )
    )


def _seed_three(h: Harness, monkeypatch):
    face = h.seed(cutout=make_pet_cutout_png(), classification=cls(view="FRONT", face_visible="yes"))
    body = h.seed(
        cutout=distinct_view_cutout("mirror"),
        classification=cls(view="LEFT", full_body_visible="yes", tail_visible="yes"),
    )
    q3 = h.seed(cutout=distinct_view_cutout("flip"), classification=cls(view="FRONT_RIGHT_3Q", full_body_visible="yes"))
    h.install_vlm(monkeypatch)
    return face, body, q3


def test_build_canonical_guide_matches_provider_reference_order(uploads, monkeypatch):
    h = Harness()
    face, body, q3 = _seed_three(h, monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)

    provider = PromptRecordingProvider([make_pet_cutout_png()])
    version = _build(h, provider)

    assert version.status == svc.STATUS_COMPLETE
    assert version.prompt_version == "canonical-prompt-v3.2"
    guide = version.output_spec["reference_guide"]
    assert [g["reference_id"] for g in guide] == [rid for rid, _ in provider.seen_refs[0]]
    assert [g["role"] for g in guide] == [role for _, role in provider.seen_refs[0]]
    assert [g["reference_id"] for g in guide] == [face.id, body.id, q3.id]
    assert version.output_spec["morphology_profile_version"] == version.reference_set_version

    prompt = provider.seen_prompts[0]
    assert prompt == version.prompt
    assert "Reference 1 — face reference (front view): authority for facial identity" in prompt
    assert "Reference 2 — full-body reference (left side)" in prompt and "tail shape" in prompt
    assert "Reference 3 — three-quarter reference (front-right three-quarter view)" in prompt
    # 실측 코트 색(합성 누끼는 갈색)이 사실 줄로 도달한다.
    assert "Coat colors measured from the photos:" in prompt and "brown" in prompt.lower()
    assert "unknown" not in prompt.lower()


def test_build_canonical_vlm_traits_reach_provider_prompt(uploads, monkeypatch):
    h = Harness()
    _seed_three(h, monkeypatch)
    # 신원/형태 프로필 양쪽이 같은 VLM 관측을 받는다 (실제 경로와 동일한 입력 형태).
    monkeypatch.setattr(
        vlm_identity, "analyze_semantic_traits",
        lambda images: {"traits": _vlm_traits(), "model": "test-stub", "analyzer": "vlm-identity-v1", "image_count": 1},
    )
    install_vlm_qa(monkeypatch, VLM_QA_OK)

    provider = PromptRecordingProvider([make_pet_cutout_png()])
    version = _build(h, provider)
    assert version.status == svc.STATUS_COMPLETE
    prompt = provider.seen_prompts[0]

    assert "The pet is a dog." in prompt
    assert "Facial markings seen in the photos: white blaze from forehead to nose." in prompt
    assert "Chest markings: white chest patch." in prompt
    assert "Back and side markings: dark saddle over the back." in prompt
    assert "Coat pattern across the body: saddle pattern." in prompt
    assert "Distinctive features to keep exactly: notch in the left ear." in prompt
    assert "Eye color: amber." in prompt and "Muzzle: black." in prompt
    assert "Tail: long, plumed." in prompt and "Tail tip: white tip." in prompt  # 세 사진이 같은 길이 단어
    assert "Paws: white socks on the front paws." in prompt
    assert "Coat as seen in the photos: golden red, white." in prompt
    assert "Golden Retriever" not in prompt and "retriever" not in prompt.lower()
    assert "unknown" not in prompt.lower()


def test_runway_limited_provider_gets_compact_v3_under_1000(uploads, monkeypatch):
    h = Harness()
    _seed_three(h, monkeypatch)
    monkeypatch.setattr(
        vlm_identity, "analyze_semantic_traits",
        lambda images: {"traits": _vlm_traits(), "model": "test-stub", "analyzer": "vlm-identity-v1", "image_count": 1},
    )
    install_vlm_qa(monkeypatch, VLM_QA_OK)

    provider = PromptRecordingProvider([make_pet_cutout_png()], max_prompt_chars=1000, name="runway")
    version = _build(h, provider)
    assert version.status == svc.STATUS_COMPLETE
    assert len(version.prompt) > 1000  # 전체 프롬프트는 상한을 넘는다 → 컴팩트로 간다
    sent = provider.seen_prompts[0]
    assert len(sent) <= 1000
    assert "exact same pet" in sent
    # 1순위 가이드, 2순위 실측 코트(+사진에서 본 색 묘사)는 반드시 남는다.
    assert "Photo 1: face (front); Photo 2: full body (left side), tail; Photo 3: 3/4 view (front-right 3/4)." in sent
    assert "Coat colors: golden red, white." in sent
    # 종 줄은 **상한 때문에만** 빠진다 — 들어갔다면 상한을 넘었을 것이다.
    if "The pet is a dog." not in sent:
        assert len(sent) + len(" The pet is a dog.") > 1000
    assert "unknown" not in sent.lower() and "Golden Retriever" not in sent
    selected = next(c for c in version.candidates if c.selected)
    assert selected.generation_metadata["prompt_variant"] == "compact"
    assert selected.generation_metadata["prompt_chars"] == len(sent)


def test_morphology_lookup_failure_does_not_block_build(uploads, monkeypatch):
    h = Harness()
    _seed_three(h, monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)

    async def boom(**kwargs):
        raise morph.PetMorphologyError("MORPHOLOGY_PROFILES_UNAVAILABLE", "down", status=503)

    monkeypatch.setattr(morph, "get_profile", boom)
    provider = PromptRecordingProvider([make_pet_cutout_png()])
    version = _build(h, provider)
    assert version.status == svc.STATUS_COMPLETE
    assert "Reference 1 — face reference" in provider.seen_prompts[0]


def test_single_photo_pet_end_to_end(uploads, monkeypatch):
    h = Harness()
    h.seed(cutout=make_pet_cutout_png(), classification=cls(view="FRONT", face_visible="yes"))
    h.install_vlm(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)

    provider = PromptRecordingProvider([make_pet_cutout_png()])
    version = _build(h, provider)
    assert version.status == svc.STATUS_COMPLETE
    prompt = provider.seen_prompts[0]
    assert len(version.output_spec["reference_guide"]) == 1
    assert "Reference 1 — face reference (front view): authority for facial identity" in prompt
    assert "Only one photo is available" in prompt
    assert "Reference 2" not in prompt
