"""
ViTDet 전역 어텐션 쿼리 청크 분할 (메모리 최적화 2단계) 검증.

가중치 없이 작은 VitMatte 모델을 무작위 초기화해 쓴다 — 검증 대상은 품질이
아니라 "eager 와 같은 수학을 내는가", "전역 블록에만 붙는가", "구조가 다르면
손대지 않는가", "출력 계약이 그대로인가" 다. 실제 가중치 비교는 integration.
"""

from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")

from backend.services import vitdet_chunked_attention as ca  # noqa: E402
from backend.services import vitmatte_service as vs  # noqa: E402


def _tiny_model(seed: int = 0):
    from transformers import VitDetConfig, VitMatteConfig, VitMatteForImageMatting

    backbone = VitDetConfig(
        hidden_size=32,
        num_hidden_layers=4,
        num_attention_heads=2,
        image_size=64,
        patch_size=16,
        num_channels=4,
        window_size=2,
        window_block_indices=[0, 2],  # 1, 3 이 전역 어텐션
        residual_block_indices=[1, 3],
        use_relative_position_embeddings=True,
        out_features=["stage4"],
    )
    cfg = VitMatteConfig(
        backbone_config=backbone,
        hidden_size=32,
        convstream_hidden_sizes=[4, 8, 16],
        fusion_hidden_sizes=[16, 8, 4, 2],
    )
    torch.manual_seed(seed)
    return VitMatteForImageMatting(cfg).eval()


def _global_layers(model):
    return [l for l in model.backbone.encoder.layer if l.window_size == 0]


def _window_layers(model):
    return [l for l in model.backbone.encoder.layer if l.window_size > 0]


@pytest.fixture(autouse=True)
def _default_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VITMATTE_ATTENTION_CHUNK_SIZE", raising=False)


# --------------------------------------------------------------------------
# 1. eager vs chunked — 어텐션 모듈 단위
# --------------------------------------------------------------------------


@pytest.mark.parametrize("chunk", [1, 3, 7, 16, 64, 10_000])
@pytest.mark.parametrize("shape", [(1, 8, 8), (1, 6, 10), (2, 5, 7)])
def test_chunked_attention_matches_eager(chunk, shape):
    from transformers.models.vitdet import modeling_vitdet as mv

    model = _tiny_model()
    attention = _global_layers(model)[0].attention
    b, h, w = shape
    x = torch.randn(b, h, w, attention.qkv.in_features, generator=torch.Generator().manual_seed(7))
    with torch.no_grad():
        expected = attention.forward(x)[0]
        fn = ca._make_chunked_forward(attention.forward, mv.get_rel_pos, chunk)
        got = fn(attention, x)[0]
    assert got.shape == expected.shape
    assert torch.allclose(got, expected, atol=1e-6, rtol=1e-5)
    assert float((got - expected).abs().max()) <= 1e-5


def test_chunked_forward_delegates_output_attentions_to_original():
    from transformers.models.vitdet import modeling_vitdet as mv

    model = _tiny_model()
    attention = _global_layers(model)[0].attention
    x = torch.randn(1, 4, 4, attention.qkv.in_features)
    fn = ca._make_chunked_forward(attention.forward, mv.get_rel_pos, 4)
    with torch.no_grad():
        outs = fn(attention, x, output_attentions=True)
    assert len(outs) == 2  # (hidden, attention_probs) — 원래 구현의 응답
    assert outs[1].shape[-1] == 16


# --------------------------------------------------------------------------
# 2. 설치 — 전역 블록에만, 인스턴스 단위로, 멱등
# --------------------------------------------------------------------------


def test_install_patches_only_global_layers_and_is_idempotent():
    model = _tiny_model()
    window_forwards_before = [l.attention.forward for l in _window_layers(model)]
    from transformers.models.vitdet.modeling_vitdet import VitDetAttention

    class_forward_before = VitDetAttention.forward

    status = ca.install_chunked_attention(model, chunk_size=16)

    assert status["active"] is True
    assert status["reason"] == "installed"
    assert status["chunk_size"] == 16
    assert status["patched_layers"] == status["global_layers"] == 2
    assert status["transformers"] == transformers.__version__
    for layer in _global_layers(model):
        assert getattr(layer.attention, "_eb_chunked_attention", False) is True
        assert "forward" in layer.attention.__dict__  # 인스턴스 override
    # 윈도우 블록과 클래스 자체는 그대로
    assert [l.attention.forward for l in _window_layers(model)] == window_forwards_before
    for layer in _window_layers(model):
        assert "forward" not in layer.attention.__dict__
    assert VitDetAttention.forward is class_forward_before

    again = ca.install_chunked_attention(model, chunk_size=16)
    assert again["active"] is True and again["reason"] == "already_installed"
    assert ca.last_status()["reason"] == "already_installed"


def test_install_does_not_touch_other_model_instances():
    a, b = _tiny_model(), _tiny_model(seed=1)
    ca.install_chunked_attention(a, chunk_size=8)
    for layer in _global_layers(b):
        assert "forward" not in layer.attention.__dict__


def test_install_disabled_by_chunk_size_zero(monkeypatch):
    model = _tiny_model()
    monkeypatch.setenv("VITMATTE_ATTENTION_CHUNK_SIZE", "0")
    status = ca.install_chunked_attention(model)
    assert status["active"] is False and status["reason"] == "disabled"
    for layer in _global_layers(model):
        assert "forward" not in layer.attention.__dict__


def test_env_chunk_size_is_used_and_defaults_to_512(monkeypatch):
    assert ca.configured_chunk_size() == 512
    monkeypatch.setenv("VITMATTE_ATTENTION_CHUNK_SIZE", "256")
    assert ca.configured_chunk_size() == 256
    model = _tiny_model()
    assert ca.install_chunked_attention(model)["chunk_size"] == 256
    monkeypatch.setenv("VITMATTE_ATTENTION_CHUNK_SIZE", "garbage")
    assert ca.configured_chunk_size() == 512


# --------------------------------------------------------------------------
# 3. 폴백 — 구조가 다르거나 자기 검증이 깨지면 손대지 않는다
# --------------------------------------------------------------------------


def test_install_falls_back_when_attention_attr_missing():
    model = _tiny_model()
    attention = _global_layers(model)[1].attention
    scale = attention.scale
    del attention.scale  # 인스턴스에 없으면 클래스에도 없다 (일반 속성)

    status = ca.install_chunked_attention(model, chunk_size=16)

    attention.scale = scale
    assert status["active"] is False
    assert status["reason"] == "missing_attr:scale"
    for layer in _global_layers(model):
        assert "forward" not in layer.attention.__dict__


def test_install_falls_back_when_forward_signature_differs():
    model = _tiny_model()
    attention = _global_layers(model)[0].attention

    def odd_forward(self, hidden_states, mask=None):  # 이름/시그니처가 다름
        return (hidden_states,)

    import types

    attention.forward = types.MethodType(odd_forward, attention)
    status = ca.install_chunked_attention(model, chunk_size=16)
    assert status["active"] is False
    assert status["reason"].startswith("unexpected_forward_signature")
    assert getattr(_global_layers(model)[1].attention, "_eb_chunked_attention", None) is None


def test_install_falls_back_when_self_test_fails(monkeypatch):
    model = _tiny_model()

    def broken(original_forward, get_rel_pos, chunk_size):
        def forward(self, hidden_state, output_attentions=False):
            return (original_forward(hidden_state)[0] * 1.5,)

        return forward

    monkeypatch.setattr(ca, "_make_chunked_forward", broken)
    status = ca.install_chunked_attention(model, chunk_size=16)
    assert status["active"] is False
    assert status["reason"].startswith("self_test_diff")
    for layer in _global_layers(model):
        assert "forward" not in layer.attention.__dict__


def test_install_falls_back_when_no_global_layers():
    from transformers import VitDetConfig, VitMatteConfig, VitMatteForImageMatting

    backbone = VitDetConfig(
        hidden_size=32, num_hidden_layers=2, num_attention_heads=2, image_size=64, patch_size=16,
        num_channels=4, window_size=2, window_block_indices=[0, 1], use_relative_position_embeddings=True,
        out_features=["stage2"],
    )
    model = VitMatteForImageMatting(
        VitMatteConfig(backbone_config=backbone, hidden_size=32, convstream_hidden_sizes=[4, 8, 16],
                       fusion_hidden_sizes=[16, 8, 4, 2])
    ).eval()
    status = ca.install_chunked_attention(model, chunk_size=16)
    assert status["active"] is False and status["reason"] == "no_global_attention_layers"


def test_install_never_raises(monkeypatch):
    model = _tiny_model()
    monkeypatch.setattr(ca, "_global_attention_layers", lambda m: (_ for _ in ()).throw(RuntimeError("x")))
    status = ca.install_chunked_attention(model, chunk_size=16)
    assert status["active"] is False and status["reason"] == "install_error:RuntimeError"


# --------------------------------------------------------------------------
# 4. 모델 전체 — 최종 알파가 같고 출력 계약이 그대로
# --------------------------------------------------------------------------


@pytest.mark.parametrize("hw", [(64, 96), (96, 64), (80, 80)])
def test_full_model_alphas_identical_and_same_shape(hw):
    h, w = hw
    x = torch.randn(1, 4, h, w, generator=torch.Generator().manual_seed(3))
    eager = _tiny_model()
    chunked = _tiny_model()
    with torch.no_grad():
        expected = eager(pixel_values=x).alphas
    assert ca.install_chunked_attention(chunked, chunk_size=5)["active"] is True
    with torch.no_grad():
        got = chunked(pixel_values=x).alphas
    assert got.shape == expected.shape == (1, 1, h, w)
    assert float((got - expected).abs().max()) <= 1e-5
    assert float((got - expected).abs().mean()) <= 1e-6


def test_uninstall_restores_eager_forward():
    model = _tiny_model()
    x = torch.randn(1, 4, 64, 64, generator=torch.Generator().manual_seed(5))
    with torch.no_grad():
        before = model(pixel_values=x).alphas
    ca.install_chunked_attention(model, chunk_size=4)
    assert ca.uninstall_chunked_attention(model) == 2
    for layer in _global_layers(model):
        assert "forward" not in layer.attention.__dict__
    with torch.no_grad():
        after = model(pixel_values=x).alphas
    assert torch.equal(before, after)


# --------------------------------------------------------------------------
# 5. 로더 통합 — _load_vitmatte 가 설치를 호출하고 상태를 남긴다
# --------------------------------------------------------------------------


def test_load_vitmatte_installs_chunked_attention_and_records_status(monkeypatch):
    import transformers as tf

    tiny = _tiny_model()
    monkeypatch.setattr(tf.VitMatteForImageMatting, "from_pretrained", classmethod(lambda cls, name: tiny))
    monkeypatch.setattr(tf.VitMatteImageProcessor, "from_pretrained", classmethod(lambda cls, name: object()))
    monkeypatch.setattr(vs, "_vitmatte_cache", {})
    monkeypatch.setattr(vs, "_vitmatte_attention_status", {})
    monkeypatch.setenv("VITMATTE_ATTENTION_CHUNK_SIZE", "8")

    processor, model = vs._load_vitmatte("tiny::test", "cpu")

    assert model is tiny
    status = vs._vitmatte_attention_status["tiny::test::cpu"]
    assert status["active"] is True and status["chunk_size"] == 8 and status["patched_layers"] == 2


def test_load_vitmatte_survives_install_failure(monkeypatch):
    import transformers as tf

    tiny = _tiny_model()
    monkeypatch.setattr(tf.VitMatteForImageMatting, "from_pretrained", classmethod(lambda cls, name: tiny))
    monkeypatch.setattr(tf.VitMatteImageProcessor, "from_pretrained", classmethod(lambda cls, name: object()))
    monkeypatch.setattr(vs, "_vitmatte_cache", {})
    monkeypatch.setattr(vs, "_vitmatte_attention_status", {})
    monkeypatch.setattr(ca, "install_chunked_attention", lambda m: (_ for _ in ()).throw(RuntimeError("boom")))

    _, model = vs._load_vitmatte("tiny::fail", "cpu")

    assert model is tiny
    assert vs._vitmatte_attention_status["tiny::fail::cpu"] == {"active": False, "reason": "setup_error"}


# --------------------------------------------------------------------------
# 6. 실제 가중치 (integration — 기본 실행에서 제외)
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_real_vitmatte_small_alpha_identical_eager_vs_chunked():
    from PIL import Image

    processor, model = vs._load_vitmatte("hustvl/vitmatte-small-composition-1k", "cpu")
    rng = np.random.default_rng(0)
    rgb = rng.integers(0, 255, size=(256, 320, 3), dtype=np.uint8)
    trimap = np.zeros((256, 320), np.uint8)
    trimap[60:200, 80:240] = 255
    trimap[50:210, 70:250] = np.where(trimap[50:210, 70:250] == 255, 255, 128)
    inputs = processor(images=Image.fromarray(rgb), trimaps=Image.fromarray(trimap), return_tensors="pt")
    ca.uninstall_chunked_attention(model)
    with torch.no_grad():
        eager = model(**inputs).alphas
    assert ca.install_chunked_attention(model, chunk_size=64)["active"]
    with torch.no_grad():
        chunked = model(**inputs).alphas
    assert float((eager - chunked).abs().max()) <= 1e-5
