"""
임시 RSS 추적(rss_trace) 검증 — 측정점이 모두 찍히고, 훅이 남지 않고, 꺼지면
아무 것도 하지 않으며, 알파 결과는 추적 유무와 무관하게 같다.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")

from backend.services import rss_trace  # noqa: E402
from backend.services import vitmatte_service as vs  # noqa: E402
from backend.tests.test_vitdet_chunked_attention import _tiny_model  # noqa: E402

EXPECTED_POINTS = [
    "before_vitmatte",
    "after_vitmatte_backbone",
    "before_decoder",
    "after_decoder",
    "after_vitmatte",
]


@pytest.fixture
def tiny_loader(monkeypatch):
    from transformers import VitMatteImageProcessor

    model = _tiny_model()
    processor = VitMatteImageProcessor()
    monkeypatch.setattr(vs, "_load_vitmatte", lambda name, device: (processor, model))
    monkeypatch.setattr(vs, "_vitmatte_attention_status", {"tiny::cpu": {"active": True, "chunk_size": 512, "reason": "installed"}})
    return model


def test_read_rss_returns_numbers():
    rss = rss_trace.read_rss_mb()
    assert rss is None or rss > 0
    mx = rss_trace.read_maxrss_mb()
    assert mx is None or mx > 0


def test_tracer_marks_every_vitmatte_point_and_removes_hooks(tiny_loader, caplog):
    rgb = np.zeros((64, 96, 3), dtype=np.uint8)
    trimap = np.zeros((64, 96), dtype=np.uint8)
    trimap[16:48, 24:72] = 255

    with caplog.at_level(logging.INFO, logger="backend.services.rss_trace"):
        with rss_trace.Tracer(tag="cutout", enabled=True) as trace:
            alpha, info = vs._run_vitmatte_roi(rgb, trimap, "tiny", "cpu", crop_bbox=(24, 16, 72, 48))

    assert alpha.shape == (64, 96)
    names = [p["point"] for p in trace.points]
    assert names == EXPECTED_POINTS
    before = trace.points[0]
    assert before["roi"] == info["roi"]
    assert before["roi_area_ratio"] == info["roi_area_ratio"]
    assert before["chunked_attention_active"] is True and before["attention_chunk_size"] == 512
    after_dec = next(p for p in trace.points if p["point"] == "after_decoder")
    assert "decoder_peak_rss_mb" in after_dec
    after_vm = trace.points[-1]
    assert after_vm["chunked_attention_active"] is True and after_vm["attention_chunk_size"] == 512
    assert all("rss_mb" in p and "maxrss_mb" in p and "t_ms" in p for p in trace.points)
    # 훅은 호출 동안만
    assert not tiny_loader.backbone._forward_hooks
    assert not tiny_loader.decoder._forward_hooks and not tiny_loader.decoder._forward_pre_hooks
    # 로그 형식
    lines = [r.getMessage() for r in caplog.records if "[RSS-TRACE]" in r.getMessage()]
    assert len(lines) == len(EXPECTED_POINTS)
    assert all(f"cutout={trace.id}" in ln for ln in lines)
    assert any("point=before_decoder" in ln for ln in lines)


def test_disabled_tracer_marks_nothing_and_adds_no_hooks(tiny_loader):
    rgb = np.zeros((64, 96, 3), dtype=np.uint8)
    trimap = np.zeros((64, 96), dtype=np.uint8)
    trimap[16:48, 24:72] = 255

    with rss_trace.Tracer(enabled=False) as trace:
        alpha, _ = vs._run_vitmatte_roi(rgb, trimap, "tiny", "cpu", crop_bbox=(24, 16, 72, 48))

    assert alpha.shape == (64, 96)
    assert trace.points == []
    assert not tiny_loader.backbone._forward_hooks and not tiny_loader.decoder._forward_hooks


def test_alpha_identical_with_and_without_trace(tiny_loader):
    rgb = (np.random.default_rng(0).integers(0, 255, size=(64, 96, 3))).astype(np.uint8)
    trimap = np.zeros((64, 96), dtype=np.uint8)
    trimap[16:48, 24:72] = 255
    trimap[12:52, 20:76] = np.where(trimap[12:52, 20:76] == 255, 255, 128)

    with rss_trace.Tracer(enabled=True):
        a1, _ = vs._run_vitmatte_roi(rgb, trimap, "tiny", "cpu", crop_bbox=(24, 16, 72, 48))
    with rss_trace.Tracer(enabled=False):
        a2, _ = vs._run_vitmatte_roi(rgb, trimap, "tiny", "cpu", crop_bbox=(24, 16, 72, 48))
    a3, _ = vs._run_vitmatte_roi(rgb, trimap, "tiny", "cpu", crop_bbox=(24, 16, 72, 48))  # tracer 없음

    assert np.array_equal(a1, a2) and np.array_equal(a2, a3)


def test_pipeline_records_yolo_and_sam2_points(monkeypatch):
    """matte_foreground_with_meta 가 YOLO/SAM2 측정점을 찍고 진단에 남긴다 (모델 목업)."""
    from .conftest import blob_mask, make_jpeg_bytes

    img_w, img_h = 256, 192
    fg = blob_mask(img_h, img_w, area_fraction=0.2)
    ys, xs = np.nonzero(fg)
    tight = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    trimap = np.where(fg > 0, 255, 0).astype(np.uint8)
    monkeypatch.setattr(vs, "_detect_subject", lambda image, yolo_model, *, conf: vs.SubjectDetection(bbox=tight, class_id=16, class_name="dog", confidence=0.9))
    monkeypatch.setattr(vs, "_detect_persons", lambda *a, **k: [])
    monkeypatch.setattr(vs, "_segment_foreground", lambda rgb, prompt_bbox, **kw: vs.SegmentationOutcome(fg_binary=fg, trimap=trimap, segmenter_used="sam2", sam2_score=0.9))

    def fake_vitmatte(rgb, trimap, model_name, device):
        t = rss_trace.current()
        assert t is not None and t.enabled  # 하위 함수가 컨텍스트로 tracer 를 본다
        return np.where(trimap == 255, 1.0, 0.0).astype(np.float32)

    monkeypatch.setattr(vs, "_run_vitmatte", fake_vitmatte)
    monkeypatch.setattr(rss_trace, "ENABLED", True)

    _, meta = vs.matte_foreground_with_meta(make_jpeg_bytes(img_w, img_h))

    names = [p["point"] for p in meta["rss_trace"]["points"]]
    assert names[:3] == ["before_yolo", "after_yolo", "after_sam2"]
    assert "before_vitmatte" in names
    assert rss_trace.current() is None  # 컨텍스트가 정리됐다


def test_pipeline_without_trace_has_no_rss_key(monkeypatch):
    from .conftest import blob_mask, make_jpeg_bytes

    img_w, img_h = 256, 192
    fg = blob_mask(img_h, img_w, area_fraction=0.2)
    ys, xs = np.nonzero(fg)
    tight = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    trimap = np.where(fg > 0, 255, 0).astype(np.uint8)
    monkeypatch.setattr(vs, "_detect_subject", lambda image, yolo_model, *, conf: vs.SubjectDetection(bbox=tight, class_id=16, class_name="dog", confidence=0.9))
    monkeypatch.setattr(vs, "_detect_persons", lambda *a, **k: [])
    monkeypatch.setattr(vs, "_segment_foreground", lambda rgb, prompt_bbox, **kw: vs.SegmentationOutcome(fg_binary=fg, trimap=trimap, segmenter_used="sam2", sam2_score=0.9))
    monkeypatch.setattr(vs, "_run_vitmatte", lambda rgb, trimap, m, d: np.where(trimap == 255, 1.0, 0.0).astype(np.float32))
    monkeypatch.setattr(rss_trace, "ENABLED", False)

    _, meta = vs.matte_foreground_with_meta(make_jpeg_bytes(img_w, img_h))

    assert "rss_trace" not in meta
