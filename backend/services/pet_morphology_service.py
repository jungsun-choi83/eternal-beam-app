"""
펫 형태(구조) 프로필 빌더 (Phase 3).

신원(코트/무늬/색)과 분리된, **개체 구조** 중심의 영구 프로필을 만든다.
원본 레퍼런스(accepted original) + 짝지어진 누끼의 결정론 측정치를 융합해
버전드 append-only 행으로 저장한다.

주의:
- 품종 템플릿으로 생성하지 않는다.
- breed 는 선택 메타데이터로만 남긴다.
- 측정 근거가 없으면 unknown 으로 기록한다.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"

STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"

MORPHOLOGY_PROFILE_CONTRACT_VERSION = "pet-morphology-profile-v1"
MORPHOLOGY_FUSION_VERSION = "morphology-fusion-v1"


class PetMorphologyError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _table() -> str:
    return os.getenv("PET_MORPHOLOGY_PROFILES_TABLE", "pet_morphology_profiles")


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
    from . import pet_identity_service, vlm_identity

    return {
        "contract": MORPHOLOGY_PROFILE_CONTRACT_VERSION,
        "morphology_fusion": MORPHOLOGY_FUSION_VERSION,
        "structural": pet_identity_service.STRUCTURAL_ANALYZER_VERSION,
        "pose_backend": "heuristic_mask_geometry",
        "vlm": (vlm_identity.VLM_ANALYZER_VERSION if vlm_identity.is_enabled() else None),
        "vlm_model": (vlm_identity.model_name() if vlm_identity.is_enabled() else None),
    }


def _unknown_field(reason: str) -> dict[str, Any]:
    return {"status": UNKNOWN, "reason": reason}


#: 결정론적 측정이 아닌 태그 — 휴리스틱 포즈 기하/VLM 추정.
_MEASURED_TAGS = ("measured", "high")


def _trait_confidence(*, supports: int, total: int, heuristic: bool = False) -> str:
    """
    관측 지지도 → 신뢰도.

    휴리스틱 관측(포즈 기하 추정, 단일 이미지 VLM)은 **단일 레퍼런스만으로
    medium 이 될 수 없다**: 한 장에서 한 번 추정한 값은 교차 확인이 없어
    Phase 6.7 의 medium 하한을 통과할 자격이 없다. 여러 레퍼런스가 뒷받침해도
    결정론적 측정치와 동급(high)으로는 올리지 않는다.
    """
    if total <= 0 or supports <= 0:
        return "low"
    ratio = supports / float(total)
    if heuristic:
        if supports < 2:
            return "low"
        return "medium" if ratio >= 0.5 else "low"
    if supports >= 2 and ratio >= 0.67:
        return "high"
    if ratio >= 0.5:
        return "medium"
    return "low"


def _fuse_numeric_trait(
    observations: list[tuple[str, float, str]],
    *,
    unit: Optional[str] = None,
    caveat: Optional[str] = None,
    classify: Optional[Callable[[float], str]] = None,
) -> dict[str, Any]:
    """(reference_id, value, confidence_tag) 목록을 가중 평균으로 융합한다."""
    if not observations:
        return _unknown_field("insufficient_cross_reference_evidence")

    conf_weight = {"measured": 1.0, "high": 1.0, "medium": 0.75, "low": 0.5}
    vals = np.asarray([float(v) for _, v, _ in observations], dtype=np.float64)
    ws = np.asarray([conf_weight.get(str(c), 0.5) for _, _, c in observations], dtype=np.float64)
    sw = float(ws.sum())
    if sw <= 1e-9:
        return _unknown_field("invalid_observation_weights")
    mean = float(np.sum(vals * ws) / sw)
    support_ids = sorted({rid for rid, _, _ in observations})
    heuristic = not any(str(c).lower() in _MEASURED_TAGS for _, _, c in observations)

    out: dict[str, Any] = {
        "status": "fused",
        "value": round(mean, 4),
        "confidence": _trait_confidence(
            supports=len(support_ids), total=len(observations), heuristic=heuristic
        ),
        "support_reference_ids": support_ids,
        "observation_count": len(observations),
        "evidence": ("heuristic" if heuristic else "measured"),
    }
    if unit:
        out["unit"] = unit
    if caveat:
        out["caveat"] = caveat
    if classify is not None:
        out["class"] = classify(mean)
    return out


def _fuse_string_trait(
    observations: list[tuple[str, str]], *, heuristic: bool = True
) -> dict[str, Any]:
    known: dict[str, list[str]] = {}
    for rid, value in observations:
        v = str(value or "").strip()
        if not v or v.lower() == UNKNOWN:
            continue
        known.setdefault(v, []).append(rid)
    if not known:
        return _unknown_field("insufficient_cross_reference_evidence")
    best, supports = max(known.items(), key=lambda kv: (len(kv[1]), kv[0]))
    support_ids = sorted(set(supports))
    return {
        "status": "fused",
        "value": best,
        "confidence": _trait_confidence(
            supports=len(support_ids),
            total=max(1, len(observations)),
            heuristic=heuristic,
        ),
        "support_reference_ids": support_ids,
        "evidence": ("heuristic" if heuristic else "measured"),
    }


def _class_body_frame_size(v: float) -> str:
    if v < 0.14:
        return "small_in_frame"
    if v > 0.55:
        return "large_in_frame"
    return "medium_in_frame"


def _class_build(v: float) -> str:
    if v < 0.82:
        return "slender"
    if v > 0.93:
        return "stocky"
    return "balanced"


def _class_torso(v: float) -> str:
    if v < 1.15:
        return "compact"
    if v > 1.65:
        return "long"
    return "standard"


def _class_leg(v: float) -> str:
    if v < 0.34:
        return "short"
    if v > 0.48:
        return "long"
    return "standard"


def _class_head(v: float) -> str:
    if v < 0.13:
        return "small"
    if v > 0.26:
        return "large"
    return "standard"


def _class_muzzle(v: float) -> str:
    if v < 0.42:
        return "short"
    if v > 0.78:
        return "long"
    return "standard"


def _distance(a: dict[str, Any], b: dict[str, Any]) -> Optional[float]:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    ax, ay = a.get("x"), a.get("y")
    bx, by = b.get("x"), b.get("y")
    if not all(isinstance(v, (int, float)) for v in (ax, ay, bx, by)):
        return None
    return float(math.hypot(float(ax) - float(bx), float(ay) - float(by)))


def _measure_leg_fraction(structural: dict[str, Any]) -> Optional[float]:
    sil = (structural.get("silhouette") or {}) if isinstance(structural, dict) else {}
    pose = (structural.get("pose") or {}) if isinstance(structural, dict) else {}
    bbox = sil.get("bbox") if isinstance(sil.get("bbox"), list) else None
    if not bbox or len(bbox) != 4:
        return None
    bbox_h = float(max(1, int(bbox[3]) - int(bbox[1]) + 1))
    kps = pose.get("keypoints") if isinstance(pose.get("keypoints"), dict) else {}
    pairs = (
        ("front_left_shoulder", "front_left_paw"),
        ("front_right_shoulder", "front_right_paw"),
        ("back_left_hip", "back_left_paw"),
        ("back_right_hip", "back_right_paw"),
    )
    dists: list[float] = []
    for a, b in pairs:
        d = _distance(kps.get(a) or {}, kps.get(b) or {})
        if d is not None and d > 1.0:
            dists.append(d)
    if not dists:
        return None
    return float(np.median(np.asarray(dists, dtype=np.float64)) / bbox_h)


def _measure_muzzle_fraction(structural: dict[str, Any]) -> Optional[float]:
    pose = (structural.get("pose") or {}) if isinstance(structural, dict) else {}
    kps = pose.get("keypoints") if isinstance(pose.get("keypoints"), dict) else {}
    nose = kps.get("nose") or {}
    head_top = kps.get("head_top") or {}
    neck = kps.get("neck") or {}
    muzzle = _distance(nose, head_top)
    head = _distance(neck, head_top)
    if muzzle is None or head is None or head <= 1e-6:
        return None
    return float(muzzle / head)


@dataclass(frozen=True)
class PetMorphologyProfile:
    id: Optional[str]
    pet_id: str
    user_id: str
    content_id: Optional[str]
    version: int
    status: str
    source_reference_ids: list[str] = field(default_factory=list)
    profile: dict[str, Any] = field(default_factory=dict)
    reference_observations: dict[str, Any] = field(default_factory=dict)
    completeness: dict[str, Any] = field(default_factory=dict)
    analyzer_versions: dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None
    deduplicated: bool = False


_SELECT = (
    "id, pet_id, user_id, content_id, version, status, source_reference_ids, profile, "
    "reference_observations, completeness, analyzer_versions, created_at"
)


def _to_profile(row: dict[str, Any], *, deduplicated: bool = False) -> PetMorphologyProfile:
    return PetMorphologyProfile(
        id=(str(row["id"]) if row.get("id") else None),
        pet_id=str(row.get("pet_id") or ""),
        user_id=str(row.get("user_id") or ""),
        content_id=(row.get("content_id") or None),
        version=int(row.get("version") or 1),
        status=str(row.get("status") or STATUS_PARTIAL),
        source_reference_ids=list(row.get("source_reference_ids") or []),
        profile=dict(row.get("profile") or {}),
        reference_observations=dict(row.get("reference_observations") or {}),
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
            logger.exception("형태 프로필 조회 실패 (pet=%s)", pid)
            raise PetMorphologyError(
                "MORPHOLOGY_PROFILES_UNAVAILABLE", "형태 프로필을 확인하지 못했습니다.", status=503
            ) from e
    return [r for r in _MOCK_PROFILES if r.get("pet_id") == pid]


async def get_profile(
    *, user_id: str, pet_id: str, version: Optional[int] = None
) -> Optional[PetMorphologyProfile]:
    from . import pet_reference_service

    try:
        await pet_reference_service.list_references(user_id=user_id, pet_id=pet_id)
    except pet_reference_service.PetReferenceError as e:
        raise PetMorphologyError(e.code, e.message, status=e.status) from e

    rows = await _profile_rows(pet_id)
    if not rows:
        return None
    if version is not None:
        for r in rows:
            if int(r.get("version") or 0) == version:
                return _to_profile(r)
        return None
    return _to_profile(max(rows, key=lambda r: int(r.get("version") or 0)))


async def _insert_profile_row(row: dict[str, Any]) -> tuple[bool, Optional[Exception]]:
    if _use_db() and _supabase():
        try:
            await asyncio.to_thread(lambda: _supabase().table(_table()).insert(row).execute())
            return True, None
        except Exception as e:  # noqa: BLE001
            return False, e
    for r in _MOCK_PROFILES:
        if r["pet_id"] == row["pet_id"] and int(r["version"]) == int(row["version"]):
            return False, PetMorphologyError("DUPLICATE", "duplicate version")
    _MOCK_PROFILES.append(dict(row))
    return True, None


def _completeness(traits: dict[str, Any]) -> dict[str, int]:
    known = 0
    unknown = 0
    for v in traits.values():
        if isinstance(v, dict) and v.get("status") == UNKNOWN:
            unknown += 1
        elif isinstance(v, dict):
            known += 1
    return {"known": known, "unknown": unknown}


def lineage_from_observations(reference_observations: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """
    저장된 형태 프로필 → 그것이 근거로 삼았던 원본→누끼 계보.

    신원 쪽(pet_identity_service.lineage_from_eligibility)과 같은 역할이다. 형태
    프로필은 reference_observations[rid] 에 cutout_reference_id 와 strict_lineage
    를 이미 박제하므로 새 컬럼이 필요 없다.

    형태 프로필은 **엄격 계보가 있는 누끼에서만** 구조를 측정한다(strict_lineage
    False 면 structural 이 unknown 이다). 그래서 누끼가 나중에 붙는 것은 곧
    partial → complete 의 차이이고, 재사용으로 덮여서는 안 된다.
    """
    out: dict[str, dict[str, Any]] = {}
    for rid, entry in (reference_observations or {}).items():
        e = entry if isinstance(entry, dict) else {}
        cutout_id = e.get("cutout_reference_id")
        out[str(rid)] = {
            "cutout_reference_id": (str(cutout_id) if cutout_id else None),
            "strict": bool(e.get("strict_lineage")),
        }
    return out


def _analyze_one_reference(
    ref: Any,
    cut: Any,
    fetch: Callable[[Any], Optional[bytes]],
) -> dict[str, Any]:
    """
    형태 프로필의 레퍼런스 1건 다운로드 + 분석 — 순수 동기 워커. 다른
    레퍼런스와 완전히 독립이라 `asyncio.to_thread` 로 동시에 돌려도
    안전하다(VLM 시맨틱 캐시 키가 이미지 바이트 기준이라 겹치지 않는다 —
    신원 프로필이 같은 사진을 이미 분석했다면 여기서는 캐시를 재사용한다).
    """
    from . import pet_identity_service, vlm_identity

    rid = str(ref.id)
    strict_lineage_ok = bool(cut and cut.parent_reference_id and cut.parent_reference_id == ref.id)
    cut_rgba = None
    if cut is not None:
        cut_bytes = fetch(cut)
        if cut_bytes:
            cut_rgba = pet_identity_service.load_rgba(cut_bytes)

    entry: dict[str, Any] = {
        "reference_id": rid,
        "cutout_reference_id": (str(cut.id) if cut and cut.id else None),
        "strict_lineage": strict_lineage_ok,
    }

    eligible_primary = bool(cut_rgba is not None and strict_lineage_ok)
    measurements: dict[str, Optional[float]] = {}
    if eligible_primary:
        structural = pet_identity_service.analyze_structural_identity(cut_rgba)
        entry["structural"] = structural

        sil = structural.get("silhouette") or {}
        area_fraction = sil.get("area_fraction")
        measurements["body_size"] = float(area_fraction) if isinstance(area_fraction, (int, float)) else None

        solidity = sil.get("silhouette_solidity")
        measurements["build"] = float(solidity) if isinstance(solidity, (int, float)) else None

        torso = (structural.get("body_length_height_ratio") or {}).get("value")
        measurements["torso"] = float(torso) if isinstance(torso, (int, float)) else None

        leg_fraction = _measure_leg_fraction(structural)
        measurements["leg"] = float(leg_fraction) if isinstance(leg_fraction, (int, float)) else None

        head_fraction = (structural.get("pose") or {}).get("head_height_fraction")
        measurements["head"] = float(head_fraction) if isinstance(head_fraction, (int, float)) else None

        muzzle_fraction = _measure_muzzle_fraction(structural)
        measurements["muzzle"] = float(muzzle_fraction) if isinstance(muzzle_fraction, (int, float)) else None
    else:
        entry["structural"] = _unknown_field(
            "no_strict_lineage_cutout" if cut_rgba is not None else "no_segmentation_available"
        )

    semantic: dict[str, Optional[str]] = {"ear": None, "tail": None, "breed": None}
    if vlm_identity.is_enabled():
        original_bytes = fetch(ref)
        sem = (
            vlm_identity.analyze_semantic_traits([(original_bytes, ref.mime_type or "image/jpeg")])
            if original_bytes
            else None
        )
        traits = (sem or {}).get("traits") if isinstance(sem, dict) else {}
        ear = str((((traits or {}).get("ears") or {}).get("shape") or "")).strip()
        tail = str((((traits or {}).get("tail") or {}).get("appearance") or "")).strip()
        breed = str(((traits or {}).get("breed_estimate") or "")).strip()
        semantic["ear"] = ear if ear and ear.lower() != UNKNOWN else None
        semantic["tail"] = tail if tail and tail.lower() != UNKNOWN else None
        semantic["breed"] = breed if breed and breed.lower() != UNKNOWN else None
        entry["semantic_traits"] = {
            "status": ("present" if sem else UNKNOWN),
            "analyzer": ((sem or {}).get("analyzer") if sem else None),
            "model": ((sem or {}).get("model") if sem else None),
            "traits": traits if isinstance(traits, dict) else None,
        }

    return {
        "entry": entry,
        "eligible_primary": eligible_primary,
        "measurements": measurements,
        "semantic": semantic,
    }


async def build_morphology_profile(
    *,
    user_id: str,
    pet_id: str,
    fetch_bytes: Optional[Callable[[Any], Optional[bytes]]] = None,
    skip_if_unchanged: bool = True,
) -> PetMorphologyProfile:
    """accepted original references 기반 형태 프로필 빌드/버전 저장."""
    from . import pet_identity_service, pet_reference_service, vlm_identity

    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    if not uid or not pid:
        raise PetMorphologyError("MORPHOLOGY_PROFILE_INVALID", "user_id 와 pet_id 가 필요합니다.")

    try:
        refs = await pet_reference_service.list_references(user_id=uid, pet_id=pid)
    except pet_reference_service.PetReferenceError as e:
        raise PetMorphologyError(e.code, e.message, status=e.status) from e

    originals = [
        r
        for r in refs
        if r.role == pet_reference_service.ROLE_ORIGINAL
        and r.acceptance_state == pet_reference_service.STATE_ACCEPTED
    ]
    if not originals:
        raise PetMorphologyError(
            "NO_ORIGINAL_REFERENCES",
            "형태 프로필을 만들 원본 레퍼런스가 없습니다.",
            status=409,
        )

    source_ids = sorted(str(r.id) for r in originals if r.id)
    versions = analyzer_versions()
    # 재사용 키의 일부다 — 누끼가 나중에 붙으면 구조 측정 가능성 자체가 달라진다.
    lineage_map = pet_reference_service.strict_lineage_map(refs)

    if skip_if_unchanged:
        rows = await _profile_rows(pid)
        if rows:
            latest = _to_profile(max(rows, key=lambda r: int(r.get("version") or 0)))
            if (
                sorted(latest.source_reference_ids) == source_ids
                and latest.analyzer_versions == versions
                and lineage_from_observations(latest.reference_observations) == lineage_map
            ):
                return _to_profile(max(rows, key=lambda r: int(r.get("version") or 0)), deduplicated=True)

    fetch = fetch_bytes or pet_identity_service._default_fetch_bytes
    pairing = pet_reference_service.pair_cutouts(refs)

    observations: dict[str, Any] = {}
    body_size_obs: list[tuple[str, float, str]] = []
    build_obs: list[tuple[str, float, str]] = []
    torso_obs: list[tuple[str, float, str]] = []
    leg_obs: list[tuple[str, float, str]] = []
    head_obs: list[tuple[str, float, str]] = []
    muzzle_obs: list[tuple[str, float, str]] = []
    ear_form_obs: list[tuple[str, str]] = []
    tail_form_obs: list[tuple[str, str]] = []
    breed_obs: list[tuple[str, str]] = []

    # 원본 등록 순으로 정렬 — primary 선택의 결정론 기준. 실제 다운로드/분석은
    # 이 순서와 무관하게 동시에 돌리고(레퍼런스마다 독립), 결과만 이 순서로
    # 순회해 합친다 — 완료 순서가 primary 를 흔들지 않는다.
    ordered = sorted(originals, key=lambda r: (r.created_at or "", str(r.id)))

    from .concurrency import gather_bounded

    analysis_results = await gather_bounded(
        [
            (
                lambda r=ref, c=pairing.get(str(ref.id)): asyncio.to_thread(
                    _analyze_one_reference, r, c, fetch
                )
            )
            for ref in ordered
        ]
    )

    primary_reference_id = None
    for ref, res in zip(ordered, analysis_results):
        rid = str(ref.id)
        observations[rid] = res["entry"]
        if res["eligible_primary"] and primary_reference_id is None:
            primary_reference_id = rid

        m = res["measurements"]
        if m.get("body_size") is not None:
            body_size_obs.append((rid, m["body_size"], "measured"))
        if m.get("build") is not None:
            build_obs.append((rid, m["build"], "measured"))
        if m.get("torso") is not None:
            torso_obs.append((rid, m["torso"], "measured"))
        if m.get("leg") is not None:
            leg_obs.append((rid, m["leg"], "low"))
        if m.get("head") is not None:
            head_obs.append((rid, m["head"], "low"))
        if m.get("muzzle") is not None:
            muzzle_obs.append((rid, m["muzzle"], "low"))

        sem = res["semantic"]
        if sem.get("ear"):
            ear_form_obs.append((rid, sem["ear"]))
        if sem.get("tail"):
            tail_form_obs.append((rid, sem["tail"]))
        if sem.get("breed"):
            breed_obs.append((rid, sem["breed"]))

    traits = {
        # ⚠️ 프레임 점유율이지 **동물의 실제 크기가 아니다**. 촬영 거리/렌즈가
        # 그대로 들어간 값이라 SMALL/MEDIUM/LARGE 체급으로 승격하면 안 된다
        # (소비자 계약: measures=frame_occupancy).
        "body_size": {
            **_fuse_numeric_trait(
                body_size_obs,
                unit="fraction_of_frame",
                caveat="frame occupancy only; NOT real animal body size (camera distance/lens dominate)",
                classify=_class_body_frame_size,
            ),
            "measures": "frame_occupancy",
            "usable_as_body_size_class": False,
        },
        "body_build": _fuse_numeric_trait(
            build_obs,
            caveat="silhouette solidity; pose/coat can affect estimate",
            classify=_class_build,
        ),
        "torso_proportion": _fuse_numeric_trait(
            torso_obs,
            unit="body_length_height_ratio",
            caveat="bbox ratio; pose-dependent",
            classify=_class_torso,
        ),
        "leg_proportion": _fuse_numeric_trait(
            leg_obs,
            unit="median_leg_length_over_bbox_height",
            caveat="heuristic pose geometry; near/far leg ambiguity",
            classify=_class_leg,
        ),
        "head_proportion": _fuse_numeric_trait(
            head_obs,
            unit="head_height_over_bbox_height",
            caveat="heuristic pose geometry",
            classify=_class_head,
        ),
        "muzzle_proportion": _fuse_numeric_trait(
            muzzle_obs,
            unit="nose_to_headtop_over_headtop_to_neck",
            caveat="heuristic pose geometry",
            classify=_class_muzzle,
        ),
        "ear_form": _fuse_string_trait(ear_form_obs),
        "tail_form": _fuse_string_trait(tail_form_obs),
    }

    # 선택 메타데이터: 품종 추정은 구조가 아니라 참고값으로만 둔다.
    breed_meta = _fuse_string_trait(breed_obs)
    profile_payload = {
        "status": "fused" if primary_reference_id else UNKNOWN,
        "primary_reference_id": primary_reference_id,
        "traits": traits,
        "metadata": {
            "breed_estimate": breed_meta,
            "notes": "breed metadata is optional and never used as a generation template",
        },
    }
    if not primary_reference_id:
        profile_payload = {
            "status": UNKNOWN,
            "reason": "no_analyzable_reference",
            "traits": traits,
            "metadata": {
                "breed_estimate": breed_meta,
                "notes": "breed metadata is optional and never used as a generation template",
            },
        }

    status = STATUS_COMPLETE if primary_reference_id else STATUS_PARTIAL
    row: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "pet_id": pid,
        "user_id": uid,
        "content_id": (originals[0].content_id or None),
        "version": 1,
        "status": status,
        "source_reference_ids": source_ids,
        "profile": profile_payload,
        "reference_observations": observations,
        "completeness": _completeness(traits),
        "analyzer_versions": versions,
        "created_at": _now_iso(),
    }

    last_err: Optional[Exception] = None
    for _ in range(3):
        rows = await _profile_rows(pid)
        row["version"] = (max((int(r.get("version") or 0) for r in rows), default=0)) + 1
        ok, err = await _insert_profile_row(row)
        if ok:
            return _to_profile(row)
        last_err = err

    logger.error("형태 프로필 기록 실패 (pet=%s): %s", pid, last_err)
    raise PetMorphologyError(
        "MORPHOLOGY_PROFILES_UNAVAILABLE", "형태 프로필을 저장하지 못했습니다.", status=503
    )
