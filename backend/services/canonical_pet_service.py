"""
정본 펫 빌더 (Canonical Pet Builder, Phase 4).

── 파이프라인 ──────────────────────────────────────────────────────────────
Phase 3 신뢰 레퍼런스 세트(멱등 보장) →
  1. 입력 레퍼런스 선택 — PRIMARY_FACE → PRIMARY_FULL_BODY → PRIMARY_3Q/최고
     측면. 최대 3장 (Runway References 한도와 일치). 업로드된 사진 전부를
     넘기지 않는다 — 가장 강한 상보적 증거만.
  2. 버전 행을 프로바이더 호출 **전에** 기록 (과금 영수증 — generation_safety
     의 교훈: 돈이 나간 뒤에 기록이 실패하면 복구 불가).
  3. PRIMARY 프로바이더로 후보 생성 (최대 CANONICAL_MAX_PRIMARY, 충분한 PASS
     가 모이면 조기 중단). 각 후보는 raw 저장 → 행 기록 → 누끼 파생 → QA 순 —
     **QA 이전에 저장**되므로 과금된 생성물이 사라지지 않는다.
  4. PRIMARY 전체가 PASS 를 못 내면 FALLBACK 프로바이더 (최대
     CANONICAL_MAX_FALLBACK). 불필요하게 둘 다 호출하지 않는다.
  5. 결정론적 랭킹 → 선택 → 선택된 raw/cutout 을 role='generated' 로 대장 기록.
  6. 버전 확정 (complete / review / failed). 버전은 불변 — 재빌드는 새 버전이다.

── 원칙 ────────────────────────────────────────────────────────────────────
* raw 는 생성 증거다 — 파괴하지 않는다. cutout 은 파생 보조 자산이다.
* 테마 없음. 정본은 PET ONLY 다.
* 프로바이더 실패(ERROR)와 QA 실패(FAIL)는 다른 것이다 — 섞지 않는다.
* VLM 확언 없는 후보는 최대 REVIEW 다 (canonical_qa 참고).
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Sequence

logger = logging.getLogger(__name__)

CANONICAL_BUILDER_VERSION = "canonical-builder-v1"

STATUS_BUILDING = "building"
STATUS_COMPLETE = "complete"
STATUS_REVIEW = "review"
STATUS_FAILED = "failed"

GENERATED_KIND_RAW = "canonical_raw"
GENERATED_KIND_CUTOUT = "canonical_cutout"

_EVAL_SCORE_KEYS = (
    "face_identity",
    "markings",
    "body_proportions",
    "tail_ears_paws",
    "anatomy",
    "overall_same_pet",
    # Phase 5 키프레임 평가 확장 — 같은 하네스/테이블을 쓴다.
    "pose_correctness",
    "phase6_suitability",
    # Phase 6 모션 비디오 평가 확장.
    "identity_fidelity",
    "motion_correctness",
    "temporal_stability",
    "naturalness",
    "start_end_quality",
)


class CanonicalPetError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _versions_table() -> str:
    return os.getenv("PET_CANONICAL_VERSIONS_TABLE", "pet_canonical_versions")


def _candidates_table() -> str:
    return os.getenv("PET_CANONICAL_CANDIDATES_TABLE", "pet_canonical_candidates")


def _evaluations_table() -> str:
    return os.getenv("PET_CANONICAL_EVALUATIONS_TABLE", "pet_canonical_evaluations")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


_MOCK_VERSIONS: list[dict[str, Any]] = []
_MOCK_CANDIDATES: list[dict[str, Any]] = []
_MOCK_EVALS: list[dict[str, Any]] = []


def __reset_for_tests() -> None:
    _MOCK_VERSIONS.clear()
    _MOCK_CANDIDATES.clear()
    _MOCK_EVALS.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def candidate_policy() -> dict[str, int]:
    """
    후보 상한 정책 — 전부 env 로 조정 가능 (요구 15).

    점진적 조기 중단 (Phase 7 최적화): 기본 stop_after_passes=1 — **첫 PASS 에서
    즉시 멈춘다.** 후보 N 의 QA 판정이 나기 전에 N+1 을 제출하는 일은 루프 구조상
    없고(순차 생성→QA), 이 값은 "PASS 뒤에도 여분을 만들 것인가"만 정한다.
    예전 기본 2 는 PASS 하나가 나온 뒤에도 유료 시도를 한 번 더 태웠다 —
    중복 PASS 가 필요하면 env 로 명시적으로 올린다.

    Phase 5 키프레임도 이 정책을 그대로 빌려 쓴다 (action_keyframe_service).
    Phase 1/3 업로드 레퍼런스에는 적용되지 않는다 — 그쪽은 생성이 아니다.
    """
    return {
        "max_primary": _int_env("CANONICAL_MAX_PRIMARY", 3),
        "max_fallback": _int_env("CANONICAL_MAX_FALLBACK", 2),
        "stop_after_passes": _int_env("CANONICAL_STOP_AFTER_PASSES", 1),
    }


def analyzer_versions() -> dict[str, Any]:
    from . import canonical_image_providers, canonical_prompt, canonical_qa, pet_reference_set_service

    providers = canonical_image_providers.resolve_providers()
    return {
        **pet_reference_set_service.analyzer_versions(),
        "canonical_builder": CANONICAL_BUILDER_VERSION,
        "canonical_prompt": canonical_prompt.CANONICAL_PROMPT_VERSION,
        "canonical_qa": canonical_qa.CANONICAL_QA_VERSION,
        "canonical_providers": [f"{p.name}:{p.model_name()}" for p in providers],
    }


# ══════════════════════════════════════════════════════════════════════════
# 데이터 모델
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CanonicalCandidate:
    id: str
    canonical_version_id: str
    pet_id: str
    user_id: str
    provider: str
    model: Optional[str]
    attempt: int
    decision: str
    model_version: Optional[str] = None
    external_job_id: Optional[str] = None
    raw_bucket: Optional[str] = None
    raw_object_path: Optional[str] = None
    cutout_bucket: Optional[str] = None
    cutout_object_path: Optional[str] = None
    prompt_version: Optional[str] = None
    input_reference_ids: list[str] = field(default_factory=list)
    generation_metadata: dict[str, Any] = field(default_factory=dict)
    qa_result: dict[str, Any] = field(default_factory=dict)
    selected: bool = False
    error: Optional[str] = None
    created_at: Optional[str] = None


@dataclass(frozen=True)
class CanonicalVersion:
    id: str
    pet_id: str
    user_id: str
    version: int
    status: str
    reference_set_id: Optional[str] = None
    reference_set_version: Optional[int] = None
    identity_profile_version: Optional[int] = None
    input_reference_ids: list[str] = field(default_factory=list)
    prompt: Optional[str] = None
    prompt_version: Optional[str] = None
    output_spec: dict[str, Any] = field(default_factory=dict)
    selected_candidate_id: Optional[str] = None
    selection_reason: Optional[str] = None
    qa_summary: dict[str, Any] = field(default_factory=dict)
    analyzer_versions: dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    candidates: list[CanonicalCandidate] = field(default_factory=list)
    deduplicated: bool = False


def _to_candidate(row: dict[str, Any]) -> CanonicalCandidate:
    return CanonicalCandidate(
        id=str(row.get("id")),
        canonical_version_id=str(row.get("canonical_version_id")),
        pet_id=str(row.get("pet_id") or ""),
        user_id=str(row.get("user_id") or ""),
        provider=str(row.get("provider") or ""),
        model=(row.get("model") or None),
        model_version=(row.get("model_version") or None),
        # attempt 0 = 계약 위반 감사 기록 (시도 소모 없음) — or-1 로 뭉개면 안 된다.
        attempt=int(row["attempt"]) if row.get("attempt") is not None else 1,
        external_job_id=(row.get("external_job_id") or None),
        raw_bucket=(row.get("raw_bucket") or None),
        raw_object_path=(row.get("raw_object_path") or None),
        cutout_bucket=(row.get("cutout_bucket") or None),
        cutout_object_path=(row.get("cutout_object_path") or None),
        prompt_version=(row.get("prompt_version") or None),
        input_reference_ids=list(row.get("input_reference_ids") or []),
        generation_metadata=dict(row.get("generation_metadata") or {}),
        qa_result=dict(row.get("qa_result") or {}),
        decision=str(row.get("decision") or "ERROR"),
        selected=bool(row.get("selected")),
        error=(row.get("error") or None),
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
    )


def _to_version(
    row: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    deduplicated: bool = False,
) -> CanonicalVersion:
    return CanonicalVersion(
        id=str(row.get("id")),
        pet_id=str(row.get("pet_id") or ""),
        user_id=str(row.get("user_id") or ""),
        version=int(row.get("version") or 1),
        status=str(row.get("status") or STATUS_BUILDING),
        reference_set_id=(str(row["reference_set_id"]) if row.get("reference_set_id") else None),
        reference_set_version=row.get("reference_set_version"),
        identity_profile_version=row.get("identity_profile_version"),
        input_reference_ids=list(row.get("input_reference_ids") or []),
        prompt=(row.get("prompt") or None),
        prompt_version=(row.get("prompt_version") or None),
        output_spec=dict(row.get("output_spec") or {}),
        selected_candidate_id=(
            str(row["selected_candidate_id"]) if row.get("selected_candidate_id") else None
        ),
        selection_reason=(row.get("selection_reason") or None),
        qa_summary=dict(row.get("qa_summary") or {}),
        analyzer_versions=dict(row.get("analyzer_versions") or {}),
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
        completed_at=(str(row["completed_at"]) if row.get("completed_at") else None),
        candidates=[_to_candidate(c) for c in sorted(candidates, key=lambda c: (str(c.get("created_at") or ""), int(c.get("attempt") or 0)))],
        deduplicated=deduplicated,
    )


# ══════════════════════════════════════════════════════════════════════════
# 저장 계층 (DB + mock)
# ══════════════════════════════════════════════════════════════════════════


async def _version_rows(pet_id: str) -> list[dict[str, Any]]:
    if _use_db() and _supabase():
        try:
            r = (
                _supabase()
                .table(_versions_table())
                .select("*")
                .eq("pet_id", pet_id)
                .order("version", desc=False)
                .execute()
            )
            return getattr(r, "data", None) or []
        except Exception as e:
            logger.exception("정본 버전 조회 실패 (pet=%s)", pet_id)
            raise CanonicalPetError(
                "CANONICAL_UNAVAILABLE", "정본 버전을 확인하지 못했습니다.", status=503
            ) from e
    return [r for r in _MOCK_VERSIONS if r.get("pet_id") == pet_id]


async def _candidate_rows(version_id: str) -> list[dict[str, Any]]:
    if _use_db() and _supabase():
        try:
            r = (
                _supabase()
                .table(_candidates_table())
                .select("*")
                .eq("canonical_version_id", version_id)
                .execute()
            )
            return getattr(r, "data", None) or []
        except Exception:
            logger.exception("정본 후보 조회 실패 (version=%s)", version_id)
            return []
    return [c for c in _MOCK_CANDIDATES if c.get("canonical_version_id") == version_id]


async def _insert(table: str, mock_store: list[dict[str, Any]], row: dict[str, Any]) -> bool:
    if _use_db() and _supabase():
        try:
            _supabase().table(table).insert(row).execute()
            return True
        except Exception:
            logger.exception("insert 실패 (table=%s)", table)
            return False
    mock_store.append(dict(row))
    return True


async def _update(table: str, mock_store: list[dict[str, Any]], row_id: str, fields: dict[str, Any]) -> None:
    if _use_db() and _supabase():
        try:
            _supabase().table(table).update(fields).eq("id", row_id).execute()
        except Exception:
            logger.exception("update 실패 (table=%s id=%s)", table, row_id)
        return
    for r in mock_store:
        if r.get("id") == row_id:
            r.update(fields)


# ══════════════════════════════════════════════════════════════════════════
# 입력 레퍼런스 선택
# ══════════════════════════════════════════════════════════════════════════


def select_input_references(refset: Any) -> list[dict[str, str]]:
    """
    신뢰 세트 → 생성 입력 (최대 3, 상보적). [{reference_id, role}].

    우선순위: PRIMARY_FACE → PRIMARY_FULL_BODY → PRIMARY_3Q → 최고 측면.
    항목이 하나도 없으면(제한 세트) 첫 원본 하나 — 사진 1장도 허용된다.
    """
    by_role = {i["role"]: i for i in (refset.items or [])}
    picks: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(item: Optional[dict[str, Any]], role: str) -> None:
        if not item or len(picks) >= 3:
            return
        rid = str(item["reference_id"])
        if rid in seen:
            return
        seen.add(rid)
        picks.append({"reference_id": rid, "role": role})

    add(by_role.get("PRIMARY_FACE"), "PRIMARY_FACE")
    add(by_role.get("PRIMARY_FULL_BODY"), "PRIMARY_FULL_BODY")
    side = by_role.get("PRIMARY_3Q")
    side_role = "PRIMARY_3Q"
    if not side:
        left, right = by_role.get("PRIMARY_LEFT"), by_role.get("PRIMARY_RIGHT")
        candidates = [(i, r) for i, r in ((left, "PRIMARY_LEFT"), (right, "PRIMARY_RIGHT")) if i]
        if candidates:
            candidates.sort(key=lambda t: -float(t[0].get("selection_score") or 0))
            side, side_role = candidates[0]
    add(side, side_role)

    if not picks and refset.source_reference_ids:
        picks.append({"reference_id": str(refset.source_reference_ids[0]), "role": "ONLY_AVAILABLE"})
    return picks


# ══════════════════════════════════════════════════════════════════════════
# 빌드
# ══════════════════════════════════════════════════════════════════════════


def _default_cutout_fn(raw_bytes: bytes) -> Optional[bytes]:
    """기존 SAM2/ViTMatte 누끼 파이프라인 재사용. 실패는 None — QA 가 REVIEW 로 다룬다."""
    try:
        from .vitmatte_service import matte_foreground

        return matte_foreground(raw_bytes)
    except Exception:
        logger.warning("정본 후보 누끼 실패", exc_info=True)
        return None


def _default_sign_url(ref: Any) -> Optional[str]:
    try:
        from .asset_url_refresh import StorageObject, sign_object

        return sign_object(StorageObject(bucket=ref.bucket, path=ref.object_path))
    except Exception:
        return None


async def build_canonical(
    *,
    user_id: str,
    pet_id: str,
    fetch_bytes: Optional[Callable[[Any], Optional[bytes]]] = None,
    providers: Optional[Sequence[Any]] = None,
    cutout_fn: Optional[Callable[[bytes], Optional[bytes]]] = None,
    sign_url_fn: Optional[Callable[[Any], Optional[str]]] = None,
    skip_if_unchanged: bool = True,
) -> CanonicalVersion:
    from . import (
        canonical_image_providers,
        canonical_prompt,
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
    if not uid or not pid:
        raise CanonicalPetError("CANONICAL_INVALID", "user_id 와 pet_id 가 필요합니다.")

    resolved_providers = list(providers) if providers is not None else canonical_image_providers.resolve_providers()
    resolved_providers = [p for p in resolved_providers if p.available()]
    if not resolved_providers:
        # 과금 전 fail-closed — 버전 행도 만들지 않는다.
        raise CanonicalPetError(
            "PROVIDER_NOT_CONFIGURED", "정본 이미지 프로바이더가 설정되지 않았습니다.", status=503
        )

    # ── Phase 3 세트 보장 (소유권 포함, 멱등) ────────────────────────────
    try:
        refset = await pet_reference_set_service.build_reference_set(
            user_id=uid, pet_id=pid, fetch_bytes=fetch_bytes, skip_if_unchanged=True
        )
    except pet_reference_set_service.PetReferenceSetError as e:
        raise CanonicalPetError(e.code, e.message, status=e.status) from e

    profile = await pet_identity_service.get_profile(
        user_id=uid, pet_id=pid, version=refset.identity_profile_version
    )

    versions_stamp = analyzer_versions() if providers is None else {
        **analyzer_versions(),
        "canonical_providers": [f"{p.name}:{p.model_name()}" for p in resolved_providers],
    }

    # ── 멱등: 같은 세트/프롬프트/프로바이더 구성의 비-실패 최신 버전 재사용 ─
    if skip_if_unchanged:
        rows = await _version_rows(pid)
        if rows:
            latest = rows[-1]
            if (
                latest.get("status") in (STATUS_COMPLETE, STATUS_REVIEW)
                and latest.get("reference_set_version") == refset.version
                and latest.get("prompt_version") == canonical_prompt.CANONICAL_PROMPT_VERSION
                and (latest.get("analyzer_versions") or {}) == versions_stamp
            ):
                cands = await _candidate_rows(str(latest["id"]))
                return _to_version(latest, cands, deduplicated=True)

    # ── 입력 레퍼런스 조립 ────────────────────────────────────────────────
    picks = select_input_references(refset)
    if not picks:
        raise CanonicalPetError(
            "NO_INPUT_REFERENCES", "생성에 쓸 신뢰 레퍼런스가 없습니다.", status=409
        )

    refs = await pet_reference_service.list_references(user_id=uid, pet_id=pid)
    refs_by_id = {str(r.id): r for r in refs}
    fetch = fetch_bytes or pet_identity_service._default_fetch_bytes
    sign = sign_url_fn or _default_sign_url
    cutout = cutout_fn or _default_cutout_fn

    provider_refs: list[CanonicalReference] = []
    ref_signatures: list[dict[str, Any]] = []
    vlm_ref_images: list[tuple[bytes, str]] = []
    for pick in picks:
        ref = refs_by_id.get(pick["reference_id"])
        if not ref:
            continue
        data = fetch(ref)
        provider_refs.append(
            CanonicalReference(
                reference_id=pick["reference_id"],
                role=pick["role"],
                url=sign(ref),
                data=data,
                mime_type=ref.mime_type or "image/jpeg",
            )
        )
        sig = ((refset.reference_analysis.get(pick["reference_id"]) or {}).get("eligibility") or {}).get("signature")
        if sig:
            ref_signatures.append(sig)
        if data:
            vlm_ref_images.append((data, ref.mime_type or "image/jpeg"))

    if not provider_refs:
        raise CanonicalPetError(
            "NO_INPUT_REFERENCES", "입력 레퍼런스를 불러오지 못했습니다.", status=409
        )

    input_ids = [p["reference_id"] for p in picks]
    prompt = canonical_prompt.build_canonical_prompt(
        visual_identity=(profile.visual_identity if profile else {}),
        structural_identity=(profile.structural_identity if profile else {}),
        reference_roles=[p["role"] for p in picks],
    )

    cid = pid[4:] if pid.startswith("pet_") else pid
    policy = candidate_policy()

    # ── 버전 행 — 프로바이더 호출 **전** ─────────────────────────────────
    rows = await _version_rows(pid)
    durable_execution = any(getattr(provider, "durable_execution", False) for provider in resolved_providers)
    resumable = rows[-1] if rows else None
    if not (
        durable_execution
        and resumable
        and resumable.get("status") == STATUS_BUILDING
        and str(resumable.get("reference_set_id") or "") == str(refset.id or "")
        and resumable.get("reference_set_version") == refset.version
        and resumable.get("prompt_version") == canonical_prompt.CANONICAL_PROMPT_VERSION
        and (resumable.get("analyzer_versions") or {}) == versions_stamp
    ):
        resumable = None

    if resumable:
        version_row = resumable
    else:
        version_row = {
            "id": str(uuid.uuid4()),
            "pet_id": pid,
            "user_id": uid,
            "version": (max((int(r.get("version") or 0) for r in rows), default=0)) + 1,
            "status": STATUS_BUILDING,
            "reference_set_id": refset.id,
            "reference_set_version": refset.version,
            "identity_profile_version": refset.identity_profile_version,
            "input_reference_ids": input_ids,
            "prompt": prompt,
            "prompt_version": canonical_prompt.CANONICAL_PROMPT_VERSION,
            # input_references: 어떤 역할의 레퍼런스가 들어갔는지 (검토 페이로드용).
            "output_spec": {**canonical_prompt.CANONICAL_OUTPUT_SPEC, "input_references": picks},
            "selected_candidate_id": None,
            "selection_reason": None,
            "qa_summary": {},
            "analyzer_versions": versions_stamp,
            "created_at": _now_iso(),
            "completed_at": None,
        }
        if not await _insert(_versions_table(), _MOCK_VERSIONS, version_row):
            raise CanonicalPetError(
                "CANONICAL_UNAVAILABLE", "정본 버전을 기록하지 못했습니다.", status=503
            )
    version_id = str(version_row["id"])

    # ── 후보 루프 ────────────────────────────────────────────────────────
    candidates = await _candidate_rows(version_id) if resumable else []
    passes = sum(1 for candidate in candidates if candidate.get("decision") == "PASS")
    contract_violation = any(
        bool((candidate.get("generation_metadata") or {}).get("contract_violation"))
        for candidate in candidates
    )

    def _prompt_for(provider: Any) -> tuple[Optional[str], str]:
        """
        프로바이더 상한에 맞는 프롬프트. (None, 이유) = 로컬 계약 검증 실패 —
        과금 호출 없이 해당 프로바이더를 중단한다 (라이브 검증된 Runway 1000자 계약).
        """
        limit = getattr(provider, "max_prompt_chars", None)
        if not limit or len(prompt) <= limit:
            return prompt, "full"
        compact = canonical_prompt.build_compact_canonical_prompt(
            visual_identity=(profile.visual_identity if profile else {}), max_chars=limit
        )
        if len(compact) <= limit:
            return compact, "compact"
        return None, f"compact prompt {len(compact)} chars still exceeds {provider.name} limit {limit}"

    async def run_provider(provider: Any, max_candidates: int, tier: str) -> None:
        nonlocal passes, contract_violation

        provider_prompt, prompt_variant = _prompt_for(provider)
        if provider_prompt is None:
            existing_contract = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.get("provider") == provider.name
                    and int(candidate.get("attempt") or 0) == 0
                ),
                None,
            )
            if existing_contract:
                contract_violation = True
                return
            # 계약 위반 — 시도 1회도 소모하지 않고 감사 기록만 남긴다.
            contract_violation = True
            row = {
                "id": str(uuid.uuid4()), "canonical_version_id": version_id,
                "pet_id": pid, "user_id": uid, "provider": provider.name,
                "model": provider.model_name(), "model_version": None, "attempt": 0,
                "external_job_id": None, "raw_bucket": None, "raw_object_path": None,
                "cutout_bucket": None, "cutout_object_path": None,
                "prompt_version": canonical_prompt.CANONICAL_COMPACT_PROMPT_VERSION,
                "input_reference_ids": input_ids,
                "generation_metadata": {"tier": tier, "contract_violation": True},
                "qa_result": {}, "decision": "ERROR", "selected": False,
                "error": f"PROVIDER_CONTRACT: {prompt_variant}"[:500],
                "created_at": _now_iso(),
            }
            await _insert(_candidates_table(), _MOCK_CANDIDATES, row)
            candidates.append(row)
            logger.error("정본 프로바이더 계약 위반 (%s): %s", provider.name, prompt_variant)
            return

        for attempt in range(1, max_candidates + 1):
            if passes >= policy["stop_after_passes"]:
                return
            existing = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.get("provider") == provider.name
                    and int(candidate.get("attempt") or 0) == attempt
                    and (candidate.get("generation_metadata") or {}).get("tier") == tier
                ),
                None,
            )
            resumable_candidate = bool(
                existing
                and existing.get("decision") == "ERROR"
                and not existing.get("error")
                and existing.get("raw_object_path")
            )
            if existing and not resumable_candidate:
                continue
            cand_id = str(existing.get("id")) if existing else str(uuid.uuid4())
            cand_row: dict[str, Any] = existing or {
                "id": cand_id,
                "canonical_version_id": version_id,
                "pet_id": pid,
                "user_id": uid,
                "provider": provider.name,
                "model": provider.model_name(),
                "model_version": None,
                "attempt": attempt,
                "external_job_id": None,
                "raw_bucket": None,
                "raw_object_path": None,
                "cutout_bucket": None,
                "cutout_object_path": None,
                "prompt_version": (
                    canonical_prompt.CANONICAL_COMPACT_PROMPT_VERSION
                    if prompt_variant == "compact"
                    else canonical_prompt.CANONICAL_PROMPT_VERSION
                ),
                "input_reference_ids": input_ids,
                "generation_metadata": {
                    "tier": tier,
                    "prompt_variant": prompt_variant,
                    "prompt_chars": len(provider_prompt),
                },
                "qa_result": {},
                "decision": "ERROR",
                "selected": False,
                "error": None,
                "created_at": _now_iso(),
            }
            try:
                # 과금 영수증 — DB 보다 로그가 먼저다 (복구 가능한 기록).
                logger.info(
                    "[canonical-receipt] pet=%s version=%s provider=%s attempt=%d",
                    pid, version_row["version"], provider.name, attempt,
                )
                result = provider.generate(
                    provider_refs, provider_prompt, dict(canonical_prompt.CANONICAL_OUTPUT_SPEC),
                    {"pet_id": pid, "canonical_version_id": version_id, "attempt": attempt},
                )
            except CanonicalProviderError as e:
                cand_row["error"] = f"{e.code}: {e.message}"[:500]
                if existing:
                    await _update(
                        _candidates_table(), _MOCK_CANDIDATES, cand_id,
                        {"error": cand_row["error"]},
                    )
                else:
                    await _insert(_candidates_table(), _MOCK_CANDIDATES, cand_row)
                    candidates.append(cand_row)
                if e.code == "PROVIDER_CONTRACT":
                    # 우리 요청이 계약을 어겼다 — 같은 요청 반복도, 폴백도 없다.
                    contract_violation = True
                    logger.error("정본 프로바이더 계약 위반 (%s): %s", provider.name, e.message)
                    return
                logger.warning("정본 후보 생성 실패 (%s attempt=%d): %s", provider.name, attempt, e.message)
                continue

            cand_row["model"] = result.model
            cand_row["model_version"] = result.model_version
            cand_row["external_job_id"] = result.external_job_id
            cand_row["generation_metadata"] = {
                **cand_row["generation_metadata"],  # tier/prompt_variant/prompt_chars 보존
                "usage": result.usage,
            }

            # raw 저장 — QA 이전. 과금된 증거는 무조건 남는다.
            raw_path = cand_row.get("raw_object_path") or (
                f"{uid}/{cid}/canonical/v{version_row['version']}/{provider.name}_a{attempt}_raw.png"
            )
            if not cand_row.get("raw_object_path"):
                try:
                    await supabase_assets.upload_asset_to_storage(raw_path, result.image_bytes, "image/png")
                    cand_row["raw_bucket"] = supabase_assets.BUCKET
                    cand_row["raw_object_path"] = raw_path
                except Exception:
                    cand_row["error"] = "RAW_STORE_FAILED"
                    await _insert(_candidates_table(), _MOCK_CANDIDATES, cand_row)
                    candidates.append(cand_row)
                    logger.exception("정본 raw 저장 실패 (%s attempt=%d)", provider.name, attempt)
                    continue

                await _insert(_candidates_table(), _MOCK_CANDIDATES, cand_row)
                candidates.append(cand_row)

            # 누끼 (기존 파이프라인 재사용) — 실패해도 후보는 남는다.
            cutout_rgba = None
            cut_bytes = cutout(result.image_bytes)
            if cut_bytes:
                cut_path = raw_path.replace("_raw.png", "_cutout.png")
                try:
                    await supabase_assets.upload_asset_to_storage(cut_path, cut_bytes, "image/png")
                    cand_row["cutout_bucket"] = supabase_assets.BUCKET
                    cand_row["cutout_object_path"] = cut_path
                except Exception:
                    logger.exception("정본 누끼 저장 실패 (%s attempt=%d)", provider.name, attempt)
                cutout_rgba = pet_identity_service.load_rgba(cut_bytes)

            vlm_qa = vlm_identity.qa_canonical_image(result.image_bytes, vlm_ref_images)
            qa = canonical_qa.evaluate_candidate(
                cutout_rgba=cutout_rgba,
                profile=profile,
                reference_signatures=ref_signatures,
                vlm_qa=vlm_qa,
            )
            cand_row["qa_result"] = qa
            cand_row["decision"] = qa["decision"]
            await _update(
                _candidates_table(), _MOCK_CANDIDATES, cand_id,
                {
                    "model": cand_row["model"],
                    "model_version": cand_row["model_version"],
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
    # 계약 위반은 QA 실패가 아니라 우리 쪽 버그다 — 그것만으로 폴백을 태우지 않는다.
    if passes == 0 and len(resolved_providers) > 1 and not contract_violation:
        await run_provider(resolved_providers[1], policy["max_fallback"], "fallback")

    # ── 결정론적 랭킹 → 선택 ─────────────────────────────────────────────
    decision_rank = {canonical_qa.PASS: 0, canonical_qa.REVIEW: 1, canonical_qa.FAIL: 2, "ERROR": 3}
    ranked = sorted(
        [c for c in candidates if c["decision"] != "ERROR"],
        key=lambda c: (
            decision_rank.get(c["decision"], 9),
            -float(c["qa_result"].get("identity_similarity") or -1.0),
            c["attempt"],
        ),
    )

    selected = ranked[0] if ranked and ranked[0]["decision"] == canonical_qa.PASS else None
    if selected:
        status = STATUS_COMPLETE
        selection_reason = (
            f"best PASS candidate: {selected['provider']} attempt {selected['attempt']}, "
            f"identity_similarity={selected['qa_result'].get('identity_similarity')}"
        )
        await _update(_candidates_table(), _MOCK_CANDIDATES, selected["id"], {"selected": True})
        selected["selected"] = True
        # 대장 기록: 생성물은 role='generated'. **절대 original 이 되지 않는다.**
        provenance = {
            "canonical_version_id": version_id,
            "candidate_id": selected["id"],
            "reference_set_version": refset.version,
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
                    logger.warning("생성 레퍼런스 대장 기록 실패 (path=%s)", path, exc_info=True)
    elif any(c["decision"] == canonical_qa.REVIEW for c in candidates):
        status = STATUS_REVIEW
        selection_reason = "no PASS candidate — human review required"
    else:
        status = STATUS_FAILED
        selection_reason = (
            "provider contract violation — 어댑터/프롬프트 설정 수정 필요 (QA 실패 아님)"
            if contract_violation and not ranked
            else "no usable candidate"
        )

    qa_summary = {
        "candidate_count": len(candidates),
        "decisions": {d: sum(1 for c in candidates if c["decision"] == d) for d in ("PASS", "REVIEW", "FAIL", "ERROR")},
        "canonical_confidence": ("low" if len({p['reference_id'] for p in picks}) < 2 else "normal"),
        "policy": policy,
    }
    final_fields = {
        "status": status,
        "selected_candidate_id": (selected["id"] if selected else None),
        "selection_reason": selection_reason,
        "qa_summary": qa_summary,
        "completed_at": _now_iso(),
    }
    await _update(_versions_table(), _MOCK_VERSIONS, version_id, final_fields)
    version_row.update(final_fields)

    return _to_version(version_row, candidates)


# ══════════════════════════════════════════════════════════════════════════
# 조회 / 평가
# ══════════════════════════════════════════════════════════════════════════


async def _assert_owned(user_id: str, pet_id: str) -> None:
    from . import pet_reference_service

    try:
        await pet_reference_service.list_references(user_id=user_id, pet_id=pet_id)
    except pet_reference_service.PetReferenceError as e:
        raise CanonicalPetError(e.code, e.message, status=e.status) from e


async def get_canonical(
    *, user_id: str, pet_id: str, version: Optional[int] = None
) -> Optional[CanonicalVersion]:
    await _assert_owned(user_id, pet_id)
    rows = await _version_rows(pet_id)
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
    return _to_version(row, await _candidate_rows(str(row["id"])))


async def list_canonical_versions(*, user_id: str, pet_id: str) -> list[CanonicalVersion]:
    await _assert_owned(user_id, pet_id)
    return [_to_version(r, []) for r in await _version_rows(pet_id)]


async def record_evaluation(
    *,
    user_id: str,
    pet_id: str,
    canonical_version_id: str,
    candidate_id: Optional[str],
    scores: dict[str, Any],
    verdict: str,
    notes: Optional[str] = None,
    provider: Optional[str] = None,
    kind: str = "canonical",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    사람 평가 기록 (Phase 4 하네스; Phase 5 키프레임도 같은 테이블을 쓴다 —
    kind='keyframe' + provider 를 호출자가 넘긴다). 프로바이더 비교용으로
    provider 를 scores 에 복사한다.
    """
    await _assert_owned(user_id, pet_id)
    if verdict not in ("PASS", "REVIEW", "FAIL"):
        raise CanonicalPetError("EVALUATION_INVALID", "verdict 는 PASS/REVIEW/FAIL 입니다.")
    clean: dict[str, Any] = {}
    for k in _EVAL_SCORE_KEYS:
        v = scores.get(k)
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError) as e:
            raise CanonicalPetError("EVALUATION_INVALID", f"{k} 점수가 숫자가 아닙니다.") from e
        if not (0 <= fv <= 10):
            raise CanonicalPetError("EVALUATION_INVALID", f"{k} 는 0~10 이어야 합니다.")
        clean[k] = fv

    if provider is None and candidate_id:
        for c in await _candidate_rows(canonical_version_id):
            if str(c.get("id")) == candidate_id:
                provider = c.get("provider")
                break

    row = {
        "id": str(uuid.uuid4()),
        "canonical_version_id": canonical_version_id,
        "candidate_id": candidate_id,
        "pet_id": pet_id,
        "user_id": user_id,
        "scores": {
            **clean,
            # 점수 아님 — 비교/집계용 메타 (provider/model/motion_id/attempt/duration 등).
            **({k: v for k, v in (extra or {}).items() if k not in clean}),
            **({"provider": provider} if provider else {}),
            **({"kind": kind} if kind != "canonical" else {}),
        },
        "verdict": verdict,
        "notes": notes,
        "created_at": _now_iso(),
    }
    if not await _insert(_evaluations_table(), _MOCK_EVALS, row):
        raise CanonicalPetError("EVALUATION_UNAVAILABLE", "평가를 저장하지 못했습니다.", status=503)
    return row


async def list_evaluation_rows(*, user_id: str) -> list[dict[str, Any]]:
    """해당 사용자의 사람 평가 행 전체 (QA 캘리브레이션 등에서 재사용)."""
    if _use_db() and _supabase():
        try:
            r = _supabase().table(_evaluations_table()).select("*").eq("user_id", user_id).execute()
            return getattr(r, "data", None) or []
        except Exception as e:
            raise CanonicalPetError(
                "EVALUATION_UNAVAILABLE", "평가를 조회하지 못했습니다.", status=503
            ) from e
    return [e for e in _MOCK_EVALS if e.get("user_id") == user_id]


async def evaluation_summary(*, user_id: str) -> dict[str, Any]:
    """프로바이더별 사람 평가 요약 — Runway vs GPT-Image 비교 근거 (작은 표본 주의)."""
    rows = await list_evaluation_rows(user_id=user_id)

    by_provider: dict[str, dict[str, Any]] = {}
    for row in rows:
        scores = row.get("scores") or {}
        provider = str(scores.get("provider") or "unknown")
        agg = by_provider.setdefault(
            provider,
            {"count": 0, "verdicts": {"PASS": 0, "REVIEW": 0, "FAIL": 0}, "score_sums": {}, "score_counts": {}},
        )
        agg["count"] += 1
        agg["verdicts"][row.get("verdict", "REVIEW")] = agg["verdicts"].get(row.get("verdict", "REVIEW"), 0) + 1
        for k in _EVAL_SCORE_KEYS:
            if isinstance(scores.get(k), (int, float)):
                agg["score_sums"][k] = agg["score_sums"].get(k, 0.0) + float(scores[k])
                agg["score_counts"][k] = agg["score_counts"].get(k, 0) + 1

    out: dict[str, Any] = {}
    for provider, agg in by_provider.items():
        out[provider] = {
            "count": agg["count"],
            "verdicts": agg["verdicts"],
            "mean_scores": {
                k: round(agg["score_sums"][k] / agg["score_counts"][k], 2)
                for k in agg["score_sums"]
            },
        }
    return {
        "providers": out,
        "note": "작은 표본이다 — 이 수치만으로 프로덕션 프로바이더를 바꾸지 않는다",
    }
