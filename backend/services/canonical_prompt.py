"""
정본 펫 프롬프트 빌더 (Phase 4) — 버전드.

── 원칙 ────────────────────────────────────────────────────────────────────
* 레퍼런스 이미지가 이미 신뢰성 있게 담고 있는 것을 말로 다시 그리지 않는다 —
  신원의 정본은 이미지다. 메타데이터는 **제약**으로만 쓴다.
* Phase 2 가 실제로 아는 값만 문장이 된다. UNKNOWN 은 절대 발명된 사실이 되지
  않는다 (unknown 필드는 그냥 침묵한다).
* 테마 어휘 금지 — 정본 펫은 PET ONLY 다. 테마는 하류 렌더링 레이어다.
  (테스트가 theme_catalog.ALL_THEME_KEYS 와의 비교로 강제한다.)
"""

from __future__ import annotations

from typing import Any

CANONICAL_PROMPT_VERSION = "canonical-prompt-v1"

#: 정본 출력 사양 — 프롬프트와 프로바이더 파라미터 양쪽에 쓰인다.
CANONICAL_OUTPUT_SPEC: dict[str, Any] = {
    "pose": "neutral standing or neutral sitting",
    "angle": "front three-quarter",
    "background": "plain solid neutral light-gray",
    "lighting": "even neutral",
    "style": "photorealistic, no stylization",
    "ratio": "1024:1024",
    "size": "1024x1024",
}

_BASE = (
    "Create a photorealistic canonical reference image of the exact same pet shown "
    "in the supplied reference photos.\n"
    "Preserve the pet's exact recognizable identity: facial proportions, coat colors, "
    "distinctive markings, ear shape, body proportions, paws and tail appearance, "
    "exactly as supported by the references. Do not invent markings or features that "
    "the references do not show.\n"
    "Show the full pet in a neutral natural standing or sitting pose, seen from a "
    "front three-quarter angle, camera at the pet's eye level, no extreme perspective. "
    "All visible legs and anatomy must be natural and consistent; paws, ears and tail "
    "visible where the pet's anatomy and the references support it. Neutral expression.\n"
    "Plain solid neutral light-gray background. Even neutral lighting. "
    "No accessories unless they are clearly part of the pet's identity in the references. "
    "No additional animals. No human. No objects. No text. No stylization."
)


def _known(value: Any) -> bool:
    """Phase 2 값이 '실제로 아는 값'인가 — unknown/dict-status-unknown 은 침묵."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() != "unknown"
    if isinstance(value, dict):
        return value.get("status") not in ("unknown", None) or (
            "status" not in value and bool(value)
        )
    if isinstance(value, list):
        return bool(value)
    return True


def _confident_traits(visual_identity: dict[str, Any]) -> list[str]:
    """측정/VLM 으로 실제 확인된 특성만 제약 문장으로 바꾼다."""
    lines: list[str] = []

    coat = visual_identity.get("coat") or {}
    if isinstance(coat, dict) and coat.get("status") == "measured":
        names = [
            c.get("name")
            for c in (coat.get("dominant_colors") or [])
            if isinstance(c, dict) and _known(c.get("name"))
        ]
        if names:
            lines.append(
                "The coat's dominant colors are "
                + ", ".join(n.replace("_", " ") for n in names)
                + ", as in the references."
            )

    semantic = visual_identity.get("semantic_traits") or {}
    traits = semantic.get("traits") if isinstance(semantic, dict) else None
    if isinstance(traits, dict):
        ears = traits.get("ears") or {}
        if isinstance(ears, dict) and _known(ears.get("shape")):
            lines.append(f"Ear shape: {ears['shape'].replace('_', ' ')}.")
        coat_t = traits.get("coat") or {}
        if isinstance(coat_t, dict) and _known(coat_t.get("marking_distribution")):
            lines.append(f"Markings: {coat_t['marking_distribution']}.")
        if isinstance(coat_t, dict) and _known(coat_t.get("length")):
            lines.append(f"Coat length: {coat_t['length']}.")
        if _known(traits.get("species")):
            lines.append(f"The pet is a {traits['species']}.")

    return lines


#: 다른 프롬프트 빌더(키프레임 등)가 같은 "아는 것만 말한다" 규칙을 재사용한다.
confident_trait_lines = _confident_traits


def build_canonical_prompt(
    *,
    visual_identity: dict[str, Any],
    structural_identity: dict[str, Any],  # noqa: ARG001 — v1 은 구조를 말로 옮기지 않는다 (레퍼런스가 담당)
    reference_roles: list[str],
) -> str:
    """레퍼런스 역할 + 확인된 특성 → 정본 프롬프트 (버전 CANONICAL_PROMPT_VERSION)."""
    parts = [_BASE]
    if reference_roles:
        parts.append(
            "The supplied references show, in order: "
            + ", ".join(r.replace("PRIMARY_", "").replace("_", " ").lower() for r in reference_roles)
            + "."
        )
    parts.extend(_confident_traits(visual_identity or {}))
    return "\n".join(parts)
