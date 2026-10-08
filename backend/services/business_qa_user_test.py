"""Phase 12 controls for the Business QA customer user-test.

This module owns rollout admission, static live-readiness checks, and customer
feedback.  It never evaluates QA, selects a candidate, submits generation, or
publishes an asset.  The generation services continue to use ``business_qa``
as their sole decision authority.
"""

from __future__ import annotations

import copy
import importlib.util
import os
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from . import business_qa


USER_TEST_VERSION = "business-user-test-v1"
MODE_OFF = "off"
MODE_ALLOWLIST = "allowlist"
MODE_ALL = "all"
VALID_MODES = frozenset({MODE_OFF, MODE_ALLOWLIST, MODE_ALL})

FEEDBACK_CATEGORIES = frozenset({"IDENTITY", "ANATOMY", "MOTION", "OTHER"})
_MOCK_FEEDBACK: dict[tuple[str, str], dict[str, Any]] = {}


class UserTestError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def __reset_for_tests() -> None:
    _MOCK_FEEDBACK.clear()


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


def mode() -> str:
    return (os.getenv("BUSINESS_QA_USER_TEST_MODE") or MODE_OFF).strip().lower()


def cohort_pet_ids() -> frozenset[str]:
    return frozenset(
        value.strip()
        for value in (os.getenv("BUSINESS_QA_USER_TEST_PET_IDS") or "").split(",")
        if value.strip()
    )


def claim_pet_allowlist() -> Optional[list[str]]:
    """Return the worker claim filter; ``None`` preserves pre-Phase-12 behavior."""

    if mode() != MODE_ALLOWLIST:
        return None
    return sorted(cohort_pet_ids())


def cohort_assignment(*, user_id: str, pet_id: str) -> dict[str, Any]:
    configured_mode = mode()
    valid = configured_mode in VALID_MODES
    enrolled = configured_mode == MODE_ALL or (
        configured_mode == MODE_ALLOWLIST and pet_id in cohort_pet_ids()
    )
    # OFF is the rollback-compatible state: it does not gate the existing
    # endpoint or worker.  It is not an enabled Phase-12 cohort.
    admitted = configured_mode == MODE_OFF or enrolled
    reason = (
        "cutover_off_existing_behavior"
        if configured_mode == MODE_OFF
        else "all_users"
        if configured_mode == MODE_ALL
        else "pet_allowlisted"
        if enrolled
        else "pet_not_allowlisted"
        if configured_mode == MODE_ALLOWLIST
        else "invalid_mode"
    )
    return {
        "version": USER_TEST_VERSION,
        "mode": configured_mode,
        "configuration_valid": valid,
        "enrolled": enrolled,
        "admitted": admitted and valid,
        "reason": reason,
        "user_id_present": bool((user_id or "").strip()),
        "pet_id": pet_id,
        "business_qa_version": business_qa.BUSINESS_QA_VERSION,
        "max_automatic_paid_candidates": business_qa.MAX_AUTOMATIC_PAID_CANDIDATES,
    }


def require_admission(*, user_id: str, pet_id: str) -> dict[str, Any]:
    assignment = cohort_assignment(user_id=user_id, pet_id=pet_id)
    if not assignment["configuration_valid"]:
        raise UserTestError(
            "BUSINESS_QA_USER_TEST_CONFIG_INVALID",
            "Business QA user-test mode is invalid.",
            status=503,
        )
    if not assignment["admitted"]:
        raise UserTestError(
            "BUSINESS_QA_USER_TEST_NOT_ENROLLED",
            "This pet is not enrolled in the limited Business QA user-test cohort.",
            status=403,
        )
    return assignment


def cutover_receipt(*, user_id: str, pet_id: str) -> dict[str, Any]:
    assignment = require_admission(user_id=user_id, pet_id=pet_id)
    return {
        **assignment,
        "authority": business_qa.BUSINESS_QA_VERSION,
        "candidate_budget": business_qa.automatic_candidate_budget("CUSTOMER_RUN"),
        "legacy_qa_retained": True,
        # Stamped once: the run keeps this authority mode for its whole life.
        business_qa.QA_AUTHORITY_STAMP_KEY: business_qa.current_qa_authority(),
        "shadow_telemetry_enabled": _flag("BUSINESS_QA_SHADOW_TELEMETRY", "1"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _provider_availability(providers: Sequence[Any]) -> dict[str, Any]:
    configured = [str(getattr(provider, "name", type(provider).__name__)) for provider in providers]
    available = []
    for provider in providers:
        try:
            if bool(provider.available()):
                available.append(str(getattr(provider, "name", type(provider).__name__)))
        except Exception:
            continue
    return {"configured": configured, "available": available, "ready": bool(available)}


def _database_readiness() -> dict[str, Any]:
    """Read-only deployment check; never claims a run or writes telemetry."""

    if not _use_db():
        return {"checked": False, "ready": True, "reason": "local_storage_mode"}
    try:
        client = _client()
        claim = client.rpc("business_qa_user_test_claim_contract", {}).execute()
        claim_version = getattr(claim, "data", None)
        if isinstance(claim_version, list):
            claim_version = claim_version[0] if claim_version else None
        client.table(
            os.getenv(
                "BUSINESS_QA_SHADOW_TELEMETRY_TABLE",
                "pet_business_qa_shadow_telemetry",
            )
        ).select("id").limit(1).execute()
        client.table(_feedback_table()).select("id").limit(1).execute()
        return {
            "checked": True,
            "ready": claim_version == USER_TEST_VERSION,
            "claim_contract_version": claim_version,
        }
    except Exception as exc:
        return {
            "checked": True,
            "ready": False,
            "reason": type(exc).__name__,
        }


def readiness_report() -> dict[str, Any]:
    """Return a read-only, no-paid-call readiness report for operator use."""

    from . import (
        canonical_image_providers,
        customer_fallback_service,
        motion_spec,
        premium_motion_finalization,
        qa_shadow_telemetry,
        video_motion_providers,
        vlm_identity,
    )

    configured_mode = mode()
    cohort = cohort_pet_ids()
    blockers: list[str] = []
    warnings: list[str] = []

    if configured_mode not in VALID_MODES:
        blockers.append("invalid_user_test_mode")
    elif configured_mode == MODE_OFF:
        blockers.append("user_test_cutover_disabled")
    elif configured_mode == MODE_ALLOWLIST and not cohort:
        blockers.append("empty_user_test_pet_allowlist")

    if business_qa.BUSINESS_QA_VERSION != "business-v1":
        blockers.append("unexpected_business_qa_version")
    if business_qa.MAX_AUTOMATIC_PAID_CANDIDATES != 2:
        blockers.append("automatic_candidate_budget_is_not_two")
    if not _flag("BUSINESS_QA_SHADOW_TELEMETRY", "1"):
        blockers.append("shadow_telemetry_disabled")
    if not _flag("PET_GENERATION_WORKER_ENABLED"):
        blockers.append("generation_worker_disabled")

    canonical = _provider_availability(canonical_image_providers.resolve_providers())
    keyframe = _provider_availability(canonical_image_providers.resolve_keyframe_providers())
    if not canonical["ready"]:
        blockers.append("canonical_provider_unavailable")
    if not keyframe["ready"]:
        blockers.append("keyframe_provider_unavailable")

    video_by_motion: dict[str, Any] = {}
    production_motions = ("BREATHING", *premium_motion_finalization.PREMIUM_MOTIONS)
    for motion_id in production_motions:
        spec = motion_spec.get_motion(motion_id)
        if not spec:
            continue
        providers = video_motion_providers.resolve_provider_order(
            motion_spec.provider_order_for_motion(motion_id)
        )
        availability = _provider_availability(providers)
        video_by_motion[motion_id] = availability
        if not availability["ready"]:
            blockers.append(f"video_provider_unavailable:{motion_id}")

    live_mode = (os.getenv("PHASE6_LIVE_MODE") or "off").strip().lower()
    video_live_allowlist = {
        value.strip()
        for value in (os.getenv("PHASE6_LIVE_ALLOWLIST") or "").split(",")
        if value.strip()
    }
    if configured_mode == MODE_ALLOWLIST:
        if live_mode != "allowlist":
            blockers.append("video_live_mode_must_be_allowlist")
        missing = sorted(cohort - video_live_allowlist)
        if missing:
            blockers.append("video_live_allowlist_missing_cohort_pets")
    elif configured_mode == MODE_ALL and live_mode != "all":
        blockers.append("video_live_mode_must_be_all")

    image_mock = (
        canonical_image_providers._mock_enabled()
        or canonical_image_providers._keyframe_mock_enabled()
    )
    video_mock = video_motion_providers._mock_enabled()
    if image_mock:
        blockers.append("image_generation_mock_enabled")
    if video_mock:
        blockers.append("video_generation_mock_enabled")

    vlm_reason = vlm_identity.unavailable_reason()
    if vlm_reason:
        blockers.append("targeted_vlm_unavailable")

    if importlib.util.find_spec("supabase") is None:
        warnings.append("supabase_sdk_not_importable_in_current_process")
    if not callable(getattr(customer_fallback_service, "resolve_best_safe_fallback", None)):
        blockers.append("fallback_resolver_unavailable")
    database = _database_readiness()
    if not database["ready"]:
        blockers.append("phase12_database_contract_unavailable")
    if not database["checked"]:
        warnings.append("phase12_database_contract_not_checked_in_local_mode")
    if configured_mode == MODE_ALLOWLIST:
        warnings.append("cohort_fallback_assets_require_read_only_live_drill")

    return {
        "version": USER_TEST_VERSION,
        "ready_to_enable": not blockers,
        "mode": configured_mode,
        "cohort_pet_count": len(cohort),
        "blockers": blockers,
        "warnings": warnings,
        "checks": {
            "business_qa_version": business_qa.BUSINESS_QA_VERSION,
            "max_automatic_paid_candidates": business_qa.MAX_AUTOMATIC_PAID_CANDIDATES,
            "legacy_qa_retained": True,
            "shadow_telemetry_enabled": qa_shadow_telemetry._enabled(),
            "worker_enabled": _flag("PET_GENERATION_WORKER_ENABLED"),
            "canonical_providers": canonical,
            "keyframe_providers": keyframe,
            "video_providers_by_motion": video_by_motion,
            "video_live_mode": live_mode,
            "video_allowlist_covers_cohort": not bool(cohort - video_live_allowlist),
            "targeted_vlm_available": vlm_reason is None,
            "targeted_vlm_unavailable_reason": vlm_reason,
            "fallback_policy_version": customer_fallback_service.FALLBACK_POLICY_VERSION,
            "fallback_resolver_available": callable(
                getattr(customer_fallback_service, "resolve_best_safe_fallback", None)
            ),
            "publication_ownership_behavior": "unchanged",
            "database": database,
        },
    }


def assert_worker_ready() -> dict[str, Any]:
    report = readiness_report()
    if mode() != MODE_OFF and not report["ready_to_enable"]:
        raise RuntimeError(
            "Business QA user-test is not ready: " + ", ".join(report["blockers"])
        )
    return report


async def read_only_fallback_drill(*, motion_id: str = "BREATHING") -> dict[str, Any]:
    """Resolve, but never publish or persist, one fallback for every cohort pet."""

    from . import customer_fallback_service, shaker_ops

    pets = sorted(cohort_pet_ids())
    results: list[dict[str, Any]] = []
    for pet_id in pets:
        try:
            user_id = await shaker_ops.resolve_pet_owner(pet_id)
            asset = await customer_fallback_service.resolve_best_safe_fallback(
                user_id=user_id,
                pet_id=pet_id,
                motion_id=motion_id,
                current_candidates=(),
            )
            results.append(
                {
                    "pet_id": pet_id,
                    "safe_asset_available": asset is not None,
                    "fallback_tier": asset.tier if asset else None,
                    "asset_kind": asset.asset_kind if asset else None,
                    "publication_id": asset.publication_id if asset else None,
                    "ownership_asset_id": asset.ownership_asset_id if asset else None,
                    "canonical_version_id": asset.canonical_version_id if asset else None,
                    "error": None if asset else "NO_SAFE_FALLBACK",
                }
            )
        except Exception as exc:  # read-only operator evidence, never a product mutation
            results.append(
                {
                    "pet_id": pet_id,
                    "safe_asset_available": False,
                    "fallback_tier": None,
                    "asset_kind": None,
                    "error": str(getattr(exc, "code", type(exc).__name__)),
                }
            )
    return {
        "version": USER_TEST_VERSION,
        "motion_id": motion_id.strip().upper(),
        "read_only": True,
        "cohort_pet_count": len(pets),
        "ready": bool(pets) and all(row["safe_asset_available"] for row in results),
        "results": results,
    }


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in {"0", "false", "no"}


def _feedback_table() -> str:
    return os.getenv(
        "BUSINESS_QA_USER_TEST_FEEDBACK_TABLE",
        "pet_business_qa_user_test_feedback",
    )


def _client():
    if not _use_db():
        return None
    from ..models.content import _supabase_client

    return _supabase_client()


def record_feedback(
    *,
    run: Any,
    user_id: str,
    accepted: bool,
    complaints: Sequence[str] = (),
    comment: Optional[str] = None,
) -> dict[str, Any]:
    """Persist product feedback without changing the run or any QA receipt."""

    if str(getattr(run, "user_id", "")) != str(user_id):
        raise UserTestError("GENERATION_RUN_NOT_FOUND", "Generation run not found.", status=404)
    business = dict(((getattr(run, "provider_state", None) or {}).get("_business_qa") or {}))
    cutover = dict(
        ((getattr(run, "provider_state", None) or {}).get("_business_qa_cutover") or {})
    )
    if cutover.get("version") != USER_TEST_VERSION or cutover.get("enrolled") is not True:
        raise UserTestError(
            "BUSINESS_QA_USER_TEST_NOT_ENROLLED",
            "Feedback is limited to the Business QA user-test cohort.",
            status=403,
        )
    terminal = str(business.get("terminal_state") or "")
    if terminal not in {business_qa.DELIVERED_GENERATED, business_qa.DELIVERED_FALLBACK}:
        raise UserTestError(
            "BUSINESS_QA_FEEDBACK_NOT_READY",
            "Feedback can be recorded only after a delivered result.",
            status=409,
        )
    normalized = sorted({str(value).strip().upper() for value in complaints if str(value).strip()})
    invalid = [value for value in normalized if value not in FEEDBACK_CATEGORIES]
    if invalid:
        raise UserTestError(
            "BUSINESS_QA_FEEDBACK_INVALID",
            f"Unsupported complaint categories: {', '.join(invalid)}",
            status=422,
        )
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "run_id": str(run.id),
        "user_id": str(user_id),
        "pet_id": str(run.pet_id),
        "motion_id": str(run.motion_id),
        "terminal_state": terminal,
        "accepted": bool(accepted),
        "complaints": normalized,
        "comment": (comment or "").strip() or None,
        "cutover_version": USER_TEST_VERSION,
        "updated_at": now,
    }
    client = _client()
    if client:
        result = client.table(_feedback_table()).upsert(
            payload, on_conflict="run_id,user_id"
        ).execute()
        rows = getattr(result, "data", None) or []
        return dict(rows[0]) if rows else payload
    key = (payload["run_id"], payload["user_id"])
    previous = _MOCK_FEEDBACK.get(key) or {}
    stored = {**previous, **copy.deepcopy(payload), "created_at": previous.get("created_at") or now}
    _MOCK_FEEDBACK[key] = stored
    return copy.deepcopy(stored)


def list_feedback() -> list[dict[str, Any]]:
    client = _client()
    if client:
        result = client.table(_feedback_table()).select("*").execute()
        return [dict(row) for row in (getattr(result, "data", None) or [])]
    return [copy.deepcopy(row) for row in _MOCK_FEEDBACK.values()]


def feedback_metrics(rows: Optional[Sequence[Mapping[str, Any]]] = None) -> dict[str, Any]:
    source = list(rows) if rows is not None else list_feedback()
    count = len(source)
    complaints = {category: 0 for category in sorted(FEEDBACK_CATEGORIES)}
    for row in source:
        for category in set(row.get("complaints") or []):
            if category in complaints:
                complaints[category] += 1
    return {
        "response_count": count,
        "accepted_count": sum(bool(row.get("accepted")) for row in source),
        "acceptance_rate": (
            round(sum(bool(row.get("accepted")) for row in source) / count, 4)
            if count else None
        ),
        "complaint_counts": complaints,
    }
