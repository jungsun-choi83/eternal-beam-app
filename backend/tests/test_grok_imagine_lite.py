"""Grok Imagine Video 1.5 Lite (fal) adapter + config-driven routing — fakes only.

Covers the grok-specific surface: payload shape against fal's documented schema,
start-only (no end frame), duration/resolution bounds enforced before billing,
cost estimate, stable identity, and that BREATHING/COME_CLOSER route to it by
default while staying swappable through MOTION_MODEL_<ID>. The shared fal queue
transport (submit/check/collect, end-frame rejection) is covered by the
FalVideoProvider tests and not re-exercised here.
"""

from __future__ import annotations

import pytest

from backend.services import motion_spec as ms
from backend.services import video_motion_providers as vp
from backend.services.video_motion_providers import (
    FalGrokImagineLiteProvider,
    MotionVideoRequest,
    VideoProviderError,
)

#: What the pipeline requests for BREATHING / COME_CLOSER (4 s, start-only I2V).
GROK_SPEC = {"resolution": "720p", "duration_sec": 4, "audio": False}


def _request(spec=None, **overrides) -> MotionVideoRequest:
    fields = dict(
        prompt="slow calm breathing",
        start_image_url="https://cdn.test/start.png",
        start_image_bytes=b"start",
        output_spec=dict(GROK_SPEC if spec is None else spec),
        metadata={"motion_version_id": "mv-1", "start_keyframe_id": "kf-1", "attempt": 1},
    )
    fields.update(overrides)
    return MotionVideoRequest(**fields)


# ── registration + routing ──────────────────────────────────────────────────


def test_grok_is_registered_single_fal_vendor():
    assert vp.is_registered_model("grok_imagine_lite")
    provider = vp.get_provider("grok_imagine_lite")
    assert isinstance(provider, FalGrokImagineLiteProvider)
    assert provider.vendor_id == vp.VENDOR_FAL
    assert provider.supports_end_frame is False  # start-only I2V
    assert provider.supports_durable_jobs is True  # usable in the worker run path


def test_grok_identity_is_stable():
    provider = vp.get_provider("grok_imagine_lite")
    assert vp.provider_identity(provider) == {
        "logical_model": "grok_imagine_lite",
        "vendor": "fal",
        "adapter": "FalGrokImagineLiteProvider",
        "vendor_model": "xai/grok-imagine-video/v1.5/lite/image-to-video",
    }


def test_endpoint_id_is_env_overridable(monkeypatch):
    monkeypatch.setenv("FAL_GROK_IMAGINE_LITE_MODEL", "xai/grok-imagine-video/v2/image-to-video")
    assert vp.get_provider("grok_imagine_lite").model_name() == (
        "xai/grok-imagine-video/v2/image-to-video"
    )


def test_breathing_and_come_closer_default_to_grok(monkeypatch):
    monkeypatch.delenv("MOTION_MODEL_BREATHING", raising=False)
    monkeypatch.delenv("MOTION_MODEL_COME_CLOSER", raising=False)
    assert ms.provider_order_for_motion("BREATHING") == (
        ms.PROVIDER_GROK_IMAGINE_LITE,
    )
    assert ms.provider_order_for_motion("COME_CLOSER") == (
        ms.PROVIDER_GROK_IMAGINE_LITE,
    )


def test_routing_is_swappable_via_env(monkeypatch):
    monkeypatch.setenv("MOTION_MODEL_BREATHING", "wan_3_standard")
    assert ms.provider_order_for_motion("BREATHING")[0] == ms.PROVIDER_WAN_3_STANDARD


# ── payload against the documented fal schema ─────────────────────────────────


def test_payload_is_start_only_and_spec_driven():
    payload = vp.get_provider("grok_imagine_lite").build_payload(_request())
    # Exactly the documented fields; no aspect_ratio, no audio, no end frame.
    assert payload == {
        "prompt": "slow calm breathing",
        "image_url": "https://cdn.test/start.png",
        "duration": 4,
        "resolution": "720p",
    }


@pytest.mark.parametrize("resolution", ["480p", "720p", "1080p"])
def test_payload_accepts_documented_resolutions(resolution):
    payload = vp.get_provider("grok_imagine_lite").build_payload(
        _request({**GROK_SPEC, "resolution": resolution})
    )
    assert payload["resolution"] == resolution


def test_payload_rejects_unknown_resolution():
    with pytest.raises(VideoProviderError) as exc:
        vp.get_provider("grok_imagine_lite").build_payload(
            _request({**GROK_SPEC, "resolution": "4k"})
        )
    assert exc.value.code == "PROVIDER_CONTRACT"


@pytest.mark.parametrize("duration", [0, 16, 20])
def test_payload_rejects_out_of_range_duration(duration):
    with pytest.raises(VideoProviderError) as exc:
        vp.get_provider("grok_imagine_lite").build_payload(
            _request({**GROK_SPEC, "duration_sec": duration})
        )
    assert exc.value.code == "PROVIDER_CONTRACT"


def test_payload_rejects_non_integer_duration():
    with pytest.raises(VideoProviderError) as exc:
        vp.get_provider("grok_imagine_lite").build_payload(
            _request({**GROK_SPEC, "duration_sec": 4.5})
        )
    assert exc.value.code == "PROVIDER_CONTRACT"


def test_payload_requires_a_duration():
    with pytest.raises(VideoProviderError) as exc:
        vp.get_provider("grok_imagine_lite").build_payload(
            _request({"resolution": "720p"})
        )
    assert exc.value.code == "PROVIDER_CONTRACT"


# ── cost estimate (lite tier: per-second + per input image) ───────────────────


def test_cost_estimate_matches_lite_tier():
    provider = vp.get_provider("grok_imagine_lite")
    # 720p = $0.03/s * 4s + $0.01 input image = $0.13.
    assert provider.estimate_cost_usd({"resolution": "720p", "duration": 4}) == 0.13
    assert provider.estimate_cost_usd({"resolution": "480p", "duration": 4}) == 0.09
    assert provider.estimate_cost_usd({"resolution": "unknown", "duration": 4}) is None
