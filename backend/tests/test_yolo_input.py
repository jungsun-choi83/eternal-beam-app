"""
YOLO 입력 채널 순서(RGB/BGR/PIL) 회귀 테스트.

핵심 주장: ultralytics 는 `np.ndarray` 를 BGR 로 해석하므로, RGB 배열을 그대로
넘기면 R/B 가 뒤바뀐다. 표준 경로는 PIL Image 를 넘기는 것이다.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from backend.services.yolo_input import compare_yolo_color_paths, to_yolo_source

from .conftest import FakeYolo, make_rgb_image


def test_to_yolo_source_returns_pil_rgb_for_ndarray():
    arr = np.zeros((8, 8, 3), dtype=np.uint8)
    arr[:, :, 0] = 200  # 빨강 우세

    out = to_yolo_source(arr)

    assert isinstance(out, Image.Image)
    assert out.mode == "RGB"
    # 채널이 뒤집히지 않고 그대로 유지되어야 한다.
    assert np.array(out)[0, 0].tolist() == [200, 0, 0]


def test_to_yolo_source_passes_pil_through_unchanged():
    img = make_rgb_image(color=(10, 20, 30))
    out = to_yolo_source(img)
    assert out is img


def test_to_yolo_source_converts_non_rgb_modes():
    rgba = Image.new("RGBA", (4, 4), (1, 2, 3, 255))
    assert to_yolo_source(rgba).mode == "RGB"

    gray = Image.new("L", (4, 4), 128)
    assert to_yolo_source(gray).mode == "RGB"


def test_compare_yolo_color_paths_feeds_three_distinct_sources():
    """
    비교 유틸이 세 경로를 모두 호출하고, 표준 경로(pil_rgb)에는 채널이 뒤집히지
    않은 PIL 이미지를, numpy_bgr 에는 반전된 배열을 넘기는지 확인한다.
    (실제 신뢰도 차이는 가중치가 필요하므로 integration 테스트에서 본다.)
    """
    img = make_rgb_image(width=16, height=16, color=(200, 100, 50))
    yolo = FakeYolo(entries=[(16, 0.9, (1.0, 2.0, 10.0, 12.0))])

    report = compare_yolo_color_paths(img, yolo, classes=[16], conf=0.1)

    assert set(report) == {"numpy_rgb", "numpy_bgr", "pil_rgb"}
    assert len(yolo.calls) == 3

    numpy_rgb_source = yolo.calls[0]["source"]
    numpy_bgr_source = yolo.calls[1]["source"]
    pil_source = yolo.calls[2]["source"]

    assert isinstance(numpy_rgb_source, np.ndarray)
    assert numpy_rgb_source[0, 0].tolist() == [200, 100, 50]

    assert isinstance(numpy_bgr_source, np.ndarray)
    assert numpy_bgr_source[0, 0].tolist() == [50, 100, 200]

    # 표준 경로는 PIL 이미지 — ultralytics 가 RGB 로 해석한다.
    assert isinstance(pil_source, Image.Image)
    assert np.array(pil_source)[0, 0].tolist() == [200, 100, 50]


def test_compare_yolo_color_paths_summarizes_detections():
    img = make_rgb_image()
    yolo = FakeYolo(entries=[(16, 0.87, (1.0, 2.0, 10.0, 12.0))])

    report = compare_yolo_color_paths(img, yolo, classes=[16])

    entry = report["pil_rgb"][0]
    assert entry["class_id"] == 16
    assert entry["class_name"] == "dog"
    assert entry["confidence"] == pytest.approx(0.87, abs=1e-4)
    assert entry["bbox"] == [1, 2, 10, 12]


def test_compare_yolo_color_paths_handles_no_detection():
    yolo = FakeYolo(entries=[])
    report = compare_yolo_color_paths(make_rgb_image(), yolo)
    assert report == {"numpy_rgb": [], "numpy_bgr": [], "pil_rgb": []}


@pytest.mark.integration
def test_real_yolo_ndarray_is_interpreted_as_bgr(monkeypatch):
    """
    실제 가중치가 있을 때만 실행 (`pytest -m integration`).

    저장소의 goya 컷아웃으로 실측한 결과 (2026-08, ultralytics 8.4.115):
        numpy RGB -> dog conf=0.9402
        numpy BGR -> dog conf=0.9510
        PIL  RGB  -> dog conf=0.9510
    numpy_bgr 와 pil_rgb 가 일치하고 numpy_rgb 만 다르다는 것이 "ndarray=BGR"
    해석의 증거다.
    """
    import os

    from backend.services.yolo_input import load_yolo

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    # ultralytics 는 절대 경로에 아포스트로피가 있으면(예: "C:/Users/Kim's ...")
    # 경로를 잘못 정규화해 가중치를 못 찾고 다운로드를 시도한다. 저장소 루트로
    # 이동한 뒤 상대 경로로 넘기면 서비스 기본 동작과 동일해진다.
    monkeypatch.chdir(root)

    sample = os.environ.get("CUTOUT_TEST_IMAGE", os.path.join("public", "demo", "goya-cutout.png"))
    if not os.path.isfile(sample):
        pytest.skip(f"test image not found: {sample}")

    weights = os.environ.get("VITMATTE_YOLO_MODEL", "yolov8n.pt")
    if not os.path.isfile(weights):
        pytest.skip(f"YOLO weights not found: {weights}")

    img = Image.open(sample).convert("RGB")
    report = compare_yolo_color_paths(
        img, load_yolo(weights), classes=[14, 15, 16, 17, 18, 19, 20, 21, 22, 23], conf=0.10
    )

    assert report["pil_rgb"], "표준 경로에서 아무것도 검출되지 않았습니다"
    assert report["numpy_bgr"], "BGR 경로에서 아무것도 검출되지 않았습니다"

    # PIL 경로와 BGR 경로는 동일해야 한다.
    assert report["pil_rgb"][0]["class_id"] == report["numpy_bgr"][0]["class_id"]
    assert report["pil_rgb"][0]["confidence"] == pytest.approx(
        report["numpy_bgr"][0]["confidence"], abs=1e-4
    )
