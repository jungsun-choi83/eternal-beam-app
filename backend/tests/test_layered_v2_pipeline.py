from __future__ import annotations

import io

import cv2
import numpy as np
import pytest
from PIL import Image

from backend.services import layered_v2_pipeline as pipeline
from backend.services import video_cutout_service
from backend.services.video_cutout_service import _temporal_alpha


def test_background_download_is_allowlisted(monkeypatch):
    monkeypatch.setenv(
        "SHAKER_LAYERED_BACKGROUND_ALLOWED_ORIGINS",
        "https://assets.eternalbeam.test",
    )
    assert pipeline.background_url_allowed("https://assets.eternalbeam.test/background.mp4")
    assert not pipeline.background_url_allowed("https://attacker.test/background.mp4")
    assert not pipeline.background_url_allowed("file:///etc/passwd")


def test_postprocess_is_an_explicit_rollout_gate(monkeypatch):
    monkeypatch.delenv("SHAKER_LAYERED_V2_POSTPROCESS", raising=False)
    assert pipeline.enabled() is False
    monkeypatch.setenv("SHAKER_LAYERED_V2_POSTPROCESS", "1")
    assert pipeline.enabled() is True


def test_contact_shadow_uses_sampled_alpha_feet_and_stays_subtle():
    shadow = pipeline._contact_shadow_metadata(
        [(20, 79, 10, 90), (22, 77, 12, 92), (21, 78, 11, 91)],
        width=100,
        height=100,
    )
    assert shadow == {
        "kind": "css-contact",
        "opacity": 0.24,
        "blur_px": 11,
        "center_x_pct": 49.5,
        "bottom_pct": 9.0,
        "width_pct": 27.84,
        "height_pct": 2.784,
    }
    assert pipeline._contact_shadow_metadata([], 100, 100) is None


def test_anchored_placement_crops_transparent_canvas_without_zooming_pet():
    placement = pipeline._anchored_placement_metadata(
        [(20, 79, 10, 90), (22, 77, 12, 92), (21, 78, 11, 91)],
        width=100,
        height=100,
    )
    assert placement == {
        "mode": "anchored",
        "center_x_pct": 50.0,
        "bottom_pct": 3.0,
        "height_pct": 91.0,
        "crop_x_min": 0.16,
        "crop_x_max": 0.84,
        "crop_y_min": 0.06,
        "crop_y_max": 0.97,
    }
    assert pipeline._anchored_placement_metadata([], 100, 100) is None


def test_temporal_matte_preserves_a_stable_silhouette_and_first_frame():
    alpha = np.zeros((64, 64), dtype=np.uint8)
    alpha[12:54, 18:47] = 255
    gray = np.tile(np.arange(64, dtype=np.uint8), (64, 1))

    first = _temporal_alpha(alpha, gray, None, None)
    stable = _temporal_alpha(alpha, gray, alpha, gray)

    assert np.array_equal(first, alpha)
    assert np.max(np.abs(stable.astype(np.int16) - alpha.astype(np.int16))) <= 1


def test_temporal_matte_never_resurrects_a_previous_fur_contour():
    current = np.zeros((64, 64), dtype=np.uint8)
    previous = np.zeros_like(current)
    previous[12:54, 18:47] = 255
    gray = np.zeros_like(current)

    stabilized = _temporal_alpha(current, gray, previous, gray)

    assert np.count_nonzero(stabilized) == 0


def test_temporal_matte_rejects_history_when_frame_content_disagrees():
    current = np.zeros((64, 64), dtype=np.uint8)
    current[12:54, 18:47] = 128
    previous = np.zeros_like(current)
    previous[12:54, 18:47] = 148
    current_gray = np.zeros_like(current)
    previous_gray = np.full_like(current, 255)

    stabilized = _temporal_alpha(
        current, current_gray, previous, previous_gray
    )

    assert np.array_equal(stabilized, current)


def test_v2_rejects_segmentation_only_fallback_but_existing_callers_do_not(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.avi"
    writer = cv2.VideoWriter(
        str(source), cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (32, 32)
    )
    assert writer.isOpened()
    writer.write(np.full((32, 32, 3), 127, dtype=np.uint8))
    writer.release()

    def fake_remove(image_bytes, **kwargs):
        meta = kwargs.get("meta_out")
        if isinstance(meta, dict):
            meta["alpha_matting_used"] = False
        image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    monkeypatch.setattr(video_cutout_service, "remove_background", fake_remove)

    normal = video_cutout_service.process_video_to_rgba(
        str(source), str(tmp_path / "normal"), output_resolution=None
    )
    assert len(normal) == 1
    with pytest.raises(RuntimeError, match="alpha matting was unavailable"):
        video_cutout_service.process_video_to_rgba(
            str(source),
            str(tmp_path / "v2"),
            output_resolution=None,
            require_alpha_matting=True,
        )
