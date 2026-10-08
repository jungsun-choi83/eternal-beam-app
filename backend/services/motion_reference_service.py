"""
모션 레퍼런스 라이브러리 + 결정론적 매칭 (Phase 6.6).

── 신원 ≠ 모션 ─────────────────────────────────────────────────────────────
정본 펫/키프레임이 "누구인가"를 정의하고, 여기의 레퍼런스는 "호환되는 동물이
어떻게 움직이는가"만 정의한다. 매칭 입력은 구조/생체역학 속성뿐이다 —
털색·무늬·눈색은 어디에도 쓰이지 않는다.

── 매칭 단계 ───────────────────────────────────────────────────────────────
LEVEL_0_OWN  펫 자신의 모션 (source_type=PET_OWN_MOTION, 같은 pet_id) — 미래
             펫 생애 아카이브의 최우선 순위. 추출은 아직 없고 계약만 지원한다.
LEVEL_1      종 EXACT + 형태 전부 EXACT + 요청 뷰/방향/속도 EXACT
LEVEL_2      MISMATCH 없음 (NEAR/UNVERIFIED 허용)
LEVEL_3      형태는 깨끗하지만 뷰/방향/속도가 어긋남 — 명시적 저하
(없음)        형태 MISMATCH 레퍼런스는 후보에서 제외된다
LEVEL_4      호환 레퍼런스 없음 → 호출자가 Phase 5.1 정책 적용
             (preferred → 저하 I2V + 경고 / required → 안전 실패)

**종 교차는 절대 없다.** 개 레퍼런스가 없다고 고양이 레퍼런스를 쓰지 않는다.

── 출처/라이선스 강제 ──────────────────────────────────────────────────────
등록에는 source_type + license + 제공자가 필요하고, APPROVED/enabled 는
commercial_use_allowed 없이는 불가능하다 — 출처 불명 자산은 구조적으로
프로덕션 해상에 나타날 수 없다.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

MOTION_MATCHING_VERSION = "motion-matching-v1"
MORPHOLOGY_PROFILE_VERSION = "morphology-profile-v1"

SPECIES = ("DOG", "CAT", "RABBIT", "OTHER")
SIZE_ORDER = ("SMALL", "MEDIUM", "LARGE")
LEG_ORDER = ("SHORT", "STANDARD", "LONG")
BODY_ORDER = ("COMPACT", "STANDARD", "LONG")
BUILD_ORDER = ("SLENDER", "BALANCED", "STOCKY")
HEAD_ORDER = ("SMALL", "STANDARD", "LARGE")
MUZZLE_ORDER = ("SHORT", "STANDARD", "LONG")
VIEWS = ("FRONT", "FRONT_3Q", "SIDE", "BACK", "UNKNOWN")
DIRECTIONS = ("TOWARD_CAMERA", "AWAY_FROM_CAMERA", "LEFT_TO_RIGHT", "RIGHT_TO_LEFT", "STATIONARY", "UNKNOWN")
SPEEDS = ("SLOW", "NORMAL", "FAST", "UNKNOWN")
SOURCE_TYPES = ("INTERNAL_RECORDING", "LICENSED_STOCK", "COMMISSIONED", "OPEN_DATASET", "PET_OWN_MOTION")
QUALITY_STATUSES = ("DRAFT", "REVIEW", "APPROVED", "REJECTED")

UNKNOWN = "UNKNOWN"

LEVEL_OWN = "LEVEL_0_OWN"
LEVEL_1 = "LEVEL_1"
LEVEL_2 = "LEVEL_2"
LEVEL_3 = "LEVEL_3"

#: 몸통 길이/키 비율 → body_length_class (잠정, MORPHOLOGY_PROFILE_VERSION 봉인).
_BODY_COMPACT_MAX = 1.15
_BODY_LONG_MIN = 1.65


class MotionReferenceError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _table() -> str:
    return os.getenv("MOTION_REFERENCES_TABLE", "motion_references")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


_MOCK_REFS: list[dict[str, Any]] = []


def __reset_for_tests() -> None:
    _MOCK_REFS.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class MotionReference:
    id: str
    reference_key: str
    version: int
    species: str
    motion_id: str
    body_size_class: str = UNKNOWN
    leg_length_class: str = UNKNOWN
    body_length_class: str = UNKNOWN
    body_build_class: str = UNKNOWN
    head_proportion_class: str = UNKNOWN
    muzzle_proportion_class: str = UNKNOWN
    ear_form: str = UNKNOWN
    tail_form: str = UNKNOWN
    motion_class: Optional[str] = None
    camera_view: str = UNKNOWN
    travel_direction: str = UNKNOWN
    speed_class: str = UNKNOWN
    start_pose: Optional[str] = None
    end_pose: Optional[str] = None
    duration_sec: Optional[float] = None
    fps: Optional[float] = None
    resolution: Optional[str] = None
    loopable: bool = False
    bucket: Optional[str] = None
    object_path: Optional[str] = None
    pet_id: Optional[str] = None
    source_type: str = "LICENSED_STOCK"
    source_description: Optional[str] = None
    license: str = ""
    license_reference: Optional[str] = None
    provider_name: Optional[str] = None
    commercial_use_allowed: bool = False
    provenance_notes: Optional[str] = None
    quality_status: str = "DRAFT"
    enabled: bool = False
    created_at: Optional[str] = None


def _to_ref(row: dict[str, Any]) -> MotionReference:
    return MotionReference(
        id=str(row.get("id")),
        reference_key=str(row.get("reference_key") or ""),
        version=int(row.get("version") or 1),
        species=str(row.get("species") or ""),
        body_size_class=str(row.get("body_size_class") or UNKNOWN),
        leg_length_class=str(row.get("leg_length_class") or UNKNOWN),
        body_length_class=str(row.get("body_length_class") or UNKNOWN),
        body_build_class=str(row.get("body_build_class") or UNKNOWN),
        head_proportion_class=str(row.get("head_proportion_class") or UNKNOWN),
        muzzle_proportion_class=str(row.get("muzzle_proportion_class") or UNKNOWN),
        ear_form=str(row.get("ear_form") or UNKNOWN),
        tail_form=str(row.get("tail_form") or UNKNOWN),
        motion_id=str(row.get("motion_id") or ""),
        motion_class=(row.get("motion_class") or None),
        camera_view=str(row.get("camera_view") or UNKNOWN),
        travel_direction=str(row.get("travel_direction") or UNKNOWN),
        speed_class=str(row.get("speed_class") or UNKNOWN),
        start_pose=(row.get("start_pose") or None),
        end_pose=(row.get("end_pose") or None),
        duration_sec=(float(row["duration_sec"]) if row.get("duration_sec") is not None else None),
        fps=(float(row["fps"]) if row.get("fps") is not None else None),
        resolution=(row.get("resolution") or None),
        loopable=bool(row.get("loopable")),
        bucket=(row.get("bucket") or None),
        object_path=(row.get("object_path") or None),
        pet_id=(row.get("pet_id") or None),
        source_type=str(row.get("source_type") or ""),
        source_description=(row.get("source_description") or None),
        license=str(row.get("license") or ""),
        license_reference=(row.get("license_reference") or None),
        provider_name=(row.get("provider_name") or None),
        commercial_use_allowed=bool(row.get("commercial_use_allowed")),
        provenance_notes=(row.get("provenance_notes") or None),
        quality_status=str(row.get("quality_status") or "DRAFT"),
        enabled=bool(row.get("enabled")),
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
    )


async def _all_rows() -> list[dict[str, Any]]:
    if _use_db() and _supabase():
        try:
            r = _supabase().table(_table()).select("*").limit(5000).execute()
            return getattr(r, "data", None) or []
        except Exception as e:
            logger.exception("모션 레퍼런스 조회 실패")
            raise MotionReferenceError(
                "MOTION_REFERENCES_UNAVAILABLE", "모션 레퍼런스를 확인하지 못했습니다.", status=503
            ) from e
    return list(_MOCK_REFS)


# ══════════════════════════════════════════════════════════════════════════
# 등록 / 수명 (출처 강제)
# ══════════════════════════════════════════════════════════════════════════


def _validate_provenance(source_type: str, license_text: str, provider_name: Optional[str]) -> None:
    if source_type not in SOURCE_TYPES:
        raise MotionReferenceError(
            "INVALID_SOURCE_TYPE",
            f"허용되지 않는 source_type: {source_type} — 무허가/스크랩 영상은 등록 자체가 불가하다.",
            status=422,
        )
    if not (license_text or "").strip():
        raise MotionReferenceError(
            "LICENSE_REQUIRED", "license 없는 레퍼런스는 등록할 수 없다.", status=422
        )
    if not (provider_name or "").strip():
        raise MotionReferenceError(
            "PROVIDER_REQUIRED", "제공자/제작자 정보 없는 레퍼런스는 등록할 수 없다.", status=422
        )


async def register_reference(
    *,
    reference_key: str,
    species: str,
    motion_id: str,
    source_type: str,
    license: str,
    provider_name: str,
    body_size_class: str = UNKNOWN,
    leg_length_class: str = UNKNOWN,
    body_length_class: str = UNKNOWN,
    camera_view: str = UNKNOWN,
    travel_direction: str = UNKNOWN,
    speed_class: str = UNKNOWN,
    object_path: Optional[str] = None,
    bucket: Optional[str] = None,
    pet_id: Optional[str] = None,
    **extra: Any,
) -> MotionReference:
    """새 레퍼런스(또는 같은 key 의 새 버전) 등록. 출처/라이선스는 필수다."""
    from . import motion_spec, supabase_assets

    key = (reference_key or "").strip().upper()
    sp = (species or "").strip().upper()
    mid = (motion_id or "").strip().upper()
    if not key:
        raise MotionReferenceError("REFERENCE_KEY_REQUIRED", "reference_key 가 필요합니다.", status=422)
    if sp not in SPECIES:
        raise MotionReferenceError("INVALID_SPECIES", f"지원하지 않는 종: {species}", status=422)
    # 모션 축은 정본 레지스트리 id 만 — 병행 명명 금지.
    spec = motion_spec.get_motion(mid)
    if not spec:
        raise MotionReferenceError(
            "UNKNOWN_MOTION", f"motion_spec 에 없는 모션 id: {motion_id}", status=422
        )
    _validate_provenance(source_type, license, provider_name)
    if source_type == "PET_OWN_MOTION" and not (pet_id or "").strip():
        raise MotionReferenceError(
            "PET_ID_REQUIRED", "PET_OWN_MOTION 은 pet_id 가 필요합니다.", status=422
        )

    rows = await _all_rows()
    same_key = [r for r in rows if r.get("reference_key") == key]
    version = (max((int(r.get("version") or 0) for r in same_key), default=0)) + 1

    row: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "reference_key": key,
        "version": version,
        "species": sp,
        "body_size_class": (body_size_class or UNKNOWN).upper(),
        "leg_length_class": (leg_length_class or UNKNOWN).upper(),
        "body_length_class": (body_length_class or UNKNOWN).upper(),
        "body_build_class": str(extra.get("body_build_class") or UNKNOWN).upper(),
        "head_proportion_class": str(extra.get("head_proportion_class") or UNKNOWN).upper(),
        "muzzle_proportion_class": str(extra.get("muzzle_proportion_class") or UNKNOWN).upper(),
        "ear_form": str(extra.get("ear_form") or UNKNOWN).upper(),
        "tail_form": str(extra.get("tail_form") or UNKNOWN).upper(),
        "motion_id": mid,
        "motion_class": spec.motion_class,
        "camera_view": (camera_view or UNKNOWN).upper(),
        "travel_direction": (travel_direction or UNKNOWN).upper(),
        "speed_class": (speed_class or UNKNOWN).upper(),
        "start_pose": extra.get("start_pose"),
        "end_pose": extra.get("end_pose"),
        "duration_sec": extra.get("duration_sec"),
        "fps": extra.get("fps"),
        "resolution": extra.get("resolution"),
        "loopable": bool(extra.get("loopable")),
        "bucket": bucket or (supabase_assets.BUCKET if object_path else None),
        "object_path": object_path,
        "pet_id": (pet_id or None),
        "source_type": source_type,
        "source_description": extra.get("source_description"),
        "license": license,
        "license_reference": extra.get("license_reference"),
        "provider_name": provider_name,
        "commercial_use_allowed": bool(extra.get("commercial_use_allowed")),
        "provenance_notes": extra.get("provenance_notes"),
        "quality_status": "DRAFT",
        "enabled": False,
        "created_at": _now_iso(),
    }

    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).insert(row).execute()
        except Exception as e:
            logger.exception("모션 레퍼런스 등록 실패 (key=%s)", key)
            raise MotionReferenceError(
                "MOTION_REFERENCES_UNAVAILABLE", "레퍼런스를 등록하지 못했습니다.", status=503
            ) from e
    else:
        _MOCK_REFS.append(dict(row))
    return _to_ref(row)


async def set_status(
    *,
    reference_key: str,
    version: int,
    quality_status: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> MotionReference:
    """
    품질/활성 상태 변경. **출처 없는 프로덕션 진입 차단이 여기서 강제된다**:
    APPROVED 또는 enabled=true 는 commercial_use_allowed + license 필수.
    """
    rows = await _all_rows()
    row = next(
        (r for r in rows if r.get("reference_key") == reference_key.upper() and int(r.get("version") or 0) == version),
        None,
    )
    if not row:
        raise MotionReferenceError("REFERENCE_NOT_FOUND", "레퍼런스가 없습니다.", status=404)

    fields: dict[str, Any] = {}
    if quality_status is not None:
        if quality_status not in QUALITY_STATUSES:
            raise MotionReferenceError("INVALID_STATUS", f"허용되지 않는 상태: {quality_status}", status=422)
        fields["quality_status"] = quality_status
    if enabled is not None:
        fields["enabled"] = bool(enabled)

    wants_production = (
        fields.get("quality_status") == "APPROVED" or fields.get("enabled") is True
    )
    if wants_production and not (row.get("commercial_use_allowed") and (row.get("license") or "").strip()):
        raise MotionReferenceError(
            "PROVENANCE_REQUIRED",
            "commercial_use_allowed + license 없는 레퍼런스는 APPROVED/enabled 가 될 수 없다.",
            status=422,
        )

    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).update(fields).eq("id", row["id"]).execute()
        except Exception as e:
            raise MotionReferenceError(
                "MOTION_REFERENCES_UNAVAILABLE", "상태를 변경하지 못했습니다.", status=503
            ) from e
        row = {**row, **fields}
    else:
        for r in _MOCK_REFS:
            if r["id"] == row["id"]:
                r.update(fields)
                row = r
    return _to_ref(row)


async def list_references(
    *,
    species: Optional[str] = None,
    motion_id: Optional[str] = None,
    quality_status: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> list[MotionReference]:
    rows = await _all_rows()
    out = []
    for r in rows:
        if species and r.get("species") != species.upper():
            continue
        if motion_id and r.get("motion_id") != motion_id.upper():
            continue
        if quality_status and r.get("quality_status") != quality_status:
            continue
        if enabled is not None and bool(r.get("enabled")) != enabled:
            continue
        out.append(_to_ref(r))
    out.sort(key=lambda r: (r.reference_key, r.version))
    return out


# ══════════════════════════════════════════════════════════════════════════
# 모션 프로필 파생 (Phase 2 구조 신원 → 형태 프로필)
# ══════════════════════════════════════════════════════════════════════════


def _confidence_ok(confidence: str, floor: str) -> bool:
    rank = {"low": 0, "medium": 1, "high": 2}
    return rank.get(str(confidence or "low").lower(), 0) >= rank.get(str(floor or "medium").lower(), 1)


def _class_from_morphology_trait(
    traits: dict[str, Any],
    key: str,
    *,
    floor: str,
) -> tuple[str, str]:
    t = traits.get(key) if isinstance(traits, dict) else None
    if not isinstance(t, dict):
        return UNKNOWN, "unavailable"
    if str(t.get("status") or "").lower() != "fused":
        return UNKNOWN, "unknown"
    if not _confidence_ok(str(t.get("confidence") or "low"), floor):
        return UNKNOWN, "low_confidence"
    raw = str(t.get("class") or t.get("value") or "").strip()
    if not raw:
        return UNKNOWN, "empty"
    return raw.upper(), str(t.get("confidence") or "low").lower()


#: 프레임 점유율 기반 형태 트레이트 — 실제 체급 근거가 아니다.
_FRAME_OCCUPANCY_TRAITS = ("body_size",)


def derive_motion_profile(
    identity_profile: Any,
    overrides: Optional[dict[str, str]] = None,
    *,
    morphology_profile: Any = None,
    confidence_floor: str = "medium",
) -> dict[str, Any]:
    """
    Phase 2 프로필 → 경량 모션/형태 프로필. 정직하게 잴 수 있는 것만:
      species       YOLO animal_class (결정론) → VLM species 폴백
      body_length   측정된 bbox 비율 (잠정 임계값)
      body_size     UNKNOWN — 사진에는 절대 스케일이 없다
      leg_length    UNKNOWN — 실루엣의 근/원위 다리 모호성 (Phase 2 문서화 한계)
    overrides 로 벤치마크/운영이 명시 지정할 수 있다 (품종 분류는 하지 않는다).
    """
    species = UNKNOWN
    body_size = UNKNOWN
    leg_length = UNKNOWN
    body_length = UNKNOWN
    body_build = UNKNOWN
    head_proportion = UNKNOWN
    muzzle_proportion = UNKNOWN
    ear_form = UNKNOWN
    tail_form = UNKNOWN
    sources: dict[str, str] = {}

    if morphology_profile is not None:
        mp = getattr(morphology_profile, "profile", None)
        if not isinstance(mp, dict):
            mp = morphology_profile if isinstance(morphology_profile, dict) else {}
        traits = (mp.get("traits") or {}) if isinstance(mp, dict) else {}

        m_species, c = _class_from_morphology_trait(traits, "species", floor=confidence_floor)
        if m_species in SPECIES:
            species = m_species
            sources["species"] = f"morphology_profile:{c}"

        # body_size_class 는 형태 프로필에서 파생하지 않는다. 유일한 후보였던
        # morphology.body_size 는 프레임 점유율(small/medium/large_in_frame)이고
        # 카메라 거리의 함수라 실제 체급이 아니다 — 실측 소스가 생기기 전까지
        # UNKNOWN 을 유지하고, 운영 override 로만 채운다.
        body_size = UNKNOWN

        body_length, c = _class_from_morphology_trait(traits, "torso_proportion", floor=confidence_floor)
        if body_length != UNKNOWN:
            sources["body_length"] = f"morphology_profile:{c}"

        leg_length, c = _class_from_morphology_trait(traits, "leg_proportion", floor=confidence_floor)
        if leg_length != UNKNOWN:
            sources["leg_length"] = f"morphology_profile:{c}"

        body_build, c = _class_from_morphology_trait(traits, "body_build", floor=confidence_floor)
        if body_build != UNKNOWN:
            sources["body_build"] = f"morphology_profile:{c}"

        head_proportion, c = _class_from_morphology_trait(traits, "head_proportion", floor=confidence_floor)
        if head_proportion != UNKNOWN:
            sources["head_proportion"] = f"morphology_profile:{c}"

        muzzle_proportion, c = _class_from_morphology_trait(traits, "muzzle_proportion", floor=confidence_floor)
        if muzzle_proportion != UNKNOWN:
            sources["muzzle_proportion"] = f"morphology_profile:{c}"

        ear_form, c = _class_from_morphology_trait(traits, "ear_form", floor=confidence_floor)
        if ear_form != UNKNOWN:
            sources["ear_form"] = f"morphology_profile:{c}"

        tail_form, c = _class_from_morphology_trait(traits, "tail_form", floor=confidence_floor)
        if tail_form != UNKNOWN:
            sources["tail_form"] = f"morphology_profile:{c}"

    if identity_profile is not None:
        elig = getattr(identity_profile, "reference_eligibility", None) or {}
        primary = (getattr(identity_profile, "visual_identity", None) or {}).get("primary_reference_id")
        entry = elig.get(primary) if primary else None
        if not entry and elig:
            entry = next(iter(elig.values()))
        animal = str((entry or {}).get("animal_class") or "").strip().upper()
        if animal in SPECIES:
            species = animal
            sources["species"] = "detector"
        else:
            traits = (
                ((getattr(identity_profile, "visual_identity", None) or {}).get("semantic_traits") or {})
                .get("traits") or {}
            )
            vlm_species = str(traits.get("species") or "").strip().upper()
            if vlm_species in SPECIES:
                species = vlm_species
                sources["species"] = "vlm"

        # 신원 프로필의 bbox 비율은 **단일 레퍼런스** 측정이다. 여러 레퍼런스를
        # 융합한 형태 프로필이 이미 몸통 비율을 줬다면 그쪽이 정본이고, 여기는
        # 형태 프로필이 쓸 만한 값을 못 준 경우의 폴백으로만 쓴다.
        if body_length == UNKNOWN:
            sil = (getattr(identity_profile, "structural_identity", None) or {}).get("silhouette") or {}
            ar = sil.get("bbox_aspect_ratio")
            if isinstance(ar, (int, float)):
                if ar <= _BODY_COMPACT_MAX:
                    body_length = "COMPACT"
                elif ar >= _BODY_LONG_MIN:
                    body_length = "LONG"
                else:
                    body_length = "STANDARD"
                # 라벨은 기존 계약 그대로 "measured" 다 (QA 신뢰도 해석 불변).
                sources["body_length"] = "measured"

    profile = {
        "profile_version": MORPHOLOGY_PROFILE_VERSION,
        "species": species,
        "body_size_class": body_size,
        "leg_length_class": leg_length,
        "body_length_class": body_length,
        "body_build_class": body_build,
        "head_proportion_class": head_proportion,
        "muzzle_proportion_class": muzzle_proportion,
        "ear_form": ear_form,
        "tail_form": tail_form,
        "morphology_profile_id": (
            str(getattr(morphology_profile, "id")) if morphology_profile is not None and getattr(morphology_profile, "id", None) else None
        ),
        "morphology_profile_version": (
            int(getattr(morphology_profile, "version")) if morphology_profile is not None and getattr(morphology_profile, "version", None) is not None else None
        ),
        "sources": sources,
    }
    for key, target in (
        ("species", "species"),
        ("body_size_class", "body_size_class"),
        ("leg_length_class", "leg_length_class"),
        ("body_length_class", "body_length_class"),
        ("body_build_class", "body_build_class"),
        ("head_proportion_class", "head_proportion_class"),
        ("muzzle_proportion_class", "muzzle_proportion_class"),
        ("ear_form", "ear_form"),
        ("tail_form", "tail_form"),
    ):
        v = (overrides or {}).get(key)
        if v:
            profile[target] = str(v).strip().upper()
            profile["sources"][key.replace("_class", "")] = "override"
    return profile


# ══════════════════════════════════════════════════════════════════════════
# 결정론적 리졸버
# ══════════════════════════════════════════════════════════════════════════

EXACT = "EXACT"
NEAR = "NEAR"
UNVERIFIED = "UNVERIFIED"
MISMATCH = "MISMATCH"
UNSPECIFIED = "UNSPECIFIED"


def _ordered_compat(pet: str, ref: str, order: tuple[str, ...]) -> str:
    if pet == UNKNOWN or ref == UNKNOWN:
        return UNVERIFIED
    if pet == ref:
        return EXACT
    try:
        if abs(order.index(pet) - order.index(ref)) == 1:
            return NEAR
    except ValueError:
        return UNVERIFIED
    return MISMATCH


def _view_compat(requested: Optional[str], ref: str) -> str:
    if not requested:
        return UNSPECIFIED
    req = requested.upper()
    if ref == UNKNOWN:
        return UNVERIFIED
    if req == ref:
        return EXACT
    if {req, ref} == {"FRONT", "FRONT_3Q"}:
        return NEAR
    return MISMATCH


def _plain_compat(requested: Optional[str], ref: str) -> str:
    if not requested:
        return UNSPECIFIED
    if ref == UNKNOWN:
        return UNVERIFIED
    return EXACT if requested.upper() == ref else MISMATCH


def _profile_plain_compat(pet: Optional[str], ref: str) -> str:
    p = str(pet or "").strip().upper()
    if not p or p == UNKNOWN:
        return UNVERIFIED
    if ref == UNKNOWN:
        return UNVERIFIED
    return EXACT if p == ref else MISMATCH


def _assess(
    profile: dict[str, Any],
    ref: MotionReference,
    *,
    pet_id: Optional[str],
    desired_view: Optional[str],
    direction: Optional[str],
    speed: Optional[str],
    motion_requirements: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """레퍼런스 1건의 호환성 — 컴포넌트별 이유를 전부 노출한다. 배제는 None."""
    if ref.species != profile.get("species"):
        return None  # 종 교차 금지 — 후보조차 되지 않는다

    compat = {
        "species": EXACT,
        "motion": EXACT,  # 풀 필터에서 이미 일치
        "body_size": _ordered_compat(profile.get("body_size_class", UNKNOWN), ref.body_size_class, SIZE_ORDER),
        "leg_class": _ordered_compat(profile.get("leg_length_class", UNKNOWN), ref.leg_length_class, LEG_ORDER),
        "body_class": _ordered_compat(profile.get("body_length_class", UNKNOWN), ref.body_length_class, BODY_ORDER),
        "body_build": _ordered_compat(profile.get("body_build_class", UNKNOWN), ref.body_build_class, BUILD_ORDER),
        "head_proportion": _ordered_compat(profile.get("head_proportion_class", UNKNOWN), ref.head_proportion_class, HEAD_ORDER),
        "muzzle_proportion": _ordered_compat(profile.get("muzzle_proportion_class", UNKNOWN), ref.muzzle_proportion_class, MUZZLE_ORDER),
        "ear_form": _profile_plain_compat(profile.get("ear_form"), ref.ear_form),
        "tail_form": _profile_plain_compat(profile.get("tail_form"), ref.tail_form),
        "view": _view_compat(desired_view, ref.camera_view),
        "direction": _plain_compat(direction, ref.travel_direction),
        "speed": _plain_compat(speed, ref.speed_class),
    }
    req = motion_requirements or {}
    mreq = (req.get("morphology") or {}) if isinstance(req, dict) else {}
    match_fields = set(
        str(k)
        for k in (
            mreq.get("match_fields")
            or ((req.get("reference") or {}).get("morphology_match_fields"))
            or ["body_size_class", "leg_length_class", "body_length_class"]
        )
    )
    compat_by_field = {
        "body_size_class": compat["body_size"],
        "leg_length_class": compat["leg_class"],
        "body_length_class": compat["body_class"],
        "body_build_class": compat["body_build"],
        "head_proportion_class": compat["head_proportion"],
        "muzzle_proportion_class": compat["muzzle_proportion"],
        "ear_form": compat["ear_form"],
        "tail_form": compat["tail_form"],
    }
    relevant_morph_fields = [f for f in match_fields if f in compat_by_field]
    if not relevant_morph_fields:
        relevant_morph_fields = ["body_size_class", "leg_length_class", "body_length_class"]
    morph = [compat_by_field[f] for f in relevant_morph_fields]
    if MISMATCH in morph:
        return None  # 명백히 다른 체형 — 사용하지 않는다 (generic 은 UNVERIFIED 로 남는다)

    # 펫 쪽 축이 UNKNOWN 인 경우(측정 불가 또는 신뢰도 하한 미달)는 **무시**한다.
    # 예전에는 UNVERIFIED 로 남아 나머지 축이 정확히 맞아도 후보 전체를 LEVEL_3
    # 으로 끌어내렸다 — 모르는 축은 판단 근거가 아니지 감점 사유가 아니다.
    # 레퍼런스 쪽 UNKNOWN(= generic 자산)은 그대로 UNVERIFIED 로 남아 저하된다.
    comparable_fields = [
        f
        for f in relevant_morph_fields
        if str(profile.get(f) or UNKNOWN).strip().upper() != UNKNOWN
    ]
    ignored_fields = [f for f in relevant_morph_fields if f not in comparable_fields]
    comparable = [compat_by_field[f] for f in comparable_fields]

    vd = [compat["view"], compat["direction"], compat["speed"]]
    if ref.source_type == "PET_OWN_MOTION" and ref.pet_id and ref.pet_id == pet_id:
        level = LEVEL_OWN  # 펫 자신의 모션 — 항상 최우선 (생애 아카이브 계약)
    elif comparable and all(c == EXACT for c in comparable) and all(c in (EXACT, UNSPECIFIED) for c in vd):
        level = LEVEL_1  # 정확한 형태 + 정확한 뷰/방향
    elif comparable and UNVERIFIED not in comparable and MISMATCH not in vd:
        level = LEVEL_2  # 인접(NEAR) 형태 — 알고 맞춘 근접 매칭
    else:
        # 비교 가능한 축이 하나도 없으면 구조 검증이 없는 것이다 — 조용히
        # LEVEL_1 로 승격하지 않고 generic 과 같은 LEVEL_3 로 둔다.
        level = LEVEL_3  # generic 형태(UNVERIFIED) 또는 뷰/방향 어긋남 — 명시적 저하

    compat_keys_for_rank = {
        "species",
        "motion",
        "view",
        "direction",
        "speed",
    }
    compat_keys_for_rank.update(
        {
            "body_size" if f == "body_size_class" else
            "leg_class" if f == "leg_length_class" else
            "body_class" if f == "body_length_class" else
            "body_build" if f == "body_build_class" else
            "head_proportion" if f == "head_proportion_class" else
            "muzzle_proportion" if f == "muzzle_proportion_class" else
            f
            for f in comparable_fields
        }
    )
    exact_count = sum(1 for k, c in compat.items() if k in compat_keys_for_rank and c == EXACT)
    unverified = sum(1 for k, c in compat.items() if k in compat_keys_for_rank and c == UNVERIFIED)
    return {
        "reference": ref,
        "compatibility": compat,
        "selection_level": level,
        "morphology_axes": {
            "match_fields": relevant_morph_fields,
            "compared": comparable_fields,
            "ignored_unknown_profile_fields": ignored_fields,
        },
        "_rank": (
            {LEVEL_OWN: 0, LEVEL_1: 1, LEVEL_2: 2, LEVEL_3: 3}[level],
            -exact_count,
            unverified,
            -ref.version,
            ref.reference_key,
        ),
    }


async def resolve_motion_reference(
    *,
    profile: dict[str, Any],
    motion_id: str,
    pet_id: Optional[str] = None,
    desired_view: Optional[str] = None,
    direction: Optional[str] = None,
    speed: Optional[str] = None,
    motion_requirements: Optional[dict[str, Any]] = None,
    include_candidates: bool = False,
) -> Optional[dict[str, Any]]:
    """
    최적 레퍼런스 (결정론). 없으면 None — 정책 적용은 호출자 몫 (LEVEL_4).
    프로덕션 풀: enabled ∧ APPROVED ∧ 같은 종 ∧ 같은 모션.
    """
    species = profile.get("species") or UNKNOWN
    if species == UNKNOWN:
        return None  # 종을 모르면 레퍼런스를 고르지 않는다

    mid = (motion_id or "").strip().upper()
    pool = [
        r
        for r in await list_references(species=species, motion_id=mid, enabled=True)
        if r.quality_status == "APPROVED"
    ]
    assessed = [
        a
        for r in pool
        if (
            a := _assess(
                profile, r, pet_id=pet_id, desired_view=desired_view,
                direction=direction, speed=speed, motion_requirements=motion_requirements,
            )
        )
    ]
    if not assessed:
        return None
    assessed.sort(key=lambda a: a["_rank"])
    best = assessed[0]

    def _payload(a: dict[str, Any]) -> dict[str, Any]:
        ref: MotionReference = a["reference"]
        return {
            "reference_key": ref.reference_key,
            "version": ref.version,
            "motion_id": ref.motion_id,
            "asset": (
                {"bucket": ref.bucket, "object_path": ref.object_path}
                if ref.object_path
                else None
            ),
            "camera_view": ref.camera_view,
            "travel_direction": ref.travel_direction,
            "speed_class": ref.speed_class,
            "loopable": ref.loopable,
            "duration_sec": ref.duration_sec,
            "quality": ref.quality_status,
            "body_size_class": ref.body_size_class,
            "leg_length_class": ref.leg_length_class,
            "body_length_class": ref.body_length_class,
            "body_build_class": ref.body_build_class,
            "head_proportion_class": ref.head_proportion_class,
            "muzzle_proportion_class": ref.muzzle_proportion_class,
            "ear_form": ref.ear_form,
            "tail_form": ref.tail_form,
            "provenance": {
                "source_type": ref.source_type,
                "license": ref.license,
                "provider": ref.provider_name,
                "commercial_use_allowed": ref.commercial_use_allowed,
            },
            "compatibility": a["compatibility"],
            "selection_level": a["selection_level"],
            "morphology_axes": a["morphology_axes"],
        }

    out = {
        "matching_version": MOTION_MATCHING_VERSION,
        **_payload(best),
    }
    if include_candidates:
        out["candidates"] = [_payload(a) for a in assessed[:10]]
    return out
