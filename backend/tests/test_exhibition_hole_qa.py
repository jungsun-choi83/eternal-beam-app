"""
전시 interior_holes QA — 발이 겹쳐 닫힌 다리 사이·배 밑 빈틈은 구멍이 아니다.

실제 사례(리트리버 정면 전신, run 9c37b8e2): 뒷발이 앞다리에 겹쳐 다리 사이 빈틈
(5409px, 실루엣의 1.08%)이 둘러싸인 구멍이 됐고, 털끝 점 5개가 개수 조건을 채워
NEEDS_REVIEW(interior_holes) 가 됐다. 아래 모양은 같은 구성(1–2% 빈틈 하나 + 털 점
5개)이라 **기본 설정에서는 실패**하고, 전시 설정(natural_gap_filter)에서만 통과한다.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from backend.services import exhibition_breathing_maps as maps
from backend.services.matte_hole_qa import HoleQaConfig, measure_frame
from backend.tests.test_exhibition_breathing_maps import frontal_dog, round_blob, side_dog, to_rgba

DEFAULT = HoleQaConfig()
EXHIBITION = maps.EXHIBITION_HOLE_QA


def _alpha(a: np.ndarray) -> np.ndarray:
    return a.astype(np.float32) / 255.0


def _fur_specks(a: np.ndarray, pts) -> None:
    """털끝의 작은 투명 점 (실루엣 가장자리 근처) — 실제 매트에 늘 몇 개 있다."""
    for x, y in pts:
        a[y : y + 3, x : x + 2] = 0


HEAD_SPECKS = [(300, 120), (400, 120), (285, 200), (415, 200), (350, 95)]


def standing_frontal(*, front_gap: bool = True, hind_gap: bool = False, specks: bool = True) -> np.ndarray:
    """정면으로 선 개. 앞발 둘이 발끝에서 맞닿아 다리 사이 빈틈이 닫혀 있다."""
    a = np.zeros((1000, 700), np.uint8)
    cv2.ellipse(a, (350, 470), (200, 190), 0, 0, 360, 255, -1)  # 몸통·가슴
    cv2.circle(a, (350, 175), 115, 255, -1)  # 머리
    cv2.rectangle(a, (300, 250), (400, 320), 255, -1)  # 목
    # 앞다리 두 개 (빈틈 18px) + 맞닿은 앞발.
    cv2.rectangle(a, (262, 560), (341, 940), 255, -1)
    cv2.rectangle(a, (359, 560), (438, 940), 255, -1)
    if front_gap:
        cv2.rectangle(a, (262, 905), (438, 940), 255, -1)
    else:
        cv2.rectangle(a, (341, 560), (359, 940), 255, -1)  # 빈틈 없음 (붙은 다리)
    # 뒷다리 (바깥쪽), 뒷발이 앞다리에 겹친다.
    cv2.rectangle(a, (190, 600), (244, 930), 255, -1)
    cv2.rectangle(a, (456, 600), (510, 930), 255, -1)
    if hind_gap:
        cv2.rectangle(a, (190, 895), (262, 930), 255, -1)  # 왼 뒷발 → 왼 앞다리
        cv2.rectangle(a, (438, 895), (510, 930), 255, -1)  # 오른 뒷발 → 오른 앞다리
    if specks:
        _fur_specks(a, HEAD_SPECKS)
    return a


def sitting_side_underbelly() -> np.ndarray:
    """옆으로 앉은 개: 배와 접힌 뒷다리 사이 가로로 긴 틈 — 앞다리·엉덩이가 양끝을 닫는다."""
    a = np.zeros((900, 900), np.uint8)
    cv2.ellipse(a, (470, 420), (260, 140), 0, 0, 360, 255, -1)  # 몸통 (배 아래끝 y≈560)
    cv2.circle(a, (230, 200), 105, 255, -1)  # 머리
    cv2.rectangle(a, (210, 260), (320, 360), 255, -1)  # 목
    cv2.rectangle(a, (300, 450), (360, 840), 255, -1)  # 앞다리
    cv2.rectangle(a, (360, 568), (600, 606), 255, -1)  # 앞으로 뻗은 뒷발 (배와 좁은 틈)
    cv2.rectangle(a, (600, 450), (690, 840), 255, -1)  # 엉덩이·뒷다리
    _fur_specks(a, [(180, 130), (280, 130), (160, 220), (300, 230), (230, 105)])
    return a


def chest_damage() -> np.ndarray:
    """가슴 속 투명 패치 여러 개 — 매팅 실패."""
    a = standing_frontal(front_gap=False)
    for x, y in [(300, 430), (350, 400), (400, 430), (320, 500), (380, 500), (350, 460)]:
        cv2.circle(a, (x, y), 15, 0, -1)
    return a


def head_damage() -> np.ndarray:
    """얼굴 속 투명 구멍 여러 개 (눈·코가 배경으로 판정된 경우)."""
    a = standing_frontal(front_gap=False)
    for x, y in [(310, 150), (390, 150), (350, 200), (330, 230), (370, 230)]:
        cv2.circle(a, (x, y), 13, 0, -1)
    return a


def torso_cut() -> np.ndarray:
    """몸통을 가로지르는 세그멘테이션 손상 — 길쭉하지만 깊다."""
    a = standing_frontal(front_gap=False)
    a[465:475, 220:480] = 0
    return a


# ── 자연 빈틈 → PASS ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "shape, n_gaps",
    [
        pytest.param(lambda: standing_frontal(front_gap=True), 1, id="front_leg_gap"),
        pytest.param(sitting_side_underbelly, 1, id="underbelly_gap"),
        pytest.param(lambda: standing_frontal(front_gap=True, hind_gap=True), 3, id="multiple_limb_gaps"),
    ],
)
def test_natural_limb_gaps_pass_but_failed_before(shape, n_gaps):
    a = _alpha(shape())
    old = measure_frame(a, DEFAULT)
    assert old["failed"] is True, old  # 수정 전 동작 재현: 거짓 양성
    assert old["ignored_large_gaps"] == 0  # 2% 미만 — 기존 '큰 빈틈' 예외로는 못 거른다

    new = measure_frame(a, EXHIBITION)
    assert new["failed"] is False, new
    assert new["ignored_natural_gaps"] == n_gaps
    assert new["hole_count"] == len(HEAD_SPECKS)  # 털 점은 여전히 센다


# ── 진짜 결함 → FAIL ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("shape", [chest_damage, head_damage, torso_cut], ids=["chest", "head", "torso_cut"])
def test_real_interior_holes_still_fail(shape):
    a = _alpha(shape())
    assert measure_frame(a, DEFAULT)["failed"] is True
    new = measure_frame(a, EXHIBITION)
    assert new["failed"] is True, new
    assert new["ignored_natural_gaps"] == 0


def test_real_defects_fail_even_alongside_natural_gaps():
    a = standing_frontal(front_gap=True)
    for x, y in [(300, 430), (350, 400), (400, 430), (320, 500), (380, 500), (350, 460)]:
        cv2.circle(a, (x, y), 15, 0, -1)
    new = measure_frame(_alpha(a), EXHIBITION)
    assert new["failed"] is True
    assert new["ignored_natural_gaps"] == 1  # 다리 사이만 면제, 가슴 구멍은 전부 센다


@pytest.mark.parametrize(
    "center, radius",
    [((350, 450), 30), ((350, 170), 25)],
    ids=["single_chest_hole", "single_head_hole"],
)
def test_chest_and_head_holes_are_never_classified_as_natural(center, radius):
    a = standing_frontal(front_gap=False)
    cv2.circle(a, center, radius, 0, -1)
    new = measure_frame(_alpha(a), EXHIBITION)
    assert new["ignored_natural_gaps"] == 0
    assert new["hole_count"] == len(HEAD_SPECKS) + 1


def test_round_hole_low_on_the_body_is_not_exempt():
    """하체라도 둥근 패치(길쭉하지 않음)는 자연 빈틈이 아니다."""
    a = standing_frontal(front_gap=False)
    cv2.circle(a, (300, 640), 14, 0, -1)
    assert measure_frame(_alpha(a), EXHIBITION)["ignored_natural_gaps"] == 0


# ── 기존 동작 불변 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("shape", [frontal_dog, side_dog, round_blob])
def test_existing_good_cases_unchanged(shape):
    a = _alpha(shape())
    old, new = measure_frame(a, DEFAULT), measure_frame(a, EXHIBITION)
    assert new.pop("ignored_natural_gaps") == 0
    assert new.pop("protected_hard_defects") == []
    assert new == old and old["failed"] is False


def test_default_config_is_unchanged_for_the_normal_pipeline():
    """일반 파이프라인(bgmodel → ViTMatte 폴백)은 필터를 켜지 않는다 — 결과 키도 그대로."""
    env = HoleQaConfig.from_env()
    assert env.natural_gap_filter is False and env.protected_hole_rule is False
    m = measure_frame(_alpha(standing_frontal(front_gap=True)), env)
    assert "ignored_natural_gaps" not in m and "protected_hard_defects" not in m and m["failed"] is True


def test_exhibition_maps_no_longer_flag_closed_leg_gap():
    res = maps.build_breathing_maps(to_rgba(standing_frontal(front_gap=True)))
    assert res.interior_holes is False
    assert res.diagnostics["hole_qa"]["ignored_natural_gaps"] == 1
    res = maps.build_breathing_maps(to_rgba(chest_damage()))
    assert res.interior_holes is True


# ── 보호 부위 큰 구멍 즉시 실패 (protected_hole_rule) ────────────────────────
#
# 집계 규칙(≥1% 그리고 ≥5개)은 큰 구멍 **하나**를 놓치고, 2% 를 넘는 구멍은 '큰 빈틈' 으로
# 아예 뺀다. 아래 결함은 모두 **기본 설정에서는 통과**(= 놓침)하고 전시 설정에서 실패한다.


def _single_defect(draw) -> np.ndarray:
    a = standing_frontal(front_gap=False, specks=False)
    draw(a)
    return a


HARD_DEFECTS = [
    pytest.param(lambda a: cv2.circle(a, (350, 450), 30, 0, -1), "upper_body", id="large_chest_hole"),
    pytest.param(lambda a: cv2.circle(a, (350, 470), 85, 0, -1), "upper_body", id="chest_hole_over_2pct"),
    pytest.param(lambda a: cv2.circle(a, (350, 180), 28, 0, -1), "upper_body", id="large_head_hole"),
    pytest.param(lambda a: cv2.circle(a, (350, 285), 25, 0, -1), "upper_body", id="neck_hole"),
    pytest.param(lambda a: a.__setitem__((slice(520, 532), slice(200, 500)), 0), "deep_torso", id="deep_torso_cut"),
    # 하체 쪽 배를 가로지른 틈 — 길쭉하고 하체지만 아래가 두꺼운 배·다리라 자연 빈틈이 아니다.
    pytest.param(lambda a: a.__setitem__((slice(590, 600), slice(250, 450)), 0), "deep_torso", id="lower_belly_slit"),
]


@pytest.mark.parametrize("draw, zone", HARD_DEFECTS)
def test_single_large_protected_hole_fails_immediately(draw, zone):
    a = _alpha(_single_defect(draw))
    old = measure_frame(a, DEFAULT)
    assert old["failed"] is False  # 기본 집계 규칙은 이 결함을 놓친다
    new = measure_frame(a, EXHIBITION)
    assert new["failed"] is True, new
    (defect,) = new["protected_hard_defects"]
    assert defect["zone"] == zone


def test_natural_gap_plus_single_chest_hole_fails():
    a = standing_frontal(front_gap=True, specks=False)
    cv2.circle(a, (350, 450), 30, 0, -1)
    new = measure_frame(_alpha(a), EXHIBITION)
    assert new["failed"] is True
    assert new["ignored_natural_gaps"] == 1  # 다리 사이 빈틈은 여전히 면제
    assert len(new["protected_hard_defects"]) == 1


def wide_leg_gap() -> np.ndarray:
    """다리 사이 빈틈이 실루엣의 2% 를 넘는 경우 — 얇은 발끝으로만 닫혀 있다."""
    a = np.zeros((1000, 700), np.uint8)
    cv2.ellipse(a, (350, 470), (200, 190), 0, 0, 360, 255, -1)
    cv2.circle(a, (350, 175), 115, 255, -1)
    cv2.rectangle(a, (300, 250), (400, 320), 255, -1)
    cv2.rectangle(a, (215, 560), (290, 940), 255, -1)
    cv2.rectangle(a, (410, 560), (485, 940), 255, -1)
    cv2.rectangle(a, (215, 910), (485, 940), 255, -1)  # 맞닿은 발 (30px)
    return a


@pytest.mark.parametrize(
    "shape",
    [
        pytest.param(lambda: standing_frontal(front_gap=True), id="leg_gap"),
        pytest.param(sitting_side_underbelly, id="underbelly_gap"),
        pytest.param(lambda: standing_frontal(front_gap=True, hind_gap=True), id="multiple_limb_gaps"),
        pytest.param(wide_leg_gap, id="leg_gap_over_2pct"),
    ],
)
def test_natural_gaps_never_trigger_the_hard_rule(shape):
    new = measure_frame(_alpha(shape()), EXHIBITION)
    assert new["failed"] is False, new
    assert new["protected_hard_defects"] == []


def test_small_protected_holes_stay_with_the_aggregate_rule():
    """작은 구멍(털 사이 점, 작은 눈가 반점)은 즉시 실패시키지 않는다."""
    a = _single_defect(lambda a: (cv2.circle(a, (350, 450), 8, 0, -1), cv2.circle(a, (320, 170), 6, 0, -1)))
    new = measure_frame(_alpha(a), EXHIBITION)
    assert new["protected_hard_defects"] == [] and new["failed"] is False


def test_hard_defect_marks_exhibition_run_for_review():
    res = maps.build_breathing_maps(to_rgba(_single_defect(lambda a: cv2.circle(a, (350, 450), 30, 0, -1))))
    assert res.interior_holes is True
    assert res.diagnostics["hole_qa"]["protected_hard_defects"][0]["zone"] == "upper_body"


def test_normal_pipeline_evaluate_ignores_single_large_hole_as_before():
    """일반 파이프라인(evaluate + from_env) 결과는 이번 변경 전과 같다 — 큰 구멍 하나로 폴백하지 않는다."""
    from backend.services import matte_hole_qa

    a = _alpha(_single_defect(lambda a: cv2.circle(a, (350, 450), 30, 0, -1)))
    qa = matte_hole_qa.evaluate([a] * 6, HoleQaConfig.from_env())
    assert qa["passed"] is True and qa["failed_frames"] == []
    assert all("protected_hard_defects" not in f for f in qa["frames"])
