"""
ViTMatte(ViTDet 백본) **전역 어텐션의 쿼리 청크 분할** — 메모리 최적화 2단계.

문제
----
ViTMatte-small 의 ViTDet 백본은 12개 블록 중 4개(2·5·8·11)가 전역 어텐션이다.
transformers 의 eager 구현은 1024×1024 입력(=4096 토큰)에서 (heads=6, 4096, 4096)
float32 점수 행렬(≈400MB)을 통째로 만들고, 상대 위치 편향을 브로드캐스트로
더하면서 같은 크기의 임시 텐서를 두 번 더 만들고, softmax 로 한 번 더 만든다 —
블록당 순간 ≈1.1GB. 이것이 2GB Render 워커를 죽이는 첫 번째 원인이다.

해결
----
수학은 그대로 두고 계산 순서만 바꾼다:

    out = softmax(scale·Q·Kᵀ + rel_h + rel_w) · V

를 쿼리 **행 청크**(기본 512행)마다 계산한다. 청크 하나의 점수 행렬은
(heads, 512, 4096) ≈ 50MB 이고 softmax 는 제자리(in-place)에서 한다. 각 쿼리
행의 softmax 는 자기 행만 보기 때문에 행을 나눠 계산해도 결과는 같다 — 부동소수
합산 순서 차이 수준(1e-6)만 남는다. 이 저장소의 측정에서는 비트 단위로 동일했다.

범위
----
- **전역 어텐션 블록(window_size == 0)에만** 인스턴스 단위로 붙인다. 윈도우
  어텐션 블록·다른 모델·클래스 자체는 건드리지 않는다.
- 가중치·출력 크기·전처리·ROI 크롭(vitmatte_service._run_vitmatte_roi)은 그대로.
- transformers 내부 구조가 기대와 다르면(속성 이름, forward 시그니처, 자기
  검증 실패) **원래 구현으로 그대로 둔다.** 잘못된 매팅보다 느린 매팅이 낫다.

환경변수
--------
  VITMATTE_ATTENTION_CHUNK_SIZE   쿼리 청크 행 수. 기본 512. 0 이하면 비활성(원래 eager).
"""

from __future__ import annotations

import logging
import os
import types
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 512

#: 자기 검증(설치 직전 무작위 입력으로 eager vs chunked 비교) 허용 오차.
SELF_TEST_ATOL = 1e-5

#: install_chunked_attention 이 마지막으로 남긴 상태 — 진단/메타에 실린다.
_last_status: dict[str, Any] = {"active": False, "reason": "not_installed"}


def configured_chunk_size(default: int = DEFAULT_CHUNK_SIZE) -> int:
    raw = os.getenv("VITMATTE_ATTENTION_CHUNK_SIZE", "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        logger.warning("VITMATTE_ATTENTION_CHUNK_SIZE=%r 는 정수가 아닙니다 — 기본 %d 사용", raw, default)
        return int(default)


def last_status() -> dict[str, Any]:
    return dict(_last_status)


# ──────────────────────────────────────────────────────────────────────────
# 청크 forward
# ──────────────────────────────────────────────────────────────────────────


def _make_chunked_forward(original_forward, get_rel_pos, chunk_size: int):
    """VitDetAttention 인스턴스에 바인딩할 forward 를 만든다.

    original_forward 는 **바인딩된** 원래 메서드 — output_attentions=True 처럼
    청크로는 답할 수 없는 요청은 그대로 원래 구현에 넘긴다.
    """
    import torch

    def forward(self, hidden_state, output_attentions: bool = False):
        if output_attentions:
            return original_forward(hidden_state, output_attentions=True)

        batch_size, height, width, _ = hidden_state.shape
        n = height * width
        bh = batch_size * self.num_heads

        # eager 와 동일한 q/k/v 배치: (batch*heads, n, head_dim)
        qkv = (
            self.qkv(hidden_state)
            .reshape(batch_size, n, 3, self.num_heads, -1)
            .permute(2, 0, 3, 1, 4)
        )
        queries, keys, values = qkv.reshape(3, bh, n, -1).unbind(0)
        del qkv

        rel_h = rel_w = None
        if self.use_relative_position_embeddings:
            # eager 의 add_decomposed_relative_positions 와 같은 분해 편향.
            # (bh, n, height) / (bh, n, width) — n² 이 아니라 n·h, n·w 크기라 작다.
            relative_height = get_rel_pos(height, height, self.rel_pos_h)
            relative_width = get_rel_pos(width, width, self.rel_pos_w)
            r_q = queries.reshape(bh, height, width, -1)
            rel_h = torch.einsum("bhwc,hkc->bhwk", r_q, relative_height).reshape(bh, n, height)
            rel_w = torch.einsum("bhwc,wkc->bhwk", r_q, relative_width).reshape(bh, n, width)
            del r_q

        keys_t = keys.transpose(-2, -1)
        out = torch.empty_like(queries)
        step = max(1, int(chunk_size))
        for start in range(0, n, step):
            end = min(n, start + step)
            scores = (queries[:, start:end] * self.scale) @ keys_t  # (bh, c, n)
            if rel_h is not None:
                scores = scores.view(bh, end - start, height, width)
                scores.add_(rel_h[:, start:end, :, None])
                scores.add_(rel_w[:, start:end, None, :])
                scores = scores.view(bh, end - start, n)
            scores = torch.softmax(scores, dim=-1, out=scores)
            out[:, start:end] = scores @ values
            del scores

        out = out.view(batch_size, self.num_heads, height, width, -1)
        out = out.permute(0, 2, 3, 1, 4).reshape(batch_size, height, width, -1)
        return (self.proj(out),)

    return forward


# ──────────────────────────────────────────────────────────────────────────
# 설치 (구조 검증 → 자기 검증 → 인스턴스 패치)
# ──────────────────────────────────────────────────────────────────────────

_REQUIRED_ATTENTION_ATTRS = (
    "qkv",
    "proj",
    "num_heads",
    "scale",
    "use_relative_position_embeddings",
    "forward",
)


def _global_attention_layers(model) -> list:
    """window_size == 0 인 ViTDet 레이어만. 구조가 다르면 빈 리스트."""
    encoder = getattr(getattr(model, "backbone", None), "encoder", None)
    layers = getattr(encoder, "layer", None)
    if layers is None:
        return []
    out = []
    for layer in layers:
        if not hasattr(layer, "window_size") or not hasattr(layer, "attention"):
            return []
        if int(getattr(layer, "window_size", 0)) == 0:
            out.append(layer)
    return out


def _structure_ok(attention, get_rel_pos) -> Optional[str]:
    """기대하는 transformers 내부 구조인지. 문제면 사유 문자열."""
    import inspect

    for name in _REQUIRED_ATTENTION_ATTRS:
        if not hasattr(attention, name):
            return f"missing_attr:{name}"
    if bool(attention.use_relative_position_embeddings):
        if not (hasattr(attention, "rel_pos_h") and hasattr(attention, "rel_pos_w")):
            return "missing_attr:rel_pos"
        if get_rel_pos is None:
            return "missing_get_rel_pos"
    try:
        params = list(inspect.signature(attention.forward).parameters)
    except (TypeError, ValueError):
        return "unreadable_forward_signature"
    if params[:1] != ["hidden_state"] or "output_attentions" not in params:
        return f"unexpected_forward_signature:{params}"
    return None


def _self_test(attention, chunked_forward, chunk_size: int) -> Optional[str]:
    """설치 대상 인스턴스에서 eager 와 chunked 를 실제로 비교한다."""
    import torch

    try:
        dim = int(attention.qkv.in_features)
        # 청크 경계가 여러 번 나오도록 토큰 수를 chunk_size 보다 크게 잡되 작게 유지.
        side = 6 if chunk_size > 36 else max(3, int(chunk_size**0.5) + 1)
        gen = torch.Generator().manual_seed(1234)
        x = torch.randn(1, side, side + 1, dim, generator=gen, dtype=attention.qkv.weight.dtype)
        with torch.no_grad():
            expected = attention.forward(x)[0]
            got = chunked_forward(x)[0]
        if expected.shape != got.shape:
            return f"self_test_shape:{tuple(expected.shape)}!={tuple(got.shape)}"
        diff = float((expected - got).abs().max())
        if not (diff <= SELF_TEST_ATOL):
            return f"self_test_diff:{diff:.3e}"
    except Exception as exc:  # noqa: BLE001 — 자기 검증 실패는 곧 '설치하지 않음'
        return f"self_test_error:{type(exc).__name__}"
    return None


def install_chunked_attention(model, *, chunk_size: Optional[int] = None) -> dict[str, Any]:
    """ViTMatte 모델의 전역 어텐션 블록에 청크 forward 를 붙인다 (멱등).

    Returns: 진단 dict — active, chunk_size, patched_layers, reason, transformers.
    어떤 이유로든 실패하면 모델을 건드리지 않은 채 active=False 로 돌아온다.
    """
    global _last_status
    size = configured_chunk_size() if chunk_size is None else int(chunk_size)
    status: dict[str, Any] = {
        "active": False,
        "chunk_size": size,
        "patched_layers": 0,
        "global_layers": 0,
        "reason": None,
        "transformers": None,
    }
    try:
        import transformers

        status["transformers"] = getattr(transformers, "__version__", None)
    except Exception:  # noqa: BLE001
        pass

    try:
        if size <= 0:
            status["reason"] = "disabled"
            return _finish(status)

        try:
            from transformers.models.vitdet import modeling_vitdet as mv
        except Exception as exc:  # noqa: BLE001
            status["reason"] = f"import_error:{type(exc).__name__}"
            return _finish(status)
        get_rel_pos = getattr(mv, "get_rel_pos", None)

        layers = _global_attention_layers(model)
        status["global_layers"] = len(layers)
        if not layers:
            status["reason"] = "no_global_attention_layers"
            return _finish(status)

        already = [l for l in layers if getattr(l.attention, "_eb_chunked_attention", None)]
        if len(already) == len(layers):
            status.update(active=True, patched_layers=len(layers), reason="already_installed")
            status["chunk_size"] = int(getattr(layers[0].attention, "_eb_chunk_size", size))
            return _finish(status)

        # 1) 구조 검증 — 하나라도 다르면 아무것도 붙이지 않는다.
        for layer in layers:
            problem = _structure_ok(layer.attention, get_rel_pos)
            if problem:
                status["reason"] = problem
                return _finish(status)

        # 2) 자기 검증 + 패치 준비 (전부 통과해야 적용)
        prepared = []
        for layer in layers:
            attention = layer.attention
            original = attention.forward  # 바운드 메서드 (인스턴스에 아직 override 없음)
            fn = _make_chunked_forward(original, get_rel_pos, size)
            bound = types.MethodType(fn, attention)
            problem = _self_test(attention, bound, size)
            if problem:
                status["reason"] = problem
                return _finish(status)
            prepared.append((attention, original, bound))

        # 3) 적용 — 인스턴스 속성으로만. 클래스/다른 모델은 그대로.
        for attention, original, bound in prepared:
            attention._eb_original_forward = original
            attention.forward = bound
            attention._eb_chunked_attention = True
            attention._eb_chunk_size = size
        status.update(active=True, patched_layers=len(prepared), reason="installed")
        return _finish(status)
    except Exception as exc:  # noqa: BLE001 — 최적화 실패가 로딩을 막으면 안 된다
        logger.exception("chunked attention install failed; keeping eager attention")
        status["reason"] = f"install_error:{type(exc).__name__}"
        return _finish(status)


def uninstall_chunked_attention(model) -> int:
    """테스트/롤백용: 인스턴스 override 를 제거해 원래 forward 로 되돌린다."""
    n = 0
    for layer in _global_attention_layers(model):
        attention = layer.attention
        if getattr(attention, "_eb_chunked_attention", None):
            for name in ("forward", "_eb_original_forward", "_eb_chunked_attention", "_eb_chunk_size"):
                if name in attention.__dict__:
                    del attention.__dict__[name]
            n += 1
    return n


def _finish(status: dict[str, Any]) -> dict[str, Any]:
    global _last_status
    _last_status = dict(status)
    logger.info(
        "vitmatte chunked attention: active=%s chunk_size=%s patched_layers=%s/%s reason=%s transformers=%s",
        status["active"],
        status["chunk_size"],
        status["patched_layers"],
        status["global_layers"],
        status["reason"],
        status["transformers"],
    )
    return status
