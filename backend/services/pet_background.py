"""
펫 적응형 중립 배경 (Pet-adaptive neutral background) — 정본 계보당 **하나**.

── 왜 필요한가 ─────────────────────────────────────────────────────────────
정본/키프레임 클린 플레이트는 지금까지 모든 펫에 같은 회색 RGB(200,200,200)을
썼다. 하류 매트(motion_delivery_service.matte_bgmodel)는 배경과의 채널 거리로
키잉하므로, 배경과 비슷한 밝기의 털(음영진 흰 털/크림/은회색)은 약해진다.
배경을 펫의 실제 코트 색에서 멀리 고르면 같은 매트가 더 잘 분리한다.

── 계약 ────────────────────────────────────────────────────────────────────
  * 선택은 **정본 빌드 시점에 한 번**이고 pet_canonical_versions.output_spec 에
    박제된다 (스키마 변경 없음). 키프레임·모션은 자기 정본의 결정을 **읽기만**
    한다 — 단계마다 다시 고르지 않는다.
  * 입력은 신원 프로필의 코트 팔레트(색 이름 + 비율)다. 평균 명도만 쓰지 않는다:
    흑백 얼룩 펫은 평균이 "중간"으로 나온다.
  * 품종 로직 없음, 다른 모델 호출 없음, 순수 결정론.
  * 팔레트는 고정된 중립 회색 4단계 — 순흑/순백은 없다.
  * 배경 계약(단일 평탄 무광, 그라디언트/바닥/그림자/반사 없음)은 그대로다.
    여기서는 **톤만** 고른다. 매트 알고리즘/임계값은 건드리지 않는다.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

SELECTOR_VERSION = "pet-background-v1"

#: (label, RGB, 프롬프트에 쓰는 톤 표현). 순서가 곧 동점 시 우선순위다 —
#: 첫 항목(light)이 지금까지의 고정 배경이라, 근거가 없으면 동작이 바뀌지 않는다.
BACKGROUND_PALETTE: tuple[tuple[str, tuple[int, int, int], str], ...] = (
    ("light", (200, 200, 200), "light-gray"),
    ("medium", (152, 152, 152), "medium-gray"),
    ("medium_dark", (112, 112, 112), "medium-dark gray"),
    ("dark", (80, 80, 80), "dark-gray"),
)

DEFAULT_LABEL = "light"

#: 이 비율 이상인 코트 색만 "지켜야 하는 색"이다 (analyze_visual_identity 의
#: dominant 기준과 같은 값). 그 아래는 평균 분리에만 기여한다.
SIGNIFICANT_FRACTION = 0.10

#: 그림자 혼동 대역 — 무채색이면서 배경보다 어둡되 아주 어둡지는 않은 색은
#: 하류 매트가 "배경의 감광 버전(그림자)"으로 볼 수 있다. 그런 색에게 그 배경은
#: 분리가 0 인 것으로 친다. 값은 motion_delivery_service 의 그림자 판정 기본값
#: (0.35 / 0.97)을 **읽기 전용 거울**로 옮긴 것이다 — 그 임계 자체는 바꾸지 않는다.
_NEUTRAL_MAX_CHANNEL_SPREAD = 24
_SHADOW_MIN_LUMA_RATIO = 0.35
_SHADOW_MAX_LUMA_RATIO = 0.97


def _palette_entry(label: str) -> Optional[tuple[str, tuple[int, int, int], str]]:
    return next((e for e in BACKGROUND_PALETTE if e[0] == label), None)


def prompt_tone(label: Optional[str]) -> str:
    """배경 label → 프롬프트 톤 표현. 모르는 label 은 기본(light-gray)."""
    entry = _palette_entry(str(label or "")) or _palette_entry(DEFAULT_LABEL)
    return entry[2]  # type: ignore[index]


def _luma(rgb: Sequence[int]) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def _named_rgb(name: str) -> Optional[tuple[int, int, int]]:
    from .pet_identity_service import _NAMED_COAT_COLORS

    for known, rgb in _NAMED_COAT_COLORS:
        if known == name:
            return (int(rgb[0]), int(rgb[1]), int(rgb[2]))
    return None


def _coat_colors(visual_identity: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    신원 프로필 코트 팔레트 → [{name, rgb, fraction}] (비율 합 1 로 정규화).

    융합 프로필의 팔레트는 색 **이름**과 비율만 담는다 — 이름은 고정 기준 RGB
    (pet_identity_service._NAMED_COAT_COLORS)로 되돌린다. 단일 레퍼런스 형태처럼
    항목에 rgb 가 직접 있으면 그것을 쓴다.
    """
    coat = (visual_identity or {}).get("coat") or {}
    if not isinstance(coat, dict) or coat.get("status") != "measured":
        return []
    merged: dict[str, dict[str, Any]] = {}
    for entry in coat.get("palette") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        try:
            fraction = float(entry.get("fraction") or 0.0)
        except (TypeError, ValueError):
            continue
        if not name or fraction <= 0:
            continue
        raw_rgb = entry.get("rgb")
        rgb: Optional[tuple[int, int, int]] = None
        if isinstance(raw_rgb, (list, tuple)) and len(raw_rgb) == 3:
            try:
                rgb = (int(raw_rgb[0]), int(raw_rgb[1]), int(raw_rgb[2]))
            except (TypeError, ValueError):
                rgb = None
        if rgb is None:
            rgb = _named_rgb(name)
        if rgb is None:
            continue
        # 같은 이름이 여러 번 나오면(단일 레퍼런스 median-cut) 기준 RGB 로 합친다.
        slot = merged.setdefault(name, {"name": name, "rgb": _named_rgb(name) or rgb, "fraction": 0.0})
        slot["fraction"] += fraction
    total = sum(c["fraction"] for c in merged.values())
    if total <= 0:
        return []
    colors = [
        {"name": c["name"], "rgb": list(c["rgb"]), "fraction": round(c["fraction"] / total, 4)}
        for c in merged.values()
    ]
    return sorted(colors, key=lambda c: (-c["fraction"], c["name"]))


def _separation(color_rgb: Sequence[int], gray_rgb: Sequence[int]) -> float:
    """
    코트 색 하나가 이 배경에서 얼마나 떨어지는가 — 하류 매트와 같은 척도
    (채널별 차이의 최댓값). 그림자 혼동 대역의 무채색은 0 이다.
    """
    distance = float(max(abs(int(c) - int(g)) for c, g in zip(color_rgb, gray_rgb)))
    spread = max(color_rgb) - min(color_rgb)
    if spread <= _NEUTRAL_MAX_CHANNEL_SPREAD:
        ratio = _luma(color_rgb) / max(1.0, _luma(gray_rgb))
        if _SHADOW_MIN_LUMA_RATIO < ratio < _SHADOW_MAX_LUMA_RATIO:
            return 0.0
    return distance


def _decision(label: str, reason: str, **extra: Any) -> dict[str, Any]:
    entry = _palette_entry(label) or _palette_entry(DEFAULT_LABEL)
    return {
        "background_rgb": list(entry[1]),  # type: ignore[index]
        "background_label": entry[0],  # type: ignore[index]
        "selector_version": SELECTOR_VERSION,
        "reason": reason,
        **extra,
    }


def select_background(visual_identity: Optional[dict[str, Any]]) -> dict[str, Any]:
    """
    코트 팔레트 → 배경 결정 (정본 output_spec 에 그대로 저장되는 형태).

    규칙: 고정 팔레트의 각 회색에 대해
      1. worst = 유의미한 코트 색(비율 ≥ 10%) 중 **가장 덜 분리되는** 색의 분리
      2. mean  = 모든 코트 색의 비율 가중 평균 분리
    를 구하고 (worst, mean) 이 가장 큰 회색을 고른다. 동점은 팔레트 순서
    (light 우선). worst 를 먼저 보므로 흑백 얼룩 펫은 양쪽 색을 모두 지키는
    중간 톤으로 가고, 한 색이 지배적인 펫은 그 색의 반대편으로 간다.
    """
    colors = _coat_colors(visual_identity)
    if not colors:
        return _decision(DEFAULT_LABEL, "coat_palette_unavailable_default", coat_summary=[], tone_scores=[])

    significant = [c for c in colors if c["fraction"] >= SIGNIFICANT_FRACTION] or colors[:1]
    scores: list[dict[str, Any]] = []
    best: Optional[tuple[float, float, int]] = None
    best_label = DEFAULT_LABEL
    for order, (label, rgb, _tone) in enumerate(BACKGROUND_PALETTE):
        worst = min(_separation(c["rgb"], rgb) for c in significant)
        mean = sum(c["fraction"] * _separation(c["rgb"], rgb) for c in colors)
        scores.append(
            {
                "label": label,
                "rgb": list(rgb),
                "worst_separation": round(worst, 1),
                "mean_separation": round(mean, 1),
            }
        )
        key = (round(worst, 1), round(mean, 1), -order)
        if best is None or key > best:
            best, best_label = key, label

    return _decision(
        best_label,
        "max_worst_case_separation_from_coat_palette",
        coat_summary=colors,
        significant_colors=[c["name"] for c in significant],
        tone_scores=scores,
    )


def from_output_spec(output_spec: Optional[dict[str, Any]]) -> dict[str, Any]:
    """
    정본 output_spec → 그 계보의 배경 결정. 하류(키프레임/모션)가 쓰는 유일한 입구.

    결정이 저장되지 않은 정본(이 기능 이전 버전)은 지금까지의 고정 배경
    (clean_plate_service.background_rgb — 기본 200,200,200)을 그대로 쓴다.
    """
    stored = (output_spec or {}).get("background_selection")
    if isinstance(stored, dict):
        rgb = stored.get("background_rgb")
        if (
            isinstance(rgb, (list, tuple))
            and len(rgb) == 3
            and all(isinstance(v, int) and 0 < v < 255 for v in rgb)
        ):
            return {**stored, "background_rgb": [int(v) for v in rgb]}

    from . import clean_plate_service

    return {
        "background_rgb": list(clean_plate_service.background_rgb()),
        "background_label": DEFAULT_LABEL,
        "selector_version": None,
        "reason": "legacy_canonical_without_background_selection",
    }


def background_rgb(decision: Optional[dict[str, Any]]) -> Optional[tuple[int, int, int]]:
    rgb = (decision or {}).get("background_rgb")
    if isinstance(rgb, (list, tuple)) and len(rgb) == 3:
        return (int(rgb[0]), int(rgb[1]), int(rgb[2]))
    return None
