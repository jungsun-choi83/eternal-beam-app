"""
클린 플레이트 + 그림자 알파 억제 단위 계약.

여기서는 프로바이더도 저장소도 쓰지 않는다 — 순수 픽셀 계약만 본다:
  * 플레이트: 누끼 전경 + 고정 중립 배경, 알파 없음, 기하 불변.
  * 그림자 억제: 배경의 감광 버전만 지운다. 몸통/털 경계는 손대지 않고,
    지울 양이 과하면 **아무것도 하지 않는다**.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from backend.services import clean_plate_service as cp
from backend.services import vitmatte_service as vs


def _rgba_png(rgba: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


def _cutout(fg_rgb=(130, 90, 60)) -> np.ndarray:
    """가운데 40×40 불투명 전경, 나머지 완전 투명."""
    rgba = np.zeros((80, 80, 4), dtype=np.uint8)
    rgba[20:60, 20:60, :3] = fg_rgb
    rgba[20:60, 20:60, 3] = 255
    return rgba


# ── 플레이트 합성 ───────────────────────────────────────────────────────────


def test_plate_is_opaque_pet_on_fixed_neutral_background():
    plate_bytes, meta = cp.build_clean_plate(_rgba_png(_cutout()))
    with Image.open(io.BytesIO(plate_bytes)) as im:
        assert im.mode == "RGB"          # 알파는 합성되어 사라진다
        arr = np.asarray(im, dtype=np.uint8)

    bg = cp.background_rgb()
    assert arr.shape == (80, 80, 3)      # 기하 불변 — 리샘플/크롭 없음
    assert tuple(arr[0, 0]) == bg        # 알파 0 → 정확히 계약된 중립 배경
    assert tuple(arr[40, 40]) == (130, 90, 60)  # 알파 255 → 펫 원본색 보존
    assert meta["plate_version"] == cp.CLEAN_PLATE_VERSION
    assert meta["background_rgb"] == list(bg)


def test_plate_background_is_flat_everywhere_outside_the_pet():
    """그림자/그라디언트가 남을 자리가 없다 — 배경은 단 하나의 색이다."""
    plate_bytes, _ = cp.build_clean_plate(_rgba_png(_cutout()))
    with Image.open(io.BytesIO(plate_bytes)) as im:
        arr = np.asarray(im, dtype=np.uint8)
    outside = np.ones((80, 80), dtype=bool)
    outside[20:60, 20:60] = False
    assert len(np.unique(arr[outside].reshape(-1, 3), axis=0)) == 1


def test_plate_background_is_configurable_but_deterministic(monkeypatch):
    monkeypatch.setenv("CLEAN_PLATE_BG_RGB", "12,34,56")
    plate_bytes, meta = cp.build_clean_plate(_rgba_png(_cutout()))
    with Image.open(io.BytesIO(plate_bytes)) as im:
        assert tuple(np.asarray(im)[0, 0]) == (12, 34, 56)
    assert meta["background_rgb"] == [12, 34, 56]


def test_plate_refuses_a_source_without_alpha():
    """알파가 없는 이미지는 '이미 배경이 구워진' 것 — 플레이트로 위장하지 않는다."""
    buf = io.BytesIO()
    Image.fromarray(np.full((40, 40, 3), 128, np.uint8), mode="RGB").save(buf, format="PNG")
    with pytest.raises(cp.CleanPlateError) as e:
        cp.build_clean_plate(buf.getvalue())
    assert e.value.code == "PLATE_SOURCE_NOT_RGBA"


def test_plate_refuses_an_empty_cutout():
    with pytest.raises(cp.CleanPlateError) as e:
        cp.build_clean_plate(_rgba_png(np.zeros((80, 80, 4), np.uint8)))
    assert e.value.code == "PLATE_SOURCE_ALPHA_EMPTY"


def test_plate_path_is_a_sibling_of_raw_and_cutout():
    assert cp.plate_object_path("u/c/canonical/v1/runway_a1_raw.png").endswith("_plate.png")
    assert cp.plate_object_path("u/c/canonical/v1/runway_a1_cutout.png") == (
        "u/c/canonical/v1/runway_a1_plate.png"
    )


# ── 그림자 알파 억제 ────────────────────────────────────────────────────────


def _shadow_scene():
    """벽→바닥 그라디언트 배경 + 갈색 펫 + 발밑 투영 그림자 + 털 경계."""
    h, w = 120, 160
    rgb = np.zeros((h, w, 3), np.uint8)
    for y in range(h):
        rgb[y, :, :] = 210 - int(40 * y / h)

    yy, xx = np.mgrid[0:h, 0:w]
    body = ((xx - 80) ** 2 / 30**2 + (yy - 55) ** 2 / 25**2) < 1
    fg = np.zeros((h, w), np.uint8)
    fg[body] = 255
    rgb[body] = (130, 90, 60)

    # 그림자 = 배경의 감광 버전 (색조 동일, 휘도만 낮다).
    shadow = (((xx - 95) ** 2 / 45**2 + (yy - 92) ** 2 / 10**2) < 1) & ~body
    rgb[shadow] = (rgb[shadow].astype(np.float32) * 0.62).astype(np.uint8)

    alpha = np.zeros((h, w), np.float32)
    alpha[body] = 1.0
    alpha[shadow] = 0.55                       # ViTMatte 가 살려낸 반투명 그림자
    fringe = vs._dilate_binary(fg, 2) & ~body  # 털 경계 — 반드시 살아남아야 한다
    alpha[fringe] = 0.5
    rgb[fringe] = (120, 85, 58)
    return rgb, alpha, fg, body, shadow, fringe


def test_shadow_alpha_is_removed_and_fur_is_preserved():
    rgb, alpha, fg, body, shadow, fringe = _shadow_scene()
    out, diag = vs.suppress_cast_shadow_alpha(rgb, alpha, fg)

    assert diag["applied"] is True
    assert out[shadow].max() == 0.0            # 그림자는 전경이 아니다
    assert out[body].min() == 1.0              # 몸통은 한 픽셀도 깎이지 않는다
    assert np.array_equal(out[fringe], alpha[fringe])  # 털 경계 알파 그대로


def test_shadow_suppression_gives_up_rather_than_eroding_a_gray_pet():
    """배경과 색이 비슷한 회색 펫: 지울 양이 과하면 **아무것도 하지 않는다**."""
    h, w = 120, 160
    rgb = np.full((h, w, 3), 205, np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    body = ((xx - 80) ** 2 / 40**2 + (yy - 60) ** 2 / 35**2) < 1
    fg = np.zeros((h, w), np.uint8)
    fg[body] = 255
    rgb[body] = (160, 158, 157)
    halo = vs._dilate_binary(fg, 10) & ~body
    rgb[halo] = (175, 173, 172)
    alpha = np.zeros((h, w), np.float32)
    alpha[body] = 1.0
    alpha[halo] = 0.6

    out, diag = vs.suppress_cast_shadow_alpha(rgb, alpha, fg)
    assert diag["applied"] is False
    assert diag["reason"] == "would_erode_subject"
    assert np.array_equal(out, alpha)


def test_shadow_suppression_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(vs, "SHADOW_SUPPRESSION_ENABLED", False)
    rgb, alpha, fg, _body, _shadow, _fringe = _shadow_scene()
    out, diag = vs.suppress_cast_shadow_alpha(rgb, alpha, fg)
    assert diag["applied"] is False and diag["reason"] == "disabled"
    assert np.array_equal(out, alpha)


def test_pixels_brighter_than_the_background_are_never_called_shadow():
    """그림자는 배경을 밝히지 않는다 — 밝은 흰 털은 후보조차 되지 않는다."""
    h, w = 100, 100
    rgb = np.full((h, w, 3), 150, np.uint8)
    fg = np.zeros((h, w), np.uint8)
    fg[30:70, 30:70] = 255
    rgb[30:70, 30:70] = (150, 150, 150)
    alpha = np.zeros((h, w), np.float32)
    alpha[30:70, 30:70] = 1.0
    wisp = (slice(70, 74), slice(40, 60))      # 배경보다 **밝은** 털 끝
    rgb[wisp] = (240, 240, 240)
    alpha[wisp] = 0.4

    out, _diag = vs.suppress_cast_shadow_alpha(rgb, alpha, fg)
    assert np.array_equal(out[wisp], alpha[wisp])
