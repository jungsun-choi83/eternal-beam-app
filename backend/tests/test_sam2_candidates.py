"""
Phase 2A — SAM2 다중 마스크 후보 선택.

핵심 주장: predicted IoU 하나만 보고 고르면 안 된다. SAM2 의 IoU 는 "이 마스크가
얼마나 정밀한가"에 대한 자기 확신이라, 엉뚱한 대상(개+소파)을 깔끔하게 자른
마스크도 높은 점수를 받는다. 형태·포함도·중심 일치도를 함께 봐야 한다.

모델은 전부 목업한다 — 검증 대상은 추론 품질이 아니라 선택 로직이다.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.services import vitmatte_service as vs

from .conftest import blob_mask, make_jpeg_bytes

IMG_W, IMG_H = 128, 96
PROMPT_BBOX = (24, 12, 104, 84)  # 80 x 72, 프레임 중앙


def _ellipse_in_bbox(bbox, *, w=IMG_W, h=IMG_H, shrink=1.0) -> np.ndarray:
    """프롬프트 bbox 안에 들어맞는 타원 마스크 (충전율 ≈ 0.785 * shrink^2)."""
    x1, y1, x2, y2 = bbox
    cy, cx = (y1 + y2) / 2.0, (x1 + x2) / 2.0
    ry = max(1.0, (y2 - y1) / 2.0 * shrink)
    rx = max(1.0, (x2 - x1) / 2.0 * shrink)
    yy, xx = np.mgrid[0:h, 0:w]
    inside = ((yy - cy) / ry) ** 2 + ((xx - cx) / rx) ** 2 <= 1.0
    return np.where(inside, 255, 0).astype(np.uint8)


def _filled_rect(bbox, *, w=IMG_W, h=IMG_H) -> np.ndarray:
    m = np.zeros((h, w), dtype=np.uint8)
    x1, y1, x2, y2 = bbox
    m[y1:y2, x1:x2] = 255
    return m


# --------------------------------------------------------------------------
# 지표 단위 동작
# --------------------------------------------------------------------------


def test_shape_term_penalises_rectangles_and_slivers():
    assert vs._shape_term(0.65) == pytest.approx(1.0)  # 전형적인 동물 실루엣
    assert vs._shape_term(1.0) == pytest.approx(0.0)  # 꽉 찬 직사각형
    assert vs._shape_term(0.0) == pytest.approx(0.0)
    assert vs._shape_term(None) == 0.0
    # 구간 밖으로 갈수록 단조 감소
    assert vs._shape_term(0.90) < vs._shape_term(0.85)
    assert vs._shape_term(0.20) < vs._shape_term(0.45)


def test_prompt_containment_detects_spill():
    inside = _ellipse_in_bbox(PROMPT_BBOX)
    assert vs._prompt_containment(inside > 0, PROMPT_BBOX) == pytest.approx(1.0, abs=0.02)

    # 프레임 전체를 덮는 마스크 → 상당 부분이 bbox 밖
    everywhere = np.full((IMG_H, IMG_W), 255, dtype=np.uint8)
    spill = vs._prompt_containment(everywhere > 0, PROMPT_BBOX)
    assert spill < 0.55


def test_center_consistency():
    centered = vs.analyze_mask(_ellipse_in_bbox(PROMPT_BBOX)).mask_bbox
    assert vs._center_consistency(centered, PROMPT_BBOX) == pytest.approx(1.0, abs=0.05)

    offset = vs.analyze_mask(_ellipse_in_bbox((0, 0, 30, 24))).mask_bbox
    assert vs._center_consistency(offset, PROMPT_BBOX) < 0.5

    assert vs._center_consistency(None, PROMPT_BBOX) == 0.0


# --------------------------------------------------------------------------
# 후보 생성 및 유효성
# --------------------------------------------------------------------------


def test_build_candidate_fills_all_metrics():
    c = vs.build_sam2_candidate(0, _ellipse_in_bbox(PROMPT_BBOX), 0.88, PROMPT_BBOX)

    assert c.index == 0
    assert c.predicted_iou == 0.88
    assert c.area_fraction > 0
    assert c.bbox_fill_ratio == pytest.approx(0.785, abs=0.05)
    assert c.prompt_containment == pytest.approx(1.0, abs=0.02)
    assert c.center_consistency == pytest.approx(1.0, abs=0.05)
    assert c.rectangle_like is False
    assert c.valid is True
    assert c.rejected_reason is None
    assert c.selection_score > 0


@pytest.mark.parametrize(
    "mask_factory,expected_reason",
    [
        (lambda: np.zeros((IMG_H, IMG_W), dtype=np.uint8), "empty"),
        (lambda: blob_mask(IMG_H, IMG_W, area_fraction=0.005), "mask_too_small"),
        (
            lambda: blob_mask(IMG_H, IMG_W, area_fraction=0.95, rectangle=True),
            "mask_too_large",
        ),
        (lambda: _filled_rect(PROMPT_BBOX), "rectangle_like"),
    ],
)
def test_invalid_candidates_are_flagged(mask_factory, expected_reason):
    c = vs.build_sam2_candidate(0, mask_factory(), 0.99, PROMPT_BBOX)
    assert c.valid is False
    assert c.rejected_reason == expected_reason


def test_candidate_to_dict_excludes_mask():
    c = vs.build_sam2_candidate(1, _ellipse_in_bbox(PROMPT_BBOX), 0.9, PROMPT_BBOX)
    d = c.to_dict()
    assert "mask" not in d
    assert set(d) == {
        "index",
        "predicted_iou",
        "area_fraction",
        "bbox_fill_ratio",
        "prompt_containment",
        "center_consistency",
        "rectangle_like",
        "valid",
        "rejected_reason",
        "selection_score",
    }


# --------------------------------------------------------------------------
# 선택 로직 — 이 파트가 Phase 2A 의 핵심
# --------------------------------------------------------------------------


def test_lower_iou_candidate_wins_when_shape_is_better():
    """
    IoU 가 더 높지만 프롬프트 박스를 꽉 채운(=배경까지 삼킨) 후보 대신,
    IoU 는 낮아도 형태가 그럴듯한 후보가 선택되어야 한다.
    """
    greedy = vs.build_sam2_candidate(0, _filled_rect((0, 0, IMG_W, IMG_H)), 0.97, PROMPT_BBOX)
    plausible = vs.build_sam2_candidate(1, _ellipse_in_bbox(PROMPT_BBOX), 0.71, PROMPT_BBOX)

    best, reason = vs.select_sam2_candidate([greedy, plausible])

    assert best.index == 1, "낮은 IoU 라도 형태가 좋은 후보가 이겨야 한다"
    assert greedy.predicted_iou > plausible.predicted_iou
    assert plausible.selection_score > greedy.selection_score
    assert reason in ("best_weighted_score", "only_valid_candidate")


def test_three_candidates_selects_best_valid():
    """SAM2 의 전형적인 3-후보 출력: 몸통 일부 / 전체 / 개+바닥."""
    partial = vs.build_sam2_candidate(
        0, _ellipse_in_bbox(PROMPT_BBOX, shrink=0.35), 0.55, PROMPT_BBOX
    )
    whole = vs.build_sam2_candidate(1, _ellipse_in_bbox(PROMPT_BBOX), 0.82, PROMPT_BBOX)
    over = vs.build_sam2_candidate(2, _filled_rect((0, 0, IMG_W, IMG_H)), 0.90, PROMPT_BBOX)

    candidates = [partial, whole, over]
    best, reason = vs.select_sam2_candidate(candidates)

    assert len(candidates) == 3
    assert best.index == 1
    assert reason == "best_weighted_score"
    # 과잉 후보는 프레임 전체라서 무효 처리(너무 큼)되어야 한다.
    assert over.valid is False


def test_single_valid_candidate_reports_reason():
    good = vs.build_sam2_candidate(0, _ellipse_in_bbox(PROMPT_BBOX), 0.8, PROMPT_BBOX)
    tiny = vs.build_sam2_candidate(1, blob_mask(IMG_H, IMG_W, area_fraction=0.004), 0.95, PROMPT_BBOX)

    best, reason = vs.select_sam2_candidate([good, tiny])

    assert best.index == 0
    assert reason == "only_valid_candidate"


def test_no_valid_candidate_still_returns_best_effort():
    """
    전부 무효여도 예외를 던지지 않는다 — 최선을 돌려보내고, 하류의 Phase 1
    게이트가 정확한 422 코드(CUTOUT_MASK_TOO_SMALL 등)를 내게 한다.
    """
    a = vs.build_sam2_candidate(0, blob_mask(IMG_H, IMG_W, area_fraction=0.004), 0.9, PROMPT_BBOX)
    b = vs.build_sam2_candidate(1, blob_mask(IMG_H, IMG_W, area_fraction=0.002), 0.5, PROMPT_BBOX)

    best, reason = vs.select_sam2_candidate([a, b])

    assert reason == "no_valid_candidate_best_effort"
    assert best.valid is False
    assert best.index == 0  # 점수가 더 높은 쪽


def test_select_raises_on_empty_list():
    with pytest.raises(RuntimeError, match="no mask candidates"):
        vs.select_sam2_candidate([])


# --------------------------------------------------------------------------
# 파이프라인 통합 — 진단 필드가 실제로 응답까지 전달되는가
# --------------------------------------------------------------------------


def _install_fake_sam2(monkeypatch, candidates_masks_and_ious):
    """_sam2_candidates 를 목업해 지정한 마스크/IoU 조합을 내게 한다."""

    def fake_candidates(rgb, prompt_bbox, model_name, device, *, multimask):
        return [
            vs.build_sam2_candidate(i, mask, iou, prompt_bbox)
            for i, (mask, iou) in enumerate(candidates_masks_and_ious)
        ]

    monkeypatch.setattr(vs, "_sam2_candidates", fake_candidates)
    monkeypatch.setattr(
        vs, "_binary_mask_to_trimap", lambda m, **kw: np.where(m > 0, 255, 0).astype(np.uint8)
    )


def test_segment_foreground_records_candidate_diagnostics(monkeypatch):
    _install_fake_sam2(
        monkeypatch,
        [
            (_filled_rect((0, 0, IMG_W, IMG_H)), 0.97),
            (_ellipse_in_bbox(PROMPT_BBOX), 0.71),
            (_ellipse_in_bbox(PROMPT_BBOX, shrink=0.3), 0.60),
        ],
    )

    outcome = vs._segment_foreground(
        np.zeros((IMG_H, IMG_W, 3), dtype=np.uint8),
        PROMPT_BBOX,
        segmenter="sam2",
        sam2_model="fake/sam2",
        device="cpu",
    )

    assert outcome.segmenter_used == "sam2"
    assert outcome.fallback is False
    assert len(outcome.sam2_candidates) == 3
    assert outcome.sam2_selected_index == 1
    assert outcome.sam2_selection_reason == "best_weighted_score"
    # sam2_score 는 선택된 후보의 predicted IoU 여야 한다 (최고 IoU 가 아니라).
    assert outcome.sam2_score == 0.71


def test_full_pipeline_exposes_candidates_in_meta(monkeypatch):
    from .test_vitmatte_pipeline import _detection, _install_pipeline

    _install_pipeline(monkeypatch, detection=_detection())
    # _install_pipeline 이 _segment_foreground 를 목업하므로, 후보를 담은
    # SegmentationOutcome 을 직접 돌려주도록 다시 덮어쓴다.
    fg = _ellipse_in_bbox(PROMPT_BBOX)
    cands = [
        vs.build_sam2_candidate(0, _filled_rect((0, 0, IMG_W, IMG_H)), 0.97, PROMPT_BBOX),
        vs.build_sam2_candidate(1, fg, 0.71, PROMPT_BBOX),
    ]
    monkeypatch.setattr(
        vs,
        "_segment_foreground",
        lambda rgb, bbox, **kw: vs.SegmentationOutcome(
            fg_binary=fg,
            trimap=np.where(fg > 0, 255, 0).astype(np.uint8),
            segmenter_used="sam2",
            sam2_score=0.71,
            sam2_multimask=True,
            sam2_candidates=cands,
            sam2_selected_index=1,
            sam2_selection_reason="best_weighted_score",
        ),
    )

    _png, meta = vs.matte_foreground_with_meta(make_jpeg_bytes(IMG_W, IMG_H))

    assert meta["sam2_multimask"] is True
    assert meta["sam2_candidate_count"] == 2
    assert meta["sam2_selected_index"] == 1
    assert meta["sam2_selection_reason"] == "best_weighted_score"
    assert meta["sam2_score"] == 0.71
    assert [c["index"] for c in meta["sam2_candidates"]] == [0, 1]
    assert meta["sam2_candidates"][0]["valid"] is False
    weights = meta["sam2_selection_weights"]
    assert pytest.approx(sum(weights.values()), abs=1e-6) == 1.0


def test_debug_artifacts_include_every_candidate(monkeypatch):
    from .test_vitmatte_pipeline import _detection, _install_pipeline

    _install_pipeline(monkeypatch, detection=_detection())
    monkeypatch.setattr(vs, "DEBUG_ARTIFACTS_ENABLED", True)

    fg = _ellipse_in_bbox(PROMPT_BBOX)
    cands = [
        vs.build_sam2_candidate(0, _filled_rect((0, 0, IMG_W, IMG_H)), 0.97, PROMPT_BBOX),
        vs.build_sam2_candidate(1, fg, 0.71, PROMPT_BBOX),
    ]
    monkeypatch.setattr(
        vs,
        "_segment_foreground",
        lambda rgb, bbox, **kw: vs.SegmentationOutcome(
            fg_binary=fg,
            trimap=np.where(fg > 0, 255, 0).astype(np.uint8),
            segmenter_used="sam2",
            sam2_candidates=cands,
            sam2_selected_index=1,
            sam2_selection_reason="best_weighted_score",
        ),
    )

    artifacts: dict[str, bytes] = {}
    vs.matte_foreground_with_meta(make_jpeg_bytes(IMG_W, IMG_H), debug_artifacts=artifacts)

    candidate_files = [k for k in artifacts if "sam2_candidate" in k]
    assert len(candidate_files) == 2
    assert any("selected" in k for k in candidate_files)
    assert any("invalid-mask_too_large" in k for k in candidate_files)
    for k in candidate_files:
        assert artifacts[k][:8] == b"\x89PNG\r\n\x1a\n"


def test_sam2_mask_wrapper_still_returns_mask_and_score(monkeypatch):
    """background_inpaint_service / auto_rigging_service 가 쓰는 시그니처 유지."""
    seen: dict = {}

    def fake_candidates(rgb, prompt_bbox, model_name, device, *, multimask):
        seen["multimask"] = multimask
        return [vs.build_sam2_candidate(0, _ellipse_in_bbox(PROMPT_BBOX), 0.83, prompt_bbox)]

    monkeypatch.setattr(vs, "_sam2_candidates", fake_candidates)

    mask, score = vs._sam2_mask(
        np.zeros((IMG_H, IMG_W, 3), dtype=np.uint8), PROMPT_BBOX, "fake/sam2", "cpu"
    )

    assert isinstance(mask, np.ndarray)
    assert mask.dtype == np.uint8
    assert score == 0.83
    assert seen["multimask"] is False, "단일 마스크 래퍼는 multimask 를 쓰지 않는다"
