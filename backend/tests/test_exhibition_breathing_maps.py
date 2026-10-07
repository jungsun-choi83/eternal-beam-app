"""exhibition_breathing_maps — 합성 실루엣으로 맵/잠금/머리/테두리 판정을 고정한다."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from backend.services import exhibition_breathing_maps as m


def frontal_dog(h: int = 640, w: int = 520) -> np.ndarray:
    """정면으로 앉은 개: 몸통 타원 + 목 + 머리 원 + 앞다리 두 개."""
    a = np.zeros((h, w), np.uint8)
    cv2.ellipse(a, (260, 360), (130, 150), 0, 0, 360, 255, -1)
    cv2.circle(a, (260, 150), 78, 255, -1)
    cv2.rectangle(a, (212, 180), (308, 240), 255, -1)
    cv2.rectangle(a, (185, 440), (225, 590), 255, -1)
    cv2.rectangle(a, (295, 440), (335, 590), 255, -1)
    return a


def side_dog(h: int = 480, w: int = 720) -> np.ndarray:
    """측면으로 선 개: 가로 몸통 + 비스듬한 목 + 머리(왼쪽) + 다리 4개 + 꼬리."""
    a = np.zeros((h, w), np.uint8)
    cv2.ellipse(a, (380, 250), (190, 85), 0, 0, 360, 255, -1)
    cv2.circle(a, (130, 140), 62, 255, -1)
    cv2.line(a, (190, 175), (240, 230), 255, 70)
    for x in (260, 300, 470, 510):
        cv2.rectangle(a, (x, 300), (x + 32, 420), 255, -1)
    cv2.line(a, (565, 230), (660, 170), 255, 14)
    return a


def round_blob(h: int = 500, w: int = 500) -> np.ndarray:
    """목 잘록이 없는 털뭉치 — 머리를 확신할 근거가 없다."""
    a = np.zeros((h, w), np.uint8)
    cv2.ellipse(a, (250, 250), (180, 170), 0, 0, 360, 255, -1)
    return a


def to_rgba(alpha: np.ndarray) -> np.ndarray:
    rgb = np.dstack([np.full_like(alpha, 150), np.full_like(alpha, 110), np.full_like(alpha, 70)])
    return np.dstack([rgb, alpha])


@pytest.mark.parametrize("shape", [frontal_dog, side_dog, round_blob])
def test_maps_match_rgba_dimensions_and_range(shape):
    rgba = to_rgba(shape())
    res = m.build_breathing_maps(rgba)
    assert res.breathing_weight.shape == rgba.shape[:2]
    assert res.locked.shape == rgba.shape[:2]
    assert res.breathing_weight.dtype == np.float32
    assert 0.0 <= res.breathing_weight.min() and res.breathing_weight.max() == pytest.approx(1.0)
    assert 0.0 <= res.locked.min() and res.locked.max() <= 1.0
    # 알파 밖에는 가중치도 잠금도 없다.
    outside = rgba[:, :, 3] == 0
    assert res.breathing_weight[outside].max() == 0.0
    assert res.locked[outside].max() == 0.0


def test_weight_peaks_on_torso_and_is_low_frequency():
    rgba = to_rgba(frontal_dog())
    res = m.build_breathing_maps(rgba)
    cx, cy = (int(v) for v in res.breathing_center)
    assert 300 < cy < 450 and 200 < cx < 320  # 몸통 타원 안
    assert res.breathing_weight[cy, cx] > 0.9
    # 저주파: 이웃 픽셀 간 변화가 작다 (경계 계단 없음).
    w = res.breathing_weight
    assert float(np.abs(np.diff(w, axis=0)).max()) < 0.1
    assert float(np.abs(np.diff(w, axis=1)).max()) < 0.1


@pytest.mark.parametrize("shape", [frontal_dog, side_dog])
def test_paws_and_ground_contact_are_locked(shape):
    alpha = shape()
    res = m.build_breathing_maps(to_rgba(alpha))
    ys = np.flatnonzero((alpha >= 128).any(axis=1))
    assert res.ground_y == pytest.approx(float(ys[-1]))
    paw_rows = slice(int(ys[-1]) - 10, int(ys[-1]) + 1)
    paw_px = alpha[paw_rows] == 255
    assert paw_px.any()
    assert res.locked[paw_rows][paw_px].min() > 0.99
    assert res.breathing_weight[paw_rows][paw_px].max() < 0.01


@pytest.mark.parametrize(
    "shape, head_xy, flip",
    [(frontal_dog, (260, 150), False), (side_dog, (130, 140), False), (side_dog, (130, 140), True)],
)
def test_head_detected_locked_and_excluded_from_weight(shape, head_xy, flip):
    alpha = shape()
    hx, hy = head_xy
    if flip:
        alpha = alpha[:, ::-1].copy()
        hx = alpha.shape[1] - 1 - hx
    res = m.build_breathing_maps(to_rgba(alpha))
    assert not res.head_low_confidence
    assert res.head.source == "auto"
    assert res.head.confidence >= m.HEAD_MIN_CONFIDENCE
    assert abs(res.head.xy[0] - hx) < 15 and abs(res.head.xy[1] - hy) < 15
    assert res.locked[hy, hx] > 0.99
    assert res.breathing_weight[hy, hx] < 0.01


def test_blob_without_neck_is_low_confidence_not_invented():
    res = m.build_breathing_maps(to_rgba(round_blob()))
    assert res.head_low_confidence
    assert res.head.confidence < m.HEAD_MIN_CONFIDENCE


def test_operator_hint_overrides_low_confidence():
    res = m.build_breathing_maps(to_rgba(round_blob()), head_hint_xy=(250.0, 120.0))
    assert not res.head_low_confidence
    assert res.head.source == "operator"
    assert res.locked[120, 250] > 0.99


def test_breathing_axis_is_unit_vector_pointing_up():
    res = m.build_breathing_maps(to_rgba(side_dog()))
    ax, ay = res.breathing_axis
    assert ay < 0
    assert (ax * ax + ay * ay) == pytest.approx(1.0, abs=1e-3)


def test_frame_edge_touch_detection():
    centered = np.zeros((300, 300), np.uint8)
    cv2.circle(centered, (150, 150), 80, 255, -1)
    assert m.detect_frame_edge_touch(centered) == {"touches_frame_edge": False, "sides": []}

    cut_paws = np.zeros((300, 300), np.uint8)
    cv2.circle(cut_paws, (150, 200), 120, 255, -1)  # 아래로 잘림
    res = m.detect_frame_edge_touch(cut_paws)
    assert res["touches_frame_edge"] is True
    assert res["sides"] == ["bottom"]

    # 테두리의 희미한 털 몇 픽셀(알파 < 128)은 잘림으로 보지 않는다.
    faint = centered.copy()
    faint[0, 10:20] = 60
    assert m.detect_frame_edge_touch(faint)["touches_frame_edge"] is False


def test_crop_rect_contains_subject_with_padding():
    alpha = np.zeros((400, 600), np.uint8)
    alpha[100:200, 250:350] = 255
    x1, y1, x2, y2 = m.subject_crop_rect(alpha)
    assert x1 < 250 and y1 < 100 and x2 > 350 and y2 > 200
    with pytest.raises(m.ExhibitionMapError):
        m.subject_crop_rect(np.zeros((10, 10), np.uint8))


def test_encodings_round_trip():
    res = m.build_breathing_maps(to_rgba(frontal_dog()))
    w16 = cv2.imdecode(np.frombuffer(m.encode_weight_png16(res.breathing_weight), np.uint8), cv2.IMREAD_UNCHANGED)
    assert w16.dtype == np.uint16 and w16.ndim == 2 and w16.shape == res.breathing_weight.shape
    assert w16.max() == 65535
    l8 = cv2.imdecode(np.frombuffer(m.encode_mask_png8(res.locked), np.uint8), cv2.IMREAD_UNCHANGED)
    assert l8.dtype == np.uint8 and l8.ndim == 2 and l8.shape == res.locked.shape
    assert l8.max() == 255


def test_clean_straight_rgba_removes_background_but_keeps_subject():
    rgba = to_rgba(frontal_dog())
    rgba[:, :, :3][rgba[:, :, 3] == 0] = (0, 255, 0)  # 원본 배경(초록)
    out = m.clean_straight_rgba(rgba)
    subject = rgba[:, :, 3] > 0
    assert np.array_equal(out[subject], rgba[subject])
    assert np.array_equal(out[:, :, 3], rgba[:, :, 3])
    assert out[0, 0, :3].tolist() == [0, 0, 0]  # 먼 배경은 지워짐
    assert (out[:, :, 1][~subject] != 255).all()  # 배경색이 남지 않음


# ── 실사형 실루엣: 목 잘록이 약하거나 머리가 몸통과 한 덩어리 ─────────────────
#
# 실제 전시 사진(리트리버 정면·흰 개 앉음·옷 입은 털북숭이·시바 정면)에서 v1 은
# 잘록함 0.03–0.12 → 신뢰도 ≈ 0 으로 전부 head_low_confidence 였고, 머리 봉우리가
# 없는 두 장은 얼굴에 호흡 가중치 0.74–0.83 이 실렸다. 아래 모양은 그 패턴을 재현한다.


def _fur(a: np.ndarray, amp: float, seed: int) -> np.ndarray:
    """털 가장자리: 윤곽을 따라 짧은 털 가닥을 뿌린다."""
    rng = np.random.default_rng(seed)
    contours, _ = cv2.findContours(a, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    out = a.copy()
    for c in contours:
        for i in range(0, len(c), 6):
            x, y = c[i][0]
            ang, length = rng.uniform(0, 2 * np.pi), rng.uniform(0.3, 1.0) * amp
            cv2.line(out, (int(x), int(y)), (int(x + length * np.cos(ang)), int(y + length * np.sin(ang))), 255, 3)
    return out


def broad_head_frontal(h: int = 900, w: int = 640) -> np.ndarray:
    """정면으로 선 큰 머리 개(리트리버형): 머리가 가슴과 굵게 이어져 목 잘록이 약하다."""
    a = np.zeros((h, w), np.uint8)
    cv2.ellipse(a, (320, 440), (175, 230), 0, 0, 360, 255, -1)
    cv2.ellipse(a, (320, 230), (150, 140), 0, 0, 360, 255, -1)
    cv2.ellipse(a, (175, 250), (45, 95), 10, 0, 360, 255, -1)
    cv2.ellipse(a, (465, 250), (45, 95), -10, 0, 360, 255, -1)
    for x in (215, 375):
        cv2.rectangle(a, (x, 600), (x + 55, 870), 255, -1)
    return a


def fluffy_frontal() -> np.ndarray:
    return _fur(broad_head_frontal(), 28, seed=1)


def sitting_weak_neck(h: int = 1000, w: int = 560) -> np.ndarray:
    """앉은 개: 머리가 가슴 위에 바로 얹히고 엉덩이·뒷다리가 넓게 퍼진다."""
    a = np.zeros((h, w), np.uint8)
    cv2.ellipse(a, (280, 230), (140, 135), 0, 0, 360, 255, -1)
    cv2.ellipse(a, (280, 510), (175, 250), 0, 0, 360, 255, -1)
    cv2.ellipse(a, (280, 770), (230, 140), 0, 0, 360, 255, -1)
    cv2.ellipse(a, (160, 160), (40, 80), -25, 0, 360, 255, -1)
    cv2.ellipse(a, (400, 160), (40, 80), 25, 0, 360, 255, -1)
    for x in (200, 310):
        cv2.rectangle(a, (x, 820), (x + 50, 960), 255, -1)
    return a


def side_weak_neck(h: int = 520, w: int = 820) -> np.ndarray:
    """측면 개: 머리가 굵은 목으로 몸통 앞쪽 위에 붙어 잘록이 약하다."""
    a = np.zeros((h, w), np.uint8)
    cv2.ellipse(a, (450, 280), (230, 105), 0, 0, 360, 255, -1)
    cv2.line(a, (200, 190), (290, 260), 255, 150)
    cv2.ellipse(a, (170, 150), (95, 80), -15, 0, 360, 255, -1)
    cv2.ellipse(a, (85, 175), (55, 35), 0, 0, 360, 255, -1)
    for x in (290, 340, 560, 610):
        cv2.rectangle(a, (x, 340), (x + 38, 480), 255, -1)
    cv2.line(a, (670, 250), (760, 190), 255, 16)
    return a


def side_head_on_back(h: int = 520, w: int = 820, hip_bump: bool = False) -> np.ndarray:
    """측면 털북숭이: 머리가 몸통 앞쪽 위에 그대로 얹혀 DT 봉우리가 따로 없다."""
    a = np.zeros((h, w), np.uint8)
    cv2.ellipse(a, (430, 300), (260, 120), 0, 0, 360, 255, -1)
    cv2.ellipse(a, (210, 215), (110, 70), 0, 0, 360, 255, -1)
    if hip_bump:
        cv2.ellipse(a, (620, 215), (90, 75), 0, 0, 360, 255, -1)
    for x in (260, 320, 560, 620):
        cv2.rectangle(a, (x, 380), (x + 40, 500), 255, -1)
    return a


def round_dog(h: int = 1000, w: int = 560) -> np.ndarray:
    """둥근 개(시바 정면형): 머리가 몸통과 한 덩어리, 뾰족한 귀 두 개, 다리."""
    a = np.zeros((h, w), np.uint8)
    cv2.ellipse(a, (280, 440), (200, 330), 0, 0, 360, 255, -1)
    cv2.fillPoly(a, [np.array([[110, 260], [140, 90], [230, 200]], np.int32),
                     np.array([[450, 260], [420, 90], [330, 200]], np.int32)], 255)
    for x in (150, 350):
        cv2.rectangle(a, (x, 700), (x + 60, 960), 255, -1)
    return a


def clothed_dog(h: int = 900, w: int = 760) -> np.ndarray:
    """옷 입은 앉은 소형견: 큰 털머리가 몸통과 붙고, 리본·옷자락이 옆으로 퍼진다."""
    a = np.zeros((h, w), np.uint8)
    cv2.ellipse(a, (330, 280), (190, 185), 0, 0, 360, 255, -1)
    cv2.ellipse(a, (340, 560), (190, 220), 0, 0, 360, 255, -1)
    cv2.fillPoly(a, [np.array([[480, 380], [700, 520], [640, 720], [520, 640]], np.int32)], 255)
    cv2.ellipse(a, (155, 280), (50, 100), 10, 0, 360, 255, -1)
    for x in (250, 360):
        cv2.rectangle(a, (x, 720), (x + 80, 840), 255, -1)
    return _fur(a, 14, seed=3)


def lying_loaf(h: int = 500, w: int = 820) -> np.ndarray:
    """웅크려 누운 덩어리 — 머리가 보이지 않는다."""
    a = np.zeros((h, w), np.uint8)
    cv2.ellipse(a, (410, 260), (330, 150), 0, 0, 360, 255, -1)
    return a


# (모양, 얼굴 위 점들) — 점은 그림에서 눈·이마·주둥이 자리.
REAL_LIKE = [
    pytest.param(broad_head_frontal, [(320, 230), (260, 180), (380, 180), (320, 320)], id="frontal_broad_head"),
    pytest.param(fluffy_frontal, [(320, 230), (260, 180), (380, 180), (320, 320)], id="fluffy"),
    pytest.param(sitting_weak_neck, [(280, 230), (220, 190), (340, 190), (280, 320)], id="sitting"),
    pytest.param(side_weak_neck, [(170, 150), (90, 175), (200, 120)], id="side_weak_neck"),
    pytest.param(side_head_on_back, [(210, 215), (150, 200), (260, 190)], id="side_head_on_back"),
    pytest.param(round_dog, [(280, 250), (200, 230), (360, 230), (280, 330)], id="round"),
    pytest.param(clothed_dog, [(330, 280), (250, 240), (410, 240), (330, 380)], id="clothed"),
]


@pytest.mark.parametrize("shape, face_pts", REAL_LIKE)
def test_real_like_visible_head_is_auto_ready_and_face_locked(shape, face_pts):
    alpha = shape()
    res = m.build_breathing_maps(to_rgba(alpha))
    assert not res.head_low_confidence, res.head.diagnostics
    assert res.head.tier in (m.HEAD_TIER_HIGH, m.HEAD_TIER_FALLBACK)
    assert res.head.source == "auto" and res.head.xy is not None
    hx, hy = (int(round(v)) for v in res.head.xy)
    assert alpha[hy, hx] == 255  # 머리 점은 실루엣 안
    for x, y in face_pts + [(hx, hy)]:
        assert res.locked[y, x] > 0.99, (x, y)
        assert res.breathing_weight[y, x] < 0.01, (x, y)
    # 호흡은 여전히 몸통에 있다: 가중치 정점은 머리 점보다 충분히 아래/뒤.
    cx, cy = res.breathing_center
    assert res.breathing_weight[int(cy), int(cx)] > 0.5
    assert np.hypot(cx - hx, cy - hy) > 0.8 * res.diagnostics["d_max_px"]


@pytest.mark.parametrize("shape", [broad_head_frontal, sitting_weak_neck, side_weak_neck])
def test_weak_neck_is_not_by_itself_a_review_reason(shape):
    """잘록함 확신이 통과선 아래여도 머리 자리가 그럴듯하면 READY 다."""
    res = m.build_breathing_maps(to_rgba(shape()))
    best = max(res.head.candidates, key=lambda c: c["plausibility"]["score"])
    assert best["constriction_confidence"] < m.HEAD_MIN_CONFIDENCE
    assert res.head.tier == m.HEAD_TIER_FALLBACK and res.head.method == "dt_peak_position"
    assert m.HEAD_MIN_CONFIDENCE <= res.head.confidence < 0.75


@pytest.mark.parametrize("shape, method", [(round_dog, "silhouette_upright"), (side_head_on_back, "silhouette_side")])
def test_head_merged_into_body_uses_silhouette_fallback(shape, method):
    res = m.build_breathing_maps(to_rgba(shape()))
    assert res.head.tier == m.HEAD_TIER_FALLBACK
    assert res.head.method == method


def test_fallback_locks_more_than_high_confidence_lock():
    """USABLE_FALLBACK 은 머리·목·윗가슴까지 넓게 잠근다 — 확신 있는 표준 잠금보다 크다."""
    high = m.build_breathing_maps(to_rgba(frontal_dog()))
    weak = m.build_breathing_maps(to_rgba(broad_head_frontal()))
    assert high.head.tier == m.HEAD_TIER_HIGH and weak.head.tier == m.HEAD_TIER_FALLBACK
    assert weak.diagnostics["head_lock_area_frac"] > high.diagnostics["head_lock_area_frac"]
    assert weak.diagnostics["head_lock_area_frac"] > 0.35


def test_side_with_raised_hip_locks_both_ends():
    res = m.build_breathing_maps(to_rgba(side_head_on_back(hip_bump=True)))
    assert res.head.tier == m.HEAD_TIER_FALLBACK
    assert sorted(res.head.diagnostics["geometry"]["locked_ends"]) == ["left", "right"]
    assert res.locked[215, 210] > 0.99 and res.breathing_weight[215, 210] < 0.01
    assert res.locked[215, 620] > 0.99 and res.breathing_weight[215, 620] < 0.01


@pytest.mark.parametrize("shape", [round_blob, lying_loaf])
def test_no_visible_head_needs_review_with_precautionary_upper_lock(shape):
    alpha = shape()
    res = m.build_breathing_maps(to_rgba(alpha))
    assert res.head_low_confidence
    assert res.head.tier == m.HEAD_TIER_NONE and res.head.xy is None
    assert res.head.confidence < m.HEAD_MIN_CONFIDENCE
    # 머리를 지어내지 않되, 얼굴이 있을 수 있는 위쪽은 숨쉬지 않는다.
    ys = np.flatnonzero((alpha >= 128).any(axis=1))
    upper_row = int(ys[0] + 0.15 * (ys[-1] - ys[0]))
    xs = np.flatnonzero(alpha[upper_row] == 255)
    assert res.breathing_weight[upper_row, xs].max() < 0.01


def test_high_confidence_head_keeps_v1_tier_and_band():
    for shape in (frontal_dog, side_dog):
        res = m.build_breathing_maps(to_rgba(shape()))
        assert res.head.tier == m.HEAD_TIER_HIGH and res.head.method == "dt_neck_saddle"
        assert res.head.confidence >= 0.75
