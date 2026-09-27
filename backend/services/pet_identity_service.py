"""
펫 신원 프로필 빌더 (Visual + Structural Identity, Phase 2).

── 파이프라인 ──────────────────────────────────────────────────────────────
pet_reference_images 의 원본(role='original') 레퍼런스들을 읽어:

  1. 적격성 평가  — 저장된 누끼 진단(YOLO/SAM2/ViTMatte 메타) + 이미지 치수 +
                    (짝지어진 누끼의) 알파 경계 접촉으로 "신원 작업에 쓸 만한가"
  2. 시각 신원    — 결정론적 측정만: 코트 색(마스크 픽셀의 median-cut 팔레트),
                    명도 톤, 영역별 색 요약, 레퍼런스 시그니처(HSV 히스토그램
                    64빈 + pHash 64비트 — 이후 동일 펫 일관성/드리프트 검사용)
  3. 구조 신원    — 결정론적("measured"): bbox 기하·실루엣 지표.
                    휴리스틱("low"): pose_estimation_service 의 마스크 기하
                    18키포인트 — 그 모듈 스스로 placeholder 라 명시하므로
                    **절대 measured 로 승격하지 않는다.**
  4. (옵션) VLM   — vlm_identity 뒤에 격리. 기본 꺼짐. 자체 네임스페이스
                    (semantic_traits)에만 기록되고 결정론적 필드를 덮지 않는다.

결과는 pet_identity_profiles 에 **불변 버전**으로 쌓인다. 재분석 = 새 버전.

── 원칙 ────────────────────────────────────────────────────────────────────
* 원본이 정본이다. 이 모듈은 pet_reference_images 를 **읽기만** 하고,
  스토리지에 아무것도 올리지 않는다.
* 잴 수 없는 것은 UNKNOWN 이다. 예: 원본에 짝지어진 누끼(알파 마스크)가 없으면
  시각/구조 분석 자체가 불가능하고, 그렇게 기록한다 — 추측으로 채우지 않는다.
* 분석 substrate 는 **누끼(파생) RGBA** 다: 알파가 피사체 마스크이고 RGB 픽셀은
  원본에서 온 것이므로, 색·실루엣 측정이 원본 증거에 근거한다.

── 왜 학습 임베딩(CLIP/DINOv2)이 아닌가 ───────────────────────────────────
Phase 2 의 목표는 완벽한 펫 생체인식이 아니라 (a) 같은-펫 일관성 검사
(b) 레퍼런스 클러스터링 (c) 큰 신원 드리프트 감지다. HSV 히스토그램 + pHash 는
그 셋을 torch 없이(512MB Render 에서 불가능) 결정론적으로 감당한다.
signature_version 이 시그니처 스키마를 봉인하므로, 이후 Modal 워커에서 학습
임베딩으로 올릴 때 옛 시그니처와 섞이지 않는다.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"

ELIGIBILITY_ANALYZER_VERSION = "eligibility-v2-lineage"
VISUAL_ANALYZER_VERSION = "visual-v2-deterministic"
STRUCTURAL_ANALYZER_VERSION = "structural-v1"
SIGNATURE_VERSION = "sig-v1-hsv64-phash64"
VISUAL_EMBEDDING_VERSION = "identity-embed-v1-rgb-grid"
IDENTITY_FUSION_VERSION = "identity-fusion-v1"
IDENTITY_PROFILE_CONTRACT_VERSION = "pet-identity-profile-v2"

STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"

#: 마스크로 인정할 최소 픽셀 수 — 이보다 작으면 측정이 무의미하다.
_MIN_MASK_PIXELS = 64

_CONFIDENCE_WEIGHT = {
    "high": 1.0,
    "medium": 0.75,
    "low": 0.5,
}

#: 코트 색 이름 팔레트 (개·고양이에서 실제로 나오는 색만; RGB 최근접 매칭).
_NAMED_COAT_COLORS: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("black", (25, 22, 20)),
    ("dark_brown", (74, 51, 34)),
    ("brown", (125, 84, 53)),
    ("red_brown", (155, 88, 49)),
    ("tan", (188, 145, 96)),
    ("golden", (204, 164, 92)),
    ("cream", (228, 212, 180)),
    ("white", (240, 238, 232)),
    ("gray", (128, 128, 126)),
    ("dark_gray", (70, 70, 70)),
)


class PetIdentityError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _table() -> str:
    return os.getenv("PET_IDENTITY_PROFILES_TABLE", "pet_identity_profiles")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


_MOCK_PROFILES: list[dict[str, Any]] = []


def __reset_for_tests() -> None:
    _MOCK_PROFILES.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def analyzer_versions() -> dict[str, Any]:
    """이 코드가 지금 만들 값들의 분석기 버전 스탬프."""
    from . import vlm_identity

    return {
        "contract": IDENTITY_PROFILE_CONTRACT_VERSION,
        "eligibility": ELIGIBILITY_ANALYZER_VERSION,
        "visual": VISUAL_ANALYZER_VERSION,
        "structural": STRUCTURAL_ANALYZER_VERSION,
        "signature": SIGNATURE_VERSION,
        "embedding": VISUAL_EMBEDDING_VERSION,
        "identity_fusion": IDENTITY_FUSION_VERSION,
        "pose_backend": "heuristic_mask_geometry",
        "vlm": (vlm_identity.VLM_ANALYZER_VERSION if vlm_identity.is_enabled() else None),
        "vlm_model": (vlm_identity.model_name() if vlm_identity.is_enabled() else None),
    }


# ══════════════════════════════════════════════════════════════════════════
# 이미지 유틸 (순수 — 테스트는 합성 이미지로 실제 계약을 검증한다)
# ══════════════════════════════════════════════════════════════════════════


def load_rgba(data: bytes) -> Optional[np.ndarray]:
    """bytes → (H,W,4) uint8. 못 읽으면 None — 분석 실패는 UNKNOWN 으로 흐른다."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            return np.asarray(im.convert("RGBA"), dtype=np.uint8)
    except Exception:
        return None


def subject_mask(rgba: np.ndarray) -> np.ndarray:
    """알파 채널 → bool 마스크. 누끼의 알파가 곧 피사체 마스크다."""
    return rgba[:, :, 3] > 128


def mask_border_contact(mask: np.ndarray) -> list[str]:
    """마스크가 프레임 가장자리에 닿은 변 목록 — 잘린 신체의 강한 신호."""
    if not mask.any():
        return []
    sides: list[str] = []
    if mask[0, :].any():
        sides.append("top")
    if mask[-1, :].any():
        sides.append("bottom")
    if mask[:, 0].any():
        sides.append("left")
    if mask[:, -1].any():
        sides.append("right")
    return sides


def _nearest_color_name(rgb: tuple[int, int, int]) -> str:
    best, best_d = UNKNOWN, float("inf")
    for name, ref in _NAMED_COAT_COLORS:
        d = sum((int(a) - int(b)) ** 2 for a, b in zip(rgb, ref))
        if d < best_d:
            best, best_d = name, d
    return best


def _dominant_colors(pixels: np.ndarray, *, max_colors: int = 5) -> list[dict[str, Any]]:
    """
    마스크 픽셀(N,3) → median-cut 팔레트. PIL quantize 라 결정론적이고
    torch/cv2 무관하다. 반환: [{hex, rgb, fraction, name}] (fraction 내림차순).
    """
    from PIL import Image

    if len(pixels) == 0:
        return []
    img = Image.fromarray(pixels.reshape(-1, 1, 3), mode="RGB")
    n = max(2, min(max_colors, len(np.unique(pixels, axis=0))))
    try:
        q = img.quantize(colors=n, method=Image.MEDIANCUT)
    except Exception:
        q = img.quantize(colors=n)
    palette = q.getpalette() or []
    counts = q.getcolors(maxcolors=n * 2) or []
    total = float(sum(c for c, _ in counts)) or 1.0

    out: list[dict[str, Any]] = []
    for count, idx in sorted(counts, reverse=True):
        rgb = tuple(int(v) for v in palette[idx * 3 : idx * 3 + 3])
        out.append(
            {
                "hex": "#%02x%02x%02x" % rgb,
                "rgb": list(rgb),
                "fraction": round(count / total, 4),
                "name": _nearest_color_name(rgb),  # type: ignore[arg-type]
            }
        )
    return out


def _unknown_field(reason: str) -> dict[str, Any]:
    return {"status": UNKNOWN, "reason": reason}


def _is_known_text(value: Any) -> bool:
    return isinstance(value, str) and value.strip() and value.strip().lower() != UNKNOWN


def _trait_confidence(*, supports: int, total: int) -> str:
    if total <= 0 or supports <= 0:
        return "low"
    ratio = supports / float(total)
    if supports >= 2 and ratio >= 0.67:
        return "high"
    if ratio >= 0.5:
        return "medium"
    return "low"


def _stable_string_trait(observations: list[tuple[str, str]]) -> dict[str, Any]:
    """
    [(reference_id, value)] → 가장 안정적으로 반복되는 문자열 특성.
    unknown/빈값은 제외한다.
    """
    known: dict[str, list[str]] = {}
    for rid, value in observations:
        if _is_known_text(value):
            key = str(value).strip()
            known.setdefault(key, []).append(str(rid))
    total = len(observations)
    if not known:
        return _unknown_field("insufficient_cross_reference_evidence")
    best_value, supports = max(
        known.items(),
        key=lambda kv: (len(kv[1]), kv[0]),
    )
    support_ids = sorted(set(supports))
    return {
        "status": "fused",
        "value": best_value,
        "confidence": _trait_confidence(supports=len(support_ids), total=total),
        "support_reference_ids": support_ids,
    }


# ══════════════════════════════════════════════════════════════════════════
# 시각 신원
# ══════════════════════════════════════════════════════════════════════════

#: 결정론적으로 잴 수 없어 시맨틱 분석(VLM)이 필요한 카테고리 — v1 은 얼굴
#: 검출기가 없으므로(펫 얼굴 모델 부재) 추측 대신 unknown 을 적는다.
_SEMANTIC_ONLY_REASON = "requires_semantic_analysis"


def analyze_visual_identity(rgba: np.ndarray) -> dict[str, Any]:
    """누끼 RGBA → 결정론적 시각 신원. 잴 수 없는 카테고리는 unknown."""
    mask = subject_mask(rgba)
    semantic_unknowns = {
        "face": _unknown_field(_SEMANTIC_ONLY_REASON),
        "eyes": _unknown_field(_SEMANTIC_ONLY_REASON),
        "ears": _unknown_field(_SEMANTIC_ONLY_REASON),
        "body_markings": _unknown_field(_SEMANTIC_ONLY_REASON),
        "paws": _unknown_field(_SEMANTIC_ONLY_REASON),
        "tail": _unknown_field(_SEMANTIC_ONLY_REASON),
        "unique_features": _unknown_field(_SEMANTIC_ONLY_REASON),
    }
    if int(mask.sum()) < _MIN_MASK_PIXELS:
        return {
            "status": UNKNOWN,
            "reason": "subject_mask_empty",
            "coat": _unknown_field("subject_mask_empty"),
            **semantic_unknowns,
        }

    pixels = rgba[:, :, :3][mask]
    colors = _dominant_colors(pixels)
    luminance = float(
        np.mean(
            0.299 * pixels[:, 0].astype(np.float64)
            + 0.587 * pixels[:, 1].astype(np.float64)
            + 0.114 * pixels[:, 2].astype(np.float64)
        )
    )
    tone = "dark" if luminance < 60 else ("light" if luminance > 170 else "medium")

    # 영역별 색 요약 — bbox 를 가로 3등분. 어느 쪽이 머리인지는 여기서 판정하지
    # 않는다(구조 분석의 head_side 가 low-confidence 로 따로 온다). 좌/중/우라는
    # 좌표 사실만 적는다.
    ys, xs = np.where(mask)
    xmin, xmax = int(xs.min()), int(xs.max())
    regions: dict[str, Any] = {}
    for label, lo, hi in (
        ("left_third", 0.0, 1 / 3),
        ("center_third", 1 / 3, 2 / 3),
        ("right_third", 2 / 3, 1.0),
    ):
        x0 = xmin + int((xmax - xmin) * lo)
        x1 = xmin + int((xmax - xmin) * hi)
        region_mask = np.zeros_like(mask)
        region_mask[:, x0 : max(x0 + 1, x1)] = mask[:, x0 : max(x0 + 1, x1)]
        region_pixels = rgba[:, :, :3][region_mask]
        if len(region_pixels) < _MIN_MASK_PIXELS:
            regions[label] = _unknown_field("region_too_small")
            continue
        top = _dominant_colors(region_pixels, max_colors=2)
        regions[label] = {"dominant": top[0]["name"] if top else UNKNOWN}

    dominant = [c for c in colors if c["fraction"] >= 0.10]
    return {
        "status": "measured",
        "coat": {
            "status": "measured",
            "dominant_colors": dominant[:2],
            "secondary_colors": dominant[2:],
            "palette": colors,
            "mean_luminance": round(luminance, 1),
            "tone": tone,
            # 길이/타입은 픽셀 통계로 신뢰성 있게 못 잰다 — VLM 영역.
            "length": _unknown_field(_SEMANTIC_ONLY_REASON),
            "texture": _unknown_field(_SEMANTIC_ONLY_REASON),
        },
        "region_color_summary": {
            "note": "horizontal thirds of subject bbox; orientation not asserted",
            **regions,
        },
        **semantic_unknowns,
    }


# ══════════════════════════════════════════════════════════════════════════
# 레퍼런스 시그니처 (유사도/드리프트)
# ══════════════════════════════════════════════════════════════════════════


def compute_reference_signature(rgba: np.ndarray) -> Optional[dict[str, Any]]:
    """
    피사체 시그니처: HSV 4×4×4 히스토그램(마스크 픽셀) + 64비트 pHash
    (피사체 bbox 크롭의 그레이스케일, numpy DCT). 마스크가 없으면 None.
    """
    from PIL import Image

    mask = subject_mask(rgba)
    if int(mask.sum()) < _MIN_MASK_PIXELS:
        return None

    # ── HSV 히스토그램 ────────────────────────────────────────────────────
    hsv = np.asarray(
        Image.fromarray(rgba[:, :, :3], mode="RGB").convert("HSV"), dtype=np.uint8
    )
    px = hsv[mask].astype(np.float64)
    hist, _ = np.histogramdd(px, bins=(4, 4, 4), range=((0, 256), (0, 256), (0, 256)))
    hist = (hist / max(1.0, hist.sum())).reshape(-1)

    # ── pHash: bbox 크롭 → 회색조 32×32 → DCT-II → 좌상 8×8(DC 제외) 중앙값 비트 ─
    ys, xs = np.where(mask)
    crop = rgba[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    gray_img = Image.fromarray(crop[:, :, :3], mode="RGB").convert("L")
    alpha = crop[:, :, 3].astype(np.float64) / 255.0
    gray = np.asarray(gray_img, dtype=np.float64) * alpha + 128.0 * (1.0 - alpha)
    small = np.asarray(
        Image.fromarray(gray.astype(np.uint8), mode="L").resize((32, 32)), dtype=np.float64
    )

    n = 32
    k = np.arange(n)
    dct_m = np.cos(np.pi / n * (k[:, None] + 0.5) * k[None, :]).T  # (freq, sample)
    freq = dct_m @ small @ dct_m.T
    low = freq[:8, :8].reshape(-1)[1:]  # DC 제외 63비트 → 64비트 정렬 위해 패딩
    median = float(np.median(low))
    bits = "".join("1" if v > median else "0" for v in low) + "0"
    phash = "%016x" % int(bits, 2)

    return {
        "version": SIGNATURE_VERSION,
        "hsv_hist": [round(float(v), 5) for v in hist],
        "phash": phash,
    }


def signature_similarity(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """두 시그니처의 유사도 — 드리프트/클러스터링 검사의 원자 연산."""
    if not a or not b or a.get("version") != b.get("version"):
        return {"comparable": False}
    ha = int(str(a["phash"]), 16)
    hb = int(str(b["phash"]), 16)
    hamming = bin(ha ^ hb).count("1")
    va = np.asarray(a.get("hsv_hist") or [], dtype=np.float64)
    vb = np.asarray(b.get("hsv_hist") or [], dtype=np.float64)
    inter = float(np.minimum(va, vb).sum()) if va.shape == vb.shape and len(va) else 0.0
    return {
        "comparable": True,
        "phash_hamming": hamming,  # 0 = 동일, 64 = 완전 상이
        "hist_intersection": round(inter, 4),  # 1.0 = 동일 분포
    }


def compute_visual_embedding(rgba: np.ndarray) -> Optional[dict[str, Any]]:
    """
    개체 시각 임베딩(결정론, 경량): 알파 마스크 bbox 를 고정 격자로 축약한 RGB 특징.
    학습 모델 없이 동일 개체 일치의 보조 신호로만 사용한다.
    """
    from PIL import Image

    mask = subject_mask(rgba)
    if int(mask.sum()) < _MIN_MASK_PIXELS:
        return None

    ys, xs = np.where(mask)
    crop = rgba[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    rgb = crop[:, :, :3].astype(np.float64) / 255.0
    alpha = crop[:, :, 3].astype(np.float64) / 255.0
    composite = (rgb * alpha[:, :, None]) + (0.5 * (1.0 - alpha[:, :, None]))
    resample_bilinear = Image.Resampling.BILINEAR if hasattr(Image, "Resampling") else Image.BILINEAR
    small = np.asarray(
        Image.fromarray(np.clip(composite * 255.0, 0, 255).astype(np.uint8), mode="RGB").resize(
            (32, 32), resample_bilinear
        ),
        dtype=np.float64,
    )
    grid = 4
    cell = 32 // grid
    feats: list[float] = []
    for gy in range(grid):
        for gx in range(grid):
            tile = small[gy * cell : (gy + 1) * cell, gx * cell : (gx + 1) * cell, :]
            feats.extend([float(tile[:, :, 0].mean()), float(tile[:, :, 1].mean()), float(tile[:, :, 2].mean())])

    vec = np.asarray(feats, dtype=np.float64)
    norm = float(np.linalg.norm(vec))
    if norm <= 1e-12:
        return None
    vec = vec / norm
    return {
        "version": VISUAL_EMBEDDING_VERSION,
        "vector": [round(float(v), 6) for v in vec],
        "dim": int(len(vec)),
    }


def embedding_similarity(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    if (
        not a
        or not b
        or a.get("version") != b.get("version")
        or int(a.get("dim") or 0) <= 0
        or int(a.get("dim") or 0) != int(b.get("dim") or 0)
    ):
        return {"comparable": False}
    va = np.asarray(a.get("vector") or [], dtype=np.float64)
    vb = np.asarray(b.get("vector") or [], dtype=np.float64)
    if va.shape != vb.shape or len(va) == 0:
        return {"comparable": False}
    dot = float(np.clip(np.dot(va, vb), -1.0, 1.0))
    return {"comparable": True, "cosine_similarity": round(dot, 4)}


def _extract_coat_pattern_value(visual: dict[str, Any]) -> str:
    region = (visual or {}).get("region_color_summary") or {}
    if not isinstance(region, dict):
        return UNKNOWN
    left = ((region.get("left_third") or {}).get("dominant"))
    center = ((region.get("center_third") or {}).get("dominant"))
    right = ((region.get("right_third") or {}).get("dominant"))
    if not (_is_known_text(left) and _is_known_text(center) and _is_known_text(right)):
        return UNKNOWN
    return f"{left}|{center}|{right}"


def _fuse_coat(visuals: Sequence[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """레퍼런스별 코트 측정치를 융합한다. 기존 coat 스키마는 유지한다."""
    measured = [(rid, (v.get("coat") or {})) for rid, v in visuals if (v.get("coat") or {}).get("status") == "measured"]
    if not measured:
        return _unknown_field("insufficient_cross_reference_evidence")

    color_stats: dict[str, dict[str, Any]] = {}
    luminance_vals: list[float] = []
    tone_obs: list[tuple[str, str]] = []
    for rid, coat in measured:
        if isinstance(coat.get("mean_luminance"), (int, float)):
            luminance_vals.append(float(coat["mean_luminance"]))
        tone_obs.append((rid, str(coat.get("tone") or UNKNOWN)))
        for c in coat.get("palette") or []:
            if not isinstance(c, dict):
                continue
            name = str(c.get("name") or "").strip()
            frac = float(c.get("fraction") or 0)
            if not _is_known_text(name) or frac <= 0:
                continue
            st = color_stats.setdefault(name, {"weight": 0.0, "support": set()})
            st["weight"] += frac
            st["support"].add(rid)

    ranked = sorted(
        color_stats.items(),
        key=lambda kv: (-float(kv[1]["weight"]), -len(kv[1]["support"]), kv[0]),
    )
    total_refs = len(measured)
    dominant: list[dict[str, Any]] = []
    secondary: list[dict[str, Any]] = []
    for i, (name, st) in enumerate(ranked):
        refs = sorted(st["support"])
        item = {
            "name": name,
            "fraction": round(float(st["weight"]) / max(1.0, float(total_refs)), 4),
            "confidence": _trait_confidence(supports=len(refs), total=total_refs),
            "support_reference_ids": refs,
        }
        if i < 2:
            dominant.append(item)
        else:
            secondary.append(item)

    tone = _stable_string_trait(tone_obs)
    mean_l = round(float(np.mean(luminance_vals)), 1) if luminance_vals else None
    return {
        "status": "measured",
        "dominant_colors": dominant,
        "secondary_colors": secondary,
        "palette": [
            {
                "name": name,
                "fraction": round(float(st["weight"]) / max(1.0, float(total_refs)), 4),
                "support_reference_ids": sorted(st["support"]),
            }
            for name, st in ranked
        ],
        "mean_luminance": mean_l,
        "tone": tone.get("value") if tone.get("status") == "fused" else UNKNOWN,
        "tone_evidence": tone,
        "length": _unknown_field(_SEMANTIC_ONLY_REASON),
        "texture": _unknown_field(_SEMANTIC_ONLY_REASON),
    }


def _fuse_embedding(embeddings: dict[str, dict[str, Any]]) -> dict[str, Any]:
    usable = [(rid, e) for rid, e in embeddings.items() if e and (e.get("version") == VISUAL_EMBEDDING_VERSION)]
    if not usable:
        return _unknown_field("no_embedding_evidence")
    vecs = [np.asarray(e.get("vector") or [], dtype=np.float64) for _, e in usable]
    dims = {v.shape for v in vecs}
    if len(dims) != 1:
        return _unknown_field("embedding_dimension_mismatch")
    centroid = np.mean(np.stack(vecs, axis=0), axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm <= 1e-12:
        return _unknown_field("embedding_zero_centroid")
    centroid = centroid / norm

    pairwise: list[float] = []
    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            sim = embedding_similarity(usable[i][1], usable[j][1])
            if sim.get("comparable"):
                pairwise.append(float(sim["cosine_similarity"]))

    support_ids = [rid for rid, _ in usable]
    conf = "high"
    if pairwise:
        m = float(np.mean(pairwise))
        if m < 0.70:
            conf = "medium"
        if m < 0.55:
            conf = "low"
    elif len(support_ids) == 1:
        conf = "medium"
    return {
        "status": "fused",
        "version": VISUAL_EMBEDDING_VERSION,
        "vector": [round(float(v), 6) for v in centroid],
        "dim": int(len(centroid)),
        "confidence": conf,
        "support_reference_ids": support_ids,
        "intra_reference_similarity": (
            {
                "mean": round(float(np.mean(pairwise)), 4),
                "min": round(float(np.min(pairwise)), 4),
                "max": round(float(np.max(pairwise)), 4),
                "pair_count": len(pairwise),
            }
            if pairwise
            else {"mean": None, "min": None, "max": None, "pair_count": 0}
        ),
    }


def _fuse_unique_features(values: list[tuple[str, list[str]]], *, total_refs: int) -> dict[str, Any]:
    seen: dict[str, set[str]] = {}
    for rid, feats in values:
        for feat in feats:
            if _is_known_text(feat):
                key = str(feat).strip()
                seen.setdefault(key, set()).add(rid)
    if not seen:
        return {"status": UNKNOWN, "items": [], "reason": "insufficient_cross_reference_evidence"}
    ranked = sorted(seen.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    items = [
        {
            "value": feat,
            "confidence": _trait_confidence(supports=len(refs), total=total_refs),
            "support_reference_ids": sorted(refs),
        }
        for feat, refs in ranked
    ]
    return {"status": "fused", "items": items}


def _semantic_text_or_unknown(value: Any) -> str:
    if _is_known_text(value):
        return str(value).strip()
    return UNKNOWN


def _compose_body_markings(traits: dict[str, Any]) -> str:
    body = traits.get("body") if isinstance(traits, dict) else {}
    coat = traits.get("coat") if isinstance(traits, dict) else {}
    parts = [
        _semantic_text_or_unknown((body or {}).get("chest_markings")),
        _semantic_text_or_unknown((body or {}).get("torso_markings")),
        _semantic_text_or_unknown((coat or {}).get("marking_distribution")),
    ]
    known = [p for p in parts if p != UNKNOWN]
    return " / ".join(known) if known else UNKNOWN


def _compose_distinctive_features(traits: dict[str, Any]) -> list[str]:
    uniq = traits.get("unique_features") if isinstance(traits, dict) else []
    if not isinstance(uniq, list):
        return []
    return [str(v).strip() for v in uniq if _is_known_text(v)]


def _semantic_traits_from_result(vlm_result: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not vlm_result or not isinstance(vlm_result.get("traits"), dict):
        return {
            "facial_markings": _unknown_field("vlm_traits_unavailable"),
            "body_markings": _unknown_field("vlm_traits_unavailable"),
            "distinctive_features": {"status": UNKNOWN, "items": [], "reason": "vlm_traits_unavailable"},
        }
    traits = vlm_result["traits"]
    facial = _semantic_text_or_unknown(((traits.get("face") or {}).get("facial_markings")))
    body = _compose_body_markings(traits)
    features = _compose_distinctive_features(traits)
    return {
        "facial_markings": (
            {"status": "vlm", "value": facial}
            if facial != UNKNOWN
            else _unknown_field("vlm_insufficient_facial_markings")
        ),
        "body_markings": (
            {"status": "vlm", "value": body}
            if body != UNKNOWN
            else _unknown_field("vlm_insufficient_body_markings")
        ),
        "distinctive_features": (
            {"status": "vlm", "items": features}
            if features
            else {"status": UNKNOWN, "items": [], "reason": "vlm_no_distinctive_features"}
        ),
    }


def _fuse_reference_traits(
    *,
    source_reference_ids: list[str],
    reference_visuals: dict[str, dict[str, Any]],
    reference_embeddings: dict[str, dict[str, Any]],
    reference_semantics: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    레퍼런스별 개체 특성을 융합해 안정적인 동일-개체 프로필을 만든다.
    morphology(구조)는 별도 structural_identity 에 남긴다.
    """
    ordered = [rid for rid in source_reference_ids if rid in reference_visuals]
    visuals = [(rid, reference_visuals[rid]) for rid in ordered]
    total = len(ordered)

    coat = _fuse_coat(visuals)
    pattern_obs = [(rid, _extract_coat_pattern_value(reference_visuals[rid])) for rid in ordered]
    coat_pattern = _stable_string_trait(pattern_obs)

    facial_obs: list[tuple[str, str]] = []
    body_obs: list[tuple[str, str]] = []
    distinct_obs: list[tuple[str, list[str]]] = []
    for rid in ordered:
        sem = reference_semantics.get(rid) or {}
        f = sem.get("facial_markings") or {}
        b = sem.get("body_markings") or {}
        d = sem.get("distinctive_features") or {}
        facial_obs.append((rid, str(f.get("value") or UNKNOWN)))
        body_obs.append((rid, str(b.get("value") or UNKNOWN)))
        distinct_obs.append((rid, list(d.get("items") or [])))

    facial_markings = _stable_string_trait(facial_obs)
    body_markings = _stable_string_trait(body_obs)
    distinctive_features = _fuse_unique_features(distinct_obs, total_refs=max(1, total))
    embedding = _fuse_embedding(reference_embeddings)

    strict_ids = [
        rid
        for rid in source_reference_ids
        if ((reference_visuals.get(rid) or {}).get("status") == "measured")
    ]
    same_individual_gate = {
        "status": ("ready" if strict_ids else UNKNOWN),
        "analyzer": IDENTITY_FUSION_VERSION,
        "reference_count": len(source_reference_ids),
        "strict_lineage_reference_count": len(strict_ids),
        "support_reference_ids": strict_ids,
        "signal_confidence": {
            "signature": _trait_confidence(supports=len(strict_ids), total=max(1, len(source_reference_ids))),
            "coat_pattern": coat_pattern.get("confidence") if isinstance(coat_pattern, dict) else "low",
            "visual_embedding": embedding.get("confidence") if isinstance(embedding, dict) else "low",
            "facial_markings": facial_markings.get("confidence") if isinstance(facial_markings, dict) else "low",
            "body_markings": body_markings.get("confidence") if isinstance(body_markings, dict) else "low",
            "distinctive_features": (
                distinctive_features.get("items", [{}])[0].get("confidence")
                if (distinctive_features.get("items") if isinstance(distinctive_features, dict) else [])
                else "low"
            ),
        },
        # HSV/pHash + embedding 은 보조 합의 신호다. 단일 신호가 독재하지 않도록
        # canonical_qa 에서 복합 판정한다.
        "thresholds": {
            "min_signature_hist_intersection": 0.16,
            "min_embedding_cosine_similarity": 0.72,
        },
    }

    return {
        "coat": coat,
        "coat_pattern": coat_pattern,
        "facial_markings": facial_markings,
        "body_markings": body_markings,
        "distinctive_features": distinctive_features,
        "visual_embedding": embedding,
        "same_individual_gate": same_individual_gate,
    }


def _fuse_semantic_traits_payload(reference_semantics: dict[str, dict[str, Any]]) -> Optional[dict[str, Any]]:
    """per-reference VLM 결과를 보수적으로 융합해 legacy semantic_traits 형식을 유지한다."""
    present: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for rid, sem in reference_semantics.items():
        if sem.get("status") == "present" and isinstance(sem.get("traits"), dict):
            present.append((rid, sem.get("traits") or {}, sem))
    if not present:
        return None

    def _pick(obs: list[tuple[str, str]]) -> str:
        fused = _stable_string_trait(obs)
        if isinstance(fused, dict) and fused.get("status") == "fused":
            return str(fused.get("value") or UNKNOWN)
        return UNKNOWN

    species = _pick([(rid, str(traits.get("species") or UNKNOWN)) for rid, traits, _ in present])
    ears_shape = _pick(
        [(rid, str(((traits.get("ears") or {}).get("shape") or UNKNOWN))) for rid, traits, _ in present]
    )
    coat_markings = _pick(
        [
            (rid, str(((traits.get("coat") or {}).get("marking_distribution") or UNKNOWN)))
            for rid, traits, _ in present
        ]
    )
    coat_length = _pick(
        [(rid, str(((traits.get("coat") or {}).get("length") or UNKNOWN))) for rid, traits, _ in present]
    )

    models = sorted({str((meta.get("model") or "")) for _, _, meta in present if meta.get("model")})
    support_ids = [rid for rid, _, _ in present]
    from . import vlm_identity

    return {
        "status": "vlm",
        "traits": {
            "species": species,
            "ears": {"shape": ears_shape},
            "coat": {
                "marking_distribution": coat_markings,
                "length": coat_length,
            },
        },
        "source_reference_ids": support_ids,
        "image_count": len(support_ids),
        "analyzer": vlm_identity.VLM_ANALYZER_VERSION,
        "model": (models[0] if len(models) == 1 else models),
    }


# ══════════════════════════════════════════════════════════════════════════
# 구조 신원
# ══════════════════════════════════════════════════════════════════════════


def analyze_structural_identity(rgba: np.ndarray) -> dict[str, Any]:
    """
    결정론적 실루엣 지표("measured") + 휴리스틱 포즈("low").

    pose_estimation_service 의 휴리스틱 백엔드는 스스로 placeholder 라 명시한다 —
    그 출력(키포인트·head_side·머리/몸 비율)은 전부 confidence "low" 로 격리되고,
    measured 지표와 절대 섞이지 않는다.
    """
    mask = subject_mask(rgba)
    if int(mask.sum()) < _MIN_MASK_PIXELS:
        return {"status": UNKNOWN, "reason": "subject_mask_empty"}

    ys, xs = np.where(mask)
    h, w = mask.shape
    bbox_w = int(xs.max() - xs.min() + 1)
    bbox_h = int(ys.max() - ys.min() + 1)
    area = int(mask.sum())

    measured: dict[str, Any] = {
        "confidence": "measured",
        "image_size": [int(w), int(h)],
        "bbox": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
        "bbox_aspect_ratio": round(bbox_w / max(1, bbox_h), 3),
        "area_fraction": round(area / float(h * w), 4),
        "bbox_fill_ratio": round(area / float(bbox_w * bbox_h), 4),
        "border_contact": mask_border_contact(mask),
    }
    try:
        import cv2

        contours, _ = cv2.findContours(
            mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if contours:
            biggest = max(contours, key=cv2.contourArea)
            hull_area = float(cv2.contourArea(cv2.convexHull(biggest)))
            if hull_area > 0:
                measured["silhouette_solidity"] = round(
                    float(cv2.contourArea(biggest)) / hull_area, 4
                )
    except Exception:
        pass  # solidity 는 부가 지표 — 없으면 없는 대로 둔다

    # ── 휴리스틱 포즈 (low confidence, 격리) ──────────────────────────────
    pose_out: dict[str, Any]
    try:
        from .pose_estimation_service import estimate_pose, keypoints_to_dict

        pose = estimate_pose(
            rgba[:, :, :3], (mask.astype(np.uint8) * 255), backend="heuristic"
        )
        head_top = pose.get("head_top")
        neck = pose.get("neck")
        head_height_fraction = None
        if head_top and neck and bbox_h > 0:
            head_height_fraction = round(abs(neck.y - head_top.y) / bbox_h, 3)
        pose_out = {
            "confidence": "low",
            "backend": pose.backend,
            "head_side": pose.head_side,
            "keypoints": keypoints_to_dict(pose),
            "head_height_fraction": head_height_fraction,
            "warnings": list(pose.warnings),
            "caveat": "heuristic silhouette geometry — placeholder per its own docs; "
            "never treat as authoritative skeleton",
        }
    except Exception as e:
        pose_out = _unknown_field(f"pose_estimation_failed: {type(e).__name__}")

    return {
        "status": "measured",
        "silhouette": measured,
        # 측면 사진 가정 하의 몸 길이/키 비율 근사 — bbox 기반이라 measured 로
        # 표기하되, 방향성 주의를 함께 남긴다.
        "body_length_height_ratio": {
            "value": measured["bbox_aspect_ratio"],
            "confidence": "measured",
            "caveat": "bbox aspect ratio; assumes near-side view, pose-dependent",
        },
        "pose": pose_out,
        # 휴리스틱은 꼬리 가시성을 판정할 수 없다(항상 꼬리 좌표를 만들어 낸다).
        "tail_visibility": _unknown_field("not_determinable_from_silhouette_v1"),
        "leg_proportions": _unknown_field("near_far_leg_ambiguity_v1"),
    }


# ══════════════════════════════════════════════════════════════════════════
# 레퍼런스 적격성
# ══════════════════════════════════════════════════════════════════════════


def _diag_lookup(diagnostics: Optional[dict[str, Any]], *keys: str) -> Any:
    """진단 dict 에서 키를 방어적으로 찾는다 — 평면/중첩 두 형태 모두."""
    if not isinstance(diagnostics, dict):
        return None
    for k in keys:
        if k in diagnostics:
            return diagnostics[k]
    nested = diagnostics.get("diagnostics")
    if isinstance(nested, dict):
        for k in keys:
            if k in nested:
                return nested[k]
    return None


def evaluate_reference_eligibility(
    ref: Any,
    cutout_rgba: Optional[np.ndarray],
    *,
    strict_lineage_ok: bool = True,
    cutout_reference: Optional[Any] = None,
) -> dict[str, Any]:
    """
    원본 레퍼런스 1건의 신원 작업 적격성.

    입력은 (a) Phase 1 이 저장한 누끼 진단 메타 (b) 짝지어진 누끼의 알파 뿐이다 —
    여기서 모델을 새로 돌리지 않는다. 뷰(FRONT/LEFT/…) 라벨은 **판정하지 않는다**:
    근거가 될 분석이 없으므로 unknown 으로 남긴다.
    """
    diag = getattr(ref, "diagnostics", None)

    subject_detected = _diag_lookup(diag, "subject_detected")
    confidence = _diag_lookup(diag, "detection_confidence", "confidence")
    animal_class = _diag_lookup(diag, "subject_class", "animal_class")
    mask_fraction = _diag_lookup(diag, "mask_area_fraction", "alpha_area_fraction")
    rectangle_like = _diag_lookup(diag, "rectangle_like_mask", "rectangle_like")
    quality_score = _diag_lookup(diag, "quality_score")

    # 사람 오염 — 진단에 사람 관련 신호가 있으면 그걸 쓰고, 없으면 unknown.
    person = getattr(ref, "person_detected", None)
    if person is None:
        person_boxes = _diag_lookup(diag, "person_boxes", "person_bbox_count")
        if isinstance(person_boxes, list):
            person = len(person_boxes) > 0
        elif isinstance(person_boxes, (int, float)):
            person = person_boxes > 0

    border: Any = UNKNOWN
    full_body: str = UNKNOWN
    if cutout_rgba is not None:
        mask = subject_mask(cutout_rgba)
        if int(mask.sum()) >= _MIN_MASK_PIXELS:
            border = mask_border_contact(mask)
            # 몸이 프레임에 잘렸다는 직접 증거만 쓴다. 접촉 없음 = "잘리지는
            # 않았다"이지 "전신이 보인다"의 증명은 아니므로 likely 로만 적는다.
            full_body = "unlikely" if border else "likely"
        else:
            border = UNKNOWN

    reasons: list[str] = []
    if subject_detected is False:
        reasons.append("subject_not_detected")
    if rectangle_like is True:
        reasons.append("rectangle_like_mask")
    if person is True:
        reasons.append("person_contamination")
    if full_body == "unlikely":
        reasons.append("subject_cropped_by_frame")
    if cutout_rgba is None:
        reasons.append("no_segmentation_available")
    if not strict_lineage_ok:
        reasons.append("no_strict_original_cutout_lineage")

    usable = (
        strict_lineage_ok
        and subject_detected is not False
        and rectangle_like is not True
        and cutout_rgba is not None
    )

    cutout_id = str(getattr(cutout_reference, "id", "") or "") or None
    original_id = str(getattr(ref, "id", "") or "") or None
    lineage = {
        "status": "strict_parent_linked" if strict_lineage_ok else UNKNOWN,
        "original_reference_id": original_id,
        "cutout_reference_id": cutout_id,
        "reason": None if strict_lineage_ok else "parent_reference_id_missing_or_mismatch",
    }

    return {
        "analyzer": ELIGIBILITY_ANALYZER_VERSION,
        "lineage": lineage,
        "subject_detected": subject_detected if subject_detected is not None else UNKNOWN,
        "animal_class": animal_class or UNKNOWN,
        "detection_confidence": confidence if confidence is not None else UNKNOWN,
        "mask_area_fraction": mask_fraction if mask_fraction is not None else UNKNOWN,
        "rectangle_like_mask": rectangle_like if rectangle_like is not None else UNKNOWN,
        "segmentation_quality_score": quality_score if quality_score is not None else UNKNOWN,
        "person_contamination": person if person is not None else UNKNOWN,
        "border_contact": border,
        "full_body_visible": full_body,
        # 근거 있는 분석이 없으므로 추측하지 않는다 (요구사항 6).
        "view_label_estimate": UNKNOWN,
        "face_usable": UNKNOWN,  # 펫 얼굴 검출기 부재 (v1)
        "tail_visible": UNKNOWN,
        "usable_for_identity": usable,
        "reasons": reasons,
    }


# ══════════════════════════════════════════════════════════════════════════
# 프로필 빌드 / 조회
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PetIdentityProfile:
    id: Optional[str]
    pet_id: str
    user_id: str
    content_id: Optional[str]
    version: int
    status: str
    source_reference_ids: list[str] = field(default_factory=list)
    reference_eligibility: dict[str, Any] = field(default_factory=dict)
    visual_identity: dict[str, Any] = field(default_factory=dict)
    structural_identity: dict[str, Any] = field(default_factory=dict)
    completeness: dict[str, Any] = field(default_factory=dict)
    analyzer_versions: dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None
    #: 이번 호출이 새 버전을 만들지 않고 기존 최신 프로필을 돌려준 것인가.
    deduplicated: bool = False


_SELECT = (
    "id, pet_id, user_id, content_id, version, status, source_reference_ids, "
    "reference_eligibility, visual_identity, structural_identity, completeness, "
    "analyzer_versions, created_at"
)


def _to_profile(row: dict[str, Any], *, deduplicated: bool = False) -> PetIdentityProfile:
    return PetIdentityProfile(
        id=(str(row["id"]) if row.get("id") else None),
        pet_id=str(row.get("pet_id") or ""),
        user_id=str(row.get("user_id") or ""),
        content_id=(row.get("content_id") or None),
        version=int(row.get("version") or 1),
        status=str(row.get("status") or STATUS_PARTIAL),
        source_reference_ids=list(row.get("source_reference_ids") or []),
        reference_eligibility=dict(row.get("reference_eligibility") or {}),
        visual_identity=dict(row.get("visual_identity") or {}),
        structural_identity=dict(row.get("structural_identity") or {}),
        completeness=dict(row.get("completeness") or {}),
        analyzer_versions=dict(row.get("analyzer_versions") or {}),
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
        deduplicated=deduplicated,
    )


async def _profile_rows(pet_id: str) -> list[dict[str, Any]]:
    pid = (pet_id or "").strip()
    if not pid:
        return []
    if _use_db() and _supabase():
        try:
            def _select():
                return (
                    _supabase()
                    .table(_table())
                    .select(_SELECT)
                    .eq("pet_id", pid)
                    .order("version", desc=False)
                    .execute()
                )

            r = await asyncio.to_thread(_select)
            return getattr(r, "data", None) or []
        except Exception as e:
            logger.exception("신원 프로필 조회 실패 (pet=%s)", pid)
            raise PetIdentityError(
                "IDENTITY_PROFILES_UNAVAILABLE", "신원 프로필을 확인하지 못했습니다.", status=503
            ) from e
    return [r for r in _MOCK_PROFILES if r.get("pet_id") == pid]


async def get_profile(
    *, user_id: str, pet_id: str, version: Optional[int] = None
) -> Optional[PetIdentityProfile]:
    """소유권이 확인된 호출자의 프로필 조회. version 없으면 최신."""
    from . import pet_reference_service

    # 소유권은 레퍼런스 대장과 같은 규칙으로 확인한다 (레지스트리 우선 TOFU).
    try:
        await pet_reference_service.list_references(user_id=user_id, pet_id=pet_id)
    except pet_reference_service.PetReferenceError as e:
        raise PetIdentityError(e.code, e.message, status=e.status) from e

    rows = await _profile_rows(pet_id)
    if not rows:
        return None
    if version is not None:
        for r in rows:
            if int(r.get("version") or 0) == version:
                return _to_profile(r)
        return None
    return _to_profile(max(rows, key=lambda r: int(r.get("version") or 0)))


def _default_fetch_bytes(ref: Any) -> Optional[bytes]:
    """스토리지에서 레퍼런스 바이트를 내려받는다 — 서명이 곧 존재 확인이다."""
    try:
        from .asset_url_refresh import StorageObject, default_bucket, sign_object

        # bucket 미기록 레퍼런스(과거 행/파생 payload)는 기본 버킷으로 서명한다.
        url = sign_object(
            StorageObject(bucket=(getattr(ref, "bucket", "") or default_bucket()), path=ref.object_path)
        )
        if not url:
            return None
        import httpx

        r = httpx.get(url, timeout=30.0, follow_redirects=True)
        if r.status_code != 200 or not r.content:
            return None
        return r.content
    except Exception:
        logger.warning("레퍼런스 다운로드 실패 (path=%s)", getattr(ref, "object_path", "?"), exc_info=True)
        return None


def _completeness(visual: dict[str, Any], structural: dict[str, Any], semantic_status: str) -> dict[str, Any]:
    def count(d: dict[str, Any]) -> tuple[int, int]:
        known = unknown = 0
        for v in d.values():
            if isinstance(v, dict) and v.get("status") == UNKNOWN:
                unknown += 1
            elif isinstance(v, dict) or v is not None:
                known += 1
        return known, unknown

    vk, vu = count({k: v for k, v in visual.items() if k not in ("status", "semantic_traits")})
    sk, su = count({k: v for k, v in structural.items() if k != "status"})
    return {
        "visual": {"known": vk, "unknown": vu},
        "structural": {"known": sk, "unknown": su},
        "semantic": semantic_status,
    }


async def _insert_profile_row(row: dict[str, Any]) -> tuple[bool, Optional[Exception]]:
    if _use_db() and _supabase():
        try:
            await asyncio.to_thread(lambda: _supabase().table(_table()).insert(row).execute())
            return True, None
        except Exception as e:  # noqa: BLE001
            return False, e
    for r in _MOCK_PROFILES:
        if r["pet_id"] == row["pet_id"] and int(r["version"]) == int(row["version"]):
            return False, PetIdentityError("DUPLICATE", "duplicate version")
    _MOCK_PROFILES.append(dict(row))
    return True, None


def lineage_from_eligibility(reference_eligibility: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """
    저장된 프로필 → 그것이 근거로 삼았던 원본→누끼 계보.

    새 컬럼을 만들지 않는다: 계보는 이미 reference_eligibility[rid]["lineage"] 에
    박제돼 있다(원본 id, 누끼 id, strict_parent_linked 여부). 그 값을
    pet_reference_service.strict_lineage_map 과 같은 모양으로 되읽어, 재사용
    판정이 **지금의 대장**과 **그때의 근거**를 직접 비교하게 한다.

    계보 기록 이전의 오래된 프로필은 여기서 (None, False) 로 읽힌다 — 그때는
    실제로 누끼를 근거로 쓰지 않았다는 뜻이므로, 지금 누끼가 있으면 불일치가
    되어 한 번 다시 빌드된다. 그것이 맞는 동작이다.
    """
    out: dict[str, dict[str, Any]] = {}
    for rid, entry in (reference_eligibility or {}).items():
        lineage = entry.get("lineage") if isinstance(entry, dict) else None
        lineage = lineage if isinstance(lineage, dict) else {}
        cutout_id = lineage.get("cutout_reference_id")
        out[str(rid)] = {
            "cutout_reference_id": (str(cutout_id) if cutout_id else None),
            "strict": lineage.get("status") == "strict_parent_linked",
        }
    return out


def _analyze_one_original(
    ref: Any,
    cut: Any,
    fetch: Callable[[Any], Optional[bytes]],
) -> dict[str, Any]:
    """
    원본 레퍼런스 1건의 다운로드 + 결정론 분석(+옵션 VLM). 순수 동기 워커라
    `asyncio.to_thread` 로 여러 레퍼런스를 동시에 돌릴 수 있다.

    다른 레퍼런스의 결과와 **완전히 독립**이다 — 서로 다른 사진의 바이트라
    VLM 시맨틱 캐시 키도 겹치지 않으므로, 동시에 실행해도 유료 호출이 늘지
    않는다(같은 사진을 형태 프로필도 분석할 때만 캐시가 재사용된다).
    """
    from . import vlm_identity

    strict_lineage_ok = bool(cut and cut.parent_reference_id and cut.parent_reference_id == ref.id)
    cut_rgba = None
    if cut is not None:
        cut_bytes = fetch(cut)
        if cut_bytes:
            cut_rgba = load_rgba(cut_bytes)

    entry = evaluate_reference_eligibility(
        ref,
        cut_rgba,
        strict_lineage_ok=strict_lineage_ok,
        cutout_reference=cut,
    )
    if cut_rgba is not None:
        sig = compute_reference_signature(cut_rgba)
        if sig:
            entry["signature"] = sig

    visual_entry: Optional[dict[str, Any]] = None
    embedding_entry: Optional[dict[str, Any]] = None
    structural_entry: Optional[dict[str, Any]] = None
    usable_primary = bool(cut_rgba is not None and entry["usable_for_identity"])
    if usable_primary:
        visual_entry = analyze_visual_identity(cut_rgba)
        embedding_entry = compute_visual_embedding(cut_rgba)
        # 어느 레퍼런스가 최종 primary 로 뽑힐지는 병렬 실행 순서가 아니라
        # 원래의 결정론적 순서(원본 등록 순)로 나중에 정한다 — 그래서 후보가
        # 될 수 있는 레퍼런스마다 구조 분석을 미리 계산해 둔다(계산 자체는
        # 결정론적이고 값싸다 — 다시 계산해도 결과가 달라지지 않는다).
        structural_entry = analyze_structural_identity(cut_rgba)

    semantic_entry: Optional[dict[str, Any]] = None
    if vlm_identity.is_enabled():
        orig_bytes = fetch(ref)
        if orig_bytes:
            result = vlm_identity.analyze_semantic_traits([(orig_bytes, ref.mime_type or "image/jpeg")])
            semantic_fields = _semantic_traits_from_result(result)
            semantic_entry = {
                **semantic_fields,
                "status": ("present" if result else UNKNOWN),
                "traits": ((result or {}).get("traits") if result else None),
                "model": ((result or {}).get("model") if result else None),
                "analyzer": ((result or {}).get("analyzer") if result else None),
            }

    return {
        "eligibility": entry,
        "usable_primary": usable_primary,
        "visual": visual_entry,
        "embedding": embedding_entry,
        "structural": structural_entry,
        "semantic": semantic_entry,
    }


async def build_identity_profile(
    *,
    user_id: str,
    pet_id: str,
    fetch_bytes: Optional[Callable[[Any], Optional[bytes]]] = None,
    skip_if_unchanged: bool = True,
) -> PetIdentityProfile:
    """
    원본 레퍼런스 → 새 신원 프로필 버전.

    * 소유권은 레퍼런스 대장과 같은 규칙 (레지스트리 우선, TOFU).
    * skip_if_unchanged=True(기본): 최신 프로필이 같은 원본 집합 + 같은 분석기
      버전으로 만들어졌으면 새 버전을 만들지 않고 그것을 돌려준다 (멱등).
    * 이 함수는 pet_reference_images 를 **읽기만** 하고 스토리지에 쓰지 않는다.
    """
    from . import pet_reference_service, vlm_identity

    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    if not uid or not pid:
        raise PetIdentityError("IDENTITY_INVALID", "user_id 와 pet_id 가 필요합니다.")

    try:
        refs = await pet_reference_service.list_references(user_id=uid, pet_id=pid)
    except pet_reference_service.PetReferenceError as e:
        raise PetIdentityError(e.code, e.message, status=e.status) from e

    originals = [
        r
        for r in refs
        if r.role == pet_reference_service.ROLE_ORIGINAL
        and r.acceptance_state == pet_reference_service.STATE_ACCEPTED
    ]
    if not originals:
        raise PetIdentityError(
            "NO_ORIGINAL_REFERENCES",
            "분석할 원본 레퍼런스가 없습니다 — 먼저 사진을 등록하세요.",
            status=409,
        )

    cutout_by_original = pet_reference_service.pair_cutouts(refs)

    source_ids = sorted(str(r.id) for r in originals if r.id)
    versions = analyzer_versions()
    # 재사용 키의 일부다 — 원본 집합이 그대로여도 누끼가 나중에 붙거나 바뀌면
    # 이 사상이 달라지고, 프로필은 다시 빌드된다 (append-only 새 버전).
    lineage_map = pet_reference_service.strict_lineage_map(refs)

    if skip_if_unchanged:
        rows = await _profile_rows(pid)
        if rows:
            latest = _to_profile(max(rows, key=lambda r: int(r.get("version") or 0)))
            if (
                sorted(latest.source_reference_ids) == source_ids
                and latest.analyzer_versions == versions
                and lineage_from_eligibility(latest.reference_eligibility) == lineage_map
            ):
                return _to_profile(
                    max(rows, key=lambda r: int(r.get("version") or 0)), deduplicated=True
                )

    fetch = fetch_bytes or _default_fetch_bytes

    # ── 레퍼런스별 적격성 + 시그니처, 프로필 수준 시각/구조는 primary 에서 ──
    # 다운로드 + 분석은 레퍼런스마다 독립이라 동시에 돌린다(레이턴시만 줄고
    # 유료 호출 수는 그대로다 — _analyze_one_original 문서 참고). 결과는
    # 완료 순서가 아니라 **원본 등록 순서**로 순회해 합쳐야 primary 선택이
    # 병렬화 전과 정확히 같은 결정론을 유지한다.
    from .concurrency import gather_bounded

    analysis_results = await gather_bounded(
        [
            (
                lambda r=ref, c=cutout_by_original.get(str(ref.id)): asyncio.to_thread(
                    _analyze_one_original, r, c, fetch
                )
            )
            for ref in originals
        ]
    )

    eligibility: dict[str, Any] = {}
    visual: dict[str, Any] = {}
    structural: dict[str, Any] = {}
    primary_reference_id: Optional[str] = None
    reference_visuals: dict[str, dict[str, Any]] = {}
    reference_embeddings: dict[str, dict[str, Any]] = {}
    reference_semantics: dict[str, dict[str, Any]] = {}

    for ref, res in zip(originals, analysis_results):
        rid = str(ref.id)
        eligibility[rid] = res["eligibility"]

        if res["visual"] is not None:
            reference_visuals[rid] = res["visual"]
        if res["embedding"] is not None:
            reference_embeddings[rid] = res["embedding"]
        if res["usable_primary"] and primary_reference_id is None:
            primary_reference_id = rid
            visual = dict(res["visual"])
            structural = res["structural"]

        if res["semantic"] is not None:
            reference_semantics[rid] = res["semantic"]

    if not visual:
        visual = {
            "status": UNKNOWN,
            "reason": "no_analyzable_reference",
            "coat": _unknown_field("no_segmentation_available"),
        }
    else:
        fused = _fuse_reference_traits(
            source_reference_ids=source_ids,
            reference_visuals=reference_visuals,
            reference_embeddings=reference_embeddings,
            reference_semantics=reference_semantics,
        )
        visual.update(fused)
    if not structural:
        structural = {"status": UNKNOWN, "reason": "no_analyzable_reference"}

    # ── VLM 시맨틱 패스 (자체 네임스페이스; 결정론적 필드를 덮지 않는다) ──
    semantic_status = "skipped_vlm_disabled"
    if vlm_identity.is_enabled():
        semantic_payload = _fuse_semantic_traits_payload(reference_semantics)
        if semantic_payload:
            visual["semantic_traits"] = semantic_payload
            semantic_status = "present"
        else:
            visual["semantic_traits"] = _unknown_field("vlm_analysis_failed")
            semantic_status = "failed"
    else:
        visual["semantic_traits"] = _unknown_field("vlm_disabled")

    status = STATUS_COMPLETE if primary_reference_id else STATUS_PARTIAL
    if primary_reference_id:
        visual["primary_reference_id"] = primary_reference_id
        structural["primary_reference_id"] = primary_reference_id

    row: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "pet_id": pid,
        "user_id": uid,
        "content_id": (originals[0].content_id or None),
        "version": 1,
        "status": status,
        "source_reference_ids": source_ids,
        "reference_eligibility": eligibility,
        "visual_identity": visual,
        "structural_identity": structural,
        "completeness": _completeness(visual, structural, semantic_status),
        "analyzer_versions": versions,
        "created_at": _now_iso(),
    }

    for _ in range(3):
        rows = await _profile_rows(pid)
        row["version"] = (max((int(r.get("version") or 0) for r in rows), default=0)) + 1
        ok, err = await _insert_profile_row(row)
        if ok:
            return _to_profile(row)
        last_err = err

    logger.error("신원 프로필 기록 실패 (pet=%s): %s", pid, last_err)
    raise PetIdentityError(
        "IDENTITY_PROFILES_UNAVAILABLE", "신원 프로필을 저장하지 못했습니다.", status=503
    )
