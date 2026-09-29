"""OOM optimisation step 3 (low-risk): single-thread torch + inference_mode.

Static guards only — no model is loaded here. The numeric equivalence
(alpha unchanged) was measured by hand with the real checkpoints; these
tests keep the wiring from silently regressing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE = REPO_ROOT / "backend/services/vitmatte_service.py"


def _worker_env() -> dict[str, dict]:
    cfg = yaml.safe_load((REPO_ROOT / "render.yaml").read_text())
    svc = next(s for s in cfg["services"] if s["name"] == "eternal-beam-generation-worker")
    return {e["key"]: e for e in svc["envVars"]}


def test_worker_env_caps_threads_and_malloc_arenas():
    env = _worker_env()
    assert env["OMP_NUM_THREADS"]["value"] == "1"
    assert env["MKL_NUM_THREADS"]["value"] == "1"
    assert env["MALLOC_ARENA_MAX"]["value"] == "2"


def test_sam2_and_vitmatte_forward_run_under_inference_mode():
    src = SERVICE.read_text()
    # SAM2 forward
    sam2 = src[src.index("def _sam2_candidates(") : src.index("def _sam2_mask(")]
    assert "with torch.inference_mode():" in sam2
    assert "torch.no_grad()" not in sam2
    # ViTMatte forward (the RSS hooks wrap it; they must stay)
    vit = src[src.index("def _run_vitmatte(") : src.index("def compute_vitmatte_roi(")]
    assert re.search(r"with torch\.inference_mode\(\):\s*\n\s*alphas = model\(\*\*inputs\)\.alphas", vit)
    assert "torch.no_grad()" not in vit
    assert 'trace.mark("after_decoder", decoder_peak_rss_mb=peak)' in vit


def test_model_loaders_pin_torch_to_one_thread():
    src = SERVICE.read_text()
    assert "torch.set_num_threads(1)" in src
    for loader in ("def _load_vitmatte(", "def _load_sam2("):
        start = src.index(loader)
        body = src[start : src.index("\ndef ", start + 1)]
        assert "_configure_torch_threads()" in body, loader


def test_configure_torch_threads_is_idempotent_and_pins_one_thread():
    torch = pytest.importorskip("torch")
    from backend.services import vitmatte_service as vs

    before = torch.get_num_threads()
    try:
        vs._configure_torch_threads()
        assert torch.get_num_threads() == 1
        vs._configure_torch_threads()  # second call is a no-op
        assert torch.get_num_threads() == 1
    finally:
        torch.set_num_threads(before)
        vs._torch_threads_configured = False
