"""
전시 캔버스 정규화 + 가슴 중심 호흡 가중치 (v3).

  · 작은 펫/큰 펫 → 같은 캔버스, 같은 실루엣 높이, 같은 지면선
  · 비율 유지, 늘림 없음, 투명 여백
  · 맵(가중치·잠금)은 정규화 캔버스 위에서 계산 → 전부 픽셀 정렬
  · 가중치 정점은 윗몸통(가슴), 머리·다리·꼬리로 새지 않음
  · manifest 좌표 = 정규화 산출물 좌표, ZIP 계약 불변
"""

from __future__ import annotations

import io
import json
import zipfile

import cv2
import numpy as np
import pytest

from backend.services import exhibition_breathing_maps as m
from backend.services import exhibition_canvas as c
from backend.services import exhibition_prep_service as svc
from backend.services import exhibition_prep_store as st
from backend.tests.test_exhibition_breathing_maps import frontal_dog, side_dog, to_rgba
from backend.tests.test_exhibition_prep_run import _decode, _jpeg, fake_matte

W, H = c.DEFAULT_CANVAS_WH
GROUND_ROW = int(round(c.GROUND_LINE_FRAC * H)) - 1


def _scaled(alpha: np.ndarray, f: float) -> np.ndarray:
    h, w = alpha.shape
    return cv2.resize(alpha, (max(1, int(w * f)), max(1, int(h * f))), interpolation=cv2.INTER_NEAREST)


def _solid_bbox(alpha: np.ndarray):
    return c.solid_bbox(alpha)


def _pt(xy, info):
    x, y = c.transform_point(xy, info)
    return int(round(x)), int(round(y))


# ── 1. 크기 정규화 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("factor", [0.4, 1.0, 1.6], ids=["small", "medium", "large"])
def test_small_and_large_pets_get_the_same_occupancy_and_ground_line(factor):
    canvas, info = c.normalize_subject_canvas(to_rgba(_scaled(frontal_dog(), factor)))
    assert canvas.shape == (H, W, 4) and canvas.dtype == np.uint8
    assert info["fit_axis"] == "height"
    assert info["subject_height_frac"] == pytest.approx(c.TARGET_HEIGHT_FRAC, abs=0.01)
    assert info["ground_y"] == GROUND_ROW
    assert info["subject_center_x"] == pytest.approx(W / 2, abs=1.5)
    assert info["upscaled"] == (info["scale"] > 1.0) == (factor < 1.5)  # 0.4·640 px, 1.0·640 px → 확대, 1.6 → 축소


def test_small_and_large_pets_have_identical_subject_boxes():
    _, small = c.normalize_subject_canvas(to_rgba(_scaled(frontal_dog(), 0.4)))
    _, large = c.normalize_subject_canvas(to_rgba(_scaled(frontal_dog(), 1.6)))
    assert abs(small["subject_wh"][1] - large["subject_wh"][1]) <= 2
    assert abs(small["subject_wh"][0] - large["subject_wh"][0]) <= 3
    assert small["ground_y"] == large["ground_y"]


def test_long_pet_is_limited_by_width_but_keeps_the_ground_line():
    canvas, info = c.normalize_subject_canvas(to_rgba(side_dog()))
    assert info["fit_axis"] == "width"
    assert info["subject_width_frac"] == pytest.approx(c.MAX_WIDTH_FRAC, abs=0.01)
    assert info["subject_height_frac"] < c.TARGET_HEIGHT_FRAC
    assert info["ground_y"] == GROUND_ROW
    assert not info["clipped"]


@pytest.mark.parametrize("shape", [frontal_dog, side_dog])
def test_aspect_ratio_is_preserved_no_stretching(shape):
    rgba = to_rgba(shape())
    canvas, info = c.normalize_subject_canvas(rgba)
    sw, sh = info["subject_wh_in_crop"]
    nw, nh = info["subject_wh"]
    assert nw / nh == pytest.approx(sw / sh, rel=0.02)
    sx, sy = info["scale_xy"]
    assert sx == pytest.approx(sy, rel=0.005) and info["scale"] == pytest.approx(sx, rel=0.005)


@pytest.mark.parametrize("shape", [frontal_dog, side_dog])
def test_paws_align_to_the_normalized_ground_line(shape):
    canvas, info = c.normalize_subject_canvas(to_rgba(shape()))
    rows = np.flatnonzero((canvas[:, :, 3] >= c.SOLID_ALPHA).any(axis=1))
    assert int(rows[-1]) == GROUND_ROW == info["ground_y"]
    assert canvas[GROUND_ROW + 1 :, :, 3].max() < c.SOLID_ALPHA  # 지면선 아래엔 실루엣이 없다


def test_padding_is_transparent_and_subject_bbox_is_recorded():
    canvas, info = c.normalize_subject_canvas(to_rgba(frontal_dog()))
    x1, y1, x2, y2 = info["subject_bbox"]
    assert (x1, y1, x2, y2) == _solid_bbox(canvas[:, :, 3])
    # 실루엣 bbox 바깥(보간 페더 2px 너머)은 완전 투명 — 여백은 투명 패딩이다.
    a = canvas[:, :, 3]
    assert a[: y1 - 2, :].max() == 0 and a[:, : x1 - 2].max() == 0 and a[:, x2 + 2 :].max() == 0
    assert a[y2 + 2 :, :].max() == 0
    assert a[0, :].max() == 0 and a[-1, :].max() == 0 and a[:, 0].max() == 0 and a[:, -1].max() == 0


def test_transform_maps_crop_coordinates_onto_the_normalized_canvas():
    rgba = to_rgba(frontal_dog())
    canvas, info = c.normalize_subject_canvas(rgba)
    x1, y1, x2, y2 = info["subject_bbox_in_crop"]
    nx1, ny1, nx2, ny2 = info["subject_bbox"]
    tx1, ty1 = c.transform_point((x1, y1), info)
    tx2, ty2 = c.transform_point((x2, y2), info)
    assert abs(tx1 - nx1) <= 1.5 and abs(ty1 - ny1) <= 1.5 and abs(tx2 - nx2) <= 1.5 and abs(ty2 - ny2) <= 1.5
    # 역변환 식도 성립한다.
    sx, sy = info["scale_xy"]
    ox, oy = info["offset_xy"]
    assert ((tx1 - ox) / sx, (ty1 - oy) / sy) == pytest.approx((x1, y1), abs=1e-6)
    assert set(info["transform"]) == {"crop_to_normalized", "normalized_to_crop", "normalized_to_source"}


def test_canvas_size_is_env_tunable(monkeypatch):
    monkeypatch.setenv("EXHIBITION_CANVAS_WH", "512x768")
    canvas, info = c.normalize_subject_canvas(to_rgba(frontal_dog()))
    assert canvas.shape == (768, 512, 4) and info["canvas_wh"] == [512, 768]
    assert info["subject_height_frac"] == pytest.approx(c.TARGET_HEIGHT_FRAC, abs=0.01)
    monkeypatch.setenv("EXHIBITION_CANVAS_WH", "banana")
    assert c.canvas_wh() == c.DEFAULT_CANVAS_WH


def test_empty_alpha_is_rejected():
    with pytest.raises(c.CanvasNormalizationError) as e:
        c.normalize_subject_canvas(np.zeros((64, 64, 4), np.uint8))
    assert e.value.code == "MAPS_EMPTY_ALPHA"


# ── 1b. SAFE PET REGION ──────────────────────────────────────────────────────


def _tall_dog(h: int = 1200, w: int = 260) -> np.ndarray:
    """키가 큰(세로로 긴) 펫 — 서 있는 자세, 폭은 좁게."""
    a = np.zeros((h, w), np.uint8)
    cv2.ellipse(a, (130, 420), (85, 220), 0, 0, 360, 255, -1)  # 몸통
    cv2.circle(a, (130, 140), 55, 255, -1)  # 머리
    cv2.rectangle(a, (95, 620), (125, 1150), 255, -1)  # 앞다리
    cv2.rectangle(a, (140, 620), (170, 1150), 255, -1)  # 뒷다리
    return a


def test_safe_region_bounds_for_the_default_1024_canvas():
    region = c.safe_region_bounds((1024, 1024))
    assert region == {"x1": 51, "y1": 153, "x2": 973, "y2": 870, "ground_line_y": 869}
    # 폭/높이가 각 상한 상수와 일치 — SAFE PET REGION 은 기존 세 분수(fraction)의 공식화일 뿐.
    assert region["x2"] - region["x1"] == round(c.MAX_WIDTH_FRAC * 1024)
    assert region["y2"] - region["y1"] == round(c.TARGET_HEIGHT_FRAC * 1024)
    assert region["ground_line_y"] == GROUND_ROW


@pytest.mark.parametrize(
    "rgba, label",
    [
        (to_rgba(_scaled(frontal_dog(), 0.4)), "small"),
        (to_rgba(_scaled(frontal_dog(), 1.6)), "large"),
        (to_rgba(_tall_dog()), "tall"),
        (to_rgba(side_dog()), "wide"),
    ],
)
def test_full_silhouette_always_stays_inside_the_safe_region(rgba, label):
    canvas, info = c.normalize_subject_canvas(rgba)
    region = info["safe_region"]
    assert region == c.safe_region_bounds((W, H))
    nx1, ny1, nx2, ny2 = info["subject_bbox"]
    assert region["x1"] <= nx1 and nx2 <= region["x2"], label
    assert region["y1"] <= ny1 and ny2 <= region["y2"], label
    # 정확한 solid-alpha bbox 로도 다시 확인 (info 가 스스로를 베낀 게 아님을 보장).
    ax1, ay1, ax2, ay2 = _solid_bbox(canvas[:, :, 3])
    assert region["x1"] <= ax1 and ax2 <= region["x2"], label
    assert region["y1"] <= ay1 and ay2 <= region["y2"], label
    assert info["ground_line_y"] == region["ground_line_y"] == GROUND_ROW


def test_tall_pet_is_height_limited_and_well_inside_the_width_bound():
    rgba = to_rgba(_tall_dog())
    canvas, info = c.normalize_subject_canvas(rgba)
    assert info["fit_axis"] == "height"
    assert info["subject_height_frac"] == pytest.approx(c.TARGET_HEIGHT_FRAC, abs=0.01)
    region = info["safe_region"]
    nx1, _, nx2, _ = info["subject_bbox"]
    assert (nx2 - nx1) < (region["x2"] - region["x1"]) * 0.6  # 좁은 몸통 — 폭 상한엔 한참 못 미친다
    assert info["ground_y"] == GROUND_ROW
    sw, sh = info["subject_wh_in_crop"]
    nw, nh = info["subject_wh"]
    assert nw / nh == pytest.approx(sw / sh, rel=0.03)  # 세로로 긴 펫도 늘어나지 않는다


@pytest.mark.parametrize(
    "rgba, label",
    [
        (to_rgba(_scaled(frontal_dog(), 0.4)), "small"),
        (to_rgba(_scaled(frontal_dog(), 1.6)), "large"),
        (to_rgba(_tall_dog()), "tall"),
        (to_rgba(side_dog()), "wide"),
    ],
)
def test_subject_is_horizontally_centered_regardless_of_size_or_shape(rgba, label):
    canvas, info = c.normalize_subject_canvas(rgba)
    nx1, _, nx2, _ = info["subject_bbox"]
    assert (nx1 + nx2) / 2.0 == pytest.approx(W / 2.0, abs=1.5), label
    assert info["subject_center_x"] == pytest.approx(W / 2.0, abs=1.5), label


# ── 2. 정규화 캔버스 위의 맵: 잠금·가슴 중심 가중치 ───────────────────────────────


def _frontal_on_canvas():
    alpha = frontal_dog()
    canvas, info = c.normalize_subject_canvas(to_rgba(alpha))
    res = m.build_breathing_maps(canvas)
    yy, xx = np.mgrid[:H, :W]
    sx, sy = info["scale_xy"]
    ox, oy = info["offset_xy"]
    tcx, tcy = c.transform_point((260, 360), info)
    torso = (((xx - tcx) / (130 * sx)) ** 2 + ((yy - tcy) / (150 * sy)) ** 2) <= 1
    hx, hy = c.transform_point((260, 150), info)
    head = ((xx - hx) ** 2 + (yy - hy) ** 2) <= (78 * sx) ** 2
    legs = (yy >= oy + 515 * sy) & (
        ((xx >= ox + 185 * sx) & (xx <= ox + 225 * sx)) | ((xx >= ox + 295 * sx) & (xx <= ox + 335 * sx))
    )
    return canvas, info, res, {"torso": torso, "head": head, "legs": legs, "torso_cy": tcy, "head_xy": (hx, hy)}


def _side_on_canvas():
    alpha = side_dog()
    canvas, info = c.normalize_subject_canvas(to_rgba(alpha))
    res = m.build_breathing_maps(canvas)
    yy, xx = np.mgrid[:H, :W]
    sx, sy = info["scale_xy"]
    ox, oy = info["offset_xy"]
    tcx, tcy = c.transform_point((380, 250), info)
    torso = (((xx - tcx) / (190 * sx)) ** 2 + ((yy - tcy) / (85 * sy)) ** 2) <= 1
    hx, hy = c.transform_point((130, 140), info)
    head = ((xx - hx) ** 2 + (yy - hy) ** 2) <= (62 * sx) ** 2
    legs = yy >= oy + 340 * sy
    tail = (xx >= ox + 575 * sx) & (yy <= oy + 235 * sy)
    return canvas, info, res, {"torso": torso, "head": head, "legs": legs, "tail": tail, "torso_cx": tcx, "head_xy": (hx, hy)}


def test_all_output_maps_are_pixel_aligned_with_the_normalized_subject():
    canvas, info, res, _ = _frontal_on_canvas()
    assert res.breathing_weight.shape == res.locked.shape == canvas.shape[:2] == (H, W)
    alpha0 = canvas[:, :, 3] == 0
    assert res.breathing_weight[alpha0].max() == 0.0 and res.locked[alpha0].max() == 0.0
    assert res.ground_y == info["ground_y"]


@pytest.mark.parametrize("build", [_frontal_on_canvas, _side_on_canvas])
def test_head_and_paws_stay_locked_on_the_normalized_canvas(build):
    canvas, info, res, r = build()
    solid = canvas[:, :, 3] >= c.SOLID_ALPHA
    hx, hy = (int(round(v)) for v in r["head_xy"])
    assert res.locked[hy, hx] > 0.99 and res.breathing_weight[hy, hx] < 0.01
    assert abs(res.head.xy[0] - hx) < 20 and abs(res.head.xy[1] - hy) < 20  # 머리 추정도 정규화 좌표
    paw_rows = slice(GROUND_ROW - 12, GROUND_ROW + 1)
    paw = solid[paw_rows]
    assert paw.any()
    assert res.locked[paw_rows][paw].min() > 0.99
    assert res.breathing_weight[paw_rows][paw].max() < 0.01


def test_strongest_breathing_weight_is_in_the_upper_torso_frontal():
    canvas, info, res, r = _frontal_on_canvas()
    px, py = res.diagnostics["chest_peak"]
    assert r["torso"][py, px]  # 정점은 몸통 안
    assert py < r["torso_cy"] - 0.15 * 150 * info["scale_xy"][1]  # 몸통 중심보다 뚜렷이 위(가슴)
    bx, by = res.breathing_center
    assert r["torso"][int(by), int(bx)] and by < r["torso_cy"]  # 호흡 중심도 몸통 안, 가슴 쪽
    w = res.breathing_weight
    upper = w[r["torso"] & (np.mgrid[:H, :W][0] < r["torso_cy"])].mean()
    lower = w[r["torso"] & (np.mgrid[:H, :W][0] >= r["torso_cy"])].mean()
    assert upper > 1.4 * lower  # 가슴이 세고 복부는 부드럽다 (0 은 아니다)
    assert lower > 0.1


def test_strongest_breathing_weight_is_toward_the_chest_side_view():
    canvas, info, res, r = _side_on_canvas()
    px, py = res.diagnostics["chest_peak"]
    assert r["torso"][py, px]
    assert px < r["torso_cx"] - 0.2 * 190 * info["scale_xy"][0]  # 머리(왼쪽) 쪽 앞가슴
    bx, by = res.breathing_center
    assert r["torso"][int(by), int(bx)] and bx < r["torso_cx"]
    w = res.breathing_weight
    xs = np.mgrid[:H, :W][1]
    front = w[r["torso"] & (xs < r["torso_cx"])].mean()
    hind = w[r["torso"] & (xs >= r["torso_cx"])].mean()
    assert front > 1.5 * hind and hind > 0.1
    assert res.diagnostics["body_axis"][0] < -0.8  # 몸 축이 머리(왼쪽)를 향한다


@pytest.mark.parametrize("build, regions", [(_frontal_on_canvas, ("head", "legs")), (_side_on_canvas, ("head", "legs", "tail"))])
def test_breathing_weight_does_not_spill_into_head_legs_or_tail(build, regions):
    canvas, info, res, r = build()
    solid = canvas[:, :, 3] >= c.SOLID_ALPHA
    w = res.breathing_weight
    for name in regions:
        region = r[name] & solid
        assert region.any(), name
        assert w[region].mean() < 0.02, (name, float(w[region].mean()))
        assert w[region].max() < 0.15, (name, float(w[region].max()))
    assert w[r["torso"]].max() == pytest.approx(1.0)


def test_weight_gradients_are_smooth_no_hard_bands():
    canvas, info, res, _ = _frontal_on_canvas()
    w = res.breathing_weight
    assert float(np.abs(np.diff(w, axis=0)).max()) < 0.06
    assert float(np.abs(np.diff(w, axis=1)).max()) < 0.06


# ── 3. 패키지 끝까지: manifest 좌표 = 정규화 산출물, ZIP 계약 불변 ──────────────────


@pytest.fixture
def stores():
    return st.InMemoryRunStore(), st.InMemoryArtifactStore()


def _run(stores, raw, matte, **create_kw):
    store, artifacts = stores
    row = svc.create_run(raw, created_by="ops-1", store=store, artifacts=artifacts, **create_kw)
    claimed = store.claim_next("w1", stale_after_minutes=20)
    svc.process_run(claimed, store=store, artifacts=artifacts, worker_id="w1", matte_fn=matte)
    return store.get(row["id"])


def test_manifest_coordinates_match_the_normalized_output(stores):
    _, artifacts = stores
    final = _run(stores, _jpeg(800, 800), fake_matte(frontal_dog))
    rid = final["id"]
    man = final["manifest_json"]
    rgba = _decode(artifacts.get(f"exhibition/{rid}/subject_rgba.png"))
    weight = _decode(artifacts.get(f"exhibition/{rid}/breathing_weight.png"))
    locked = _decode(artifacts.get(f"exhibition/{rid}/locked_mask.png"))
    assert rgba.shape[:2] == weight.shape == locked.shape == (H, W) == tuple(man["canvas_wh"][::-1])

    norm = man["normalization"]
    assert norm["version"] == c.CANVAS_VERSION and norm["canvas_wh"] == [W, H]
    assert list(_solid_bbox(rgba[:, :, 3])) == norm["subject_bbox"]
    assert norm["subject_height_frac"] == pytest.approx(c.TARGET_HEIGHT_FRAC, abs=0.01)
    assert man["anchors"]["ground_y"] == norm["ground_y"] == GROUND_ROW
    for key in ("scale", "scale_xy", "offset_xy", "subject_bbox_in_crop", "crop_wh", "fit_axis", "transform"):
        assert key in norm

    # SAFE PET REGION — manifest 에 그대로 기록되고, 실제 실루엣이 그 안에 있다.
    assert norm["safe_region"] == c.safe_region_bounds((W, H))
    assert norm["ground_line_y"] == norm["safe_region"]["ground_line_y"] == GROUND_ROW
    nx1, ny1, nx2, ny2 = norm["subject_bbox"]
    region = norm["safe_region"]
    assert region["x1"] <= nx1 and nx2 <= region["x2"]
    assert region["y1"] <= ny1 and ny2 <= region["y2"]

    hx, hy = (int(round(v)) for v in man["anchors"]["head_point"]["xy"])
    assert rgba[hy, hx, 3] >= c.SOLID_ALPHA and locked[hy, hx] >= 250 and weight[hy, hx] < 655
    bx, by = (int(round(v)) for v in man["anchors"]["breathing_center"])
    assert rgba[by, bx, 3] >= c.SOLID_ALPHA and weight[by, bx] > 0.5 * 65535 and locked[by, bx] < 5
    assert hy < by < norm["ground_y"]  # 가슴은 머리 아래(+y), 지면 위
    ax, ay = man["anchors"]["breathing_axis"]
    assert ay < 0 and (ax * ax + ay * ay) == pytest.approx(1.0, abs=1e-3)
    assert man["diagnostics"]["weight_method"] == m.WEIGHT_METHOD and man["map_method"] == "dt-torso-v3"


def test_small_and_large_pets_arrive_at_the_same_presentation_scale_through_the_package(stores):
    _, artifacts = stores
    small = _run(stores, _jpeg(800, 800), fake_matte(lambda: _scaled(frontal_dog(), 0.5)))
    stores2 = (st.InMemoryRunStore(), st.InMemoryArtifactStore())
    _, artifacts2 = stores2
    large = _run(stores2, _jpeg(1600, 1600), fake_matte(frontal_dog))  # 원본 크기 두 배(800→1600), 실루엣 2 배

    a1 = _decode(artifacts.get(f"exhibition/{small['id']}/subject_rgba.png"))[:, :, 3]
    a2 = _decode(artifacts2.get(f"exhibition/{large['id']}/subject_rgba.png"))[:, :, 3]
    b1, b2 = _solid_bbox(a1), _solid_bbox(a2)
    assert abs((b1[3] - b1[1]) - (b2[3] - b2[1])) <= 2  # 같은 높이
    assert b1[3] == b2[3] == GROUND_ROW + 1  # 같은 지면선
    assert abs((b1[0] + b1[2]) - (b2[0] + b2[2])) <= 4  # 같은 가로 중심
    assert small["manifest_json"]["normalization"]["scale"] > large["manifest_json"]["normalization"]["scale"]


def test_zip_contract_is_unchanged_by_normalization(stores):
    _, artifacts = stores
    final = _run(stores, _jpeg(800, 800), fake_matte(frontal_dog), exhibition_id="expo-1", allocate_queue_number=lambda: 7)
    assert final["status"] == st.STATUS_READY and final["package_id"] == "visitor_0007"
    with zipfile.ZipFile(io.BytesIO(artifacts.get(final["package_zip_path"]))) as zf:
        assert set(zf.namelist()) == {"subject_rgba.png", "breathing_weight.png", "locked_mask.png", "manifest.json"}
        man = json.loads(zf.read("manifest.json"))
        for name in ("subject_rgba.png", "breathing_weight.png", "locked_mask.png"):
            arr = cv2.imdecode(np.frombuffer(zf.read(name), np.uint8), cv2.IMREAD_UNCHANGED)
            assert arr.shape[:2] == (H, W)
    assert man["schema_version"] == "EXHIBITION_RIG_INPUT_V1"
    assert man["canvas_wh"] == [W, H] and man["normalization"]["ground_y"] == GROUND_ROW
    assert man["files"]["subject_rgba"]["path"] == "subject_rgba.png"
