"""
정본 프롬프트 v3.1 — apricot 개 실사 회귀 + 위에서 본/잘린 레퍼런스 + 모순 해소.

픽스처(tests/data/apricot_dog_canonical_v3.json)는 2026-10-12 의 실제 v3 생성 실행에서
가져온 신원/형태 프로필·VLM 관측·레퍼런스 세트 분석이다 (스토리지 경로/사용자 제거).
그 실행의 프롬프트에는 다음 결함이 있었고, v3.1 은 각각을 고친다:

  * 세 사진이 전부 "apricot" 이라 했는데 색 줄에는 팔레트 이름(tan, brown, red brown…)만.
  * 가슴의 흰 반점(정면 사진)이 "no markings"(옆 사진)에 사전순 동률로 졌다.
  * 꼬리 "short" 는 옆 사진 한 장의 추정이 사전순으로 이긴 것이다.
  * 위에서 내려다본, 위가 잘린 사진이 "tail reference" 로 얼굴·체형 비율 권위를 받았다.
  * "well groomed" 가 짧게 민 몸 코트를 풍성한 테디베어 컷으로 바꿀 여지를 줬다.

유료 호출은 없다 (가짜 프로바이더, 주입된 VLM 결과).
"""

from __future__ import annotations

import io
import json
import pathlib
from types import SimpleNamespace

import anyio
import pytest
from PIL import Image

from backend.services import canonical_pet_service as svc
from backend.services import canonical_prompt as cp
from backend.services import canonical_reference_selector as sel
from backend.services import durable_provider_jobs
from backend.services import pet_identity_service as ids
from backend.services import pet_morphology_service as morph
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry, vlm_identity

from .test_canonical_pet_builder import VLM_QA_OK, distinct_view_cutout, install_vlm_qa
from .test_canonical_prompt_v3 import PromptRecordingProvider, _analysis, _refset
from .test_pet_identity_profile import make_pet_cutout_png
from .test_pet_reference_sets import PET, USER, Harness, cls
from .test_provider_contract import RICH_VISUAL

FIX = json.loads((pathlib.Path(__file__).parent / "data" / "apricot_dog_canonical_v3.json").read_text())
FACE, BODY, OVERHEAD = [p["reference_id"] for p in FIX["input_references"]]
FACTS_HEADER = "Verified identity facts for this individual (from its own photos):"


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


def _apricot_refset() -> SimpleNamespace:
    return SimpleNamespace(**FIX["refset"])


def _apricot_morph() -> SimpleNamespace:
    return SimpleNamespace(**FIX["morphology"])


def _apricot_guide() -> list[dict]:
    return sel.reference_guide(_apricot_refset(), FIX["input_references"])


def _apricot_prompt() -> str:
    return cp.build_canonical_prompt(
        visual_identity=FIX["visual_identity"],
        structural_identity={},
        reference_roles=[p["role"] for p in FIX["input_references"]],
        reference_guide=_apricot_guide(),
        morphology_profile=_apricot_morph(),
        background_tone="medium-dark gray",
    )


def _facts(prompt: str) -> str:
    return prompt.split(FACTS_HEADER, 1)[1]


# ══════════════════════════════════════════════════════════════════════════
# 기준선: 실제 v3 프롬프트에 결함이 있었다 (픽스처가 올바른 사례임을 보증)
# ══════════════════════════════════════════════════════════════════════════


def test_fixture_documents_the_real_v3_defects():
    sent = FIX["v3_prompt_sent"]
    assert "Tail: short, held upright/curved over the back" in sent
    assert "Coat colors measured from the photos: tan, brown. Secondary tones: cream, red brown, dark brown." in sent
    assert "Reference 3 — tail reference: authority for facial identity" in sent
    assert "Body markings seen in the photos: solid apricot, short-clipped curly coat with no markings" in sent
    assert "well groomed" in sent
    # 세 사진의 VLM 관측은 전부 apricot 을 말했다.
    for rid in (FACE, BODY, OVERHEAD):
        colors = FIX["morphology"]["reference_observations"][rid]["semantic_traits"]["traits"]["coat"]["dominant_colors"]
        assert any("apricot" in c for c in colors)


# ══════════════════════════════════════════════════════════════════════════
# 변경 3 — 위에서 본/잘린 레퍼런스의 권위
# ══════════════════════════════════════════════════════════════════════════


def test_overhead_cropped_photo_gets_no_proportion_or_face_authority():
    face, body, overhead = _apricot_guide()
    assert overhead["view"] == "overhead view" and overhead["view_label"] == "TOP"
    assert overhead["caveats"] == [sel.CAVEAT_OVERHEAD, sel.CAVEAT_CROPPED]
    for banned in (sel._CONTRIB_FACE, sel._CONTRIB_BODY, sel._CONTRIB_TAIL, sel._CONTRIB_3Q):
        assert banned not in overhead["contributes"]
    for kept in (sel._CONTRIB_TAIL_FORESHORTENED, sel._CONTRIB_COAT_DISTRIBUTION, sel._CONTRIB_GROOMING, sel._CONTRIB_PAWS):
        assert kept in overhead["contributes"]
    # 옆 전신 사진이 비율·꼬리 길이의 유일한 권위다.
    assert body["caveats"] == [] and sel._CONTRIB_BODY in body["contributes"] and sel._CONTRIB_TAIL in body["contributes"]
    assert face["caveats"] == [] and sel._CONTRIB_FACE in face["contributes"]

    prompt = _apricot_prompt()
    assert "Reference 3 — tail reference (overhead view): authority for" in prompt
    assert "not its length — it is foreshortened from above" in prompt
    r3 = next(l for l in prompt.splitlines() if l.startswith("Reference 3 — "))
    assert "facial identity" not in r3 and "body proportions" not in r3
    assert "Reference 2 — full-body reference (left side): authority for" in prompt
    assert "body proportions: leg length, torso length" in prompt


def test_cropped_side_photo_keeps_coat_evidence_but_loses_proportions():
    a = _analysis(view="LEFT", full_body_visible="yes", tail_visible="yes")
    a["eligibility"].update({"reasons": ["subject_cropped_by_frame"], "border_contact": ["bottom"], "full_body_visible": "unlikely"})
    guide = sel.reference_guide(_refset({"x": a}), [{"reference_id": "x", "role": "PRIMARY_FULL_BODY"}])
    entry = guide[0]
    assert entry["caveats"] == [sel.CAVEAT_CROPPED] and entry["view"] == "left side"
    assert sel._CONTRIB_BODY not in entry["contributes"]
    assert sel._CONTRIB_COAT_DISTRIBUTION in entry["contributes"]
    assert sel._CONTRIB_GROOMING in entry["contributes"]
    assert sel._CONTRIB_TAIL in entry["contributes"]  # 옆에서 본 꼬리는 길이 근거가 된다


def test_uncropped_overhead_photo_is_still_not_a_proportion_authority():
    a = _analysis(view="TOP", full_body_visible="yes", face_visible="yes", tail_visible="yes")
    entry = sel.reference_guide(_refset({"x": a}), [{"reference_id": "x", "role": "SUPPORT_1"}])[0]
    assert entry["caveats"] == [sel.CAVEAT_OVERHEAD]
    assert sel._CONTRIB_BODY not in entry["contributes"] and sel._CONTRIB_FACE not in entry["contributes"]
    assert sel._CONTRIB_TAIL_FORESHORTENED in entry["contributes"]


# ══════════════════════════════════════════════════════════════════════════
# 변경 1 — 코트 색: 사진에서 본 이름이 앞선다
# ══════════════════════════════════════════════════════════════════════════


def test_coat_line_leads_with_apricot_and_drops_low_confidence_palette_names():
    facts = _facts(_apricot_prompt())
    coat = facts.strip().splitlines()[0]
    assert coat.startswith("Coat as seen in the photos: apricot, cream, tan.")
    assert "do not shift it darker, redder or more saturated" in coat
    assert coat.endswith("(Measured pixel palette: tan, brown; cream.)")
    assert "red brown" not in coat and "dark brown" not in coat  # 한 장짜리 저신뢰 팔레트 항목


def test_coat_words_tokenize_modifiers_and_require_two_photos():
    obs = {
        "a": {"coat": {"dominant_colors": ["light apricot"], "secondary_colors": ["pale blonde"]}},
        "b": {"coat": {"dominant_colors": ["apricot", "golden tan"], "secondary_colors": ["white"]}},
        "c": {"coat": {"dominant_colors": ["Apricot/cream"], "secondary_colors": ["tan"]}},
    }
    # 'apricot' 만 2장 이상 (수식어 light 는 벗겨진다); cream/tan/blonde/white 는 한 장씩.
    assert cp._coat_words(obs) == ["apricot"]
    # 한 장뿐이면 그 사진의 색 — 수식어만 벗긴다.
    assert cp._coat_words({"o": {"coat": {"dominant_colors": ["dark chocolate brown"], "secondary_colors": ["white"]}}}) == ["chocolate brown", "white"]
    # 겹치는 색이 하나도 없으면 지배색 합집합 (두 색 개).
    assert cp._coat_words({"a": {"coat": {"dominant_colors": ["black"]}}, "b": {"coat": {"dominant_colors": ["white"]}}}) == ["black", "white"]
    assert cp._coat_words({}) == []


def test_previous_pet_without_vlm_words_keeps_measured_coat_line():
    """VLM 색 묘사가 없는(또는 VLM 이 꺼진) 예전 펫 — v3 의 실측 코트 줄이 그대로 나온다."""
    lines = cp.identity_fact_lines(RICH_VISUAL, None)
    assert lines[0] == (
        "Coat colors measured from the photos: black, tan. Keep these exact tones and their relative "
        "lightness; do not shift the hue."
    )
    assert cp.compact_identity_lines(RICH_VISUAL, None)[0] == "Coat colors: black, tan."
    assert "The pet is a dog." in lines and "Coat length: medium." in lines
    assert all("unknown" not in l.lower() for l in lines)


# ══════════════════════════════════════════════════════════════════════════
# 변경 2 — 모순은 근거로 푼다 (사전순 없음)
# ══════════════════════════════════════════════════════════════════════════


def test_chest_patch_and_clipped_back_are_labelled_not_contradictory():
    facts = _facts(_apricot_prompt())
    assert "Chest markings: White patch centered on the chest, extending in an irregular elongated shape" in facts
    assert "Back and side markings: solid apricot, short-clipped curly coat with no markings." in facts
    assert "Coat pattern across the body: solid uniform apricot with no distinct markings; body coat clipped shorter" in facts
    # v3 의 모순 문장은 사라졌다: 가슴 반점이 "no markings" 에 묻히지 않는다.
    assert "Body markings seen in the photos:" not in facts
    assert "White chest blaze that splits into an upper patch and a lower patch" in facts


def test_tail_length_claim_from_one_photo_is_dropped_and_overhead_carriage_kept():
    facts = _facts(_apricot_prompt())
    tail = next(l for l in facts.splitlines() if l.startswith("Tail: "))
    assert "short" not in tail.lower()
    assert tail.startswith("Tail: held upright/curved over the back, covered in fluffy apricot wavy fur; ")
    assert "curving forward, covered in slightly wavy apricot-blonde feathering, lighter than the body coat (seen from above)" in tail
    assert tail.endswith("Keep its length and carriage exactly as in the photos.")
    assert "Tail tip: no distinct marking; same apricot color as body." in facts


def test_face_facts_come_from_the_front_photo_not_the_overhead():
    facts = _facts(_apricot_prompt())
    assert "Facial markings seen in the photos: Uniform apricot-cream face with no distinct markings; slightly darker tan shading across the top of the head" in facts
    assert "Uniform light apricot/cream face" not in facts  # 위에서 본 사진의 얼굴 묘사는 근거가 아니다
    assert "Eye color: dark brown, nearly black, round and prominent." in facts
    assert "Muzzle: Apricot/light tan fur on the muzzle with a solid black nose" in facts


def test_single_photo_distinctive_features_are_all_kept():
    facts = _facts(_apricot_prompt())
    line = next(l for l in facts.splitlines() if l.startswith("Distinctive features to keep exactly: "))
    for feat in (
        "Longer straight silky ear and head hair contrasting with the curly body coat",
        "Rounded 'bob'-style haircut framing the face with long straight ear fringes",
        "White chest blaze that splits into an upper patch and a lower patch separated by apricot fur",
        "Wispy pale cream beard and mustache around the muzzle",
    ):
        assert feat in line


def test_no_breed_or_numeric_morphology_reaches_the_facts():
    prompt = _apricot_prompt()
    facts = _facts(prompt)
    for banned in ("Poodle", "Maltipoo", "poodle", "slender", "compact", "small_in_frame", "frame", "0.6986", "2.357"):
        assert banned not in facts, banned
    assert "unknown" not in prompt.lower()


def _two_photo_guide(a: dict, b: dict, roles=("SUPPORT_1", "SUPPORT_2")) -> list[dict]:
    return sel.reference_guide(
        _refset({"a": a, "b": b}),
        [{"reference_id": "a", "role": roles[0]}, {"reference_id": "b", "role": roles[1]}],
    )


def _morph_from(traits_by_rid: dict[str, dict]) -> SimpleNamespace:
    return SimpleNamespace(
        profile={"status": "fused", "traits": {}},
        reference_observations={
            rid: {"semantic_traits": {"status": "present", "traits": t}} for rid, t in traits_by_rid.items()
        },
    )


def test_equal_evidence_descriptive_disagreement_is_composed_in_supplied_order_not_alphabetically():
    guide = _two_photo_guide(
        _analysis(view="LEFT", full_body_visible="yes"), _analysis(view="RIGHT", full_body_visible="yes")
    )
    morph_p = _morph_from(
        {
            "a": {"body": {"torso_markings": "zebra-like dark stripes over the shoulders"}},
            "b": {"body": {"torso_markings": "a single dark patch on the right flank"}},
        }
    )
    lines = cp.identity_fact_lines({}, morph_p, guide)
    torso = next(l for l in lines if l.startswith("Back and side markings: "))
    # 사전순이면 "a single…" 이 먼저다. 공급 순서(a 먼저)를 따라 "zebra…" 가 먼저여야 한다.
    assert torso == (
        "Back and side markings: zebra-like dark stripes over the shoulders; a single dark patch on the right flank."
    )


def test_equal_evidence_categorical_disagreement_stays_silent():
    guide = _two_photo_guide(_analysis(view="FRONT", face_visible="yes"), _analysis(view="FRONT", face_visible="yes"))
    morph_p = _morph_from({"a": {"eyes": {"color": "amber"}}, "b": {"eyes": {"color": "blue"}}})
    assert not any(l.startswith("Eye color") for l in cp.identity_fact_lines({}, morph_p, guide))
    # 한쪽이 unknown 이면 아는 쪽이 남는다 — 다수결이 아니다.
    morph_p = _morph_from({"a": {"eyes": {"color": "amber"}}, "b": {"eyes": {"color": "unknown"}}})
    assert "Eye color: amber." in cp.identity_fact_lines({}, morph_p, guide)


def test_stronger_evidence_wins_over_weaker_regardless_of_text_order():
    # 정면 사진(얼굴 근거)의 눈 색이 위에서 본 사진의 눈 색을 이긴다 — 공급 순서와 무관.
    guide = sel.reference_guide(
        _refset({"top": _analysis(view="TOP", face_visible="yes"), "front": _analysis(view="FRONT", face_visible="yes")}),
        [{"reference_id": "top", "role": "SUPPORT_1"}, {"reference_id": "front", "role": "SUPPORT_2"}],
    )
    morph_p = _morph_from({"top": {"eyes": {"color": "blue"}}, "front": {"eyes": {"color": "amber"}}})
    assert "Eye color: amber." in cp.identity_fact_lines({}, morph_p, guide)


def test_without_a_guide_contradictions_stay_silent_instead_of_being_concatenated():
    morph_p = _morph_from({"a": {"paws": {"colors_markings": "white socks"}}, "b": {"paws": {"colors_markings": "black paws"}}})
    assert not any(l.startswith("Paws") for l in cp.identity_fact_lines({}, morph_p, None))


def test_tail_length_is_kept_only_when_two_photos_agree():
    guide = _two_photo_guide(
        _analysis(view="LEFT", full_body_visible="yes", tail_visible="yes"),
        _analysis(view="RIGHT", full_body_visible="yes", tail_visible="yes"),
    )
    agree = _morph_from({"a": {"tail": {"appearance": "short, curled over the back"}}, "b": {"tail": {"appearance": "short, curled over the back"}}})
    assert "Tail: short, curled over the back." in cp.identity_fact_lines({}, agree, guide)
    disagree = _morph_from({"a": {"tail": {"appearance": "short, curled over the back"}}, "b": {"tail": {"appearance": "long plumed tail carried high"}}})
    tail = next(l for l in cp.identity_fact_lines({}, disagree, guide) if l.startswith("Tail: "))
    assert "short" not in tail and "long" not in tail
    assert tail == "Tail: curled over the back; plumed tail carried high. Keep its length and carriage exactly as in the photos."


def test_overhead_only_tail_evidence_gives_carriage_without_length():
    guide = sel.reference_guide(
        _refset({"top": _analysis(view="TOP", full_body_visible="yes", tail_visible="yes")}),
        [{"reference_id": "top", "role": "ONLY_AVAILABLE"}],
    )
    morph_p = _morph_from({"top": {"tail": {"appearance": "long feathered tail curving forward"}}})
    tail = next(l for l in cp.identity_fact_lines({}, morph_p, guide) if l.startswith("Tail: "))
    assert tail == "Tail: feathered tail curving forward (seen from above). Keep its length and carriage exactly as in the photos."


# ══════════════════════════════════════════════════════════════════════════
# 변경 4 — 그루밍/체형 보존, 자세·구도 개선 허용
# ══════════════════════════════════════════════════════════════════════════


def test_grooming_clause_replaces_well_groomed_and_keeps_presentation_freedom():
    prompt = _apricot_prompt()
    assert "well groomed" not in prompt and "tidier" not in prompt
    assert "Keep the coat length, clip pattern and fur density exactly as in the photos" in prompt
    assert "a short-clipped body stays short-clipped" in prompt
    assert "do not make the coat fuller, fluffier, more uniform or more show-groomed" in prompt
    assert "Keep the body as lean or as sturdy as the full-body photo shows; do not add bulk." in prompt
    # 허용되는 개선은 그대로다.
    assert "premium, well-composed portrait" in prompt and "studio-quality lighting" in prompt
    assert "A different camera angle or a better, more composed posture than the originals is fine" in prompt
    assert "front three-quarter angle, camera at the pet's eye level" in prompt
    assert "Plain solid neutral medium-dark gray background" in prompt
    assert cp.CANONICAL_PROMPT_VERSION == "canonical-prompt-v3.2"
    assert cp.CANONICAL_COMPACT_PROMPT_VERSION == "canonical-prompt-compact-v3.2"


def test_compact_for_runway_keeps_guide_apricot_and_grooming_rule():
    compact = cp.build_compact_canonical_prompt(
        visual_identity=FIX["visual_identity"], reference_guide=_apricot_guide(),
        morphology_profile=_apricot_morph(), background_tone="medium-dark gray",
    )
    assert len(compact) <= 1000
    assert "exact same pet" in compact
    assert "Keep coat length, clip and density as in the photos (no fluffing); keep the body as lean as shown." in compact
    assert "Photo 3: tail (overhead), paws" in compact and "Each photo rules what it shows." in compact
    assert "Coat colors: apricot, cream, tan." in compact
    assert "short" not in compact.split("stylization.", 1)[1]  # 꼬리 길이 추정이 컴팩트에도 없다
    assert "unknown" not in compact.lower()


# ══════════════════════════════════════════════════════════════════════════
# 회귀 — 예전에 성공하던 펫 / 실제 세트 빌더를 통한 잘린 overhead 사진
# ══════════════════════════════════════════════════════════════════════════


def test_legacy_roles_only_call_is_unchanged():
    prompt = cp.build_canonical_prompt(visual_identity={}, structural_identity={}, reference_roles=["PRIMARY_FACE", "SUPPORT_1"])
    assert "Reference 1 — face reference: authority for every identity detail it clearly shows." in prompt
    assert "Verified identity facts" not in prompt
    assert "Keep the coat length, clip pattern and fur density exactly as in the photos" in prompt


def _build(h: Harness, provider, **kw):
    return _run(
        svc.build_canonical(
            user_id=USER, pet_id=PET, fetch_bytes=h.fetch, providers=[provider],
            cutout_fn=lambda raw: raw, **kw,
        )
    )


def test_previously_successful_three_photo_pet_still_completes_on_v31(uploads, monkeypatch):
    h = Harness()
    h.seed(cutout=make_pet_cutout_png(), classification=cls(view="FRONT", face_visible="yes"))
    h.seed(cutout=distinct_view_cutout("mirror"), classification=cls(view="LEFT", full_body_visible="yes", tail_visible="yes"))
    h.seed(cutout=distinct_view_cutout("flip"), classification=cls(view="FRONT_RIGHT_3Q", full_body_visible="yes"))
    h.install_vlm(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)

    provider = PromptRecordingProvider([make_pet_cutout_png()])
    version = _build(h, provider)
    assert version.status == svc.STATUS_COMPLETE
    assert version.prompt_version == "canonical-prompt-v3.2"
    prompt = provider.seen_prompts[0]
    assert prompt == version.prompt
    assert "Reference 1 — face reference (front view): authority for facial identity" in prompt
    assert "Reference 2 — full-body reference (left side)" in prompt and "body proportions" in prompt
    assert "the grooming pattern: where the coat is clipped short and where it is left longer" in prompt
    assert "Coat colors measured from the photos:" in prompt  # VLM 색 묘사 없음 → 실측 줄 (v3 과 동일)
    assert "well groomed" not in prompt
    guide = version.output_spec["reference_guide"]
    assert [g["caveats"] for g in guide] == [[], [], []]
    assert [g["reference_id"] for g in guide] == [rid for rid, _ in provider.seen_refs[0]]


def _top_cropped_cutout() -> bytes:
    """피사체가 위 테두리에 닿도록 잘린 누끼 — 실사 overhead 사진의 적격성(subject_cropped_by_frame)을 재현한다."""
    # 다른 사진이어야 한다 (뒤집기) — 같은 누끼를 자르기만 하면 pHash 가 가까워 선택기가 중복으로 뺀다.
    im = Image.open(io.BytesIO(distinct_view_cutout("flip"))).convert("RGBA")
    alpha = im.getchannel("A")
    bbox = alpha.getbbox()
    cropped = im.crop((0, bbox[1] + 5, im.width, im.height))  # 피사체 윗부분을 잘라낸다
    buf = io.BytesIO(); cropped.save(buf, format="PNG")
    return buf.getvalue()


def test_cropped_overhead_reference_through_the_real_set_builder(uploads, monkeypatch):
    h = Harness()
    face = h.seed(cutout=make_pet_cutout_png(), classification=cls(view="FRONT", face_visible="yes", distinct_markings_visible="yes"))
    body = h.seed(cutout=distinct_view_cutout("mirror"), classification=cls(view="LEFT", full_body_visible="yes", tail_visible="yes"))
    top = h.seed(
        cutout=_top_cropped_cutout(),
        classification=cls(view="TOP", face_visible="yes", full_body_visible="yes", tail_visible="yes", paws_visible="yes"),
    )
    h.install_vlm(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)

    refset = h.build()
    elig = refset.reference_analysis[top.id]["eligibility"]
    assert "top" in (elig.get("border_contact") or []) and "subject_cropped_by_frame" in elig["reasons"]

    provider = PromptRecordingProvider([make_pet_cutout_png()])
    version = _build(h, provider)
    assert version.status == svc.STATUS_COMPLETE
    guide = {g["reference_id"]: g for g in version.output_spec["reference_guide"]}
    assert set(guide) == {face.id, body.id, top.id}
    assert guide[top.id]["caveats"] == [sel.CAVEAT_OVERHEAD, sel.CAVEAT_CROPPED]
    assert sel._CONTRIB_BODY not in guide[top.id]["contributes"] and sel._CONTRIB_FACE not in guide[top.id]["contributes"]
    assert sel._CONTRIB_TAIL_FORESHORTENED in guide[top.id]["contributes"]
    assert sel._CONTRIB_BODY in guide[body.id]["contributes"]
    prompt = provider.seen_prompts[0]
    top_line = next(l for l in prompt.splitlines() if l.startswith("Reference ") and "(overhead view)" in l)
    assert "foreshortened from above" in top_line and "body proportions" not in top_line
