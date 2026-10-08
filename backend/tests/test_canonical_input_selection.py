"""
정본(Canonical) 입력 레퍼런스 선택 — 신원 우선 선택기 계약 테스트 (Phase 2).

정책: 사진 1장은 완전히 지원된다. 2–3장은 선택적인 신원 보강이다. 정면/측면/3Q
커버리지를 강제하지 않는다 — 같은 개체라는 가장 믿을 만한 근거를 주는 1–3장을
고른다. 3자리를 채우려고 나쁜/중복 사진을 쓰지 않는다.

- 풀: 살아 있는 원본 ∧ 적격 ∧ 품질 바닥 이상. 사용자가 뺀 사진은 후보가 아니다.
- 씨앗 → 탐욕 추가(새 신원 가치가 충분할 때만) → pHash 로 보수적인 중복 제거.
- VLM 이 꺼져 있으면 뷰 커버리지를 지어내지 않는다 (중립 역할).
- 사진 2장의 대칭 불일치는 둘 다 버리지 않는다 — 다만 신원이 교차 확인됐다고
  주장하지 않는다.
- 확정된 **하나의** 목록이 프로바이더 입력·output_spec·QA 를 전부 만든다.
- 세트 빌드/역할 선택(build_reference_set / select_roles)은 바뀌지 않는다.
"""

from __future__ import annotations

from types import SimpleNamespace

import anyio
import pytest

from backend.services import canonical_image_providers as providers_mod
from backend.services import canonical_pet_service as svc
from backend.services import canonical_qa
from backend.services import canonical_reference_selector as selector
from backend.services import durable_provider_jobs
from backend.services import pet_identity_service as ids
from backend.services import pet_morphology_service as morph
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry, vlm_identity

from .test_canonical_pet_builder import (
    VLM_QA_OK,
    FakeProvider,
    distinct_view_cutout,
)
from .test_pet_identity_profile import make_pet_cutout_png, make_striped_cutout_png
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


# ── 합성 분석 (세트가 저장하는 모양 그대로) ─────────────────────────────────

#: 서로 멀리 떨어진 pHash (해밍 거리 32) — "서로 다른 이미지".
PHASH = {
    "p0": "0000000000000000",
    "p1": "ffffffff00000000",
    "p2": "00000000ffffffff",
    "p3": "ffff0000ffff0000",
    "p4": "0f0f0f0f0f0f0f0f",
}


def _a(
    *,
    usable=True,
    quality=0.8,
    label="CONSISTENT",
    view="UNKNOWN",
    vconf="high",
    phash: str | None = PHASH["p0"],
    reasons=None,
    **visible,
) -> dict:
    """VLM 이 꺼진 분석이 기본이다 (뷰 UNKNOWN, 가시성 전부 unknown)."""
    vis = {k: "unknown" for k in VIS_KEYS}
    vis.update(visible)
    eligibility = {
        "usable_for_identity": usable,
        "reasons": list(reasons or ([] if usable else ["no_segmentation_available"])),
        "full_body_visible": "likely",
    }
    if phash:
        eligibility["signature"] = {
            "version": ids.SIGNATURE_VERSION,
            "phash": phash,
            "hsv_hist": [1.0] + [0.0] * 63,
        }
    return {
        "eligibility": eligibility,
        "quality": {"base_quality": quality},
        "consistency": {"label": label},
        "classification": {"view_label": view, "view_confidence": vconf, "visibility": vis},
    }


def _refset(analyses: dict[str, dict]):
    return SimpleNamespace(
        items=[], source_reference_ids=sorted(analyses), reference_analysis=analyses
    )


def _ids(result) -> list[str]:
    return [p["reference_id"] for p in result["selected"]]


def _roles(result) -> list[str]:
    return [p["role"] for p in result["selected"]]


def _status(result, rid: str) -> str:
    return result["decisions"][rid]["status"]


# ══════════════════════════════════════════════════════════════════════════
# 여러 장: 쓸모 있을 때만 더한다
# ══════════════════════════════════════════════════════════════════════════


def test_three_good_complementary_photos_are_all_selected():
    result = selector.select(
        _refset(
            {
                "face": _a(view="FRONT", face_visible="yes", ears_visible="yes", phash=PHASH["p0"]),
                "body": _a(view="LEFT", full_body_visible="yes", tail_visible="yes", phash=PHASH["p1"]),
                "q3": _a(view="FRONT_RIGHT_3Q", distinct_markings_visible="yes", phash=PHASH["p2"]),
            }
        )
    )
    assert sorted(_ids(result)) == ["body", "face", "q3"]
    assert result["mode"] == selector.MODE_SEMANTIC
    assert result["identity_confidence"] == "unverified"
    roles = dict(zip(_ids(result), _roles(result)))
    assert roles["face"] == "PRIMARY_FACE"
    assert roles["body"] == "PRIMARY_FULL_BODY"
    assert roles["q3"] == "PRIMARY_3Q"
    assert all(_status(result, rid) == selector.SELECTED for rid in ("face", "body", "q3"))


def test_three_useful_same_view_photos_can_all_be_selected():
    """같은 뷰여도 서로 다른 좋은 사진이면 독립된 신원 근거다 — 뷰 다양성은 선택 사항이다."""
    result = selector.select(
        _refset(
            {
                "a": _a(view="FRONT", face_visible="yes", quality=0.85, phash=PHASH["p0"]),
                "b": _a(view="FRONT", face_visible="yes", quality=0.80, phash=PHASH["p1"]),
                "c": _a(view="FRONT", face_visible="yes", quality=0.75, phash=PHASH["p2"]),
            }
        )
    )
    assert sorted(_ids(result)) == ["a", "b", "c"]
    for rid in ("b", "c"):
        decision = result["decisions"][rid]
        assert decision["reason"] == "independent_quality_evidence"
        assert decision["identity_gain"] == 0 and decision["view_bonus"] == 0
    # 역할은 서로 다르고, 세 장 다 "얼굴"이라고 반복해 주장하지 않는다.
    assert len(set(_roles(result))) == 3
    assert _roles(result).count("PRIMARY_FACE") == 1


def test_good_front_plus_duplicate_plus_blurry_partial_selects_one():
    result = selector.select(
        _refset(
            {
                "front": _a(view="FRONT", face_visible="yes", quality=0.85, phash=PHASH["p0"]),
                # 같은 사진을 다시 저장한 것 — pHash 가 거의 같다.
                "resaved": _a(view="FRONT", face_visible="yes", quality=0.80, phash="0000000000000007"),
                # 흐리고 일부만 보이는 사진 — 바닥은 넘지만 새 신원 단서가 없다.
                "blurry": _a(quality=0.45, phash=PHASH["p1"]),
            }
        )
    )
    assert _ids(result) == ["front"]
    assert _status(result, "resaved") == selector.DROPPED_REDUNDANT
    assert result["decisions"]["resaved"]["min_phash_distance"] == 3
    assert _status(result, "blurry") == selector.DROPPED_NO_IDENTITY_GAIN
    assert result["identity_confidence"] == "low"  # 한 장뿐이다


def test_two_useful_photos_are_both_selected():
    result = selector.select(
        _refset(
            {
                "a": _a(view="FRONT", face_visible="yes", phash=PHASH["p0"]),
                "b": _a(view="LEFT", full_body_visible="yes", phash=PHASH["p1"]),
            }
        )
    )
    assert sorted(_ids(result)) == ["a", "b"]
    assert result["identity_confidence"] == "unverified"
    assert result["identity_agreement"]["status"] == "not_contradicted"


@pytest.mark.parametrize("distance_phash", ["0000000000000000", "0000000000000001", "00000000000003ff"])
def test_two_near_duplicates_select_one(distance_phash):
    """정확히 같은 사진(0), 재저장(1), 약한 블러·노출 변화(10) — 전부 한 장으로 접힌다."""
    result = selector.select(
        _refset(
            {
                "a": _a(quality=0.9, phash="0000000000000000"),
                "b": _a(quality=0.8, phash=distance_phash),
            }
        )
    )
    assert _ids(result) == ["a"]
    assert _status(result, "b") == selector.DROPPED_REDUNDANT


def test_redundancy_is_conservative_distinct_images_are_not_collapsed():
    """
    pHash 는 거의-같은 이미지만 잡는다. 크롭이나 다른 각도는 거리가 멀다 — 그런
    사진을 중복으로 **잘못** 버리지 않는다 (그 대신 크롭을 중복으로 잡지도 못한다:
    알려진 한계).
    """
    result = selector.select(
        _refset(
            {
                "a": _a(quality=0.9, phash="0000000000000000"),
                "b": _a(quality=0.8, phash="00000000000007ff"),  # 거리 11 — 임계 바로 밖
            }
        )
    )
    assert sorted(_ids(result)) == ["a", "b"]
    assert result["decisions"]["b"]["min_phash_distance"] == 11


def test_a_photo_below_the_quality_floor_is_dropped_and_logged():
    result = selector.select(
        _refset(
            {
                "good": _a(quality=0.8, phash=PHASH["p0"]),
                "weak": _a(quality=0.30, phash=PHASH["p1"], face_visible="yes"),
            }
        )
    )
    assert _ids(result) == ["good"]
    weak = result["decisions"]["weak"]
    assert weak["status"] == selector.DROPPED_LOW_QUALITY
    assert weak["base_quality"] == 0.30
    assert str(selector.QUALITY_FLOOR) in weak["reason"]
    assert result["thresholds"]["quality_floor"] == selector.QUALITY_FLOOR


def test_a_unique_marking_image_is_selected_even_at_modest_quality():
    """고유 무늬를 보여 주는 사진은 품질이 평범해도 새 신원 정보다."""
    analyses = {
        "front": _a(view="FRONT", face_visible="yes", quality=0.85, phash=PHASH["p0"]),
        "marking": _a(quality=0.42, distinct_markings_visible="yes", phash=PHASH["p1"]),
        "plain": _a(quality=0.42, phash=PHASH["p2"]),
    }
    result = selector.select(_refset(analyses))
    assert sorted(_ids(result)) == ["front", "marking"]
    marking = result["decisions"]["marking"]
    assert marking["reason"] == "adds_identity_evidence"
    assert marking["new_identity_facets"] == ["distinct_markings_visible"]
    # 같은 품질인데 새 단서가 없는 사진은 들어오지 못한다 — 자리 채우기는 없다.
    assert _status(result, "plain") == selector.DROPPED_NO_IDENTITY_GAIN
    assert dict(zip(_ids(result), _roles(result)))["marking"] == "PRIMARY_MARKINGS"


def test_a_different_view_earns_a_bonus_only_when_it_shows_new_identity_information():
    base = {"front": _a(view="FRONT", face_visible="yes", quality=0.85, phash=PHASH["p0"])}
    informative = selector.select(
        _refset({**base, "left": _a(view="LEFT", full_body_visible="yes", quality=0.5, phash=PHASH["p1"])})
    )
    assert informative["decisions"]["left"]["view_bonus"] > 0

    # 뷰는 다르지만 새로 보여 주는 것이 없다 — 가산점이 없다.
    empty = selector.select(
        _refset({**base, "left": _a(view="LEFT", face_visible="yes", quality=0.5, phash=PHASH["p1"])})
    )
    assert empty["decisions"]["left"]["view_bonus"] == 0
    assert _status(empty, "left") == selector.DROPPED_NO_IDENTITY_GAIN


def test_never_more_than_three_and_the_rest_are_logged():
    analyses = {
        f"r{i}": _a(quality=0.9 - i * 0.02, phash=PHASH[f"p{i}"]) for i in range(5)
    }
    result = selector.select(_refset(analyses))
    assert _ids(result) == ["r0", "r1", "r2"]
    for rid in ("r3", "r4"):
        assert _status(result, rid) == selector.DROPPED_NO_IDENTITY_GAIN
        assert result["decisions"][rid]["reason"] == "selection_full"


# ══════════════════════════════════════════════════════════════════════════
# 사진 1장
# ══════════════════════════════════════════════════════════════════════════


def test_one_good_photo_works():
    result = selector.select(_refset({"only": _a(quality=0.9)}))
    assert result["selected"] == [{"reference_id": "only", "role": "ONLY_AVAILABLE"}]
    assert _status(result, "only") == selector.SELECTED
    assert result["identity_agreement"]["status"] == "single_reference"
    assert result["identity_confidence"] == "low"

    evidenced = selector.select(_refset({"only": _a(view="FRONT", face_visible="yes")}))
    assert evidenced["selected"] == [{"reference_id": "only", "role": "PRIMARY_FACE"}]


def test_one_blurry_eligible_photo_does_not_crash_and_is_low_confidence():
    result = selector.select(_refset({"blurry": _a(quality=0.2)}))
    assert result["selected"] == [{"reference_id": "blurry", "role": "ONLY_AVAILABLE"}]
    decision = result["decisions"]["blurry"]
    assert decision["status"] == selector.DEGRADED_FALLBACK
    assert selector.DROPPED_LOW_QUALITY in decision["reason"]
    assert result["identity_confidence"] == "low"


def test_degraded_fallback_never_picks_an_ineligible_photo_over_an_eligible_one():
    result = selector.select(
        _refset(
            {
                "a-ineligible": _a(usable=False, quality=0.95),
                "m-eligible": _a(usable=True, quality=0.30),  # 바닥 아래지만 적격
                "z-ineligible": _a(usable=False, quality=0.90),
            }
        )
    )
    assert result["selected"] == [{"reference_id": "m-eligible", "role": "ONLY_AVAILABLE"}]
    assert _status(result, "m-eligible") == selector.DEGRADED_FALLBACK
    assert _status(result, "a-ineligible") == selector.DROPPED_INELIGIBLE


def test_nothing_eligible_still_proceeds_with_the_best_single_photo():
    result = selector.select(
        _refset({"a": _a(usable=False, quality=0.2), "b": _a(usable=False, quality=0.6)})
    )
    assert _ids(result) == ["b"]
    assert _status(result, "b") == selector.DEGRADED_FALLBACK


def test_empty_and_unanalysed_sets_do_not_crash():
    assert selector.select(SimpleNamespace(items=[], source_reference_ids=[], reference_analysis={}))[
        "selected"
    ] == []
    bare = SimpleNamespace(items=[], source_reference_ids=["b", "a"], reference_analysis={})
    assert selector.select(bare)["selected"] == [{"reference_id": "a", "role": "ONLY_AVAILABLE"}]


# ══════════════════════════════════════════════════════════════════════════
# VLM 꺼짐
# ══════════════════════════════════════════════════════════════════════════


def test_vlm_off_selects_on_quality_and_redundancy_without_inventing_views(caplog):
    result = selector.select(
        _refset(
            {
                "a": _a(quality=0.9, phash=PHASH["p0"]),
                "b": _a(quality=0.8, phash=PHASH["p1"]),
                "c": _a(quality=0.7, phash=PHASH["p2"]),
                "dup": _a(quality=0.85, phash="0000000000000001"),
                "weak": _a(quality=0.5, phash=PHASH["p3"]),
            }
        )
    )
    assert result["mode"] == selector.MODE_DETERMINISTIC
    assert _ids(result) == ["a", "b", "c"]
    # 뷰를 지어내지 않는다: 근거 없는 PRIMARY_* 역할이 없다.
    assert _roles(result) == ["SUPPORT_1", "SUPPORT_2", "SUPPORT_3"]
    assert all(d["view_label"] is None and d["identity_facets"] == [] for d in result["decisions"].values())
    assert _status(result, "dup") == selector.DROPPED_REDUNDANT


def test_vlm_off_deterministic_full_body_guess_is_not_used_as_a_role():
    """'프레임에 잘리지 않았다'는 결정론적 추정은 뷰 근거가 아니다 — 역할로 쓰지 않는다."""
    analysis = _a(quality=0.9)
    assert analysis["eligibility"]["full_body_visible"] == "likely"
    assert sets.role_fit("PRIMARY_FULL_BODY", analysis)[1].startswith("deterministic:")
    assert selector.select(_refset({"only": analysis}))["selected"][0]["role"] == "ONLY_AVAILABLE"


# ══════════════════════════════════════════════════════════════════════════
# 역할
# ══════════════════════════════════════════════════════════════════════════


def test_only_available_is_used_only_for_a_single_unlabelled_reference():
    # 근거 있는 역할 한 장 + 근거 없는 한 장 → "only available" 이 아니라 SUPPORT_1.
    mixed = selector.select(
        _refset(
            {
                "face": _a(view="FRONT", face_visible="yes", quality=0.9, phash=PHASH["p0"]),
                "plain": _a(quality=0.8, phash=PHASH["p1"]),
            }
        )
    )
    assert dict(zip(_ids(mixed), _roles(mixed))) == {"face": "PRIMARY_FACE", "plain": "SUPPORT_1"}

    # 중립 레퍼런스끼리만 번호를 매긴다: PRIMARY_FACE + SUPPORT_1 + SUPPORT_2.
    three = selector.select(
        _refset(
            {
                "face": _a(view="FRONT", face_visible="yes", quality=0.9, phash=PHASH["p0"]),
                "plain1": _a(quality=0.8, phash=PHASH["p1"]),
                "plain2": _a(quality=0.7, phash=PHASH["p2"]),
            }
        )
    )
    assert _roles(three) == ["PRIMARY_FACE", "SUPPORT_1", "SUPPORT_2"]
    assert "ONLY_AVAILABLE" not in _roles(three)


def test_every_role_the_selector_can_emit_is_accepted_by_prompt_and_providers(monkeypatch):
    from backend.services import canonical_prompt

    monkeypatch.setenv("RUNWAY_API_KEY", "test-not-a-real-key")
    emittable = [
        *selector._SEMANTIC_ROLE_ORDER,
        *selector.NEUTRAL_ROLES,
        selector.ROLE_ONLY_AVAILABLE,
    ]
    # 태그는 역할에서 16자로 잘려 만들어진다 — 잘린 뒤에도 전부 서로 달라야 한다.
    tags = {role: role.lower().replace("primary_", "")[:16] for role in emittable}
    assert len(set(tags.values())) == len(emittable)
    assert all(1 <= len(tag) <= 16 and tag.replace("_", "").isalnum() for tag in tags.values())
    # 새로 더한 중립 역할은 글자로 시작하는 3자 이상 태그다 (기존 "3q" 는 그대로 둔다).
    for role in (*selector.NEUTRAL_ROLES, selector.ROLE_ONLY_AVAILABLE):
        assert len(tags[role]) >= 3 and tags[role][0].isalpha()
    assert set(selector._SEMANTIC_ROLE_ORDER) <= set(sets.ROLES)  # 기존 역할만 재사용

    for start in range(0, len(emittable), 3):
        roles = emittable[start : start + 3]
        references = [
            providers_mod.CanonicalReference(reference_id=str(i), role=role, url=f"https://x/{i}.jpg", data=b"x")
            for i, role in enumerate(roles)
        ]
        payload = providers_mod.RunwayImageProvider()._payload(
            references, "short prompt", dict(canonical_prompt.CANONICAL_OUTPUT_SPEC)
        )
        assert [r["tag"] for r in payload["referenceImages"]] == [tags[r] for r in roles]
        prompt = canonical_prompt.build_canonical_prompt(
            visual_identity={}, structural_identity={}, reference_roles=roles
        )
        assert "The supplied references show, in order:" in prompt

    assert svc.ROLE_ONLY_AVAILABLE == selector.ROLE_ONLY_AVAILABLE
    assert (svc.ROLE_SUPPORT_1, svc.ROLE_SUPPORT_2, svc.ROLE_SUPPORT_3) == selector.NEUTRAL_ROLES


# ══════════════════════════════════════════════════════════════════════════
# 일관성: 2장 대칭 불일치 / 한 장만 동떨어짐
# ══════════════════════════════════════════════════════════════════════════


def test_two_photos_that_mismatch_each_other_are_both_kept_but_not_called_confirmed():
    result = selector.select(
        _refset(
            {
                "a": _a(quality=0.70, label="LIKELY_MISMATCH", phash=PHASH["p0"]),
                "b": _a(quality=0.80, label="LIKELY_MISMATCH", phash=PHASH["p1"]),
            }
        )
    )
    assert result["selected"] == [
        {"reference_id": "b", "role": "SUPPORT_1"},
        {"reference_id": "a", "role": "SUPPORT_2"},
    ]
    agreement = result["identity_agreement"]
    assert agreement["status"] == "ambiguous_mismatch"
    assert agreement["ambiguous_reference_ids"] == ["a", "b"]
    assert agreement["reliable_pairwise_signal"] is False
    # 두 장이 들어갔다고 해서 신원 신뢰도가 높다고 말하지 않는다.
    assert result["identity_confidence"] == "low"


def test_no_pair_is_ever_reported_as_cross_verified():
    """믿을 만한 쌍별 동일-개체 신호가 없다 — 서로 맞는 두 장도 '확인됨'이 아니다."""
    result = selector.select(
        _refset({"a": _a(phash=PHASH["p0"]), "b": _a(phash=PHASH["p1"])})
    )
    assert result["identity_agreement"]["reliable_pairwise_signal"] is False
    assert result["identity_agreement"]["status"] == "not_contradicted"
    assert "not cross-verified" in result["identity_agreement"]["note"]


def test_the_two_photo_rule_does_not_override_eligibility_or_quality():
    ineligible = selector.select(
        _refset(
            {
                "a": _a(usable=False, quality=0.9, label="LIKELY_MISMATCH", phash=PHASH["p0"]),
                "b": _a(quality=0.6, label="LIKELY_MISMATCH", phash=PHASH["p1"]),
            }
        )
    )
    assert ineligible["selected"] == [{"reference_id": "b", "role": "ONLY_AVAILABLE"}]

    weak = selector.select(
        _refset(
            {
                "a": _a(quality=0.30, label="LIKELY_MISMATCH", phash=PHASH["p0"]),
                "b": _a(quality=0.80, label="LIKELY_MISMATCH", phash=PHASH["p1"]),
            }
        )
    )
    assert _ids(weak) == ["b"]
    assert _status(weak, "a") == selector.DROPPED_LOW_QUALITY


def test_three_photos_with_one_outlier_isolate_it():
    result = selector.select(
        _refset(
            {
                "a": _a(quality=0.8, phash=PHASH["p0"]),
                "b": _a(quality=0.8, phash=PHASH["p1"]),
                "odd": _a(quality=0.95, label="LIKELY_MISMATCH", phash=PHASH["p2"]),
            }
        )
    )
    assert sorted(_ids(result)) == ["a", "b"]
    assert _status(result, "odd") == selector.DROPPED_INELIGIBLE
    assert result["decisions"]["odd"]["reason"] == "likely_mismatch_outlier"
    assert result["identity_agreement"]["status"] == "not_contradicted"


def test_mutual_mismatch_without_an_agreeing_pair_is_kept_and_marked_ambiguous():
    """서로 맞는 2장이 없으면 어느 사진이 틀렸는지 알 수 없다 — 버리지 않고 모호하다고 적는다."""
    result = selector.select(
        _refset(
            {
                "a": _a(quality=0.9, label="LIKELY_MISMATCH", phash=PHASH["p0"]),
                "b": _a(quality=0.8, label="LIKELY_MISMATCH", phash=PHASH["p1"]),
                "c": _a(quality=0.7, label="LIKELY_MISMATCH", phash=PHASH["p2"]),
            }
        )
    )
    assert _ids(result) == ["a", "b", "c"]
    assert result["identity_agreement"]["status"] == "ambiguous_mismatch"
    assert result["identity_confidence"] == "low"


# ══════════════════════════════════════════════════════════════════════════
# 풀: 살아 있는 원본만 / 해석 가능성은 확정의 일부
# ══════════════════════════════════════════════════════════════════════════


def test_references_that_are_not_active_originals_are_never_candidates():
    analyses = {
        "kept": _a(quality=0.6, phash=PHASH["p0"]),
        "removed": _a(quality=0.99, view="FRONT", face_visible="yes", phash=PHASH["p1"]),
    }
    result = selector.select(_refset(analyses), active_ids={"kept"})
    assert _ids(result) == ["kept"]
    removed = result["decisions"]["removed"]
    assert removed["status"] == selector.DROPPED_INELIGIBLE
    assert removed["reason"] == "not_an_active_original"

    # 살아 있는 것이 하나도 없으면 저하 폴백조차 뺀 사진을 쓰지 않는다.
    assert selector.select(_refset(analyses), active_ids=set())["selected"] == []


def test_an_unresolvable_reference_is_dropped_before_finalizing_and_the_next_is_considered():
    analyses = {
        "best": _a(quality=0.95, phash=PHASH["p0"]),
        "second": _a(quality=0.80, phash=PHASH["p1"]),
        "third": _a(quality=0.70, phash=PHASH["p2"]),
        "fourth": _a(quality=0.60, phash=PHASH["p3"]),
    }
    asked: list[str] = []

    def resolve(rid: str):
        asked.append(rid)
        return "bytes_unavailable" if rid in ("best", "third") else None

    result = selector.select(_refset(analyses), resolve=resolve)
    # 씨앗이 해석되지 않자 다음 후보가 씨앗이 됐고, 세 번째 대신 네 번째가 들어왔다.
    assert _ids(result) == ["second", "fourth"]
    for rid in ("best", "third"):
        assert _status(result, rid) == selector.DROPPED_UNRESOLVABLE
        assert result["decisions"][rid]["reason"] == "bytes_unavailable"
    # 선택된 것은 전부 해석 확인을 거쳤다 — 확정된 목록에 미확인 레퍼런스가 없다.
    assert set(_ids(result)) <= set(asked)
    assert all(_status(result, rid) == selector.SELECTED for rid in _ids(result))


def test_nothing_resolvable_yields_an_empty_selection_not_a_guess():
    result = selector.select(
        _refset({"a": _a(), "b": _a(phash=PHASH["p1"])}), resolve=lambda rid: "missing_from_ledger"
    )
    assert result["selected"] == []
    assert {d["status"] for d in result["decisions"].values()} == {selector.DROPPED_UNRESOLVABLE}


# ══════════════════════════════════════════════════════════════════════════
# 결정 로그 / 결정론
# ══════════════════════════════════════════════════════════════════════════


def test_every_reference_gets_exactly_one_logged_decision_with_scores():
    analyses = {
        "anchor": _a(view="FRONT", face_visible="yes", quality=0.9, phash=PHASH["p0"]),
        "added": _a(view="LEFT", full_body_visible="yes", quality=0.7, phash=PHASH["p1"]),
        "dup": _a(quality=0.85, phash="0000000000000001"),
        "weak": _a(quality=0.2, phash=PHASH["p2"]),
        "bad": _a(usable=False, quality=0.9, reasons=["rectangle_like_mask"], phash=PHASH["p3"]),
        "plain": _a(quality=0.4, phash=PHASH["p4"]),
    }
    result = selector.select(_refset(analyses))
    assert set(result["decisions"]) == set(analyses)
    statuses = {rid: d["status"] for rid, d in result["decisions"].items()}
    assert statuses == {
        "anchor": selector.SELECTED,
        "added": selector.SELECTED,
        "dup": selector.DROPPED_REDUNDANT,
        "weak": selector.DROPPED_LOW_QUALITY,
        "bad": selector.DROPPED_INELIGIBLE,
        "plain": selector.DROPPED_NO_IDENTITY_GAIN,
    }
    assert result["decisions"]["bad"]["reason"] == "rectangle_like_mask"
    assert result["decisions"]["anchor"]["seed_score"] > 0.9
    assert result["decisions"]["added"]["add_score"] >= selector.ADD_MIN_SCORE
    assert result["decisions"]["plain"]["add_score"] < selector.ADD_MIN_SCORE
    for decision in result["decisions"].values():
        assert decision["reason"] and "base_quality" in decision and "eligible" in decision
    assert result["selector_version"] == selector.SELECTOR_VERSION


def test_selection_is_deterministic_and_independent_of_input_order():
    analyses = {
        "b": _a(quality=0.8, phash=PHASH["p1"]),
        "a": _a(quality=0.8, phash=PHASH["p0"]),
        "c": _a(quality=0.8, phash=PHASH["p2"]),
    }
    forward = SimpleNamespace(items=[], source_reference_ids=["a", "b", "c"], reference_analysis=analyses)
    backward = SimpleNamespace(items=[], source_reference_ids=["c", "b", "a"], reference_analysis=analyses)
    first = selector.select(forward)
    assert first == selector.select(forward) == selector.select(backward)
    assert _ids(first) == ["a", "b", "c"]  # 동점은 reference_id 로 안정적으로 갈린다


def test_the_sets_role_items_are_not_consulted():
    """선택기는 세트의 items(역할 승자)에 기대지 않는다 — 분석만 읽는다."""
    analyses = {"a": _a(quality=0.5, phash=PHASH["p0"]), "b": _a(quality=0.9, phash=PHASH["p1"])}
    refset = SimpleNamespace(
        items=[{"reference_id": "a", "role": "PRIMARY_FULL_BODY", "selection_score": 0.1}],
        source_reference_ids=["a", "b"],
        reference_analysis=analyses,
    )
    assert _ids(selector.select(refset))[0] == "b"
    assert svc.select_input_references(refset) == selector.select(refset)["selected"]


def test_the_builder_version_stamp_is_unchanged():
    """선택기 버전은 output_spec 에만 남는다 — 멱등 스탬프를 건드리면 기존 펫이 다시 빌드된다."""
    assert svc.CANONICAL_BUILDER_VERSION == "canonical-builder-v1"
    assert "selector" not in " ".join(map(str, svc.analyzer_versions().keys()))


# ══════════════════════════════════════════════════════════════════════════
# 실제 세트 빌드 → build_canonical: 하나의 목록
# ══════════════════════════════════════════════════════════════════════════


class RecordingProvider(FakeProvider):
    def __init__(self, images):
        super().__init__("fake", images)
        self.seen: list[list[tuple[str, str]]] = []

    def generate(self, references, prompt, output_spec, metadata):
        self.seen.append([(r.reference_id, r.role) for r in references])
        assert all(r.data for r in references)  # 확정된 레퍼런스는 전부 바이트가 있다
        return super().generate(references, prompt, output_spec, metadata)


def _build(h: Harness, provider, **kw):
    return _run(
        svc.build_canonical(
            user_id=USER,
            pet_id=PET,
            fetch_bytes=kw.pop("fetch_bytes", h.fetch),
            providers=[provider],
            cutout_fn=lambda raw: raw,
            **kw,
        )
    )


def _seed_three(h: Harness, monkeypatch):
    face = h.seed(cutout=make_pet_cutout_png(), classification=cls(view="FRONT", face_visible="yes"))
    body = h.seed(
        cutout=distinct_view_cutout("mirror"),
        classification=cls(view="LEFT", full_body_visible="yes", tail_visible="yes"),
    )
    q3 = h.seed(
        cutout=distinct_view_cutout("flip"),
        classification=cls(view="FRONT_RIGHT_3Q", full_body_visible="yes"),
    )
    h.install_vlm(monkeypatch)
    return face, body, q3


def test_provider_output_spec_and_qa_all_use_the_same_selected_ids(uploads, monkeypatch):
    h = Harness()
    face, body, q3 = _seed_three(h, monkeypatch)
    originals = {r.id: h.bytes_by_path[r.object_path] for r in (face, body, q3)}

    vlm_reference_sets: list[list[bytes]] = []

    def fake_vlm_qa(candidate, references, candidate_mime="image/png", **kwargs):
        vlm_reference_sets.append([data for data, _mime in references])
        return VLM_QA_OK

    monkeypatch.setattr(vlm_identity, "qa_canonical_image", fake_vlm_qa)
    # VLM QA 는 결정론 QA 가 애매할 때만 불린다 — 여기서는 항상 부르게 해서 VLM 이
    # 받는 레퍼런스 이미지를 본다.
    from backend.services import vlm_escalation

    real_should_call = vlm_escalation.should_call_vlm
    monkeypatch.setattr(
        vlm_escalation,
        "should_call_vlm",
        lambda **kwargs: {**real_should_call(**kwargs), "decision": vlm_escalation.CALL},
    )

    signature_sets: list[list[dict]] = []
    real_evaluate = canonical_qa.evaluate_candidate

    def recording_evaluate(**kwargs):
        signature_sets.append(list(kwargs["reference_signatures"]))
        return real_evaluate(**kwargs)

    monkeypatch.setattr(canonical_qa, "evaluate_candidate", recording_evaluate)

    provider = RecordingProvider([make_pet_cutout_png()])
    version = _build(h, provider)

    picks = version.output_spec["input_references"]
    selected_ids = [p["reference_id"] for p in picks]
    assert sorted(selected_ids) == sorted([face.id, body.id, q3.id])
    assert [p["role"] for p in picks] == ["PRIMARY_FACE", "PRIMARY_FULL_BODY", "PRIMARY_3Q"]

    # 프로바이더 입력 == output_spec == 버전/후보 행의 입력 id.
    assert provider.seen and all(call == [(p["reference_id"], p["role"]) for p in picks] for call in provider.seen)
    assert version.input_reference_ids == selected_ids
    assert all(c.input_reference_ids == selected_ids for c in version.candidates)

    # QA 시그니처 == 선택된 레퍼런스들의 시그니처 (같은 순서).
    refset = _run(sets.get_set(user_id=USER, pet_id=PET, version=version.reference_set_version))
    expected_signatures = [refset.reference_analysis[rid]["eligibility"]["signature"] for rid in selected_ids]
    assert signature_sets and all(sigs == expected_signatures for sigs in signature_sets)

    # VLM QA 이미지 == 선택된 레퍼런스들의 원본 바이트 (같은 순서).
    expected_bytes = [originals[rid] for rid in selected_ids]
    assert vlm_reference_sets and all(images == expected_bytes for images in vlm_reference_sets)

    # 결정 로그가 output_spec 에 남는다 — 선택 목록과 한 치도 어긋나지 않는다.
    selection = version.output_spec["selection"]
    assert selection["selector_version"] == selector.SELECTOR_VERSION
    assert selection["selected"] == picks
    assert set(selection["decisions"]) == {face.id, body.id, q3.id}
    assert version.qa_summary["canonical_confidence"] == "unverified"


def test_a_reference_whose_bytes_cannot_be_read_is_dropped_before_the_list_is_final(
    uploads, monkeypatch
):
    h = Harness()
    face, body, q3 = _seed_three(h, monkeypatch)
    monkeypatch.setattr(vlm_identity, "qa_canonical_image", lambda *a, **k: VLM_QA_OK)
    refset = h.build()  # 분석은 바이트가 다 있을 때 끝났다

    def flaky_fetch(ref):
        return None if ref.object_path == body.object_path else h.fetch(ref)

    provider = RecordingProvider([make_pet_cutout_png()])
    version = _build(
        h, provider, fetch_bytes=flaky_fetch,
        pinned_reference_set_id=refset.id, pinned_reference_set_version=refset.version,
    )

    selected_ids = [p["reference_id"] for p in version.output_spec["input_references"]]
    assert body.id not in selected_ids and sorted(selected_ids) == sorted([face.id, q3.id])
    decision = version.output_spec["selection"]["decisions"][body.id]
    assert decision["status"] == selector.DROPPED_UNRESOLVABLE
    assert decision["reason"] == "bytes_unavailable"
    # 읽지 못한 레퍼런스는 어디에도 남지 않았다 — 생성과 기록이 같은 목록이다.
    assert version.input_reference_ids == selected_ids
    assert all([rid for rid, _ in call] == selected_ids for call in provider.seen)


def test_a_finalized_reference_that_later_cannot_be_loaded_fails_explicitly(uploads, monkeypatch):
    h = Harness()
    face, body, q3 = _seed_three(h, monkeypatch)
    monkeypatch.setattr(vlm_identity, "qa_canonical_image", lambda *a, **k: VLM_QA_OK)
    version = _build(h, RecordingProvider([make_pet_cutout_png()]))
    assert version.output_spec.get("selection")
    candidate = version.candidates[0]

    def later_fetch(ref):
        # 후보 자산은 읽히지만, 확정된 입력 레퍼런스 한 장이 사라졌다.
        return None if getattr(ref, "object_path", "") == body.object_path else h.fetch(ref) or make_pet_cutout_png()

    with pytest.raises(svc.CanonicalPetError) as error:
        _run(
            svc.reevaluate_canonical_candidate(
                user_id=USER,
                pet_id=PET,
                canonical_version_id=version.id,
                candidate_id=candidate.id,
                fetch_bytes=later_fetch,
                cutout_fn=lambda raw: raw,
                vlm_cache_mode="refresh",
            )
        )
    assert error.value.code == "INPUT_REFERENCE_UNRESOLVABLE"


def test_two_disjoint_photos_end_to_end_both_reach_the_provider_with_low_confidence(uploads):
    """
    실제 세트 빌드를 거친다: 색 분포가 겹치지 않는 적격 사진 2장 → 둘 다
    LIKELY_MISMATCH → 세트의 items 는 비어 있다(세트 빌드는 바뀌지 않았다).
    그래도 정본 입력은 비지 않고 두 장이 그대로 프로바이더에 간다 — 다만 신원이
    서로 확인됐다고 말하지 않는다.
    """
    h = Harness()
    r1 = h.seed(cutout=make_pet_cutout_png())
    r2 = h.seed(cutout=make_striped_cutout_png())

    refset = h.build()
    for r in (r1, r2):
        assert refset.reference_analysis[r.id]["consistency"]["label"] == sets.LIKELY_MISMATCH
        assert refset.reference_analysis[r.id]["excluded_reason"] == "excluded_likely_mismatch"
    assert refset.items == []  # select_roles / build_reference_set 은 그대로다

    provider = RecordingProvider([make_pet_cutout_png()])
    version = _build(h, provider)

    picks = version.output_spec["input_references"]
    assert {p["reference_id"] for p in picks} == {r1.id, r2.id}
    assert [p["role"] for p in picks] == ["SUPPORT_1", "SUPPORT_2"]
    assert all(call == [(p["reference_id"], p["role"]) for p in picks] for call in provider.seen)

    selection = version.output_spec["selection"]
    assert selection["mode"] == selector.MODE_DETERMINISTIC
    assert selection["identity_agreement"]["status"] == "ambiguous_mismatch"
    assert version.qa_summary["canonical_confidence"] == "low"


def test_three_real_photos_with_one_outlier_isolate_it_with_and_without_vlm(uploads, monkeypatch):
    h = Harness()
    r1 = h.seed(cutout=make_pet_cutout_png(), classification=cls(view="FRONT", face_visible="yes"))
    r2 = h.seed(
        cutout=distinct_view_cutout("mirror"),
        classification=cls(view="LEFT", full_body_visible="yes"),
    )
    outlier = h.seed(
        cutout=make_striped_cutout_png(),
        classification=cls(view="RIGHT", full_body_visible="yes"),
    )

    without_vlm = selector.select(h.build())
    assert without_vlm["mode"] == selector.MODE_DETERMINISTIC
    assert sorted(_ids(without_vlm)) == sorted([r1.id, r2.id])
    assert _roles(without_vlm) == ["SUPPORT_1", "SUPPORT_2"]
    assert without_vlm["decisions"][outlier.id]["reason"] == "likely_mismatch_outlier"

    h.install_vlm(monkeypatch)
    with_vlm = selector.select(h.build(force=True))
    assert with_vlm["mode"] == selector.MODE_SEMANTIC
    assert sorted(_ids(with_vlm)) == sorted([r1.id, r2.id])
    assert with_vlm["decisions"][outlier.id]["status"] == selector.DROPPED_INELIGIBLE


def test_real_duplicate_and_resaved_cutouts_collapse_to_one(uploads):
    """실제 시그니처 계산을 거친다: 같은 누끼, 그리고 축소·확대로 다시 저장한 누끼."""
    import io

    from PIL import Image

    base = make_pet_cutout_png()
    im = Image.open(io.BytesIO(base)).convert("RGBA")
    resaved_im = im.resize((im.width * 3 // 4, im.height * 3 // 4)).resize(im.size)
    buf = io.BytesIO()
    resaved_im.save(buf, format="PNG")

    h = Harness()
    first = h.seed(cutout=base)
    h.seed(cutout=base)
    h.seed(cutout=buf.getvalue())
    result = selector.select(h.build())

    assert len(result["selected"]) == 1
    dropped = [d for rid, d in result["decisions"].items() if d["status"] == selector.DROPPED_REDUNDANT]
    assert len(dropped) == 2
    assert all(d["min_phash_distance"] <= selector.PHASH_NEAR_DUPLICATE_MAX for d in dropped)
    assert first.id in result["decisions"]


def test_a_real_crop_is_not_detected_as_redundant_known_limitation(uploads):
    """
    알려진 한계를 고정해 둔다: 같은 사진의 크롭은 pHash 거리가 멀어 중복으로 잡히지
    않는다. 복잡한 휴리스틱을 더하지 않고 그대로 둔다 — 이 테스트가 깨지면 신호가
    바뀐 것이므로 임계값을 다시 봐야 한다.
    """
    import io

    from PIL import Image

    base = make_pet_cutout_png()
    im = Image.open(io.BytesIO(base)).convert("RGBA")
    buf = io.BytesIO()
    im.crop((0, 0, im.width, int(im.height * 0.7))).save(buf, format="PNG")

    h = Harness()
    h.seed(cutout=base)
    crop = h.seed(cutout=buf.getvalue())
    result = selector.select(h.build())
    decision = result["decisions"][crop.id]
    assert decision["status"] != selector.DROPPED_REDUNDANT
    assert (decision.get("min_phash_distance") or 99) > selector.PHASH_NEAR_DUPLICATE_MAX


def test_a_removed_original_in_a_pinned_set_never_reaches_the_provider(uploads, monkeypatch):
    """
    세트가 만들어진 뒤 사용자가 사진 한 장을 뺐다(Phase 1 의 rejected). 그 세트를
    고정해 빌드해도 뺀 사진은 후보가 아니다.
    """
    h = Harness()
    face, body, q3 = _seed_three(h, monkeypatch)
    monkeypatch.setattr(vlm_identity, "qa_canonical_image", lambda *a, **k: VLM_QA_OK)
    refset = h.build()
    assert face.id in refset.source_reference_ids

    _run(refs.reject_original(user_id=USER, pet_id=PET, reference_id=face.id))

    provider = RecordingProvider([make_pet_cutout_png()])
    version = _build(
        h, provider,
        pinned_reference_set_id=refset.id, pinned_reference_set_version=refset.version,
    )
    selected_ids = [p["reference_id"] for p in version.output_spec["input_references"]]
    assert face.id not in selected_ids and sorted(selected_ids) == sorted([body.id, q3.id])
    decision = version.output_spec["selection"]["decisions"][face.id]
    assert decision["status"] == selector.DROPPED_INELIGIBLE
    assert decision["reason"] == "not_an_active_original"
    assert all(face.id not in [rid for rid, _ in call] for call in provider.seen)


def test_one_real_photo_builds_end_to_end(uploads):
    h = Harness()
    only = h.seed(cutout=make_pet_cutout_png())
    provider = RecordingProvider([make_pet_cutout_png()])
    version = _build(h, provider)
    assert version.output_spec["input_references"] == [
        {"reference_id": only.id, "role": "ONLY_AVAILABLE"}
    ]
    assert provider.seen == [[(only.id, "ONLY_AVAILABLE")]]
    assert version.qa_summary["canonical_confidence"] == "low"
    assert version.status in (svc.STATUS_COMPLETE, svc.STATUS_REVIEW)


# ══════════════════════════════════════════════════════════════════════════
# 신뢰도 필드가 주장하는 것 — 확인 없는 여러 장은 "normal" 이 아니다
# ══════════════════════════════════════════════════════════════════════════


def test_two_different_pets_selected_together_are_never_normal(uploads):
    """
    실제 세트 빌드를 거친다. 서로 다른 동물 두 마리(코트가 전혀 다르다) — 선택기는
    둘을 가려낼 믿을 만한 신호가 없어 둘 다 뽑는다. 그 사실을 "normal" 로 포장하지 않는다.
    """
    h = Harness()
    h.seed(cutout=make_pet_cutout_png())
    h.seed(cutout=make_striped_cutout_png())
    result = selector.select(h.build())
    assert len(result["selected"]) == 2
    assert result["identity_confidence"] != "normal"

    # 색 분포가 우연히 겹쳐 "모순 없음"으로 통과하는 다른 펫 두 마리도 마찬가지다
    # (캘리브레이션: 서로 다른 펫 쌍의 절반 이상이 CONSISTENT 로 나왔다).
    passing = selector.select(
        _refset(
            {
                "pet_a": _a(quality=0.89, label="CONSISTENT", phash=PHASH["p0"]),
                "pet_b": _a(quality=0.71, label="CONSISTENT", phash=PHASH["p1"]),
            }
        )
    )
    assert _ids(passing) == ["pet_a", "pet_b"]
    assert passing["identity_agreement"]["status"] == "not_contradicted"
    assert passing["identity_confidence"] == selector.CONFIDENCE_UNVERIFIED


@pytest.mark.parametrize("labels", [
    ("CONSISTENT", "CONSISTENT", "CONSISTENT"),
    ("CONSISTENT", "UNCERTAIN", "CONSISTENT"),
    ("UNCERTAIN", "UNCERTAIN", "UNCERTAIN"),
])
def test_three_mutually_unverified_references_are_never_normal(labels):
    result = selector.select(
        _refset(
            {
                name: _a(quality=0.9 - i * 0.05, label=label, phash=PHASH[f"p{i}"])
                for i, (name, label) in enumerate(zip("abc", labels))
            }
        )
    )
    assert _ids(result) == ["a", "b", "c"]
    assert result["identity_confidence"] == selector.CONFIDENCE_UNVERIFIED
    assert result["identity_agreement"]["verified_same_individual"] is False


def test_not_contradicted_does_not_mean_verified():
    result = selector.select(
        _refset(
            {
                "face": _a(view="FRONT", face_visible="yes", phash=PHASH["p0"]),
                "body": _a(view="LEFT", full_body_visible="yes", phash=PHASH["p1"]),
            }
        )
    )
    agreement = result["identity_agreement"]
    assert agreement["status"] == "not_contradicted"
    assert agreement["verified_same_individual"] is False
    assert agreement["reliable_pairwise_signal"] is False
    assert result["identity_confidence"] == selector.CONFIDENCE_UNVERIFIED
    # 믿을 만한 확인 신호가 없는 한 "normal" 은 나올 수 없다.
    assert selector.RELIABLE_PAIRWISE_IDENTITY_SIGNAL is False


def test_normal_confidence_is_unreachable_without_a_reliable_identity_signal():
    """어떤 입력으로도 — 품질이 높고, 상보적이고, 일관돼도 — normal 이 나오지 않는다."""
    shapes = [
        {"only": _a(quality=0.99, view="FRONT", face_visible="yes")},
        {"a": _a(quality=0.99, phash=PHASH["p0"]), "b": _a(quality=0.99, phash=PHASH["p1"])},
        {
            "a": _a(quality=0.99, view="FRONT", face_visible="yes", phash=PHASH["p0"]),
            "b": _a(quality=0.99, view="LEFT", full_body_visible="yes", phash=PHASH["p1"]),
            "c": _a(quality=0.99, view="FRONT_RIGHT_3Q", distinct_markings_visible="yes", phash=PHASH["p2"]),
        },
        {"a": _a(label="LIKELY_MISMATCH", phash=PHASH["p0"]), "b": _a(label="LIKELY_MISMATCH", phash=PHASH["p1"])},
        {"weak": _a(quality=0.1)},
    ]
    seen = {selector.select(_refset(analyses))["identity_confidence"] for analyses in shapes}
    assert seen == {selector.CONFIDENCE_LOW, selector.CONFIDENCE_UNVERIFIED}


def test_existing_low_confidence_cases_stay_low():
    single = selector.select(_refset({"only": _a(quality=0.9)}))
    assert single["identity_confidence"] == selector.CONFIDENCE_LOW

    degraded = selector.select(_refset({"blurry": _a(quality=0.2)}))
    assert _status(degraded, "blurry") == selector.DEGRADED_FALLBACK
    assert degraded["identity_confidence"] == selector.CONFIDENCE_LOW

    ambiguous = selector.select(
        _refset(
            {
                "a": _a(quality=0.7, label="LIKELY_MISMATCH", phash=PHASH["p0"]),
                "b": _a(quality=0.8, label="LIKELY_MISMATCH", phash=PHASH["p1"]),
            }
        )
    )
    assert ambiguous["identity_agreement"]["status"] == "ambiguous_mismatch"
    assert ambiguous["identity_confidence"] == selector.CONFIDENCE_LOW

    # 여러 장 중 중복이 접혀 한 장만 남은 경우도 한 장이다.
    collapsed = selector.select(
        _refset({"a": _a(quality=0.9, phash="0000000000000000"), "b": _a(quality=0.8, phash="0000000000000001")})
    )
    assert _ids(collapsed) == ["a"]
    assert collapsed["identity_confidence"] == selector.CONFIDENCE_LOW


def test_the_confidence_correction_changed_no_threshold_and_no_selection():
    assert selector.QUALITY_FLOOR == 0.35
    assert selector.PHASH_NEAR_DUPLICATE_MAX == 10
    assert selector.ADD_MIN_SCORE == 0.33
    assert selector.MAX_REFERENCES == 3

    # 대표 입력의 선택·역할·결정 상태는 신뢰도 필드와 무관하게 그대로다.
    analyses = {
        "anchor": _a(view="FRONT", face_visible="yes", quality=0.9, phash=PHASH["p0"]),
        "added": _a(view="LEFT", full_body_visible="yes", quality=0.7, phash=PHASH["p1"]),
        "dup": _a(quality=0.85, phash="0000000000000001"),
        "weak": _a(quality=0.2, phash=PHASH["p2"]),
        "bad": _a(usable=False, quality=0.9, reasons=["rectangle_like_mask"], phash=PHASH["p3"]),
        "plain": _a(quality=0.4, phash=PHASH["p4"]),
    }
    result = selector.select(_refset(analyses))
    assert result["selected"] == [
        {"reference_id": "anchor", "role": "PRIMARY_FACE"},
        {"reference_id": "added", "role": "PRIMARY_FULL_BODY"},
    ]
    assert {rid: d["status"] for rid, d in result["decisions"].items()} == {
        "anchor": selector.SELECTED,
        "added": selector.SELECTED,
        "dup": selector.DROPPED_REDUNDANT,
        "weak": selector.DROPPED_LOW_QUALITY,
        "bad": selector.DROPPED_INELIGIBLE,
        "plain": selector.DROPPED_NO_IDENTITY_GAIN,
    }
    assert result["decisions"]["added"]["add_score"] == 0.8
    assert result["identity_confidence"] == selector.CONFIDENCE_UNVERIFIED


def test_unverified_reaches_the_stored_canonical_summary(uploads, monkeypatch):
    """build_canonical 이 선택기의 신뢰도를 그대로 qa_summary 에 싣는다."""
    h = Harness()
    _seed_three(h, monkeypatch)
    monkeypatch.setattr(vlm_identity, "qa_canonical_image", lambda *a, **k: VLM_QA_OK)
    version = _build(h, RecordingProvider([make_pet_cutout_png()]))
    assert len(version.output_spec["input_references"]) == 3
    assert version.output_spec["selection"]["identity_confidence"] == "unverified"
    assert version.qa_summary["canonical_confidence"] == "unverified"
