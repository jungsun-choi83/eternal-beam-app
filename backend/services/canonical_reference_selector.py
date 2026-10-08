"""
정본(Canonical) 입력 레퍼런스 선택 — 신원 우선 (Phase 2, Stage 2B).

── 정책 ───────────────────────────────────────────────────────────────────
사진 1장은 완전히 지원된다. 2–3장은 **선택적인 신원 보강**이다.

목표는 정면/측면/3Q 커버리지를 채우는 것이 아니다. **같은 개체라는 가장 믿을
만한 근거**를 주는 1–3장을 고르는 것이다. 우선순위:

  1. 신원 근거   2. 적격성   3. 품질   4. 상보적인 신원 정보
  5. (선택) 쓸모 있는 뷰 다양성   6. 중복 제거

다른 뷰는 신원 정보를 더할 때만 가치가 있다. 같은 뷰의 사진도 쓸모 있을 수 있다.
3자리를 채우려고 나쁜/중복 사진을 쓰지 않는다.

── 무엇을 읽는가 ──────────────────────────────────────────────────────────
세트의 레퍼런스별 분석(reference_analysis)만 읽는다. 세트 빌드와 역할 선택
(build_reference_set / select_roles)은 바꾸지 않고, 세트의 items 도 쓰지 않는다.
여기서 모델을 돌리지 않는다 — 결정론적이고 순수하다(resolve 콜백만 예외).

── 신호의 신뢰도 (Stage 2.0 프로브) ───────────────────────────────────────
* usable_for_identity, base_quality — 선택에 쓸 만큼 믿을 만하다.
* VLM 뷰/가시성 — VLM 이 켜져 있을 때만. 꺼져 있으면 뷰 커버리지를 **지어내지 않는다.**
* pHash — 거의-같은 이미지(재저장·약한 블러)만 잡는다. 크롭은 못 잡는다. 그래서
  **보수적인 중복 제거**에만 쓴다.
* HSV 히스토그램 / 결정론 임베딩 — 같은 개체의 근거로 **믿지 않는다** (노출만
  달라져도 "다른 동물" 수준으로 떨어진다). 3장 이상에서 한 장이 나머지 둘과
  동떨어졌을 때 그 한 장을 격리하는 데에만 쓴다.

**믿을 만한 쌍별(pairwise) 동일-개체 신호는 없다.** 그래서 여러 장이 뽑혀도
"신원이 서로 확인됐다"고 주장하지 않고, 그 사실을 결정 로그에 남긴다.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

SELECTOR_VERSION = "canonical-input-selector-v2-identity-first"

MAX_REFERENCES = 3

# ── 임계값 (잠정 — 합성 픽스처 기준. 실사 캘리브레이션 전이다) ──────────────────
#: 품질 바닥. base_quality 가 이보다 낮은 사진은 후보가 되지 않는다.
QUALITY_FLOOR = 0.35
#: pHash 해밍 거리가 이 이하면 "거의 같은 이미지"로 본다. 프로브: 재저장 0,
#: 약한 블러 4, 노출 변화 8 / 서로 다른 이미지(크롭·좌우 반전·다른 펫) 30 이상.
PHASH_NEAR_DUPLICATE_MAX = 10
#: 추가 후보의 최소 점수. 이보다 낮으면 "새 신원 가치가 부족하다"로 떨어진다.
#: 점수 = 0.6×품질 + 0.4×새 신원 단서 (+뷰 가산점). 새 단서가 없는 사진(같은 뷰,
#: 또는 VLM 꺼짐)은 품질 0.55 이상이어야 들어온다 — 바닥(0.35)을 겨우 넘는 사진을
#: 자리 채우기로 쓰지 않는다. 고유 무늬처럼 새 단서를 보여 주는 사진은 품질이
#: 바닥 근처여도 들어온다.
ADD_MIN_SCORE = 0.33
_ADD_QUALITY_WEIGHT = 0.6
_ADD_GAIN_WEIGHT = 0.4
_VIEW_BONUS = 0.10
_SEED_VISIBILITY_BONUS = 0.25

#: VLM 이 "보인다"고 말한 신원 단서의 가중치. 얼굴과 고유 무늬가 가장 크다.
FACET_WEIGHTS: dict[str, float] = {
    "face_visible": 1.0,
    "distinct_markings_visible": 1.0,
    "full_body_visible": 0.7,
    "left_side_visible": 0.4,
    "right_side_visible": 0.4,
    "ears_visible": 0.3,
    "tail_visible": 0.3,
    "paws_visible": 0.2,
}

_CONF_WEIGHT = {"high": 1.0, "medium": 0.75}

# ── 결정 로그의 상태 ────────────────────────────────────────────────────────
SELECTED = "selected"
DEGRADED_FALLBACK = "degraded_fallback"
DROPPED_INELIGIBLE = "dropped_ineligible"
DROPPED_LOW_QUALITY = "dropped_low_quality"
DROPPED_REDUNDANT = "dropped_redundant"
DROPPED_NO_IDENTITY_GAIN = "dropped_no_identity_gain"
DROPPED_UNRESOLVABLE = "dropped_unresolvable"

# ── 역할 ────────────────────────────────────────────────────────────────────
#: 선택된 레퍼런스가 정확히 한 장이고 그 한 장에 근거 있는 뷰 역할이 없을 때만.
ROLE_ONLY_AVAILABLE = "ONLY_AVAILABLE"
#: 근거 있는 뷰 역할이 없는 레퍼런스의 중립 역할. **중립 레퍼런스끼리만** 번호를
#: 매긴다(전체 순번이 아니다). 짧고 서로 달라 Runway 의 16자 태그로 줄여도 겹치지 않는다.
NEUTRAL_ROLES = ("SUPPORT_1", "SUPPORT_2", "SUPPORT_3")

#: VLM 근거가 있을 때 붙일 수 있는 기존 역할 — 출력 순서이기도 하다.
_SEMANTIC_ROLE_ORDER = (
    "PRIMARY_FACE",
    "PRIMARY_FULL_BODY",
    "PRIMARY_3Q",
    "PRIMARY_LEFT",
    "PRIMARY_RIGHT",
    "PRIMARY_FRONT",
    "PRIMARY_MARKINGS",
    "PRIMARY_BACK",
    "PRIMARY_TAIL",
)
#: 이보다 약한 역할 적합도는 "근거 있는 역할"로 치지 않는다.
_ROLE_MIN_FIT = 0.5

# ── 신원 신뢰도 (이 필드가 **주장하는 것**) ─────────────────────────────────
#: 한 장뿐이거나, 뽑힌 사진들이 서로 어긋나거나(ambiguous_mismatch), 저하 폴백이다.
CONFIDENCE_LOW = "low"
#: 2–3장이 뽑혔지만 **같은 개체라는 확인이 없다.** "서로 모순되지 않는다"는
#: "같은 개체로 확인됐다"가 아니다 — 캘리브레이션에서 서로 다른 펫의 쌍 대부분이
#: 모순 없이 통과했다.
CONFIDENCE_UNVERIFIED = "unverified"
#: 믿을 만한 동일-개체 확인 신호가 실제로 확인해 줬을 때만.
CONFIDENCE_NORMAL = "normal"

#: 쌍별 동일-개체를 **확인**해 주는 믿을 만한 신호가 있는가. 지금은 없다 — HSV
#: 히스토그램 일관성 라벨과 결정론 임베딩은 그 역할을 하지 못한다. 그런 신호가
#: 생기기 전에는 CONFIDENCE_NORMAL 이 나오지 않는다.
RELIABLE_PAIRWISE_IDENTITY_SIGNAL = False

MODE_SEMANTIC = "vlm_semantic"
MODE_DETERMINISTIC = "deterministic_no_vlm"


def _analysis(refset: Any, rid: str) -> dict[str, Any]:
    return (getattr(refset, "reference_analysis", None) or {}).get(rid) or {}


def _usable(a: dict[str, Any]) -> bool:
    return bool((a.get("eligibility") or {}).get("usable_for_identity"))


def _quality(a: dict[str, Any]) -> float:
    value = (a.get("quality") or {}).get("base_quality")
    return float(value) if isinstance(value, (int, float)) else 0.0


def _likely_mismatch(a: dict[str, Any]) -> bool:
    return (a.get("consistency") or {}).get("label") == "LIKELY_MISMATCH"


def _visibility(a: dict[str, Any]) -> dict[str, Any]:
    return (a.get("classification") or {}).get("visibility") or {}


def _facets(a: dict[str, Any]) -> set[str]:
    vis = _visibility(a)
    return {key for key in FACET_WEIGHTS if vis.get(key) == "yes"}


def _occlusion_factor(a: dict[str, Any]) -> float:
    vis = _visibility(a)
    factor = 1.0
    if vis.get("heavy_occlusion") == "yes":
        factor *= 0.5
    if vis.get("person_obstruction") == "yes":
        factor *= 0.5
    return factor


def _confident_view(a: dict[str, Any]) -> Optional[str]:
    c = a.get("classification") or {}
    view = str(c.get("view_label") or "UNKNOWN")
    if view == "UNKNOWN" or str(c.get("view_confidence") or "low") not in _CONF_WEIGHT:
        return None
    return view


def _has_semantics(a: dict[str, Any]) -> bool:
    return bool(_facets(a)) or _confident_view(a) is not None


def _phash_distance(a: dict[str, Any], b: dict[str, Any]) -> Optional[int]:
    """두 레퍼런스의 pHash 해밍 거리. 시그니처가 없거나 버전이 다르면 None."""
    sa = (a.get("eligibility") or {}).get("signature") or {}
    sb = (b.get("eligibility") or {}).get("signature") or {}
    if not sa.get("phash") or not sb.get("phash") or sa.get("version") != sb.get("version"):
        return None
    try:
        return bin(int(str(sa["phash"]), 16) ^ int(str(sb["phash"]), 16)).count("1")
    except ValueError:
        return None


def _semantic_role(a: dict[str, Any], taken: set[str]) -> Optional[str]:
    """
    이 레퍼런스에 **VLM 근거가 있는** 기존 역할 하나. 없으면 None (→ 중립 역할).

    세트의 role_fit 을 그대로 읽는다(바꾸지 않는다). "프레임에 잘리지 않았다"만으로
    인정되는 결정론적 전신 추정은 뷰 근거가 아니므로 역할로 쓰지 않는다.
    """
    from .pet_reference_set_service import role_fit

    if not a.get("classification") or not a.get("eligibility"):
        return None
    # 우선순위 순서로 **처음** 근거가 충분한 역할 — 얼굴이 보이는 정면 사진은
    # "front" 가 아니라 "face" 다 (예전 선택기의 우선순위와 같다).
    for role in _SEMANTIC_ROLE_ORDER:
        if role in taken:
            continue
        fit, basis = role_fit(role, a)
        if fit >= _ROLE_MIN_FIT and not basis.startswith("deterministic:"):
            return role
    return None


def select(
    refset: Any,
    *,
    active_ids: Optional[set[str]] = None,
    resolve: Optional[Callable[[str], Optional[str]]] = None,
) -> dict[str, Any]:
    """
    세트 → **하나의** 확정된 입력 목록 + 모든 레퍼런스에 대한 결정 로그.

    active_ids  지금 살아 있는(accepted) 원본 id. 주어지면 그 밖의 레퍼런스(사용자가
                뺀 사진 등)는 후보가 되지 않는다.
    resolve     레퍼런스를 실제로 쓸 수 있는지 확인한다(대장에 있고 바이트를 읽을 수
                있는가). 문제가 없으면 None, 있으면 이유 문자열. **선택을 확정하기
                전에** 부른다 — 쓸 수 없는 레퍼런스는 dropped_unresolvable 로 기록하고
                다음 후보를 본다. 확정된 목록에서 나중에 조용히 빠지는 일은 없다.

    반환: {selector_version, mode, selected: [{reference_id, role}], decisions,
           identity_agreement, identity_confidence, thresholds}
    """
    ids = sorted(dict.fromkeys(str(r) for r in (getattr(refset, "source_reference_ids", None) or [])))
    analyses = {rid: _analysis(refset, rid) for rid in ids}
    mode = MODE_SEMANTIC if any(_has_semantics(a) for a in analyses.values()) else MODE_DETERMINISTIC

    decisions: dict[str, dict[str, Any]] = {}
    for rid in ids:
        a = analyses[rid]
        decisions[rid] = {
            "status": None,
            "reason": None,
            "eligible": _usable(a),
            "base_quality": round(_quality(a), 4),
            "consistency_label": (a.get("consistency") or {}).get("label"),
            "identity_facets": sorted(_facets(a)),
            "view_label": _confident_view(a),
        }

    def drop(rid: str, status: str, reason: str, **extra: Any) -> None:
        decisions[rid].update({"status": status, "reason": reason, **extra})

    unresolvable: set[str] = set()

    def resolvable(rid: str) -> bool:
        if rid in unresolvable:
            return False
        reason = resolve(rid) if resolve else None
        if reason:
            unresolvable.add(rid)
            drop(rid, DROPPED_UNRESOLVABLE, str(reason))
            return False
        return True

    # ── 1. 후보 풀: 살아 있는 원본 ∧ 적격 ∧ 품질 바닥 이상 ────────────────────
    live = [rid for rid in ids if active_ids is None or rid in active_ids]
    for rid in ids:
        if rid not in live:
            drop(rid, DROPPED_INELIGIBLE, "not_an_active_original")
    pool: list[str] = []
    for rid in live:
        a = analyses[rid]
        if not _usable(a):
            reasons = (a.get("eligibility") or {}).get("reasons") or []
            drop(rid, DROPPED_INELIGIBLE, ",".join(map(str, reasons)) or "not_usable_for_identity")
        elif _quality(a) < QUALITY_FLOOR:
            drop(rid, DROPPED_LOW_QUALITY, f"base_quality<{QUALITY_FLOOR}")
        else:
            pool.append(rid)

    # ── 2. 일관성: 동떨어진 한 장만 격리한다 ─────────────────────────────────
    # 서로 맞는 사진이 2장 이상 있을 때에만 LIKELY_MISMATCH 를 "그 사진이 틀렸다"로
    # 읽는다. 그렇지 않으면(사진 2장의 대칭 불일치 등) 어느 쪽이 틀렸는지 말해 줄
    # 근거가 없다 — 버리지 않고, 모호하다고 기록한다.
    flagged = [rid for rid in pool if _likely_mismatch(analyses[rid])]
    agreeing = [rid for rid in pool if rid not in flagged]
    ambiguous: list[str] = []
    if flagged and len(agreeing) >= 2:
        for rid in flagged:
            drop(rid, DROPPED_INELIGIBLE, "likely_mismatch_outlier")
        pool = agreeing
    elif flagged:
        ambiguous = list(flagged)

    def visibility_score(rid: str) -> float:
        a = analyses[rid]
        total = sum(FACET_WEIGHTS[f] for f in _facets(a))
        return min(1.0, total / 2.0) * _occlusion_factor(a)

    def seed_score(rid: str) -> float:
        return round(_quality(analyses[rid]) + _SEED_VISIBILITY_BONUS * visibility_score(rid), 4)

    selected: list[str] = []

    # ── 3. 씨앗: 가장 강한 신원 기준점 (특정 각도를 강제하지 않는다) ───────────
    for rid in sorted(pool, key=lambda r: (-seed_score(r), r)):
        if resolvable(rid):
            selected.append(rid)
            decisions[rid].update(
                {"status": SELECTED, "reason": "identity_anchor", "seed_score": seed_score(rid)}
            )
            break

    # ── 4. 탐욕 추가: 새 신원 가치가 충분할 때만 ─────────────────────────────
    remaining = [rid for rid in pool if rid not in selected and rid not in unresolvable]
    while selected and remaining:
        covered: set[str] = set().union(*(_facets(analyses[s]) for s in selected))
        views = {v for s in selected if (v := _confident_view(analyses[s]))}

        scored: list[tuple[float, str, dict[str, Any]]] = []
        for rid in list(remaining):
            a = analyses[rid]
            distances = [d for s in selected if (d := _phash_distance(a, analyses[s])) is not None]
            nearest = min(distances) if distances else None
            if nearest is not None and nearest <= PHASH_NEAR_DUPLICATE_MAX:
                drop(
                    rid, DROPPED_REDUNDANT,
                    f"phash_distance<={PHASH_NEAR_DUPLICATE_MAX}",
                    min_phash_distance=nearest,
                )
                remaining.remove(rid)
                continue
            new_facets = sorted(_facets(a) - covered)
            gain = min(1.0, sum(FACET_WEIGHTS[f] for f in new_facets)) * _occlusion_factor(a)
            view = _confident_view(a)
            # 다른 뷰는 **새 신원 단서를 실제로 보여 줄 때만** 가산점을 받는다.
            view_bonus = (
                _VIEW_BONUS * _CONF_WEIGHT[str(a["classification"].get("view_confidence"))]
                if view and view not in views and gain > 0
                else 0.0
            )
            score = round(
                _ADD_QUALITY_WEIGHT * _quality(a) + _ADD_GAIN_WEIGHT * gain + view_bonus, 4
            )
            scored.append(
                (
                    score,
                    rid,
                    {
                        "add_score": score,
                        "identity_gain": round(gain, 4),
                        "new_identity_facets": new_facets,
                        "view_bonus": round(view_bonus, 4),
                        "min_phash_distance": nearest,
                    },
                )
            )
        if not scored:
            break
        scored.sort(key=lambda t: (-t[0], t[1]))
        score, rid, detail = scored[0]
        if score < ADD_MIN_SCORE:
            for _score, other, other_detail in scored:
                drop(other, DROPPED_NO_IDENTITY_GAIN, f"add_score<{ADD_MIN_SCORE}", **other_detail)
            remaining = []
            break
        remaining.remove(rid)
        if not resolvable(rid):
            continue
        if len(selected) >= MAX_REFERENCES:
            break
        selected.append(rid)
        decisions[rid].update(
            {
                "status": SELECTED,
                "reason": "adds_identity_evidence" if detail["identity_gain"] > 0 else "independent_quality_evidence",
                **detail,
            }
        )
        if len(selected) >= MAX_REFERENCES:
            for other in remaining:
                drop(other, DROPPED_NO_IDENTITY_GAIN, "selection_full")
            remaining = []

    # ── 5. 폴백: 후보가 하나도 남지 않았다 — 사진 1장은 언제나 허용된다 ─────────
    degraded = False
    if not selected:
        ranked = sorted(live, key=lambda r: (not _usable(analyses[r]), -_quality(analyses[r]), r))
        for rid in ranked:
            if rid in unresolvable:
                continue
            previous = decisions[rid]["status"]
            if resolvable(rid):
                selected.append(rid)
                degraded = True
                decisions[rid].update(
                    {
                        "status": DEGRADED_FALLBACK,
                        "reason": f"no_candidate_passed_gates (was {previous})",
                    }
                )
                break

    # ── 6. 역할: 근거 있는 기존 역할, 없으면 중립 ────────────────────────────
    roles: dict[str, str] = {}
    taken: set[str] = set()
    for rid in selected:
        role = _semantic_role(analyses[rid], taken) if mode == MODE_SEMANTIC else None
        if role:
            roles[rid] = role
            taken.add(role)
    unlabelled = [rid for rid in selected if rid not in roles]
    if len(selected) == 1 and unlabelled:
        roles[unlabelled[0]] = ROLE_ONLY_AVAILABLE
    else:
        for index, rid in enumerate(unlabelled):
            roles[rid] = NEUTRAL_ROLES[index]

    def output_order(rid: str) -> tuple[int, int]:
        role = roles[rid]
        if role in _SEMANTIC_ROLE_ORDER:
            return (0, _SEMANTIC_ROLE_ORDER.index(role))
        return (1, selected.index(rid))

    final = sorted(selected, key=output_order)
    for rid in final:
        decisions[rid]["role"] = roles[rid]

    # ── 7. 신원 합의: 주장할 수 있는 만큼만 ──────────────────────────────────
    kept_ambiguous = sorted(rid for rid in ambiguous if rid in selected)
    if len(final) < 2:
        agreement_status = "single_reference"
    elif kept_ambiguous:
        agreement_status = "ambiguous_mismatch"
    else:
        agreement_status = "not_contradicted"
    identity_agreement = {
        # 쌍별 동일-개체를 **확인**해 주는 믿을 만한 신호는 지금 없다.
        "reliable_pairwise_signal": RELIABLE_PAIRWISE_IDENTITY_SIGNAL,
        # "모순되지 않음"은 확인이 아니다 — 확인 신호가 실제로 확인했을 때만 참.
        "verified_same_individual": False,
        "signal_used": "hsv_hist_consistency_label (weak — outlier isolation only)",
        "status": agreement_status,
        "ambiguous_reference_ids": kept_ambiguous,
        "note": (
            "selected references disagree on coat colour distribution and no third "
            "reference can say which is wrong; both kept, identity not cross-confirmed"
            if kept_ambiguous
            else "multiple references are independent evidence, not cross-verified identity"
            if len(final) >= 2
            else "one reference only"
        ),
    }
    # 신뢰도는 **근거가 뒷받침하는 만큼만** 말한다. 여러 장이 뽑혔다는 사실이나
    # "서로 모순되지 않는다"(not_contradicted)는 같은 개체라는 확인이 아니다.
    if len(final) < 2 or kept_ambiguous or degraded:
        identity_confidence = CONFIDENCE_LOW
    elif identity_agreement["reliable_pairwise_signal"] and identity_agreement["verified_same_individual"]:
        identity_confidence = CONFIDENCE_NORMAL
    else:
        identity_confidence = CONFIDENCE_UNVERIFIED

    return {
        "selector_version": SELECTOR_VERSION,
        "mode": mode,
        "selected": [{"reference_id": rid, "role": roles[rid]} for rid in final],
        "decisions": decisions,
        "identity_agreement": identity_agreement,
        "identity_confidence": identity_confidence,
        "thresholds": {
            "quality_floor": QUALITY_FLOOR,
            "phash_near_duplicate_max": PHASH_NEAR_DUPLICATE_MAX,
            "add_min_score": ADD_MIN_SCORE,
            "max_references": MAX_REFERENCES,
        },
    }
