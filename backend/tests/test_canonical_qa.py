from __future__ import annotations

import anyio
import pytest

from backend.services import business_qa, canonical_qa
from backend.services import pet_identity_service as ids
from backend.services import pet_reference_service as refs
from backend.services import pet_registry
from backend.services import supabase_assets

from .conftest import make_jpeg_bytes
from .test_canonical_pet_builder import VLM_QA_OK
from .test_pet_identity_profile import DIAG, make_pet_cutout_png


def _run(coro):
    return anyio.run(lambda: coro)


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("CANONICAL_QA_MIN_RESOLUTION", "100")

    async def fake_upload(path, data, content_type):
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    for m in (refs, pet_registry, ids):
        m.__reset_for_tests()
    yield
    for m in (refs, pet_registry, ids):
        m.__reset_for_tests()


def _seed_strict_profile(*, user_id="alice@test", content_id="cid1"):
    original = _run(
        refs.record_original(
            user_id=user_id,
            content_id=content_id,
            data=make_jpeg_bytes(140, 120),
            mime_type="image/jpeg",
            diagnostics=DIAG,
        )
    )
    cutout = _run(
        refs.record_derived(
            user_id=user_id,
            content_id=content_id,
            object_path=f"{user_id}/{content_id}/references/cutout_{original.content_hash[:16]}.png",
            derived_kind="cutout_reference",
            parent_reference_id=original.id,
            mime_type="image/png",
        )
    )
    cutout_bytes = make_pet_cutout_png()
    bytes_by_path = {
        original.object_path: make_jpeg_bytes(140, 120),
        cutout.object_path: cutout_bytes,
    }

    def fetch(ref):
        return bytes_by_path.get(ref.object_path)

    profile = _run(ids.build_identity_profile(user_id=user_id, pet_id="pet_cid1", fetch_bytes=fetch))
    sig = profile.reference_eligibility[original.id]["signature"]
    return profile, sig, cutout_bytes


def test_canonical_qa_consumes_fused_embedding_and_pattern(monkeypatch):
    profile, sig, cutout = _seed_strict_profile()
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=VLM_QA_OK,
    )
    assert qa["checks"]["visual_embedding"] == canonical_qa.PASS
    assert qa["checks"]["coat_pattern"] == canonical_qa.PASS
    assert qa["identity_evidence"]["comparisons"]["signature_hist_intersection"]["reference_count"] == 1
    assert qa["identity_evidence"]["comparisons"]["visual_embedding"]["cosine_similarity"] is not None
    assert qa["decision"] == canonical_qa.PASS


def test_canonical_qa_flags_embedding_mismatch(monkeypatch):
    profile, sig, _ = _seed_strict_profile()
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(make_pet_cutout_png(body=(20, 20, 20), patch=(245, 245, 245), cropped=True)),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=VLM_QA_OK,
    )
    assert qa["checks"]["coat_pattern"] in (canonical_qa.FAIL, canonical_qa.REVIEW, "unknown")
    assert qa["checks"]["identity_similarity"] in (canonical_qa.FAIL, canonical_qa.REVIEW)


def test_canonical_qa_fail_closed_on_strong_pattern_contradiction():
    profile, sig, _ = _seed_strict_profile()
    profile.visual_identity["coat_pattern"] = {
        "status": "fused",
        "value": "brown|brown|brown",
        "confidence": "high",
        "support_reference_ids": ["r1", "r2"],
    }
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(make_pet_cutout_png(body=(240, 240, 240), patch=(15, 15, 15))),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=VLM_QA_OK,
    )
    assert qa["checks"]["coat_pattern"] == canonical_qa.FAIL
    assert "coat_pattern_strong_mismatch" in qa["reasons"]
    result = _business_result(qa)
    assert result["integrity_status"] == "PASS"
    assert result["retry_action"] == "STOP"


def test_canonical_qa_uses_multi_reference_signature_evidence(monkeypatch):
    from backend.services import pet_identity_service as pid

    profile, sig, cutout = _seed_strict_profile()
    profile.visual_identity["same_individual_gate"] = {
        "status": "ready",
        "thresholds": {
            "min_signature_hist_intersection": 0.5,
            "min_embedding_cosine_similarity": 0.72,
        },
    }

    def fake_sig_sim(a, b):
        marker = str(b.get("phash") or "")
        if marker.endswith("a"):
            return {"comparable": True, "hist_intersection": 0.62, "phash_hamming": 12}
        return {"comparable": True, "hist_intersection": 0.22, "phash_hamming": 28}

    monkeypatch.setattr(pid, "signature_similarity", fake_sig_sim)
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[{**sig, "phash": "0" * 15 + "a"}, {**sig, "phash": "0" * 16}],
        vlm_qa=VLM_QA_OK,
    )
    assert qa["identity_evidence"]["comparisons"]["signature_hist_intersection"]["reference_count"] == 2
    assert qa["checks"]["identity_similarity"] == canonical_qa.FAIL
    assert any("same_individual_gate_signature_contradiction" in r for r in qa["reasons"])


def _set_pattern(profile, value, *, confidence="high", supports=2):
    profile.visual_identity["coat_pattern"] = {
        "status": "fused",
        "value": value,
        "confidence": confidence,
        "support_reference_ids": [f"r{i}" for i in range(supports)],
    }


# 후보의 실측 패턴: 기본 누끼 = brown|brown|brown, 반전 누끼 = white|white|white.
_INVERTED = dict(body=(240, 240, 240), patch=(15, 15, 15))


def _mirrored_three_tone_cutout() -> bytes:
    """좌/중/우 색이 다른 누끼 — mirror 관계 검증용."""
    import io

    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (200, 150), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, color in enumerate(((240, 240, 240), (125, 84, 53), (15, 15, 15))):
        d.rectangle((20 + i * 55, 40, 20 + (i + 1) * 55, 120), fill=(*color, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_canonical_qa_pattern_family_equivalence_is_review_not_pass_or_fail():
    """golden/tan 은 brown 계열, cream 은 white 계열 — 죽이지도, 자동 승인하지도 않는다."""
    for prof_value, candidate_kwargs in (
        ("golden|tan|golden", {}),
        ("cream|cream|cream", _INVERTED),
    ):
        profile, sig, _ = _seed_strict_profile()
        _set_pattern(profile, prof_value, confidence="high", supports=2)
        qa = canonical_qa.evaluate_candidate(
            cutout_rgba=ids.load_rgba(make_pet_cutout_png(**candidate_kwargs)),
            profile=profile,
            reference_signatures=[sig],
            vlm_qa=VLM_QA_OK,
        )
        assert qa["checks"]["coat_pattern"] == canonical_qa.REVIEW, prof_value
        assert "coat_pattern_family_equivalent_not_exact" in qa["reasons"]
        assert "coat_pattern_strong_mismatch" not in qa["reasons"]
        # 계열만 같은 패턴은 하드페일로도, coat_pattern PASS 로도 승격되지 않는다.
        assert qa["checks"]["coat_pattern"] != canonical_qa.FAIL, prof_value

    # 반전 누끼 케이스는 coat_colors/시그니처가 따로 FAIL 이라 전체가 FAIL 이다.
    profile, sig, _ = _seed_strict_profile()
    _set_pattern(profile, "cream|cream|cream", confidence="high", supports=2)
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(make_pet_cutout_png(**_INVERTED)),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=VLM_QA_OK,
    )
    assert qa["decision"] == canonical_qa.FAIL

    # 후보가 프로필과 같은 누끼일 때: 핵심 검사가 전부 PASS 라면 자문 검사(coat_pattern)
    # 의 REVIEW 하나가 전체를 REVIEW 로 끌어내리지 않는다 (v4).
    profile, sig, cutout = _seed_strict_profile()
    _set_pattern(profile, "golden|tan|golden", confidence="high", supports=2)
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=VLM_QA_OK,
    )
    assert qa["checks"]["coat_pattern"] == canonical_qa.REVIEW
    assert qa["decision"] == canonical_qa.PASS


def test_canonical_qa_pattern_exact_and_mirrored_still_pass():
    """이름까지 일치(좌우 반전 포함)하는 것만 PASS 다."""
    # 정확 일치: 후보 실측이 brown|brown|brown 이다.
    profile, sig, cutout = _seed_strict_profile()
    _set_pattern(profile, "brown|brown|brown", confidence="high", supports=2)
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=VLM_QA_OK,
    )
    assert qa["identity_evidence"]["comparisons"]["coat_pattern"]["relation"] == "exact"
    assert qa["checks"]["coat_pattern"] == canonical_qa.PASS

    # 좌우 반전도 동일 개체의 정당한 변형이다 — 후보 실측은 white|brown|black.
    profile, sig, _ = _seed_strict_profile()
    _set_pattern(profile, "black|brown|white", confidence="high", supports=2)
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(_mirrored_three_tone_cutout()),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=VLM_QA_OK,
    )
    assert qa["identity_evidence"]["comparisons"]["coat_pattern"]["candidate"] == "white|brown|black"
    assert qa["identity_evidence"]["comparisons"]["coat_pattern"]["relation"] == "mirror"
    assert qa["checks"]["coat_pattern"] == canonical_qa.PASS


def test_canonical_qa_pattern_relation_classification():
    """계열 정규화는 비교에만 쓰이고, 정확 일치와 계열 일치를 구분해서 돌려준다."""
    rel = canonical_qa._pattern_relation
    assert rel("brown|brown|brown", "brown|brown|brown") == "exact"
    assert rel("black|brown|white", "white|brown|black") == "mirror"
    assert rel("golden|tan|golden", "brown|brown|brown") == "family"
    assert rel("cream|cream|cream", "white|white|white") == "family"
    assert rel("white|brown|cream", "cream|golden|white") == "family"
    assert rel("cream|brown|white", "white|golden|cream") == "family"
    assert rel("brown|brown|brown", "white|white|white") == "contradiction"
    assert rel("brown|brown|brown", "") == "unknown"
    assert rel("brown|unknown|brown", "brown|brown|brown") == "unknown"


def test_canonical_qa_pattern_mismatch_medium_confidence_is_review():
    profile, sig, _ = _seed_strict_profile()
    _set_pattern(profile, "brown|brown|brown", confidence="medium", supports=2)
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(make_pet_cutout_png(**_INVERTED)),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=VLM_QA_OK,
    )
    assert qa["checks"]["coat_pattern"] == canonical_qa.REVIEW
    assert "coat_pattern_mismatch" in qa["reasons"]
    assert "coat_pattern_strong_mismatch" not in qa["reasons"]


def test_canonical_qa_pattern_mismatch_single_reference_is_review():
    """레퍼런스 1장짜리 프로필은 high 신뢰라도 패턴만으로 하드페일하지 않는다."""
    profile, sig, _ = _seed_strict_profile()
    _set_pattern(profile, "brown|brown|brown", confidence="high", supports=1)
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(make_pet_cutout_png(**_INVERTED)),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=VLM_QA_OK,
    )
    assert qa["checks"]["coat_pattern"] == canonical_qa.REVIEW
    assert "coat_pattern_mismatch_single_reference" in qa["reasons"]
    result = _business_result(qa)
    assert result["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert result["retry_action"] == "STOP"


def test_canonical_qa_high_embedding_does_not_rewrite_coat_pattern():
    """임베딩은 보조 신호다 — 0.90 을 넘겨도 coat_pattern REVIEW 를 PASS 로 바꾸지 못한다."""
    from .test_pet_identity_profile import make_striped_cutout_png

    profile, sig, _ = _seed_strict_profile()
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(make_striped_cutout_png()),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=VLM_QA_OK,
    )
    emb = qa["identity_evidence"]["comparisons"]["visual_embedding"]["cosine_similarity"]
    assert emb >= 0.90
    assert qa["checks"]["coat_pattern"] == canonical_qa.REVIEW
    assert not any("overridden" in r for r in qa["reasons"])
    # 다른 증거(시그니처/코트 계열)의 반증도 임베딩이 덮지 못한다.
    assert qa["decision"] == canonical_qa.FAIL


def test_canonical_qa_single_reference_embedding_does_not_hard_fail(monkeypatch):
    from backend.services import pet_identity_service as pid

    profile, sig, cutout = _seed_strict_profile()
    assert len(profile.visual_identity["visual_embedding"]["support_reference_ids"]) == 1
    monkeypatch.setattr(
        pid, "embedding_similarity", lambda a, b: {"comparable": True, "cosine_similarity": 0.10}
    )
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=VLM_QA_OK,
    )
    assert qa["checks"]["visual_embedding"] == canonical_qa.REVIEW
    assert any("too_low_single_reference" in r for r in qa["reasons"])
    assert qa["decision"] == canonical_qa.REVIEW


def test_canonical_qa_persists_vlm_evidence():
    profile, sig, cutout = _seed_strict_profile()
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa={**VLM_QA_OK, "identity_notes": "matching chest patch"},
    )
    vlm = qa["vlm"]
    for key in (
        "same_pet",
        "same_pet_confidence",
        "face_head_consistent",
        "ear_muzzle_consistent",
        "distinctive_markings_consistent",
        "persistent_morphology_consistent",
        "presentation_difference_only",
        "anatomy_plausible",
        "single_pet",
        "human_present",
        "background_neutral",
        "full_body_visible",
        "major_occlusion",
        "source",
        "model",
    ):
        assert key in vlm, key
    assert vlm["same_pet"] == "yes"
    assert vlm["same_pet_confidence"] == "high"
    assert vlm["identity_notes"] == "matching chest patch"
    assert qa["qa_version"] == "canonical-qa-v5"


def test_canonical_qa_vlm_different_pet_still_fails():
    profile, sig, cutout = _seed_strict_profile()
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa={**VLM_QA_OK, "same_pet": "no", "identity_notes": "different blaze"},
    )
    assert qa["checks"]["vlm_same_pet"] == canonical_qa.FAIL
    assert "vlm_says_different_pet" in qa["reasons"]
    assert qa["decision"] == canonical_qa.FAIL
    assert qa["vlm"]["same_pet"] == "no"
    assert qa["vlm"]["identity_notes"] == "different blaze"


# ══════════════════════════════════════════════════════════════════════════
# Business QA vNext Phase 2 — enhancement-tolerant Canonical authority
# ══════════════════════════════════════════════════════════════════════════


def _presentation_variant(raw: bytes, *, gains: tuple[float, float, float], lift: float = 0.0) -> bytes:
    import io

    import numpy as np
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as image:
        rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    rgb = rgba[:, :, :3].astype(np.float64)
    rgb = rgb * np.asarray(gains, dtype=np.float64)[None, None, :] + lift
    rgba[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(out, format="PNG")
    return out.getvalue()


def _business_result(qa_result: dict) -> dict:
    business_qa.attach_business_result(
        qa_result,
        attempt_number=1,
        request_kind="CANONICAL",
    )
    return qa_result["business_qa"]


def test_same_pet_brighter_presentation_is_deliverable():
    profile, sig, cutout = _seed_strict_profile()
    brighter = _presentation_variant(cutout, gains=(1.35, 1.35, 1.35), lift=22)
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(brighter),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa={**VLM_QA_OK, "presentation_difference_only": "yes"},
    )
    result = _business_result(qa)

    assert result["integrity_status"] == "PASS"
    assert business_qa.is_deliverable(qa) is True
    assert result["retry_action"] == "STOP"
    assert result["authority_profile"] == "canonical-identity-v2"


def test_same_pet_white_balance_change_is_deliverable():
    profile, sig, cutout = _seed_strict_profile()
    balanced = _presentation_variant(cutout, gains=(0.72, 1.08, 1.32), lift=12)
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(balanced),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa={**VLM_QA_OK, "presentation_difference_only": "yes"},
    )
    result = _business_result(qa)

    assert result["integrity_status"] == "PASS"
    assert result["delivery_action"] in ("DELIVER", "DELIVER_WITH_ADVISORY")
    assert result["retry_action"] == "STOP"


def test_hsv_histogram_mismatch_is_diagnostic_only(monkeypatch):
    from backend.services import pet_identity_service as pid

    profile, sig, cutout = _seed_strict_profile()
    monkeypatch.setattr(
        pid,
        "signature_similarity",
        lambda a, b: {"comparable": True, "hist_intersection": 0.0, "phash_hamming": 31},
    )
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=VLM_QA_OK,
    )
    assert qa["checks"]["identity_similarity"] == canonical_qa.FAIL

    result = _business_result(qa)
    assert result["authority_evidence"]["DIAGNOSTIC_ONLY"]["identity_similarity"] == "FAIL"
    assert result["integrity_status"] == "PASS"
    assert result["retry_action"] == "STOP"


def test_rgb_grid_mismatch_is_diagnostic_only(monkeypatch):
    from backend.services import pet_identity_service as pid

    profile, sig, cutout = _seed_strict_profile()
    profile.visual_identity["visual_embedding"]["support_reference_ids"] = ["r1", "r2"]
    monkeypatch.setattr(
        pid,
        "embedding_similarity",
        lambda a, b: {"comparable": True, "cosine_similarity": 0.05},
    )
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=VLM_QA_OK,
    )
    assert qa["checks"]["visual_embedding"] == canonical_qa.FAIL

    result = _business_result(qa)
    assert result["authority_evidence"]["DIAGNOSTIC_ONLY"]["visual_embedding"] == "FAIL"
    assert result["integrity_status"] == "PASS"
    assert result["retry_action"] == "STOP"


def test_coat_color_mismatch_alone_cannot_regenerate():
    profile, sig, _ = _seed_strict_profile()
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(make_pet_cutout_png(**_INVERTED)),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=VLM_QA_OK,
    )
    assert qa["checks"]["coat_colors"] == canonical_qa.FAIL

    result = _business_result(qa)
    assert result["authority_evidence"]["IDENTITY_SUPPORT"]["coat_colors"] == "FAIL"
    assert result["integrity_status"] == "PASS"
    assert result["retry_action"] == "STOP"


def test_minor_structure_difference_is_supporting_only():
    profile, sig, cutout = _seed_strict_profile()
    rgba = ids.load_rgba(cutout)
    candidate_ar = ids.analyze_structural_identity(rgba)["silhouette"]["bbox_aspect_ratio"]
    profile.structural_identity["silhouette"]["bbox_aspect_ratio"] = candidate_ar / 2.0
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=rgba,
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=VLM_QA_OK,
    )
    assert qa["checks"]["structure"] == canonical_qa.REVIEW

    result = _business_result(qa)
    assert result["authority_evidence"]["IDENTITY_SUPPORT"]["structure"] == "REVIEW"
    assert result["integrity_status"] == "PASS"
    assert result["retry_action"] == "STOP"


def test_strong_facial_identity_contradiction_blocks():
    profile, sig, cutout = _seed_strict_profile()
    profile.visual_identity["same_individual_gate"]["strict_lineage_reference_count"] = 2
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa={**VLM_QA_OK, "face_head_consistent": "no"},
    )
    result = _business_result(qa)

    assert qa["business_signals"]["canonical_face_head_identity"] == canonical_qa.FAIL
    assert result["integrity_status"] == "FAIL"
    assert result["delivery_action"] == "BLOCK"
    assert result["retry_action"] == "REGENERATE"


def test_strong_persistent_morphology_contradiction_requires_multi_reference_evidence():
    profile, sig, cutout = _seed_strict_profile()
    contradiction = {**VLM_QA_OK, "persistent_morphology_consistent": "no"}

    weak = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=contradiction,
    )
    weak_result = _business_result(weak)
    assert weak["business_signals"]["canonical_persistent_morphology_identity"] == canonical_qa.REVIEW
    assert weak_result["retry_action"] == "STOP"

    profile.visual_identity["same_individual_gate"]["strict_lineage_reference_count"] = 2
    strong = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=contradiction,
    )
    strong_result = _business_result(strong)
    assert strong["business_signals"]["canonical_persistent_morphology_identity"] == canonical_qa.FAIL
    assert strong_result["integrity_status"] == "FAIL"
    assert strong_result["retry_action"] == "REGENERATE"


def test_low_confidence_same_pet_disagreement_is_not_a_hard_contradiction():
    profile, sig, cutout = _seed_strict_profile()
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa={
            **VLM_QA_OK,
            "same_pet": "no",
            "same_pet_confidence": "low",
            "face_head_consistent": "unknown",
            "ear_muzzle_consistent": "unknown",
            "distinctive_markings_consistent": "unknown",
        },
    )
    result = _business_result(qa)

    assert qa["checks"]["vlm_same_pet"] == canonical_qa.FAIL
    assert result["authority_evidence"]["IDENTITY_SUPPORT"]["vlm_same_pet"] == "FAIL"
    assert result["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert result["retry_action"] == "STOP"


def test_strong_distinctive_marking_contradiction_with_good_evidence_blocks():
    profile, sig, cutout = _seed_strict_profile()
    profile.visual_identity["facial_markings"] = {
        "status": "fused",
        "value": "white blaze from forehead to muzzle",
        "confidence": "high",
        "support_reference_ids": ["r1", "r2"],
    }
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa={**VLM_QA_OK, "distinctive_markings_consistent": "no"},
    )
    result = _business_result(qa)

    assert qa["business_signals"]["canonical_distinctive_markings_identity"] == canonical_qa.FAIL
    assert qa["identity_evidence"]["business_identity"]["strong_profile_marking_evidence"] is True
    assert result["integrity_status"] == "FAIL"
    assert result["retry_action"] == "REGENERATE"


def test_ambiguous_single_reference_marking_disagreement_is_advisory():
    profile, sig, cutout = _seed_strict_profile()
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa={**VLM_QA_OK, "distinctive_markings_consistent": "no"},
    )
    result = _business_result(qa)

    assert qa["business_signals"]["canonical_distinctive_markings_identity"] == canonical_qa.REVIEW
    assert result["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert result["retry_action"] == "STOP"


def test_severe_anatomy_corruption_remains_integrity_fail():
    profile, sig, cutout = _seed_strict_profile()
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa={**VLM_QA_OK, "anatomy_plausible": "no"},
    )
    result = _business_result(qa)

    assert result["authority_evidence"]["INTEGRITY_HARD"]["vlm_anatomy"] == "FAIL"
    assert result["integrity_status"] == "FAIL"
    assert result["delivery_action"] == "BLOCK"


def test_missing_or_invalid_cutout_is_integrity_fail():
    profile, sig, _ = _seed_strict_profile()
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=None,
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=VLM_QA_OK,
    )
    result = _business_result(qa)

    assert qa["checks"]["cutout"] == "unknown"  # legacy evidence is unchanged
    assert qa["business_signals"]["canonical_cutout_integrity"] == canonical_qa.FAIL
    assert result["integrity_status"] == "FAIL"
    assert result["delivery_action"] == "BLOCK"


@pytest.mark.parametrize(
    "vlm_update",
    ({"single_pet": "no"}, {"human_present": "yes"}),
)
def test_duplicate_pet_or_forbidden_contamination_remains_integrity_fail(vlm_update):
    profile, sig, cutout = _seed_strict_profile()
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa={**VLM_QA_OK, **vlm_update},
    )
    result = _business_result(qa)

    assert result["authority_evidence"]["INTEGRITY_HARD"]["vlm_composition"] == "FAIL"
    assert result["integrity_status"] == "FAIL"
    assert result["delivery_action"] == "BLOCK"


# ══════════════════════════════════════════════════════════════════════════
# coat_pattern 은 자문 검사다 (v4)
# ══════════════════════════════════════════════════════════════════════════
#
# 핵심 검사(CORE_CHECKS)가 전부 PASS 인 후보를 coat_pattern REVIEW 하나가
# REVIEW 로 끌어내리던 문제. 검사값과 이유/증거는 그대로 두고 판정만 바꾼다.


def _core_all_pass() -> dict[str, str]:
    return {key: canonical_qa.PASS for key in canonical_qa.CORE_CHECKS}


def test_decide_core_pass_with_advisory_review_is_pass():
    checks = {**_core_all_pass(), "coat_pattern": canonical_qa.REVIEW}
    assert canonical_qa.decide(checks) == canonical_qa.PASS


def test_decide_advisory_fail_still_blocks():
    checks = {**_core_all_pass(), "coat_pattern": canonical_qa.FAIL}
    assert canonical_qa.decide(checks) == canonical_qa.FAIL


def test_decide_core_review_is_still_review():
    for key in canonical_qa.CORE_CHECKS:
        checks = {**_core_all_pass(), key: canonical_qa.REVIEW, "coat_pattern": canonical_qa.PASS}
        assert canonical_qa.decide(checks) == canonical_qa.REVIEW, key
        # 자문 검사가 REVIEW 여도 핵심 REVIEW 를 승격시키지 않는다.
        checks["coat_pattern"] = canonical_qa.REVIEW
        assert canonical_qa.decide(checks) == canonical_qa.REVIEW, key


def test_decide_core_unknown_is_still_review():
    """VLM 확언이 없으면(unknown) 여전히 자동 승인 불가."""
    for key in ("vlm_same_pet", "vlm_anatomy", "vlm_composition"):
        checks = {**_core_all_pass(), key: "unknown", "coat_pattern": canonical_qa.REVIEW}
        assert canonical_qa.decide(checks) == canonical_qa.REVIEW, key


def test_decide_core_fail_is_fail():
    for key in canonical_qa.CORE_CHECKS:
        checks = {**_core_all_pass(), key: canonical_qa.FAIL, "coat_pattern": canonical_qa.PASS}
        assert canonical_qa.decide(checks) == canonical_qa.FAIL, key


def test_canonical_qa_advisory_coat_pattern_review_does_not_block_pass():
    """실제 평가 경로: 핵심 전부 PASS + coat_pattern REVIEW → PASS, 증거는 보존."""
    profile, sig, cutout = _seed_strict_profile()
    _set_pattern(profile, "golden|tan|golden", confidence="high", supports=2)
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=VLM_QA_OK,
    )
    assert [qa["checks"][k] for k in canonical_qa.CORE_CHECKS] == [canonical_qa.PASS] * len(
        canonical_qa.CORE_CHECKS
    )
    assert qa["decision"] == canonical_qa.PASS
    # coat_pattern 을 PASS 로 고쳐 쓰지 않는다 — REVIEW 와 그 이유/증거가 남는다.
    assert qa["checks"]["coat_pattern"] == canonical_qa.REVIEW
    assert "coat_pattern_family_equivalent_not_exact" in qa["reasons"]
    assert "advisory_checks_not_blocking:coat_pattern" in qa["reasons"]
    comparison = qa["identity_evidence"]["comparisons"]["coat_pattern"]
    assert comparison["relation"] == "family"
    assert comparison["profile"] == "golden|tan|golden"


def test_canonical_qa_advisory_marker_absent_when_coat_pattern_passes():
    profile, sig, cutout = _seed_strict_profile()
    _set_pattern(profile, "brown|brown|brown", confidence="high", supports=2)
    qa = canonical_qa.evaluate_candidate(
        cutout_rgba=ids.load_rgba(cutout),
        profile=profile,
        reference_signatures=[sig],
        vlm_qa=VLM_QA_OK,
    )
    assert qa["checks"]["coat_pattern"] == canonical_qa.PASS
    assert qa["decision"] == canonical_qa.PASS
    assert not any(r.startswith("advisory_checks_not_blocking") for r in qa["reasons"])
