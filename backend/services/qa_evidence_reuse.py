"""Lineage-bound QA evidence reuse receipts for Business QA vNext.

Inherited evidence describes an approved upstream baseline. It never certifies
that a newly generated asset has no new identity, anatomy, pose, or motion
defect; stage-local measurements and Phase 8/9 escalation retain that authority.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Optional, Sequence

from . import business_qa


QA_EVIDENCE_REUSE_VERSION = "qa-evidence-reuse-v1"

COMPUTED = "computed"
CACHE_HIT = "cache_hit"
INHERITED = "inherited"
ESCALATED = "escalated"


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "__dict__"):
        return _plain(vars(value))
    return str(value)


def fingerprint(value: Any) -> str:
    payload = json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _lineage_mismatches(
    lineage: Mapping[str, Any], expected: Mapping[str, Any]
) -> list[str]:
    return [
        f"lineage_changed:{key}"
        for key, expected_value in expected.items()
        if expected_value is not None and lineage.get(key) != expected_value
    ]


def profile_evidence(
    *,
    identity_profile: Any,
    reference_set: Any,
    expected_identity_profile_version: Optional[int],
    expected_reference_set_version: Optional[int],
) -> dict[str, Any]:
    """Summarize pinned profile/reference evidence without copying large observations."""

    lineage = {
        "identity_profile_id": getattr(identity_profile, "id", None),
        "identity_profile_version": getattr(identity_profile, "version", None),
        "identity_profile_status": getattr(identity_profile, "status", None),
        "reference_set_id": getattr(reference_set, "id", None),
        "reference_set_version": getattr(reference_set, "version", None),
        "reference_set_status": getattr(reference_set, "status", None),
        "reference_set_identity_profile_id": getattr(
            reference_set, "identity_profile_id", None
        ),
        "reference_set_identity_profile_version": getattr(
            reference_set, "identity_profile_version", None
        ),
        "morphology_profile_id": getattr(reference_set, "morphology_profile_id", None),
        "morphology_profile_version": getattr(reference_set, "morphology_profile_version", None),
        "source_reference_ids_hash": fingerprint(
            list(getattr(reference_set, "source_reference_ids", None) or [])
        ),
        "identity_analyzers_hash": fingerprint(
            dict(getattr(identity_profile, "analyzer_versions", None) or {})
        ),
        "reference_analyzers_hash": fingerprint(
            dict(getattr(reference_set, "analyzer_versions", None) or {})
        ),
    }
    expected = {
        "identity_profile_version": expected_identity_profile_version,
        "reference_set_version": expected_reference_set_version,
    }
    stale = []
    if identity_profile is None:
        stale.append("identity_profile_missing")
    elif str(getattr(identity_profile, "status", "")).lower() != "complete":
        stale.append("identity_profile_not_complete")
    if reference_set is None:
        stale.append("reference_set_missing")
    elif str(getattr(reference_set, "status", "")).lower() != "complete":
        stale.append("reference_set_not_complete")
    if identity_profile is not None and reference_set is not None:
        linked_id = getattr(reference_set, "identity_profile_id", None)
        if linked_id is not None and linked_id != getattr(identity_profile, "id", None):
            stale.append("reference_set_identity_profile_changed")
        linked_version = getattr(reference_set, "identity_profile_version", None)
        if linked_version is not None and linked_version != getattr(
            identity_profile, "version", None
        ):
            stale.append("reference_set_identity_profile_changed")
    stale.extend(_lineage_mismatches(lineage, expected))
    valid = not stale
    domains = []
    if valid:
        domains = ["identity_profile", "markings", "morphology_profile", "reference_set"]
    return {
        "version": QA_EVIDENCE_REUSE_VERSION,
        "source_stage": "PROFILE",
        "status": INHERITED if valid else "stale",
        "valid": valid,
        "baseline_only": True,
        "domains": domains,
        "lineage": lineage,
        "fingerprint": fingerprint({"lineage": lineage, "domains": domains}),
        "stale_reasons": stale,
    }


def approved_asset_evidence(
    *,
    source_stage: str,
    qa_result: Mapping[str, Any],
    lineage: Mapping[str, Any],
    expected_lineage: Mapping[str, Any],
    expected_qa_version: str,
) -> dict[str, Any]:
    """Export approved evidence only when its QA and lineage are still exact."""

    stale = _lineage_mismatches(lineage, expected_lineage)
    if str(qa_result.get("qa_version") or "") != str(expected_qa_version):
        stale.append("qa_version_changed")
    if not business_qa.is_deliverable(qa_result):
        stale.append("source_not_deliverable")

    checks = dict(qa_result.get("checks") or {})
    signals = dict(qa_result.get("business_signals") or {})
    hard_identity_fail = any(
        str(value).upper() == business_qa.FAIL
        for name, value in {**checks, **signals}.items()
        if "identity" in str(name) or str(name) == "vlm_same_pet"
    )
    if hard_identity_fail:
        stale.append("source_identity_failed")

    valid = not stale
    stage = str(source_stage or "").upper()
    domains = []
    if valid:
        domains = ["identity", "markings", "anatomy"]
        if stage == "KEYFRAME":
            domains.extend(["pose", "visibility"])
        for source in (
            (qa_result.get("qa_evidence_reuse") or {}).get("inherited_sources") or []
        ):
            if isinstance(source, Mapping) and source.get("valid") is True:
                domains.extend(str(domain) for domain in source.get("domains") or [])
        domains = list(dict.fromkeys(domains))
    evidence = {
        "checks": {
            name: value
            for name, value in checks.items()
            if name in {"vlm_same_pet", "vlm_anatomy", "vlm_composition"}
        },
        "signals": {
            name: value
            for name, value in signals.items()
            if "identity" in name or "pose" in name or "visibility" in name
        },
        "upstream_fingerprints": [
            source.get("fingerprint")
            for source in (
                (qa_result.get("qa_evidence_reuse") or {}).get("inherited_sources") or []
            )
            if isinstance(source, Mapping)
            and source.get("valid") is True
            and source.get("fingerprint")
        ],
    }
    return {
        "version": QA_EVIDENCE_REUSE_VERSION,
        "source_stage": stage,
        "status": INHERITED if valid else "stale",
        "valid": valid,
        "baseline_only": True,
        "domains": domains,
        "source_qa_version": qa_result.get("qa_version"),
        "lineage": dict(lineage),
        "fingerprint": fingerprint(
            {"qa_version": qa_result.get("qa_version"), "lineage": lineage, "evidence": evidence}
        ),
        "evidence": evidence if valid else {},
        "stale_reasons": stale,
    }


def attach_receipt(
    qa_result: dict[str, Any],
    *,
    stage: str,
    escalation: Mapping[str, Any],
    vlm_result: Optional[Mapping[str, Any]],
    inherited: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Persist computed/cache/inherited/escalated provenance beside legacy evidence."""

    events: list[dict[str, Any]] = [
        {
            "status": COMPUTED,
            "domain": "stage_deterministic_qa",
            "source_stage": str(stage or "").upper(),
        }
    ]
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for source in inherited:
        item = dict(source)
        if item.get("valid") is True:
            accepted.append(item)
            for domain in item.get("domains") or []:
                events.append(
                    {
                        "status": INHERITED,
                        "domain": str(domain),
                        "source_stage": item.get("source_stage"),
                        "fingerprint": item.get("fingerprint"),
                    }
                )
        else:
            rejected.append(item)

    requested = list(escalation.get("requested_tasks") or [])
    task_evidence = dict((vlm_result or {}).get("targeted_vlm_evidence") or {})
    for task in requested:
        events.append({"status": ESCALATED, "task": task})
        raw = dict(task_evidence.get(task) or {})
        cache = dict(raw.get("cache_receipt") or {})
        if cache.get("status") in {COMPUTED, CACHE_HIT}:
            events.append(
                {
                    "status": cache["status"],
                    "task": task,
                    "cache_key": cache.get("cache_key"),
                }
            )

    summary = {status: 0 for status in (COMPUTED, CACHE_HIT, INHERITED, ESCALATED)}
    for event in events:
        status = str(event.get("status") or "")
        if status in summary:
            summary[status] += 1
    qa_result["qa_evidence_reuse"] = {
        "version": QA_EVIDENCE_REUSE_VERSION,
        "stage": str(stage or "").upper(),
        "events": events,
        "summary": summary,
        "inherited_sources": accepted,
        "rejected_stale_sources": rejected,
    }
    return qa_result
