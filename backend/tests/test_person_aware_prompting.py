"""
Phase 2B — 사람 인지 SAM2 프롬프팅.

전제: Phase 2A 의 후보 랭킹만으로는 손이 안 없어진다. 세 후보 **모두** 손을
포함하기 때문이다. 그래서 음성 포인트(부정 증거)가 필요하다.

여기서 검증하는 것은 "SAM2 가 더 잘 자르는가"(모델 성능)가 아니라
**포인트를 안전한 위치에 만드는가**, 그리고 **명백히 나을 때만 채택하는가**이다.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.services import person_prompting as pp
from backend.services import vitmatte_service as vs

from .conftest import make_jpeg_bytes

IMG_W, IMG_H = 160, 120
PET_BBOX = (20, 20, 100, 100)
#: 사람 손이 개의 오른쪽에 붙어 있는 상황
PERSON_BBOX = (90, 30, 150, 90)


def _rect_mask(bbox, w=IMG_W, h=IMG_H) -> np.ndarray:
    m = np.zeros((h, w), dtype=np.uint8)
    x1, y1, x2, y2 = bbox
    m[y1:y2, x1:x2] = 255
    return m


def _pet_plus_hand() -> np.ndarray:
    """개 몸통(20..100) + 오른쪽으로 삐져나온 손(100..140) 이 이어진 마스크."""
    m = _rect_mask((20, 20, 100, 100))
    m[45:75, 100:140] = 255
    return m


def _pet_only() -> np.ndarray:
    return _rect_mask((20, 20, 100, 100))


class _FakeCandidate:
    """Sam2Candidate 의 최소 인터페이스."""

    def __init__(self, mask, selection_score, valid=True, index=0):
        self.mask = mask
        self.selection_score = selection_score
        self.valid = valid
        self.index = index


def _selector(candidates):
    return max(candidates, key=lambda c: c.selection_score), "best_weighted_score"


# --------------------------------------------------------------------------
# 1. 겹침 계산
# --------------------------------------------------------------------------


def test_bbox_overlap_fraction():
    assert pp.bbox_overlap_fraction((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)
    assert pp.bbox_overlap_fraction((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(0.5)
    assert pp.bbox_overlap_fraction((0, 0, 10, 10), (50, 50, 60, 60)) == 0.0
    assert pp.bbox_overlap_fraction((0, 0, 0, 0), (0, 0, 10, 10)) == 0.0


def test_person_region_mask_unions_boxes():
    region = pp.person_region_mask((IMG_H, IMG_W), [(0, 0, 10, 10), (20, 20, 30, 30)])
    assert region[5, 5] == 255
    assert region[25, 25] == 255
    assert region[15, 15] == 0


def test_person_only_region_excludes_the_pet_bbox():
    """
    사람이 개를 안고 있으면 두 bbox 가 겹친다. 겹친 부분에는 개의 몸이 있으므로
    '확실한 사람'에서 빼야 한다 — 안 그러면 음성 포인트가 개 위에 찍힌다.
    """
    region = pp.person_only_region_mask((IMG_H, IMG_W), [PERSON_BBOX], PET_BBOX)

    # 펫 박스와 겹치는 부분(x 90..100)은 제외되어야 한다
    assert region[60, 95] == 0
    # 펫 박스 밖의 사람 영역은 남아야 한다
    assert region[60, 120] == 255


# --------------------------------------------------------------------------
# 2. 포인트 생성 — 요구사항 4·5·6
# --------------------------------------------------------------------------


def test_positive_point_lands_inside_pet_and_negative_inside_person():
    mask = _pet_plus_hand()
    region = pp.person_only_region_mask((IMG_H, IMG_W), [PERSON_BBOX], PET_BBOX)

    positives, negatives, err = pp.build_prompt_points(mask, region)

    assert err is None
    assert positives and negatives

    for x, y in positives:
        assert mask[y, x] > 0, "양성 포인트는 마스크 안에 있어야 한다"
        assert region[y, x] == 0, "양성 포인트가 사람 영역에 있으면 안 된다"

    for x, y in negatives:
        assert region[y, x] > 0, "음성 포인트는 사람 영역 안에 있어야 한다"
        assert mask[y, x] > 0, "음성 포인트는 오염된 마스크 안이어야 의미가 있다"


def test_negative_points_avoid_the_hand_fur_boundary():
    """
    요구사항 6 — 음성 포인트가 손-털 경계에 붙으면 SAM2 가 털까지 지운다.
    펫 코어에서 keep-out 만큼 떨어져 있는지 확인한다.
    """
    mask = _pet_plus_hand()
    region = pp.person_only_region_mask((IMG_H, IMG_W), [PERSON_BBOX], PET_BBOX)

    _pos, negatives, err = pp.build_prompt_points(mask, region)
    assert err is None and negatives

    # 개 몸통(사람 영역 밖 마스크)까지의 최단 거리를 잰다.
    pet_only = ((mask > 0) & (region == 0)).astype(np.uint8)
    ys, xs = np.where(pet_only > 0)
    keepout = max(2, int(min(IMG_H, IMG_W) * pp.PET_KEEPOUT_FRAC))

    for x, y in negatives:
        d = np.min(np.hypot(xs - x, ys - y))
        assert d >= keepout, f"음성 포인트 ({x},{y}) 가 펫 경계에 너무 가깝다 (d={d:.1f})"


def test_points_are_spatially_separated():
    mask = _pet_plus_hand()
    region = pp.person_only_region_mask((IMG_H, IMG_W), [PERSON_BBOX], PET_BBOX)
    positives, _neg, _err = pp.build_prompt_points(mask, region)
    if len(positives) > 1:
        for i in range(len(positives)):
            for j in range(i + 1, len(positives)):
                (x1, y1), (x2, y2) = positives[i], positives[j]
                assert np.hypot(x1 - x2, y1 - y2) > 2


def test_no_negative_points_when_mask_has_no_person_overlap():
    mask = _pet_only()
    region = pp.person_region_mask((IMG_H, IMG_W), [(140, 100, 160, 120)])
    _pos, negatives, err = pp.build_prompt_points(mask, region)
    assert negatives == []
    assert err == "no_safe_person_region"


def test_no_positive_point_when_mask_is_entirely_person():
    mask = _rect_mask((110, 30, 150, 90))  # 펫 박스 밖 — 전부 확실한 사람 영역
    region = pp.person_only_region_mask((IMG_H, IMG_W), [PERSON_BBOX], PET_BBOX)
    positives, negatives, err = pp.build_prompt_points(mask, region)
    assert positives == [] and negatives == []
    assert err == "no_confident_pet_interior"


# --------------------------------------------------------------------------
# 3. 채택 판정 — 요구사항 10
# --------------------------------------------------------------------------


def _evaluate(base, corrected, *, base_score=0.70, corrected_score=0.72, valid=True):
    region = pp.person_only_region_mask((IMG_H, IMG_W), [PERSON_BBOX], PET_BBOX)
    return pp.evaluate_correction(
        base,
        corrected,
        region,
        base_score=base_score,
        corrected_score=corrected_score,
        corrected_valid=valid,
    )


def test_correction_accepted_when_it_removes_the_hand():
    accepted, reason, metrics = _evaluate(_pet_plus_hand(), _pet_only())
    assert accepted is True
    assert reason is None
    assert metrics["removal_precision"] >= pp.MIN_REMOVAL_PRECISION
    assert 0 < metrics["removed_fraction"] <= pp.MAX_REMOVAL_FRACTION


def test_correction_rejected_when_it_eats_the_pet():
    """개까지 크게 깎아 먹으면 채택하지 않는다."""
    tiny = _rect_mask((20, 20, 40, 40))
    accepted, reason, _m = _evaluate(_pet_plus_hand(), tiny)
    assert accepted is False
    assert reason == "removed_too_much"


def test_correction_rejected_when_removal_is_not_person_specific():
    """면적은 적당히 줄었지만 지운 곳이 사람이 아니라 개인 경우."""
    base = _pet_plus_hand()
    corrected = base.copy()
    corrected[20:45, 20:100] = 0  # 개 몸통 윗부분만 지움 (사람 영역 아님)
    accepted, reason, metrics = _evaluate(base, corrected)
    assert accepted is False
    assert reason == "removal_not_person_specific"
    assert metrics["removal_precision"] < pp.MIN_REMOVAL_PRECISION


def test_correction_rejected_when_nothing_meaningful_changed():
    base = _pet_plus_hand()
    corrected = base.copy()
    corrected[46, 120] = 0
    accepted, reason, _m = _evaluate(base, corrected)
    assert accepted is False
    assert reason == "no_meaningful_change"


def test_correction_rejected_when_candidate_invalid():
    accepted, reason, _m = _evaluate(_pet_plus_hand(), _pet_only(), valid=False)
    assert accepted is False
    assert reason == "corrected_mask_invalid"


def test_correction_rejected_on_score_regression():
    accepted, reason, _m = _evaluate(
        _pet_plus_hand(), _pet_only(), base_score=0.90, corrected_score=0.40
    )
    assert accepted is False
    assert reason == "score_regression"


# --------------------------------------------------------------------------
# 4. 전체 절차 + 진단
# --------------------------------------------------------------------------


def _apply(base_mask, corrected_mask, *, person_boxes=(PERSON_BBOX,), corrected_score=0.75):
    calls: dict = {}

    def run_sam2(*, positive_points, negative_points):
        calls["positive"] = positive_points
        calls["negative"] = negative_points
        return [_FakeCandidate(corrected_mask, corrected_score)]

    mask, cands, result = pp.apply_person_aware_prompting(
        base_mask=base_mask,
        base_score=0.70,
        base_valid=True,
        pet_bbox=PET_BBOX,
        person_boxes=list(person_boxes),
        frame_shape=(IMG_H, IMG_W),
        run_sam2=run_sam2,
        select_candidate=_selector,
    )
    return mask, cands, result, calls


def test_full_flow_accepts_and_reports_diagnostics():
    mask, _cands, result, calls = _apply(_pet_plus_hand(), _pet_only())

    assert mask is not None
    d = result.to_dict()
    assert d["person_detected"] is True
    assert d["person_boxes"] == [list(PERSON_BBOX)]
    assert d["person_pet_overlap"] > 0
    assert d["person_aware_prompting_used"] is True
    assert d["positive_points"] and d["negative_points"]
    assert d["original_selected_score"] == 0.70
    assert d["corrected_selected_score"] == 0.75
    assert d["corrected_mask_selected"] is True
    assert d["possible_human_contamination"] is False
    # SAM2 에 실제로 양성/음성이 전달됐는지
    assert calls["positive"] and calls["negative"]


def test_full_flow_keeps_baseline_when_correction_rejected():
    """보정이 개를 먹으면 기준선을 유지하고 오염 가능성을 표시한다."""
    mask, _cands, result, _calls = _apply(_pet_plus_hand(), _rect_mask((20, 20, 40, 40)))

    assert mask is None, "채택되지 않으면 호출자는 기준선을 그대로 쓴다"
    assert result.person_aware_prompting_used is True
    assert result.corrected_mask_selected is False
    assert result.rejected_reason == "removed_too_much"
    assert result.possible_human_contamination is True


def test_skips_when_no_person():
    mask, _c, result, _calls = _apply(_pet_only(), _pet_only(), person_boxes=())
    assert mask is None
    assert result.person_detected is False
    assert result.skipped_reason == "no_person_detected"
    assert result.person_aware_prompting_used is False


def test_skips_when_person_does_not_overlap_pet():
    mask, _c, result, _calls = _apply(
        _pet_only(), _pet_only(), person_boxes=((150, 110, 160, 120),)
    )
    assert mask is None
    assert result.skipped_reason == "person_does_not_overlap_pet"


def test_skips_when_mask_is_not_contaminated():
    """사람이 펫 박스와 겹쳐도 마스크가 사람을 안 물었으면 건드리지 않는다."""
    mask, _c, result, _calls = _apply(_pet_only(), _pet_only())
    assert mask is None
    assert result.skipped_reason == "no_contamination_in_mask"
    assert result.person_aware_prompting_used is False


def test_sam2_reprompt_failure_is_contained():
    def boom(*, positive_points, negative_points):
        raise RuntimeError("cuda oom")

    mask, _c, result = pp.apply_person_aware_prompting(
        base_mask=_pet_plus_hand(),
        base_score=0.7,
        base_valid=True,
        pet_bbox=PET_BBOX,
        person_boxes=[PERSON_BBOX],
        frame_shape=(IMG_H, IMG_W),
        run_sam2=boom,
        select_candidate=_selector,
    )
    assert mask is None
    assert result.skipped_reason == "sam2_reprompt_failed:RuntimeError"
    assert result.possible_human_contamination is True


# --------------------------------------------------------------------------
# 5. vitmatte 파이프라인 통합
# --------------------------------------------------------------------------


# 파이프라인 통합 테스트는 test_vitmatte_pipeline 의 공용 목업을 재사용하므로
# 그쪽 프레임 크기(128x96)를 그대로 따른다.
PIPE_W, PIPE_H = 128, 96


def test_pipeline_exposes_person_aware_diagnostics(monkeypatch):
    from .conftest import blob_mask
    from .test_vitmatte_pipeline import _detection, _install_pipeline

    fg = blob_mask(PIPE_H, PIPE_W, area_fraction=0.3)
    _install_pipeline(monkeypatch, detection=_detection(), mask=fg)
    monkeypatch.setattr(
        vs, "_detect_persons", lambda img, model, *, conf: [(90, 20, 128, 80)]
    )

    _png, meta = vs.matte_foreground_with_meta(make_jpeg_bytes(PIPE_W, PIPE_H))

    for key in (
        "person_detected",
        "person_boxes",
        "person_pet_overlap",
        "person_aware_prompting_used",
        "positive_points",
        "negative_points",
        "original_selected_score",
        "corrected_selected_score",
        "corrected_mask_selected",
        "possible_human_contamination",
    ):
        assert key in meta, f"missing Phase 2B diagnostic: {key}"


def test_pipeline_skips_person_stage_for_grabcut(monkeypatch):
    """GrabCut 은 포인트 프롬프트가 없으므로 2B 를 건너뛴다."""
    from .test_vitmatte_pipeline import _detection, _install_pipeline
    from .conftest import blob_mask

    fg = blob_mask(PIPE_H, PIPE_W, area_fraction=0.3)
    _install_pipeline(
        monkeypatch,
        detection=_detection(),
        segmenter_outcome=vs.SegmentationOutcome(
            fg_binary=fg,
            trimap=np.where(fg > 0, 255, 0).astype(np.uint8),
            segmenter_used="grabcut",
            fallback=True,
            fallback_reason="sam2_failed",
        ),
    )

    _png, meta = vs.matte_foreground_with_meta(make_jpeg_bytes(PIPE_W, PIPE_H))

    assert meta["person_aware_prompting_used"] is False
    assert meta["skipped_reason"] == "segmenter_not_sam2"


def test_pipeline_can_be_disabled(monkeypatch):
    from .test_vitmatte_pipeline import _detection, _install_pipeline

    _install_pipeline(monkeypatch, detection=_detection())
    monkeypatch.setattr(vs, "PERSON_AWARE_PROMPTING_ENABLED", False)

    _png, meta = vs.matte_foreground_with_meta(make_jpeg_bytes(PIPE_W, PIPE_H))

    assert meta["person_aware_prompting_used"] is False
    assert meta["skipped_reason"] == "disabled"


def test_sam2_candidates_passes_points_to_processor(monkeypatch):
    """박스 + 양성/음성 포인트가 실제로 processor 에 전달되는지."""
    captured: dict = {}

    class _FakeProcessor:
        def __call__(self, **kwargs):
            captured.update(kwargs)
            return _FakeInputs()

        def post_process_masks(self, pred_masks, original_sizes):
            # 실제 반환 형태: list[ tensor(num_boxes, num_masks, H, W) ]
            arr = np.zeros((1, 1, IMG_H, IMG_W), dtype=np.uint8)
            arr[0, 0, 30:70, 30:70] = 1
            return [_Torchish(arr)]

    class _FakeInputs(dict):
        def __init__(self):
            super().__init__(original_sizes=[(IMG_H, IMG_W)])

        def to(self, device):
            return self

    class _Torchish:
        """torch.Tensor 의 최소 인터페이스 (shape / 인덱싱 / numpy)."""

        def __init__(self, arr):
            self._arr = arr

        @property
        def shape(self):
            return self._arr.shape

        def __getitem__(self, i):
            return _Torchish(self._arr[i])

        def numpy(self):
            return self._arr

    class _FakeModel:
        def __call__(self, **kwargs):
            captured["multimask_output"] = kwargs.get("multimask_output")
            return type("O", (), {"pred_masks": _Cpu(), "iou_scores": _Cpu()})()

    class _Cpu:
        def cpu(self):
            return self

        def detach(self):
            return self

        def reshape(self, *_a):
            return self

        def tolist(self):
            return [0.9]

    monkeypatch.setattr(vs, "_load_sam2", lambda m, d: (_FakeProcessor(), _FakeModel()))

    vs._sam2_candidates(
        np.zeros((IMG_H, IMG_W, 3), dtype=np.uint8),
        PET_BBOX,
        "fake/sam2",
        "cpu",
        multimask=True,
        positive_points=[(50, 50)],
        negative_points=[(120, 60), (130, 65)],
    )

    assert captured["input_boxes"] == [[list(PET_BBOX)]]
    assert captured["input_points"] == [[[[50, 50], [120, 60], [130, 65]]]]
    assert captured["input_labels"] == [[[1, 0, 0]]]
    assert captured["multimask_output"] is True
