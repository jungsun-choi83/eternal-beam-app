"""
액션 키프레임 빌더 (Phase 5).

── 파이프라인 ──────────────────────────────────────────────────────────────
승인된 정본 펫(Phase 4) + 역할 스펙(action_keyframe_spec) →
  1. 정본 앵커 확정 — 신원의 출발점은 항상 정본 raw 다. 매 액션마다 고객
     원본에서 신원을 다시 만들지 않는다. REVIEW 정본은 기본 거절
     (KEYFRAME_ALLOW_REVIEW_CANONICAL=1 로만 명시적으로 허용).
  2. 보조 신원 제약 — 신뢰 세트의 PRIMARY_FACE/FULL_BODY 원본 최대 2장.
  3. Phase 4 프로바이더/후보/한도 정책 **그대로 재사용** — 프롬프트만
     "같은 펫, 통제된 다른 포즈"다.
  4. QA = 정본 QA(신원/코트/누끼) + 포즈 QA(VLM) + 해부학. 포즈가 바뀌는
     역할(LIE/SLEEP)은 프로필 비율 비교를 끄고 VLM 해부학 확인이 구조 검증을
     대신한다. VLM 확언 없으면 최대 REVIEW.
  5. 선택 → 대장 기록(role='generated', kind keyframe_raw/cutout) → 불변 버전.

테마/배경/환경 오브젝트 없음. 프로덕션 Luma/Wan 경로는 이 모듈과 무관하다.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

KEYFRAME_BUILDER_VERSION = "keyframe-builder-v1"
KEYFRAME_QA_VERSION = "keyframe-qa-v1"

STATUS_BUILDING = "building"
STATUS_COMPLETE = "complete"
STATUS_REVIEW = "review"
STATUS_FAILED = "failed"

GENERATED_KIND_RAW = "keyframe_raw"
GENERATED_KIND_CUTOUT = "keyframe_cutout"

#: 프로필 bbox 비율 비교가 무의미해지는(포즈가 실루엣을 바꾸는) 역할.
_POSE_CHANGING_ROLES = ("LIE", "SLEEP")


class ActionKeyframeError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _keyframes_table() -> str:
    return os.getenv("PET_ACTION_KEYFRAMES_TABLE", "pet_action_keyframes")


def _candidates_table() -> str:
    return os.getenv("PET_ACTION_KEYFRAME_CANDIDATES_TABLE", "pet_action_keyframe_candidates")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


_MOCK_KEYFRAMES: list[dict[str, Any]] = []
_MOCK_CANDIDATES: list[dict[str, Any]] = []


def __reset_for_tests() -> None:
    _MOCK_KEYFRAMES.clear()
    _MOCK_CANDIDATES.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _allow_review_canonical() -> bool:
    """REVIEW 정본을 앵커로 쓸지 — 기본 거절, 명시적 정책으로만 허용."""
    return os.getenv("KEYFRAME_ALLOW_REVIEW_CANONICAL", "0").strip().lower() in ("1", "true", "yes")


def analyzer_versions() -> dict[str, Any]:
    from . import action_keyframe_spec, canonical_pet_service

    return {
        **canonical_pet_service.analyzer_versions(),
        "keyframe_builder": KEYFRAME_BUILDER_VERSION,
        "keyframe_spec": action_keyframe_spec.KEYFRAME_SPEC_VERSION,
        "keyframe_prompt": action_keyframe_spec.KEYFRAME_PROMPT_VERSION,
        "keyframe_qa": KEYFRAME_QA_VERSION,
    }


# ══════════════════════════════════════════════════════════════════════════
# 키프레임 QA — 정본 QA + 포즈
# ══════════════════════════════════════════════════════════════════════════


def evaluate_keyframe_candidate(
    *,
    cutout_rgba: Optional[np.ndarray],
    profile: Any,
    canonical_signature: Optional[dict[str, Any]],
    reference_signatures: list[dict[str, Any]],
    spec: Any,
    vlm_qa: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """
    신원(정본 시그니처 우선) + 포즈 + 해부학 + 사용성.

    포즈 판정은 VLM 전용이다 — 실루엣 휴리스틱으로 "엎드림"을 판정하는 것은
    Phase 2 부터 일관되게 거부해 온 종류의 추측이다. VLM 없으면 pose=unknown →
    최대 REVIEW.
    """
    from . import canonical_qa

    pose_changing = spec.role in _POSE_CHANGING_ROLES
    sigs = ([canonical_signature] if canonical_signature else []) + list(reference_signatures)
    base = canonical_qa.evaluate_candidate(
        cutout_rgba=cutout_rgba,
        profile=profile,
        reference_signatures=sigs,
        vlm_qa=vlm_qa,
        compare_structure=not pose_changing,
    )
    checks = dict(base["checks"])
    reasons = list(base["reasons"])

    def v(key: str) -> str:
        return str((vlm_qa or {}).get(key) or "unknown")

    if vlm_qa:
        if v("pose_matches") == "no" or v("body_orientation_ok") == "no":
            checks["pose"] = canonical_qa.FAIL
            reasons.append("pose_not_achieved")
        elif v("pose_matches") == "yes":
            if v("required_regions_visible") == "no":
                checks["pose"] = canonical_qa.REVIEW
                reasons.append("required_regions_not_visible")
            else:
                checks["pose"] = canonical_qa.PASS
        else:
            checks["pose"] = "unknown"
            reasons.append("pose_uncertain")
    else:
        checks["pose"] = "unknown"
        reasons.append("pose_qa_unavailable")

    # 포즈가 바뀌는 역할: 프로필 비율 비교 대신 VLM 해부학 확인이 구조를 담당한다.
    if pose_changing and checks.get("structure") == "unknown" and checks.get("vlm_anatomy") == canonical_qa.PASS:
        checks["structure"] = canonical_qa.PASS
        reasons.append("structure_via_vlm_anatomy")

    values = list(checks.values())
    if canonical_qa.FAIL in values:
        decision = canonical_qa.FAIL
    elif all(x == canonical_qa.PASS for x in values):
        decision = canonical_qa.PASS
    else:
        decision = canonical_qa.REVIEW

    return {
        "qa_version": KEYFRAME_QA_VERSION,
        "base_qa_version": base["qa_version"],
        "identity_similarity": base["identity_similarity"],
        "checks": checks,
        "reasons": reasons,
        "decision": decision,
        "pose": {
            "required": spec.required_pose,
            "matches": v("pose_matches"),
            "confidence": v("pose_confidence"),
        },
        "vlm": base.get("vlm"),
    }


# ══════════════════════════════════════════════════════════════════════════
# 데이터 모델
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class KeyframeCandidate:
    id: str
    keyframe_id: str
    provider: str
    attempt: int
    decision: str
    model: Optional[str] = None
    external_job_id: Optional[str] = None
    raw_object_path: Optional[str] = None
    cutout_object_path: Optional[str] = None
    input_canonical_candidate_id: Optional[str] = None
    input_reference_ids: list[str] = field(default_factory=list)
    generation_metadata: dict[str, Any] = field(default_factory=dict)
    qa_result: dict[str, Any] = field(default_factory=dict)
    selected: bool = False
    error: Optional[str] = None
    created_at: Optional[str] = None


@dataclass(frozen=True)
class ActionKeyframe:
    id: str
    pet_id: str
    user_id: str
    keyframe_role: str
    version: int
    status: str
    canonical_version_id: Optional[str] = None
    canonical_version: Optional[int] = None
    selected_candidate_id: Optional[str] = None
    selection_reason: Optional[str] = None
    prompt: Optional[str] = None
    prompt_version: Optional[str] = None
    spec: dict[str, Any] = field(default_factory=dict)
    qa_summary: dict[str, Any] = field(default_factory=dict)
    analyzer_versions: dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    candidates: list[KeyframeCandidate] = field(default_factory=list)
    deduplicated: bool = False


def _to_candidate(row: dict[str, Any]) -> KeyframeCandidate:
    return KeyframeCandidate(
        id=str(row.get("id")),
        keyframe_id=str(row.get("keyframe_id")),
        provider=str(row.get("provider") or ""),
        model=(row.get("model") or None),
        attempt=int(row.get("attempt") or 1),
        external_job_id=(row.get("external_job_id") or None),
        raw_object_path=(row.get("raw_object_path") or None),
        cutout_object_path=(row.get("cutout_object_path") or None),
        input_canonical_candidate_id=(
            str(row["input_canonical_candidate_id"]) if row.get("input_canonical_candidate_id") else None
        ),
        input_reference_ids=list(row.get("input_reference_ids") or []),
        generation_metadata=dict(row.get("generation_metadata") or {}),
        qa_result=dict(row.get("qa_result") or {}),
        decision=str(row.get("decision") or "ERROR"),
        selected=bool(row.get("selected")),
        error=(row.get("error") or None),
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
    )


def _to_keyframe(
    row: dict[str, Any], candidates: list[dict[str, Any]], *, deduplicated: bool = False
) -> ActionKeyframe:
    return ActionKeyframe(
        id=str(row.get("id")),
        pet_id=str(row.get("pet_id") or ""),
        user_id=str(row.get("user_id") or ""),
        keyframe_role=str(row.get("keyframe_role") or ""),
        version=int(row.get("version") or 1),
        status=str(row.get("status") or STATUS_BUILDING),
        canonical_version_id=(str(row["canonical_version_id"]) if row.get("canonical_version_id") else None),
        canonical_version=row.get("canonical_version"),
        selected_candidate_id=(
            str(row["selected_candidate_id"]) if row.get("selected_candidate_id") else None
        ),
        selection_reason=(row.get("selection_reason") or None),
        prompt=(row.get("prompt") or None),
        prompt_version=(row.get("prompt_version") or None),
        spec=dict(row.get("spec") or {}),
        qa_summary=dict(row.get("qa_summary") or {}),
        analyzer_versions=dict(row.get("analyzer_versions") or {}),
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
        completed_at=(str(row["completed_at"]) if row.get("completed_at") else None),
        candidates=[
            _to_candidate(c)
            for c in sorted(candidates, key=lambda c: (str(c.get("created_at") or ""), int(c.get("attempt") or 0)))
        ],
        deduplicated=deduplicated,
    )


async def _keyframe_rows(pet_id: str, role: Optional[str] = None) -> list[dict[str, Any]]:
    if _use_db() and _supabase():
        try:
            q = _supabase().table(_keyframes_table()).select("*").eq("pet_id", pet_id)
            if role:
                q = q.eq("keyframe_role", role)
            r = q.order("version", desc=False).execute()
            return getattr(r, "data", None) or []
        except Exception as e:
            logger.exception("키프레임 조회 실패 (pet=%s)", pet_id)
            raise ActionKeyframeError(
                "KEYFRAMES_UNAVAILABLE", "키프레임을 확인하지 못했습니다.", status=503
            ) from e
    return [
        r
        for r in _MOCK_KEYFRAMES
        if r.get("pet_id") == pet_id and (role is None or r.get("keyframe_role") == role)
    ]


async def _candidate_rows(keyframe_id: str) -> list[dict[str, Any]]:
    if _use_db() and _supabase():
        try:
            r = (
                _supabase()
                .table(_candidates_table())
                .select("*")
                .eq("keyframe_id", keyframe_id)
                .execute()
            )
            return getattr(r, "data", None) or []
        except Exception:
            logger.exception("키프레임 후보 조회 실패 (kf=%s)", keyframe_id)
            return []
    return [c for c in _MOCK_CANDIDATES if c.get("keyframe_id") == keyframe_id]


# ══════════════════════════════════════════════════════════════════════════
# 빌드
# ══════════════════════════════════════════════════════════════════════════


def _rank_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from . import canonical_qa

    decision_rank = {canonical_qa.PASS: 0, canonical_qa.REVIEW: 1, canonical_qa.FAIL: 2, "ERROR": 3}
    return sorted(
        [c for c in rows if c["decision"] != "ERROR"],
        key=lambda c: (
            decision_rank.get(c["decision"], 9),
            -float((c.get("qa_result") or {}).get("identity_similarity") or -1.0),
            c["attempt"],
        ),
    )


async def build_keyframe(
    *,
    user_id: str,
    pet_id: str,
    keyframe_role: str,
    fetch_bytes: Optional[Callable[[Any], Optional[bytes]]] = None,
    providers: Optional[Sequence[Any]] = None,
    cutout_fn: Optional[Callable[[bytes], Optional[bytes]]] = None,
    sign_url_fn: Optional[Callable[[Any], Optional[str]]] = None,
    skip_if_unchanged: bool = True,
) -> ActionKeyframe:
    from . import (
        action_keyframe_spec,
        canonical_image_providers,
        canonical_pet_service,
        canonical_qa,
        pet_identity_service,
        pet_reference_service,
        pet_reference_set_service,
        supabase_assets,
        vlm_identity,
    )
    from .canonical_image_providers import CanonicalProviderError, CanonicalReference

    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    spec = action_keyframe_spec.get_role(keyframe_role)
    if not uid or not pid:
        raise ActionKeyframeError("KEYFRAME_INVALID", "user_id 와 pet_id 가 필요합니다.")
    if not spec:
        raise ActionKeyframeError(
            "UNKNOWN_KEYFRAME_ROLE",
            f"지원하지 않는 키프레임 역할입니다: {keyframe_role}",
            status=422,
        )

    resolved_providers = list(providers) if providers is not None else canonical_image_providers.resolve_providers()
    resolved_providers = [p for p in resolved_providers if p.available()]
    if not resolved_providers:
        raise ActionKeyframeError(
            "PROVIDER_NOT_CONFIGURED", "이미지 프로바이더가 설정되지 않았습니다.", status=503
        )

    # ── 정본 앵커 (소유권 검사 포함) ──────────────────────────────────────
    try:
        canonical = await canonical_pet_service.get_canonical(user_id=uid, pet_id=pid)
    except canonical_pet_service.CanonicalPetError as e:
        raise ActionKeyframeError(e.code, e.message, status=e.status) from e
    if not canonical:
        raise ActionKeyframeError(
            "CANONICAL_REQUIRED", "승인된 정본 펫이 없습니다 — 먼저 정본을 빌드하세요.", status=409
        )

    anchor = next((c for c in canonical.candidates if c.selected), None)
    if canonical.status == canonical_pet_service.STATUS_REVIEW and anchor is None:
        if not _allow_review_canonical():
            raise ActionKeyframeError(
                "CANONICAL_NOT_APPROVED",
                "정본이 REVIEW 상태입니다 — 승인 전에는 키프레임을 만들지 않습니다 "
                "(KEYFRAME_ALLOW_REVIEW_CANONICAL=1 로만 명시적 허용).",
                status=409,
            )
        ranked = _rank_candidates(
            [
                {
                    "decision": c.decision,
                    "qa_result": c.qa_result,
                    "attempt": c.attempt,
                    "_obj": c,
                }
                for c in canonical.candidates
            ]
        )
        anchor = ranked[0]["_obj"] if ranked else None
    if canonical.status == canonical_pet_service.STATUS_FAILED or anchor is None:
        raise ActionKeyframeError(
            "CANONICAL_NOT_APPROVED", "쓸 수 있는 정본 후보가 없습니다.", status=409
        )

    fetch = fetch_bytes or pet_identity_service._default_fetch_bytes
    sign = sign_url_fn or canonical_pet_service._default_sign_url
    cutout = cutout_fn or canonical_pet_service._default_cutout_fn

    def _obj(bucket: Optional[str], path: Optional[str]):
        return SimpleNamespace(bucket=bucket or "", object_path=path or "", mime_type="image/png")

    anchor_ref = _obj(anchor.raw_bucket, anchor.raw_object_path)
    anchor_bytes = fetch(anchor_ref)
    if not anchor_bytes:
        raise ActionKeyframeError(
            "CANONICAL_ASSET_UNAVAILABLE", "정본 raw 이미지를 불러오지 못했습니다.", status=503
        )

    canonical_signature = None
    if anchor.cutout_object_path:
        cut_bytes = fetch(_obj(anchor.cutout_bucket, anchor.cutout_object_path))
        if cut_bytes:
            rgba = pet_identity_service.load_rgba(cut_bytes)
            if rgba is not None:
                canonical_signature = pet_identity_service.compute_reference_signature(rgba)

    # ── 보조 신원 제약: 신뢰 세트의 얼굴/전신 원본 최대 2장 ───────────────
    refset = await pet_reference_set_service.get_set(
        user_id=uid, pet_id=pid, version=canonical.reference_set_version
    )
    refs = await pet_reference_service.list_references(user_id=uid, pet_id=pid)
    refs_by_id = {str(r.id): r for r in refs}

    provider_refs: list[CanonicalReference] = [
        CanonicalReference(
            reference_id=f"canonical:{anchor.id}",
            role="CANONICAL",
            url=sign(anchor_ref),
            data=anchor_bytes,
            mime_type="image/png",
        )
    ]
    reference_signatures: list[dict[str, Any]] = []
    secondary_ids: list[str] = []
    vlm_ref_images: list[tuple[bytes, str]] = [(anchor_bytes, "image/png")]
    if refset:
        by_role = {i["role"]: i for i in refset.items}
        for role in ("PRIMARY_FACE", "PRIMARY_FULL_BODY"):
            item = by_role.get(role)
            if not item or len(provider_refs) >= 3:
                continue
            rid = str(item["reference_id"])
            ref = refs_by_id.get(rid)
            if not ref or rid in secondary_ids:
                continue
            data = fetch(ref)
            if not data:
                continue
            secondary_ids.append(rid)
            provider_refs.append(
                CanonicalReference(
                    reference_id=rid, role=role, url=sign(ref), data=data,
                    mime_type=ref.mime_type or "image/jpeg",
                )
            )
            if len(vlm_ref_images) < vlm_identity.MAX_IMAGES:
                vlm_ref_images.append((data, ref.mime_type or "image/jpeg"))
            sig = ((refset.reference_analysis.get(rid) or {}).get("eligibility") or {}).get("signature")
            if sig:
                reference_signatures.append(sig)

    profile = await pet_identity_service.get_profile(
        user_id=uid, pet_id=pid, version=canonical.identity_profile_version
    )
    prompt = action_keyframe_spec.build_keyframe_prompt(
        spec, (profile.visual_identity if profile else {})
    )

    versions_stamp = {
        **analyzer_versions(),
        "canonical_providers": [f"{p.name}:{p.model_name()}" for p in resolved_providers],
    }

    # ── 멱등: 같은 정본/프롬프트/구성의 비-실패 최신 버전 재사용 ──────────
    if skip_if_unchanged:
        rows = await _keyframe_rows(pid, spec.role)
        if rows:
            latest = rows[-1]
            if (
                latest.get("status") in (STATUS_COMPLETE, STATUS_REVIEW)
                and str(latest.get("canonical_version_id")) == str(canonical.id)
                and latest.get("prompt_version") == action_keyframe_spec.KEYFRAME_PROMPT_VERSION
                and (latest.get("analyzer_versions") or {}) == versions_stamp
            ):
                return _to_keyframe(latest, await _candidate_rows(str(latest["id"])), deduplicated=True)

    cid = pid[4:] if pid.startswith("pet_") else pid
    policy = canonical_pet_service.candidate_policy()

    rows = await _keyframe_rows(pid, spec.role)
    kf_row: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "pet_id": pid,
        "user_id": uid,
        "canonical_version_id": canonical.id,
        "canonical_version": canonical.version,
        "keyframe_role": spec.role,
        "version": (max((int(r.get("version") or 0) for r in rows), default=0)) + 1,
        "status": STATUS_BUILDING,
        "selected_candidate_id": None,
        "selection_reason": None,
        "prompt": prompt,
        "prompt_version": action_keyframe_spec.KEYFRAME_PROMPT_VERSION,
        "spec": action_keyframe_spec.role_spec_snapshot(spec),
        "qa_summary": {},
        "analyzer_versions": versions_stamp,
        "created_at": _now_iso(),
        "completed_at": None,
    }
    if not await canonical_pet_service._insert(_keyframes_table(), _MOCK_KEYFRAMES, kf_row):
        raise ActionKeyframeError(
            "KEYFRAMES_UNAVAILABLE", "키프레임 버전을 기록하지 못했습니다.", status=503
        )
    keyframe_id = kf_row["id"]
    input_ids = [f"canonical:{anchor.id}"] + secondary_ids

    candidates: list[dict[str, Any]] = []
    passes = 0

    async def run_provider(provider: Any, max_candidates: int, tier: str) -> None:
        nonlocal passes
        for attempt in range(1, max_candidates + 1):
            if passes >= policy["stop_after_passes"]:
                return
            cand_id = str(uuid.uuid4())
            cand_row: dict[str, Any] = {
                "id": cand_id,
                "keyframe_id": keyframe_id,
                "pet_id": pid,
                "user_id": uid,
                "keyframe_role": spec.role,
                "provider": provider.name,
                "model": provider.model_name(),
                "model_version": None,
                "attempt": attempt,
                "external_job_id": None,
                "raw_bucket": None,
                "raw_object_path": None,
                "cutout_bucket": None,
                "cutout_object_path": None,
                "prompt_version": action_keyframe_spec.KEYFRAME_PROMPT_VERSION,
                "input_canonical_candidate_id": anchor.id,
                "input_reference_ids": input_ids,
                "generation_metadata": {"tier": tier},
                "qa_result": {},
                "decision": "ERROR",
                "selected": False,
                "error": None,
                "created_at": _now_iso(),
            }
            try:
                logger.info(
                    "[keyframe-receipt] pet=%s role=%s v=%s provider=%s attempt=%d",
                    pid, spec.role, kf_row["version"], provider.name, attempt,
                )
                result = provider.generate(
                    provider_refs, prompt,
                    {**action_keyframe_spec.role_spec_snapshot(spec), "ratio": "1024:1024", "size": "1024x1024"},
                    {"pet_id": pid, "keyframe_id": keyframe_id, "attempt": attempt},
                )
            except CanonicalProviderError as e:
                cand_row["error"] = f"{e.code}: {e.message}"[:500]
                await canonical_pet_service._insert(_candidates_table(), _MOCK_CANDIDATES, cand_row)
                candidates.append(cand_row)
                continue

            cand_row["model"] = result.model
            cand_row["external_job_id"] = result.external_job_id
            cand_row["generation_metadata"] = {"tier": tier, "usage": result.usage}

            raw_path = (
                f"{uid}/{cid}/keyframes/{spec.role.lower()}/v{kf_row['version']}/"
                f"{provider.name}_a{attempt}_raw.png"
            )
            try:
                await supabase_assets.upload_asset_to_storage(raw_path, result.image_bytes, "image/png")
                cand_row["raw_bucket"] = supabase_assets.BUCKET
                cand_row["raw_object_path"] = raw_path
            except Exception:
                cand_row["error"] = "RAW_STORE_FAILED"
                await canonical_pet_service._insert(_candidates_table(), _MOCK_CANDIDATES, cand_row)
                candidates.append(cand_row)
                continue

            await canonical_pet_service._insert(_candidates_table(), _MOCK_CANDIDATES, cand_row)
            candidates.append(cand_row)

            cutout_rgba = None
            cut_bytes = cutout(result.image_bytes)
            if cut_bytes:
                cut_path = raw_path.replace("_raw.png", "_cutout.png")
                try:
                    await supabase_assets.upload_asset_to_storage(cut_path, cut_bytes, "image/png")
                    cand_row["cutout_bucket"] = supabase_assets.BUCKET
                    cand_row["cutout_object_path"] = cut_path
                except Exception:
                    logger.exception("키프레임 누끼 저장 실패")
                cutout_rgba = pet_identity_service.load_rgba(cut_bytes)

            vlm_qa = vlm_identity.qa_action_keyframe(
                result.image_bytes,
                vlm_ref_images,
                required_pose=spec.required_pose,
                required_visibility=spec.required_visibility,
            )
            qa = evaluate_keyframe_candidate(
                cutout_rgba=cutout_rgba,
                profile=profile,
                canonical_signature=canonical_signature,
                reference_signatures=reference_signatures,
                spec=spec,
                vlm_qa=vlm_qa,
            )
            cand_row["qa_result"] = qa
            cand_row["decision"] = qa["decision"]
            await canonical_pet_service._update(
                _candidates_table(), _MOCK_CANDIDATES, cand_id,
                {
                    "model": cand_row["model"],
                    "external_job_id": cand_row["external_job_id"],
                    "generation_metadata": cand_row["generation_metadata"],
                    "cutout_bucket": cand_row["cutout_bucket"],
                    "cutout_object_path": cand_row["cutout_object_path"],
                    "qa_result": qa,
                    "decision": qa["decision"],
                },
            )
            if qa["decision"] == canonical_qa.PASS:
                passes += 1

    await run_provider(resolved_providers[0], policy["max_primary"], "primary")
    if passes == 0 and len(resolved_providers) > 1:
        await run_provider(resolved_providers[1], policy["max_fallback"], "fallback")

    ranked = _rank_candidates(candidates)
    selected = ranked[0] if ranked and ranked[0]["decision"] == canonical_qa.PASS else None
    if selected:
        status = STATUS_COMPLETE
        selection_reason = (
            f"best PASS candidate: {selected['provider']} attempt {selected['attempt']}, "
            f"identity_similarity={selected['qa_result'].get('identity_similarity')}"
        )
        await canonical_pet_service._update(
            _candidates_table(), _MOCK_CANDIDATES, selected["id"], {"selected": True}
        )
        selected["selected"] = True
        provenance = {
            "keyframe_id": keyframe_id,
            "keyframe_role": spec.role,
            "canonical_version_id": canonical.id,
            "candidate_id": selected["id"],
            "input_reference_ids": input_ids,
            "provider": selected["provider"],
            "model": selected["model"],
        }
        for path, kind in (
            (selected.get("raw_object_path"), GENERATED_KIND_RAW),
            (selected.get("cutout_object_path"), GENERATED_KIND_CUTOUT),
        ):
            if path:
                try:
                    await pet_reference_service.record_generated(
                        user_id=uid, content_id=cid, object_path=path,
                        generated_kind=kind, mime_type="image/png", provenance=provenance,
                    )
                except Exception:
                    logger.warning("키프레임 대장 기록 실패 (path=%s)", path, exc_info=True)
    elif any(c["decision"] == canonical_qa.REVIEW for c in candidates):
        status = STATUS_REVIEW
        selection_reason = "no PASS candidate — human review required"
    else:
        status = STATUS_FAILED
        selection_reason = "no usable candidate"

    final_fields = {
        "status": status,
        "selected_candidate_id": (selected["id"] if selected else None),
        "selection_reason": selection_reason,
        "qa_summary": {
            "candidate_count": len(candidates),
            "decisions": {
                d: sum(1 for c in candidates if c["decision"] == d)
                for d in ("PASS", "REVIEW", "FAIL", "ERROR")
            },
            "policy": policy,
        },
        "completed_at": _now_iso(),
    }
    await canonical_pet_service._update(_keyframes_table(), _MOCK_KEYFRAMES, keyframe_id, final_fields)
    kf_row.update(final_fields)
    return _to_keyframe(kf_row, candidates)


# ══════════════════════════════════════════════════════════════════════════
# 조회 / 평가
# ══════════════════════════════════════════════════════════════════════════


async def _assert_owned(user_id: str, pet_id: str) -> None:
    from . import pet_reference_service

    try:
        await pet_reference_service.list_references(user_id=user_id, pet_id=pet_id)
    except pet_reference_service.PetReferenceError as e:
        raise ActionKeyframeError(e.code, e.message, status=e.status) from e


async def get_keyframe(
    *, user_id: str, pet_id: str, keyframe_role: str, version: Optional[int] = None
) -> Optional[ActionKeyframe]:
    await _assert_owned(user_id, pet_id)
    role = (keyframe_role or "").strip().upper()
    rows = await _keyframe_rows(pet_id, role)
    if not rows:
        return None
    row = None
    if version is not None:
        for r in rows:
            if int(r.get("version") or 0) == version:
                row = r
                break
    else:
        row = max(rows, key=lambda r: int(r.get("version") or 0))
    if not row:
        return None
    return _to_keyframe(row, await _candidate_rows(str(row["id"])))


async def list_keyframes(*, user_id: str, pet_id: str) -> list[ActionKeyframe]:
    """역할별 최신 버전만."""
    await _assert_owned(user_id, pet_id)
    rows = await _keyframe_rows(pet_id)
    latest: dict[str, dict[str, Any]] = {}
    for r in rows:
        role = str(r.get("keyframe_role") or "")
        if role not in latest or int(r.get("version") or 0) > int(latest[role].get("version") or 0):
            latest[role] = r
    return [_to_keyframe(r, []) for r in latest.values()]


async def record_keyframe_evaluation(
    *,
    user_id: str,
    pet_id: str,
    keyframe_id: str,
    candidate_id: Optional[str],
    scores: dict[str, Any],
    verdict: str,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Phase 4 평가 하네스 재사용 — 같은 테이블, kind='keyframe' + 역할/프로바이더 기록."""
    from . import canonical_pet_service

    provider = None
    role = None
    for c in await _candidate_rows(keyframe_id):
        if candidate_id and str(c.get("id")) == candidate_id:
            provider = c.get("provider")
            role = c.get("keyframe_role")
            break
    try:
        return await canonical_pet_service.record_evaluation(
            user_id=user_id,
            pet_id=pet_id,
            canonical_version_id=keyframe_id,
            candidate_id=candidate_id,
            scores={**scores, **({"keyframe_role": role} if role else {})},
            verdict=verdict,
            notes=notes,
            provider=provider,
            kind="keyframe",
        )
    except canonical_pet_service.CanonicalPetError as e:
        raise ActionKeyframeError(e.code, e.message, status=e.status) from e
