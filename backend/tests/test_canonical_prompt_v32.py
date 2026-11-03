"""
정본 프롬프트 v3.2 — 다리/발/귀/꼬리 털 양 보존 (과장도 일괄 축소도 금지).

실제 펫 테스트에서 다리·발·귀·꼬리의 털이 실제보다 풍성하게(플러시 토이 실루엣으로)
부풀려지는 드리프트가 보였다. v3.2 는 프롬프트 문구만 바꾼다:

  1. 믿을 만한(잘리지 않은) 서있는 측면/3-4 뷰 전신 레퍼런스를 다리 두께·발 크기·
     부위별 털 양의 권위로 삼는다.
  2. 그 양과 분포를 그대로 재현한다 — 풍성화도 일괄 축소도 금지. apricot(곱슬·풍성)
     픽스처와 짧은/매끈한 코트 픽스처 둘 다로, 이 문구가 **개체와 무관하게 항상**
     양방향(과장 금지 + 축소 금지)으로 실린다는 것을 확인한다.

선택적 실루엣 임계값·레퍼런스 순서·형태 프로필 빌더는 바꾸지 않는다 — 이 파일은
그 경계도 함께 확인한다. 유료 호출 없음(프롬프트 빌더는 순수 함수).
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.services import canonical_prompt as cp
from backend.services import canonical_reference_selector as sel

from .test_canonical_prompt_v3 import RICH_VISUAL_IDENTITY, THREE_PICKS, THREE_VIEWS, _morphology, _refset, _vlm_traits
from .test_canonical_prompt_v31 import FIX, _apricot_guide, _apricot_morph, _apricot_prompt

FUR_PHRASE_FULL = (
    "Where a reliable, uncropped standing side or three-quarter full-body reference is "
    "available, treat it as the authority for how thick the legs and paws look and for "
    "how much fur covers the legs, paws, ears and tail"
)
FUR_NO_ENLARGE = "Do not enlarge the legs, paws, ears or tail into a fuller, fluffier or plush-toy silhouette"
FUR_NO_SLIM_EITHER = (
    "do not slim or thin them either — a naturally fluffy-legged pet must stay "
    "exactly as fluffy as its photos show, and a naturally slim or short-coated pet must "
    "stay exactly that slim"
)
FUR_PERSPECTIVE = (
    "A different camera angle, distance or pose changes how thick a limb looks, so compare "
    "proportions rather than requiring identical pixel measurements"
)
FUR_PHRASE_COMPACT = "Match leg/paw/ear/tail fur volume to a full-body reference (angle, not pixel size)"
FUR_NO_SLIM_COMPACT = "no fluffing or slimming either way"


# ══════════════════════════════════════════════════════════════════════════
# 1. 버전
# ══════════════════════════════════════════════════════════════════════════


def test_version_bumped_to_v32():
    assert cp.CANONICAL_PROMPT_VERSION == "canonical-prompt-v3.2"
    assert cp.CANONICAL_COMPACT_PROMPT_VERSION == "canonical-prompt-compact-v3.2"


# ══════════════════════════════════════════════════════════════════════════
# 2. apricot(곱슬·풍성) 개 — 전신 레퍼런스 권위 + 양방향 보존, 풀 프롬프트
# ══════════════════════════════════════════════════════════════════════════


def test_apricot_dog_full_prompt_carries_the_fur_volume_authority_clause():
    prompt = _apricot_prompt()
    assert FUR_PHRASE_FULL in prompt
    assert FUR_NO_ENLARGE in prompt
    assert FUR_NO_SLIM_EITHER in prompt
    assert FUR_PERSPECTIVE in prompt
    # 곱슬/풍성한 실제 코트 사실은 여전히 그대로 보존된다 — 새 절이 기존 사실을 밀어내지 않는다.
    assert "Coat length: medium." in prompt
    assert "Coat texture: curly." in prompt


def test_apricot_dog_full_body_reference_is_the_reliable_uncropped_authority():
    """apricot 세트의 옆 전신 사진이 바로 '잘리지 않은 서있는 측면 전신 레퍼런스'다."""
    face, body, overhead = _apricot_guide()
    assert body["caveats"] == []  # 잘리지 않음, 위에서 보지 않음 — 권위가 될 자격
    assert sel._CONTRIB_BODY in body["contributes"]
    # 위에서 본/잘린 사진은 비율·권위 후보에서 계속 제외된다 (v3.1 규칙 — 바꾸지 않음).
    assert overhead["caveats"] == [sel.CAVEAT_OVERHEAD, sel.CAVEAT_CROPPED]


def test_apricot_dog_compact_prompt_keeps_fur_clause_within_provider_limit():
    compact = cp.build_compact_canonical_prompt(
        visual_identity=FIX["visual_identity"], reference_guide=_apricot_guide(),
        morphology_profile=_apricot_morph(), background_tone="medium-dark gray",
    )
    assert len(compact) <= 1000
    assert FUR_PHRASE_COMPACT in compact
    assert FUR_NO_SLIM_COMPACT in compact
    # 핵심 사실(가이드 + 코트 색)은 새 절 때문에 밀려나지 않는다.
    assert "Coat colors: apricot, cream, tan." in compact
    assert "Each photo rules what it shows." in compact


# ══════════════════════════════════════════════════════════════════════════
# 3. 다른 털 유형 — 짧고 매끈한 코트. 같은 문구가 개체와 무관하게 실린다
# ══════════════════════════════════════════════════════════════════════════


def _short_coat_visual_identity() -> dict:
    vi = {k: v for k, v in RICH_VISUAL_IDENTITY.items() if k != "semantic_traits"}
    vi["coat"] = {
        "status": "measured",
        "dominant_colors": [{"name": "black", "fraction": 0.7}],
        "secondary_colors": [{"name": "tan", "fraction": 0.2}],
        "palette": [],
    }
    vi["semantic_traits"] = {
        "status": "vlm",
        "traits": {
            "species": "dog",
            "ears": {"shape": "pricked"},
            "coat": {"marking_distribution": "no markings", "length": "short"},
        },
    }
    return vi


def _short_coat_morphology() -> SimpleNamespace:
    return _morphology(
        {
            "face": _vlm_traits(
                breed_estimate="Boxer",
                coat={"dominant_colors": ["black"], "secondary_colors": ["tan"], "length": "short", "texture": "smooth",
                      "marking_distribution": "no markings"},
            ),
        },
        tail_form="short, docked",
        ear_form="pricked",
    )


def test_short_smooth_coated_dog_gets_the_same_bidirectional_fur_clause():
    """짧고 매끈한 코트 개체도 apricot 과 **똑같은** 문구를 받는다 — 데이터 의존 규칙이 아니다."""
    guide = sel.reference_guide(_refset(THREE_VIEWS), THREE_PICKS)
    prompt = cp.build_canonical_prompt(
        visual_identity=_short_coat_visual_identity(),
        structural_identity={},
        reference_roles=[p["role"] for p in THREE_PICKS],
        reference_guide=guide,
        morphology_profile=_short_coat_morphology(),
    )
    assert FUR_PHRASE_FULL in prompt
    assert FUR_NO_ENLARGE in prompt
    assert FUR_NO_SLIM_EITHER in prompt
    # 짧은 코트 사실은 그대로 보존 — 새 절이 "풍성하게" 로 덮어쓰지 않는다.
    assert "Coat length: short." in prompt

    apricot_prompt = _apricot_prompt()
    # 정확히 같은 문구 — 개체 데이터가 풍성한 코트인지 매끈한 코트인지에 따라 바뀌지 않는다.
    for phrase in (FUR_PHRASE_FULL, FUR_NO_ENLARGE, FUR_NO_SLIM_EITHER, FUR_PERSPECTIVE):
        assert phrase in apricot_prompt and phrase in prompt


def test_short_smooth_coated_dog_compact_prompt_also_carries_the_clause():
    guide = sel.reference_guide(_refset(THREE_VIEWS), THREE_PICKS)
    compact = cp.build_compact_canonical_prompt(
        visual_identity=_short_coat_visual_identity(), reference_guide=guide,
        morphology_profile=_short_coat_morphology(),
    )
    assert len(compact) <= 1000
    assert FUR_PHRASE_COMPACT in compact
    assert FUR_NO_SLIM_COMPACT in compact
    assert "Coat colors: black, tan." in compact  # 높은 우선순위 사실은 새 절과 함께 살아남는다


# ══════════════════════════════════════════════════════════════════════════
# 4. 양방향 — "가늘게" 전용 규칙이 아니다 (풍성 쪽 금지와 축소 쪽 금지가 항상 같이 온다)
# ══════════════════════════════════════════════════════════════════════════


def test_the_rule_never_appears_one_sided_in_either_prompt():
    """풍성화 금지 문구가 있으면 축소 금지 문구도 항상 같이 있다 — 일괄 슬림 규칙이 아니다."""
    for prompt in (_apricot_prompt(), cp.build_canonical_prompt(
        visual_identity=_short_coat_visual_identity(), structural_identity={},
        reference_roles=[p["role"] for p in THREE_PICKS],
        reference_guide=sel.reference_guide(_refset(THREE_VIEWS), THREE_PICKS),
        morphology_profile=_short_coat_morphology(),
    )):
        has_no_enlarge = "plush-toy silhouette" in prompt
        has_no_slim = "do not slim or thin them either" in prompt
        assert has_no_enlarge and has_no_slim
        assert "naturally fluffy-legged pet must stay exactly as fluffy" in prompt
        assert "naturally slim or short-coated pet must stay exactly that slim" in prompt

    compact = cp.build_compact_canonical_prompt(
        visual_identity=FIX["visual_identity"], reference_guide=_apricot_guide(), morphology_profile=_apricot_morph(),
    )
    assert "no fluffing or slimming either way" in compact  # 컴팩트도 한쪽만 말하지 않는다


# ══════════════════════════════════════════════════════════════════════════
# 5. 범위 경계 — 임계값/순서/형태 프로필 빌더는 바꾸지 않았다
# ══════════════════════════════════════════════════════════════════════════


def test_no_optional_silhouette_threshold_was_introduced():
    forbidden_names = (
        "SILHOUETTE_THRESHOLD", "FUR_VOLUME_THRESHOLD", "silhouette_threshold",
        "fur_silhouette_gate", "compute_fur_ratio",
    )
    for name in forbidden_names:
        assert not hasattr(cp, name)


def test_reference_ordering_and_guide_lines_are_unchanged():
    """레퍼런스 순서/가이드 문구는 v3.1 과 완전히 같다 — v3.2 는 문구 추가일 뿐이다."""
    guide = _apricot_guide()
    lines = cp.reference_guide_lines(guide)
    assert lines[0] == "Reference photos, in the order supplied:"
    assert lines[1].startswith("Reference 1 —") and lines[2].startswith("Reference 2 —") and lines[3].startswith("Reference 3 —")
    compact_line = cp.compact_reference_guide_line(guide)
    assert compact_line.startswith("Photo 1:") and "Photo 2:" in compact_line and "Photo 3:" in compact_line


def test_morphology_profile_fact_extraction_is_unaffected():
    """형태 프로필 관측 기반 사실 추출(_fact_items 등)은 v3.1 과 동일하게 동작한다."""
    prompt = _apricot_prompt()
    assert "Verified identity facts for this individual (from its own photos):" in prompt
    assert "Eye color:" in prompt
    assert "Ear shape: floppy." in prompt


# ══════════════════════════════════════════════════════════════════════════
# 6. 보존 대상은 그대로 — 자세/신원/코트 색/무늬/프리미엄 표현
# ══════════════════════════════════════════════════════════════════════════


def test_preserved_identity_and_presentation_clauses_remain_intact():
    prompt = _apricot_prompt()
    assert "Presentation may improve" in prompt
    assert "Do not idealize the pet toward a breed standard" in prompt
    assert "Do not change a clearly standing pet into a sitting pose" in prompt
    assert "do not shift coat colors toward a more typical shade" in prompt
