"""
정본 펫 프롬프트 빌더 (Phase 4) — 버전드.

── 원칙 ────────────────────────────────────────────────────────────────────
* 레퍼런스 이미지가 이미 신뢰성 있게 담고 있는 것을 말로 다시 그리지 않는다 —
  신원의 정본은 이미지다. 메타데이터는 **제약**으로만 쓴다.
* Phase 2 가 실제로 아는 값만 문장이 된다. UNKNOWN 은 절대 발명된 사실이 되지
  않는다 (unknown 필드는 그냥 침묵한다).
* 테마 어휘 금지 — 정본 펫은 PET ONLY 다. 테마는 하류 렌더링 레이어다.
  (테스트가 theme_catalog.ALL_THEME_KEYS 와의 비교로 강제한다.)

── v3 (2026-10-12) ─────────────────────────────────────────────────────────
실제 펫 테스트에서 코트 색 이동·체형 비율 변화·얼굴 변화가 보였다. v2 는
"레퍼런스를 보존하라"고만 말했고, 어떤 사진이 무엇의 근거인지, 이 개체의
어떤 특징이 확인됐는지를 말하지 않았다. v3 는:

  1. 사진별 기여 가이드 — 공급 순서 그대로 "Reference N" 으로, 세트의 뷰/
     가시성 근거에서 읽은 기여만 적는다 (canonical_reference_selector.
     reference_guide). 한 장이 모든 특징을 지배하지 않는다.
  2. 개체 신원 사실 — 실측 코트 팔레트 + 영역별 색 분포(coat_pattern) +
     VLM 이 실제 사진에서 본 얼굴/몸 무늬·고유 특징·눈/주둥이/발/꼬리
     묘사(형태 프로필의 레퍼런스별 관측치에서 다수결) — 아는 것만.
  3. 보존 대상과 바꿔도 되는 것을 분리 — 신원(얼굴 구조, 비율, 코트의
     색상·명암 관계·무늬 위치)은 보존, 자세/각도/그루밍/조명/구도는 개선 허용.
     품종 표준으로 이상화하지 않는다.

형태 프로필의 **수치 형태 클래스(torso/leg/head/muzzle/body_build/body_size)는
의도적으로 넣지 않는다** — 전부 포즈/원근/촬영 거리 의존이라는 caveat 가 붙어
있고, 틀린 "stocky"/"long" 한 마디가 곧 비율 드리프트다. 비율은 사진별
가이드(전신 사진이 비율의 근거)로 고정한다.

── v3.2 (2026-10-13) ────────────────────────────────────────────────────────
실제 펫 테스트에서 다리/발/귀/꼬리의 털 양이 과장되는(실제보다 풍성하고
'플러시 토이' 같은 실루엣으로 부풀려지는) 드리프트가 보였다. v3.1 은 코트의
"fuller/fluffier" 를 전신적으로만 금지했지만, 다리·발·귀·꼬리처럼 포즈·원근에
따라 두께가 쉽게 달라 보이는 부위를 따로 짚지 않았다. v3.2 는:

  1. 믿을 만한(잘리지 않은) 서있는 측면/3-4 뷰 전신 레퍼런스가 있으면, 그
     사진을 다리 두께·발 크기·부위별(다리/발/귀/꼬리) 털 양의 근거로 삼는다.
  2. 그 양과 분포를 그대로 재현한다 — 풍성하게도, 가늘게도 바꾸지 않는다.
     원래 털이 많은 개체는 그대로 풍성해야 한다(일괄 "다리를 가늘게" 규칙은
     넣지 않는다). 카메라 각도·거리·자세 차이는 겉보기 두께를 바꾸므로,
     픽셀 치수 일치가 아니라 사진이 보여주는 비율로 비교한다.

기존 전신 사진별 가이드·레퍼런스 순서·형태 프로필 빌더는 바꾸지 않는다 — 이
제약은 전부 프롬프트 문구 추가일 뿐이다.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Optional

# v2 (2026-09-17): 그림자 금지 절 추가. 모델이 그려 넣던 접지(contact)/투영
# (cast) 그림자가 누끼 알파로 살아남아 키프레임·모션까지 얼룩으로 번졌다 —
# 알파 쪽 억제(vitmatte_service)와 **짝을 이루는** 생성 측 제약이다.
# v3 (2026-10-12): 사진별 기여 가이드 + 개체 신원 사실 + 보존/개선 분리 (위).
# v3.2 (2026-10-13): 다리/발/귀/꼬리 털 양 보존 — 전신 레퍼런스가 권위, 과장(풍성화)도
# 일괄 가늘게도 금지 (위 모듈 문서 참고).
# 버전을 올리는 이유: 멱등 게이트가 prompt_version 을 비교한다 — 안 올리면
# 예전 문구로 만든 정본이 새 문구 아래에서도 영원히 재사용된다.
CANONICAL_PROMPT_VERSION = "canonical-prompt-v3.2"

#: 정본 출력 사양 — 프롬프트와 프로바이더 파라미터 양쪽에 쓰인다.
CANONICAL_OUTPUT_SPEC: dict[str, Any] = {
    "pose": "preserve reference-supported natural posture",
    "angle": "front three-quarter",
    "background": "plain solid neutral light-gray, no contact or cast shadow",
    "lighting": "even neutral",
    "style": "photorealistic, no stylization",
    "ratio": "1024:1024",
    "size": "1024x1024",
}

#: 배경 톤 기본 표현 — pet_background 가 다른 톤을 고르지 않은 경우.
DEFAULT_BACKGROUND_TONE = "light-gray"

UNKNOWN = "unknown"


def _with_tone(template: str, background_tone: Optional[str]) -> str:
    """프롬프트 베이스의 배경 톤 자리를 채운다 — 플레이트 색과 말이 어긋나지 않게."""
    return template.replace("{background_tone}", background_tone or DEFAULT_BACKGROUND_TONE)


#: 배경 톤 자리({background_tone})가 있는 템플릿. _BASE 는 기본 톤으로 채운 값이다.
#: 문단 순서: 과제 → 보존(신원) → 개선 허용(표현) → 구도/배경 → 금지.
_BASE_TEMPLATE = (
    "Create a premium photorealistic canonical reference portrait of the exact same "
    "individual pet shown in the supplied reference photos. The photos are the authority "
    "for this pet's identity; the text below only adds constraints that were verified "
    "from those photos.\n"
    "Preserve the pet's exact recognizable identity: facial structure and proportions, "
    "eye shape and spacing, muzzle length and shape, ear shape and set, head-to-body "
    "ratio, leg length relative to the body, torso length, overall build, paws and tail "
    "appearance, and the exact coat — its natural hue and saturation, how its lighter "
    "and darker areas relate to each other, where each color sits on the body, and "
    "every marking in its real position and shape. Do not idealize the pet toward a "
    "breed standard; do not slim, bulk, lengthen or shorten the body or legs; do not "
    "smooth away asymmetries; do not shift coat colors toward a more typical shade. "
    "Do not invent markings, colors or features that the references do not show.\n"
    "Presentation may improve: show the pet calm and alert, with clean studio-quality "
    "lighting and a premium, well-composed portrait. Keep the coat length, clip pattern "
    "and fur density exactly as in the photos — a short-clipped body stays short-clipped, "
    "and longer fringes stay only where the photos show them; do not make the coat "
    "fuller, fluffier, more uniform or more show-groomed than it is. Keep the body as "
    "lean or as sturdy as the full-body photo shows; do not add bulk. "
    "Where a reliable, uncropped standing side or three-quarter full-body reference is "
    "available, treat it as the authority for how thick the legs and paws look and for "
    "how much fur covers the legs, paws, ears and tail; reproduce that amount and its "
    "distribution exactly, not a breed-typical or idealized amount. A different camera "
    "angle, distance or pose changes how thick a limb looks, so compare proportions "
    "rather than requiring identical pixel measurements. Do not enlarge the legs, paws, "
    "ears or tail into a fuller, fluffier or plush-toy silhouette than the photos show, "
    "and do not slim or thin them either — a naturally fluffy-legged pet must stay "
    "exactly as fluffy as its photos show, and a naturally slim or short-coated pet must "
    "stay exactly that slim. Show the full pet "
    "including all legs and paws, in the natural posture most strongly supported by the "
    "reference photos. Do not change a clearly standing pet into a sitting pose, or a "
    "clearly sitting pet into a standing pose. A different camera angle or a better, "
    "more composed posture than the originals is fine as long as the pet itself is "
    "unchanged. "
    "Show the pet from a front three-quarter angle, camera at the pet's eye level, "
    "no extreme perspective. "
    "All visible legs and anatomy must be natural and consistent; paws, ears and tail "
    "visible where the pet's anatomy and the references support it. Neutral expression.\n"
    "Plain solid neutral {background_tone} background: a single flat tone with no gradient, "
    "no floor plane and no horizon line. Even neutral lighting. "
    "No contact shadow under the pet, no cast shadow on the background, no reflection "
    "and no darkening anywhere around the paws — the pet must be the only thing in the "
    "frame that is not the flat background. "
    "No accessories unless they are clearly part of the pet's identity in the references. "
    "No additional animals. No human. No objects. No text. No stylization."
)
_BASE = _with_tone(_BASE_TEMPLATE, None)


def _known(value: Any) -> bool:
    """Phase 2 값이 '실제로 아는 값'인가 — unknown/dict-status-unknown 은 침묵."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() != UNKNOWN
    if isinstance(value, dict):
        return value.get("status") not in (UNKNOWN, None) or (
            "status" not in value and bool(value)
        )
    if isinstance(value, list):
        return bool(value)
    return True


def _text(value: Any) -> Optional[str]:
    """아는 문자열이면 정리해서, 아니면 None."""
    if isinstance(value, str) and _known(value):
        return " ".join(value.strip().split())
    return None


def _confident_traits(visual_identity: dict[str, Any]) -> list[str]:
    """측정/VLM 으로 실제 확인된 특성만 제약 문장으로 바꾼다 (v2 와 동일 — 키프레임 공유)."""
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
#: 키프레임 로직은 이 변경의 범위 밖이다 — v2 출력 그대로.
confident_trait_lines = _confident_traits


def background_spec_text(background_tone: Optional[str] = None) -> str:
    """output_spec["background"] 값 — 선택된 톤으로."""
    return _with_tone("plain solid neutral {background_tone}, no contact or cast shadow", background_tone)


# ══════════════════════════════════════════════════════════════════════════
# v3.1 — 개체 신원 사실 (아는 것만, **근거로 고른다**)
# ══════════════════════════════════════════════════════════════════════════
#
# v3 은 레퍼런스별 VLM 관측을 다수결(_majority)로 합쳤고, 신원 프로필의 융합
# 문자열은 사전순 동률 처리(max(count, value))였다. apricot 개 실사에서 그
# 결과는: 세 사진이 전부 "apricot" 이라 했는데 색 줄이 침묵했고(세 문자열이
# 달라 동률), 가슴의 흰 반점이 "no markings" 에 사전순으로 졌고, 꼬리는 옆
# 사진 한 장의 "short" 가 위 사진의 긴 깃털 묘사를 사전순으로 이겼다.
#
# v3.1 규칙:
#   * 색 단어는 사진별 관측을 **토큰 단위로 합산**한다 — 수식어(light/pale…)를
#     벗긴 색 이름이 2장 이상에서 보이면 채택, 지배색 언급이 많은 순.
#   * 서술 특성은 **그 특성을 실제로 보여 주는 사진**(가이드의 뷰/가시성)이
#     고른다. 근거가 같은 사진끼리 말이 다르면 — 서술형은 둘 다 적고(구성),
#     범주형(눈 색·꼬리 끝·질감…)은 **침묵**한다. 사전순은 어디에도 없다.
#   * 가슴/몸통/분포는 따로 라벨을 붙여 적는다 — "가슴에 흰 반점" 과 "등·옆구리에
#     무늬 없음" 은 모순이 아니다.
#   * 꼬리 길이 단어(short/long…)는 꼬리가 보이는 사진 2장 이상이 같은 말을
#     할 때만 남긴다. 아니면 벗기고 "길이는 사진 그대로"라고 말한다. 위에서
#     본 사진은 자세·깃털·색의 근거이지 길이의 근거가 아니다.
#   * 한 장의 믿을 만한 사진에서만 보이는 고유 특징은 그대로 남긴다 (다수결 없음).

import re

_COLOR_MODIFIERS = frozenset(
    "light pale dark deep bright rich soft warm cool medium slightly very mostly solid uniform mixed "
    "muted dull vivid and with or a of the tone tones toned coat colored coloured shade shades".split()
)
_LENGTH_WORDS = re.compile(r"\b(short|long|medium[- ]length|stubby|docked|bobbed)\b", re.IGNORECASE)

#: 서술형(둘 다 적어도 되는) 특성 — 근거 동률이면 최대 이만큼 구성한다.
_COMPOSE_MAX = 2


def _color_key(phrase: Any) -> Optional[str]:
    """'light apricot' → 'apricot', 'golden tan' → 'golden tan'. 모르면 None."""
    text = _text(phrase)
    if not text:
        return None
    tokens = [t for t in re.split(r"[\s/,]+", text.lower()) if t]
    kept = [t for t in tokens if t not in _COLOR_MODIFIERS and t != UNKNOWN]
    return " ".join(kept) or None


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


class _Evidence:
    """가이드 항목 하나 → 그 사진이 어떤 특성의 근거가 될 수 있는지."""

    __slots__ = ("facets", "view", "overhead", "cropped")

    def __init__(self, entry: dict[str, Any]):
        from .canonical_reference_selector import CAVEAT_CROPPED, CAVEAT_OVERHEAD

        self.facets = set(entry.get("facets") or [])
        self.view = entry.get("view_label")
        caveats = set(entry.get("caveats") or [])
        self.overhead = CAVEAT_OVERHEAD in caveats
        self.cropped = CAVEAT_CROPPED in caveats

    def score(self, kind: str) -> int:
        from .canonical_reference_selector import FACE_VIEWS, PROPORTION_VIEWS

        f, v = self.facets, self.view
        face = ("face_visible" in f or v in FACE_VIEWS) and not self.overhead
        side = v in PROPORTION_VIEWS
        full = "full_body_visible" in f or v == "FULL_BODY" or side
        if kind == "face":
            return 3 if face else 0
        if kind == "ears":
            base = 2 if "ears_visible" in f else (1 if face else 0)
            return base + (1 if base and not self.overhead else 0)
        if kind == "chest":
            return (2 if face else 0) + (1 if full else 0)
        if kind == "torso":
            base = 2 if (full or self.overhead) else 0
            return base + (1 if base and not self.cropped else 0) + (1 if side else 0)
        if kind == "tail":
            base = 2 if "tail_visible" in f else 0
            return base + (1 if base and side else 0)
        if kind == "paws":
            base = 2 if "paws_visible" in f else 0
            return base + (1 if base and full else 0)
        return 0


def _evidence_map(reference_guide: Optional[list[dict[str, Any]]]) -> dict[str, _Evidence]:
    out: dict[str, _Evidence] = {}
    for entry in reference_guide or []:
        rid = entry.get("reference_id")
        if rid:
            out[str(rid)] = _Evidence(entry)
    return out


def _select(
    kind: str,
    values: list[tuple[str, Any]],
    evidence: dict[str, _Evidence],
    *,
    compose: bool,
    label_overhead: bool = False,
) -> Optional[str]:
    """
    [(reference_id, 관측 문자열)] → 근거 점수로 고른 문장.

    compose=True  : 최고 점수 사진의 묘사 + 같은 점수의 다른 묘사(최대 _COMPOSE_MAX).
    compose=False : 최고 점수 사진 하나. 같은 점수의 사진이 다른 말을 하면 None(침묵).
    사전순 동률 처리는 없다.
    """
    scored: list[tuple[int, int, str, str]] = []
    for order, (rid, raw) in enumerate(values):
        text = _text(raw)
        if not text:
            continue
        ev = evidence.get(str(rid))
        scored.append((ev.score(kind) if ev else 0, order, str(rid), text))
    if not scored:
        return None
    # 가이드가 있으면 이 특성의 근거가 **있는** 사진만 쓴다. 어느 사진도 근거로
    # 표시되지 않았는데 VLM 이 값을 봤다면(분류기가 facet 을 안 적은 경우) 근거
    # 없음으로 취급한다 — 하나로 합의될 때만 쓰고, 모순이면 침묵한다.
    known = [k for k in scored if k[0] > 0] if evidence else scored
    if not known:
        known = scored
        compose = False
    known.sort(key=lambda t: (-t[0], t[1]))
    top_score = known[0][0]
    top = [k for k in known if k[0] == top_score]
    distinct: list[tuple[str, str]] = []
    for _s, _o, rid, text in top:
        if all(_norm(text) != _norm(t) for _r, t in distinct):
            distinct.append((rid, text))
    # 근거(가이드)가 전혀 없으면 구성하지 않는다 — 무엇이 맞는지 가릴 수 없는
    # 모순을 이어 붙이느니 침묵한다. 프로덕션 빌드에는 항상 가이드가 있다.
    if not compose or not evidence:
        return distinct[0][1] if len(distinct) == 1 else None
    parts: list[str] = []
    for rid, text in distinct[:_COMPOSE_MAX]:
        ev = evidence.get(rid)
        parts.append(f"{text} (seen from above)" if label_overhead and ev and ev.overhead else text)
    return "; ".join(parts)


def _observations(morphology_profile: Any, evidence: dict[str, _Evidence]) -> dict[str, dict[str, Any]]:
    """형태 프로필의 레퍼런스별 VLM traits — 가이드가 있으면 **공급된 사진만**."""
    raw = getattr(morphology_profile, "reference_observations", None)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for rid in sorted(raw):
        if evidence and str(rid) not in evidence:
            continue
        entry = raw.get(rid) or {}
        sem = entry.get("semantic_traits") if isinstance(entry, dict) else None
        traits = (sem or {}).get("traits") if isinstance(sem, dict) else None
        if isinstance(traits, dict):
            out[str(rid)] = traits
    return out


def _field(traits: dict[str, Any], *path: str) -> Any:
    cur: Any = traits
    for key in path:
        cur = cur.get(key) if isinstance(cur, dict) else None
    return cur


def _coat_words(observations: dict[str, dict[str, Any]]) -> list[str]:
    """
    사진별 VLM 색 묘사 → 2장 이상이 말한 색 이름(지배색 언급 많은 순).
    사진이 한 장뿐이면 그 사진의 색. 아무것도 겹치지 않으면 지배색 합집합.
    """
    support: dict[str, set[str]] = {}
    dominant: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    n = 0
    for rid, traits in observations.items():
        coat = traits.get("coat") if isinstance(traits.get("coat"), dict) else {}
        dom = [k for k in (_color_key(x) for x in (coat.get("dominant_colors") or [])) if k]
        sec = [k for k in (_color_key(x) for x in (coat.get("secondary_colors") or [])) if k]
        if not dom and not sec:
            continue
        n += 1
        for key in dom:
            support.setdefault(key, set()).add(rid)
            dominant[key] = dominant.get(key, 0.0) + 1.0
            first_seen.setdefault(key, len(first_seen))
        for key in sec:
            support.setdefault(key, set()).add(rid)
            dominant.setdefault(key, 0.0)
            first_seen.setdefault(key, len(first_seen))
    if not support:
        return []
    keys = [k for k, s in support.items() if len(s) >= 2] if n >= 2 else list(support)
    if not keys:
        keys = [k for k, d in dominant.items() if d > 0]
    keys.sort(key=lambda k: (-dominant.get(k, 0.0), -len(support[k]), first_seen[k]))
    return keys[:4]


def _palette_names(entries: Any) -> list[str]:
    """실측 팔레트 이름 — 저신뢰(한 장짜리) 항목은 뺀다."""
    out: list[str] = []
    for c in entries or []:
        if not isinstance(c, dict) or not _known(c.get("name")):
            continue
        if str(c.get("confidence") or "").lower() == "low":
            continue
        name = str(c["name"]).replace("_", " ")
        if name not in out:
            out.append(name)
    return out


def _fused_value(field: Any) -> Optional[str]:
    """융합 문자열 특성({status: fused|vlm, value}) → 값 또는 None."""
    if not isinstance(field, dict) or field.get("status") in (UNKNOWN, None):
        return None
    return _text(field.get("value"))


def _coat_items(visual_identity: dict[str, Any], observations: dict[str, dict[str, Any]]) -> list[tuple[str, Optional[str]]]:
    """(전체 문장, 컴팩트 문장) 쌍. 사진에서 본 색 이름이 있으면 그것이 앞선다."""
    items: list[tuple[str, Optional[str]]] = []
    coat = visual_identity.get("coat") or {}
    measured = isinstance(coat, dict) and coat.get("status") == "measured"
    dominant = _palette_names(coat.get("dominant_colors")) if measured else []
    secondary = _palette_names(coat.get("secondary_colors")) if measured else []
    words = _coat_words(observations)

    if words:
        full = (
            "Coat as seen in the photos: " + ", ".join(words) + ". Keep this exact tone and its "
            "lightness; do not shift it darker, redder or more saturated."
        )
        if dominant:
            full += " (Measured pixel palette: " + ", ".join(dominant)
            full += ("; " + ", ".join(secondary) if secondary else "") + ".)"
        items.append((full, "Coat colors: " + ", ".join(words) + "."))
    elif dominant:
        full = "Coat colors measured from the photos: " + ", ".join(dominant) + "."
        if secondary:
            full += " Secondary tones: " + ", ".join(secondary) + "."
        full += " Keep these exact tones and their relative lightness; do not shift the hue."
        # 컴팩트는 지배색만 — Runway 1000자 예산에서 종/눈 색 줄이 밀려나지 않게.
        items.append((full, "Coat colors: " + ", ".join(dominant) + "."))

    pattern = _fused_value(visual_identity.get("coat_pattern"))
    if pattern:
        parts = [p.strip().replace("_", " ") for p in pattern.split("|")]
        if len(parts) == 3 and all(_known(p) for p in parts):
            # 영역 분포는 전체 프롬프트 전용(compact="") — 컴팩트 예산에서 종·눈 색보다
            # 앞에 서면 안 된다 (한 장 지지의 저신뢰 측정치다).
            if len(set(parts)) == 1:
                items.append((f"The coat reads as one {parts[0]} tone across the body.", ""))
            else:
                items.append(
                    (
                        "The coat is not one flat color: across the body its regions read as "
                        + ", ".join(parts)
                        + " — keep that regional variation where the references show it.",
                        "",
                    )
                )
    return items


def _strip_length(text: str) -> str:
    """길이 단어를 붙은 쉼표와 함께 걷어낸다 — 'short, held' → 'held', 'in long, wavy' → 'in wavy'."""
    out = re.sub(r"\s*,?\s*\b(short|long|medium[- ]length|stubby|docked|bobbed)\b,?", " ", text, flags=re.IGNORECASE)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,;.])", r"\1", out)
    out = re.sub(r"^[\s,;]+", "", out)
    return out.strip()


def _tail_line(
    observations: dict[str, dict[str, Any]], evidence: dict[str, _Evidence], morphology_profile: Any
) -> Optional[str]:
    values = [(rid, _field(t, "tail", "appearance")) for rid, t in observations.items()]
    known = [(rid, _text(v)) for rid, v in values if _text(v)]
    if not known:
        traits = ((getattr(morphology_profile, "profile", None) or {}).get("traits") or {})
        fallback = _fused_value(traits.get("tail_form"))
        if not fallback:
            return None
        # 융합 꼬리 형태는 한 장 지지일 수 있다 — 길이 단어는 믿지 않는다.
        return f"Tail: {_strip_length(fallback)}. Keep its length and carriage exactly as in the photos."
    # 길이 단어는 꼬리가 보이는 사진 2장 이상이 **같은** 말을 할 때만.
    tokens: dict[str, set[str]] = {}
    for rid, text in known:
        found = {m.group(1).lower().replace("-", " ") for m in _LENGTH_WORDS.finditer(text)}
        if found:
            tokens[rid] = found
    agreed = (
        len(tokens) >= 2
        and all(len(s) == 1 for s in tokens.values())
        and len(set.intersection(*tokens.values())) == 1
    )
    cleaned = known if agreed else [(rid, _strip_length(text)) for rid, text in known]
    cleaned = [(rid, t) for rid, t in cleaned if t]
    body = _select("tail", cleaned, evidence, compose=True, label_overhead=True)
    if not body:
        return None
    # 위에서 본 사진은 자세·깃털·색의 근거다 — 옆 사진이 1순위여도 그 묘사를
    # "(seen from above)" 로 덧붙인다 (길이 단어는 이미 벗겨졌다).
    if "(seen from above)" not in body:
        overhead = [
            (rid, t) for rid, t in cleaned
            if (ev := evidence.get(str(rid))) and ev.overhead and ev.score("tail") > 0
            and _norm(t) not in {_norm(p) for p in body.split("; ")}
        ]
        if overhead:
            body += f"; {overhead[0][1]} (seen from above)"
    line = f"Tail: {body}."
    if not agreed:
        line += " Keep its length and carriage exactly as in the photos."
    return line


def _fact_items(
    visual_identity: dict[str, Any],
    morphology_profile: Any = None,
    reference_guide: Optional[list[dict[str, Any]]] = None,
) -> list[tuple[str, Optional[str]]]:
    """(전체 문장, 컴팩트 문장) 목록. 컴팩트가 None 이면 전체 문장을 상한 이하일 때 싣고,
    "" 이면 컴팩트에 싣지 않는다. 순서가 곧 중요도다."""
    vi = visual_identity or {}
    evidence = _evidence_map(reference_guide)
    obs = _observations(morphology_profile, evidence)
    semantic = vi.get("semantic_traits") or {}
    traits = semantic.get("traits") if isinstance(semantic, dict) else {}
    traits = traits if isinstance(traits, dict) else {}
    coat_t = traits.get("coat") if isinstance(traits.get("coat"), dict) else {}
    ears_t = traits.get("ears") if isinstance(traits.get("ears"), dict) else {}
    morph_traits = ((getattr(morphology_profile, "profile", None) or {}).get("traits") or {})

    def pick(kind: str, *path: str, compose: bool, label_overhead: bool = False) -> Optional[str]:
        return _select(
            kind, [(rid, _field(t, *path)) for rid, t in obs.items()], evidence,
            compose=compose, label_overhead=label_overhead,
        )

    items: list[tuple[str, Optional[str]]] = []
    items.extend(_coat_items(vi, obs))

    species = _text(traits.get("species")) or pick("torso", "species", compose=False) or pick("face", "species", compose=False)
    if species:
        items.append((f"The pet is a {species}.", None))

    # ── 얼굴 / 가슴 / 몸통 — 라벨을 붙여 따로 적는다 ───────────────────────
    facial = pick("face", "face", "facial_markings", compose=True) or _fused_value(vi.get("facial_markings"))
    if facial:
        items.append((f"Facial markings seen in the photos: {facial}.", None))
    chest = pick("chest", "body", "chest_markings", compose=True)
    torso = pick("torso", "body", "torso_markings", compose=True)
    distribution = pick("torso", "coat", "marking_distribution", compose=True)
    if chest:
        items.append((f"Chest markings: {chest}.", None))
    if torso:
        items.append((f"Back and side markings: {torso}.", None))
    if distribution:
        items.append((f"Coat pattern across the body: {distribution}.", None))
    if not (chest or torso or distribution):
        body = _fused_value(vi.get("body_markings"))
        if body:
            items.append((f"Body markings seen in the photos: {body}.", None))
        elif _text(coat_t.get("marking_distribution")):
            items.append((f"Markings: {_text(coat_t.get('marking_distribution'))}.", None))

    # ── 고유 특징 — 한 장에서만 보여도 남긴다 (합집합) ─────────────────────
    distinctive = vi.get("distinctive_features") or {}
    feats: list[str] = []
    if isinstance(distinctive, dict) and distinctive.get("status") not in (UNKNOWN, None):
        feats = [f for f in (_text(i.get("value")) for i in (distinctive.get("items") or []) if isinstance(i, dict)) if f]
    for _rid, t in obs.items():
        uniq = t.get("unique_features") if isinstance(t.get("unique_features"), list) else []
        for f in (_text(x) for x in uniq):
            if f and all(_norm(f) != _norm(g) for g in feats):
                feats.append(f)
    if feats:
        items.append(("Distinctive features to keep exactly: " + "; ".join(feats[:5]) + ".", None))

    eye = pick("face", "eyes", "color", compose=False)
    if eye:
        items.append((f"Eye color: {eye}.", f"Eye color: {eye}."))
    muzzle = pick("face", "face", "muzzle_color", compose=True)
    if muzzle:
        items.append((f"Muzzle: {muzzle}.", None))

    ear_shape = (
        _text(ears_t.get("shape"))
        or pick("ears", "ears", "shape", compose=False)
        or _fused_value(morph_traits.get("ear_form"))
    )
    if ear_shape:
        line = f"Ear shape: {ear_shape.replace('_', ' ')}."
        items.append((line, line))
    ear_markings = pick("ears", "ears", "color_markings", compose=True)
    if ear_markings:
        items.append((f"Ear hair and markings: {ear_markings}.", None))

    tail = _tail_line(obs, evidence, morphology_profile)
    if tail:
        items.append((tail, None))
    tail_tip = pick("tail", "tail", "tip_marking", compose=False)
    if tail_tip:
        items.append((f"Tail tip: {tail_tip}.", None))
    paws = pick("paws", "paws", "colors_markings", compose=True)
    if paws:
        items.append((f"Paws: {paws}.", None))

    length = _text(coat_t.get("length")) or pick("torso", "coat", "length", compose=False)
    if length:
        items.append((f"Coat length: {length}.", f"Coat length: {length}."))
    texture = pick("torso", "coat", "texture", compose=False)
    if texture:
        items.append((f"Coat texture: {texture}.", f"Coat texture: {texture}."))
    return items


def identity_fact_lines(
    visual_identity: dict[str, Any],
    morphology_profile: Any = None,
    reference_guide: Optional[list[dict[str, Any]]] = None,
) -> list[str]:
    """
    이 개체에 대해 **확인된** 사실만 문장으로. 순서가 곧 중요도다 (컴팩트
    프롬프트는 뒤에서부터 떨어뜨린다). unknown/빈값/근거 없는 동률은 문장이 되지
    않는다. 품종 추정은 절대 쓰지 않는다 — 품종 표준으로의 이상화가 곧 신원 손실이다.
    """
    return [full for full, _compact in _fact_items(visual_identity, morphology_profile, reference_guide)]


# ══════════════════════════════════════════════════════════════════════════
# v3 — 사진별 기여 가이드
# ══════════════════════════════════════════════════════════════════════════


def _role_summary(role: str) -> str:
    return role.replace("PRIMARY_", "").replace("_", " ").lower()


def _guide_from_roles(reference_roles: list[str]) -> list[dict[str, Any]]:
    """가이드 없이 역할만 받은 호출(구 계약) — 역할 이름만으로 최소 가이드를 만든다."""
    from .canonical_reference_selector import _CONTRIB_GENERIC, _ROLE_HEADLINE, NEUTRAL_ROLES

    return [
        {
            "reference_id": None,
            "role": role,
            "headline": _ROLE_HEADLINE.get(role)
            or ("supporting reference" if role in NEUTRAL_ROLES else "reference photo"),
            "view": None,
            "facets": [],
            "evidence": "none",
            "contributes": [_CONTRIB_GENERIC],
        }
        for role in reference_roles
    ]


def reference_guide_lines(reference_guide: list[dict[str, Any]]) -> list[str]:
    """가이드 → "Reference N — headline (view): authority for …" 줄들 + 결합 규칙."""
    if not reference_guide:
        return []
    lines = ["Reference photos, in the order supplied:"]
    for index, entry in enumerate(reference_guide, start=1):
        headline = str(entry.get("headline") or "reference photo")
        view = entry.get("view")
        label = f"{headline} ({view})" if view else headline
        contributes = [c for c in (entry.get("contributes") or []) if isinstance(c, str) and c]
        lines.append(f"Reference {index} — {label}: authority for " + "; ".join(contributes) + ".")
    if len(reference_guide) >= 2:
        lines.append(
            "Each photo is authoritative for what it clearly shows. Combine them; do not let "
            "one photo override another on features it does not show, and do not borrow a "
            "feature from one photo's angle or lighting that the others contradict."
        )
    else:
        lines.append(
            "Only one photo is available: keep every identity detail it shows and do not "
            "add anything it does not show."
        )
    return lines


def build_canonical_prompt(
    *,
    visual_identity: dict[str, Any],
    structural_identity: dict[str, Any],  # noqa: ARG001 — 수치 구조는 말로 옮기지 않는다 (모듈 문서 참고)
    reference_roles: list[str],
    background_tone: Optional[str] = None,
    reference_guide: Optional[list[dict[str, Any]]] = None,
    morphology_profile: Any = None,
) -> str:
    """
    레퍼런스 가이드 + 확인된 개체 사실 → 정본 프롬프트 (버전 CANONICAL_PROMPT_VERSION).

    reference_guide: canonical_reference_selector.reference_guide 결과 (공급 순서).
                     없으면 reference_roles 만으로 최소 가이드를 만든다.
    morphology_profile: PetMorphologyProfile — VLM 문자열 관측만 쓴다 (수치 클래스 제외).
    """
    parts = [_with_tone(_BASE_TEMPLATE, background_tone)]
    if reference_roles:
        parts.append(
            "The supplied references show, in order: "
            + ", ".join(_role_summary(r) for r in reference_roles)
            + "."
        )
    guide = reference_guide if reference_guide is not None else _guide_from_roles(reference_roles)
    parts.extend(reference_guide_lines(guide))
    facts = identity_fact_lines(visual_identity or {}, morphology_profile, guide)
    if facts:
        parts.append("Verified identity facts for this individual (from its own photos):")
        parts.extend(facts)
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════
# 컴팩트 프롬프트 (Phase 6.5 라이브 검증 결과) — Runway promptText ≤ 1000자
# ══════════════════════════════════════════════════════════════════════════
#
# 라이브 검증에서 확인된 실제 계약: Runway gen4_image 의 promptText 최대 1000자.
# 전체 내부 프롬프트를 자르는(truncate) 대신, 짧은 전용 변형을 쓴다:
# 신원의 정본은 어차피 레퍼런스 이미지다 — 프롬프트에는 고신뢰 제약만 싣는다.
# UNKNOWN 특성은 여기서도 문장이 되지 않는다. 줄은 중요도 순이고 상한에 맞을
# 때까지 **뒤에서부터** 떨어진다.

CANONICAL_COMPACT_PROMPT_VERSION = "canonical-prompt-compact-v3.2"

_COMPACT_BASE_TEMPLATE = (
    "Photorealistic canonical reference of the exact same pet in the supplied photos, which define its identity. "
    "Keep face/head, eye/muzzle/ear shape, head-to-body ratio, leg/torso length and build, and the exact coat "
    "(hue, shading, placement, every marking). "
    "Keep coat length, clip and density as in the photos (no fluffing); keep the body as lean as shown. "
    "Match leg/paw/ear/tail fur volume to a full-body reference (angle, not pixel size); "
    "no fluffing or slimming either way. "
    "No breed idealization; invent nothing unseen. "
    "Full body, reference-supported posture (no stand/sit switch), front 3/4 view at eye level, "
    "neutral expression, studio lighting, plain solid {background_tone} background (no floor plane). "
    "No contact or cast shadow; no accessories, other animals, humans, objects, text or stylization."
)
_COMPACT_BASE = _with_tone(_COMPACT_BASE_TEMPLATE, None)

#: 컴팩트 프롬프트에 실을 수 있는 사실 한 줄의 최대 길이 — 자유 서술(무늬 묘사)이
#: 길면 자르지 않고 **뺀다** (잘린 문장은 거짓 사실이 될 수 있다).
_COMPACT_FACT_MAX_CHARS = 110


def _compact_trait_lines(visual_identity: dict[str, Any]) -> list[str]:
    """고신뢰 특성만, 짧은 형태로. 실측 코트 색 + VLM 확인된 종/귀 모양 (v2 와 동일 — 키프레임 공유)."""
    lines: list[str] = []
    coat = (visual_identity or {}).get("coat") or {}
    if isinstance(coat, dict) and coat.get("status") == "measured":
        names = [
            str(c.get("name")).replace("_", " ")
            for c in (coat.get("dominant_colors") or [])
            if isinstance(c, dict) and _known(c.get("name"))
        ]
        if names:
            lines.append(f"Coat colors: {', '.join(names)}.")
    traits = ((visual_identity or {}).get("semantic_traits") or {}).get("traits") or {}
    if isinstance(traits, dict):
        if _known(traits.get("species")):
            lines.append(f"The pet is a {traits['species']}.")
        ears = traits.get("ears") or {}
        if isinstance(ears, dict) and _known(ears.get("shape")):
            lines.append(f"{str(ears['shape']).replace('_', ' ').capitalize()} ears.")
    return lines


_COMPACT_VIEW: dict[str, str] = {
    "front view": "front",
    "left side": "left side",
    "right side": "right side",
    "rear view": "rear",
    "front-left three-quarter view": "front-left 3/4",
    "front-right three-quarter view": "front-right 3/4",
    "face close-up": "face close-up",
    "full-body view": "full body",
    "overhead view": "overhead",
}

_COMPACT_HEADLINE: dict[str, str] = {
    "face reference": "face",
    "full-body reference": "full body",
    "three-quarter reference": "3/4 view",
    "left-side reference": "left side",
    "right-side reference": "right side",
    "front reference": "front",
    "markings reference": "markings",
    "rear reference": "rear",
    "tail reference": "tail",
    "the only reference photo": "the pet",
    "supporting reference": "the same pet",
    "reference photo": "the same pet",
}


def compact_reference_guide_line(reference_guide: Optional[list[dict[str, Any]]]) -> Optional[str]:
    """가이드 → "Photo 1: face (front); photo 2: full body (left side), tail. Each photo rules what it shows." """
    if not reference_guide:
        return None
    bits: list[str] = []
    for index, entry in enumerate(reference_guide, start=1):
        head = _COMPACT_HEADLINE.get(str(entry.get("headline") or ""), "the same pet")
        view = _COMPACT_VIEW.get(str(entry.get("view") or ""))
        extras = []
        facets = set(entry.get("facets") or [])
        if "tail_visible" in facets and head != "tail":
            extras.append("tail")
        if "distinct_markings_visible" in facets and head != "markings":
            extras.append("markings")
        if "paws_visible" in facets:
            extras.append("paws")
        text = head
        if view and view != head:
            text += f" ({view})"
        if extras:
            text += ", " + ", ".join(extras)
        bits.append(f"Photo {index}: {text}")
    return "; ".join(bits) + ". Each photo rules what it shows."


def compact_identity_lines(
    visual_identity: dict[str, Any],
    morphology_profile: Any = None,
    reference_guide: Optional[list[dict[str, Any]]] = None,
) -> list[str]:
    """컴팩트용 사실 줄 — identity_fact_lines 와 같은 출처/순서. 짧은 전용 문장이
    있으면 그것을, 없으면 전체 문장이 상한 이하일 때만 싣는다 (잘라서 거짓을 만들지 않는다)."""
    out: list[str] = []
    for full, compact in _fact_items(visual_identity or {}, morphology_profile, reference_guide):
        if compact == "":
            continue  # 전체 프롬프트 전용 줄
        line = compact or full
        if len(line) <= _COMPACT_FACT_MAX_CHARS:
            out.append(line)
    return out


def build_compact_canonical_prompt(
    *,
    visual_identity: dict[str, Any],
    max_chars: int = 1000,
    background_tone: Optional[str] = None,
    reference_guide: Optional[list[dict[str, Any]]] = None,
    morphology_profile: Any = None,
) -> str:
    base = _with_tone(_COMPACT_BASE_TEMPLATE, background_tone)
    lines: list[str] = []
    guide_line = compact_reference_guide_line(reference_guide)
    if guide_line:
        lines.append(guide_line)
    lines.extend(compact_identity_lines(visual_identity or {}, morphology_profile, reference_guide))

    while lines:
        prompt = " ".join([base] + lines)

        if len(prompt) <= max_chars:
            return prompt

        lines.pop()

    if len(base) <= max_chars:
        return base

    raise ValueError(
        f"_COMPACT_BASE exceeds max_chars: "
        f"{len(base)} > {max_chars}"
    )
