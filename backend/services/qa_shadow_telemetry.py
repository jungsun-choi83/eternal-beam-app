"""Fail-open shadow telemetry for legacy QA versus Business QA.

This module is deliberately observational.  It consumes already-persisted QA
receipts and provider-job receipts; it never evaluates QA, calls a provider, or
asks a VLM a question.  Telemetry storage failures are logged and swallowed so
they cannot change customer delivery or retry behavior.
"""

from __future__ import annotations

import copy
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence


logger = logging.getLogger(__name__)

SHADOW_TELEMETRY_VERSION = "business-qa-shadow-v1"
RUN_STAGE = "RUN"
_MOCK_ROWS: dict[str, dict[str, Any]] = {}


def __reset_for_tests() -> None:
    _MOCK_ROWS.clear()


def _enabled() -> bool:
    return os.getenv("BUSINESS_QA_SHADOW_TELEMETRY", "1").strip().lower() not in {
        "0", "false", "no",
    }


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in {
        "0", "false", "no",
    }


def _table() -> str:
    return os.getenv(
        "BUSINESS_QA_SHADOW_TELEMETRY_TABLE",
        "pet_business_qa_shadow_telemetry",
    )


def _client():
    if not _use_db():
        return None
    from ..models.content import _supabase_client

    return _supabase_client()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime] = None) -> str:
    return (value or _now()).isoformat()


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _elapsed_ms(start: Any, end: Any) -> Optional[float]:
    a, b = _parse(start), _parse(end)
    if not a or not b:
        return None
    return round(max(0.0, (b - a).total_seconds() * 1000.0), 3)


def _key(run_id: str, stage: str, candidate_id: Optional[str] = None) -> str:
    return f"{run_id}:{stage}:{candidate_id or 'aggregate'}"


def _get(telemetry_key: str) -> Optional[dict[str, Any]]:
    client = _client()
    if client:
        result = (
            client.table(_table())
            .select("*")
            .eq("telemetry_key", telemetry_key)
            .limit(1)
            .execute()
        )
        rows = getattr(result, "data", None) or []
        return dict(rows[0]) if rows else None
    row = _MOCK_ROWS.get(telemetry_key)
    return copy.deepcopy(row) if row else None


def _rows_for_run(run_id: str) -> list[dict[str, Any]]:
    client = _client()
    if client:
        result = client.table(_table()).select("*").eq("run_id", run_id).execute()
        return [dict(row) for row in (getattr(result, "data", None) or [])]
    return [copy.deepcopy(row) for row in _MOCK_ROWS.values() if row.get("run_id") == run_id]


def _upsert(row: Mapping[str, Any]) -> None:
    if not _enabled():
        return
    try:
        payload = {**dict(row), "updated_at": _iso()}
        existing = (
            _get(str(payload.get("telemetry_key") or ""))
            if payload.get("telemetry_key")
            else None
        )
        if existing:
            payload = {**existing, **payload}
        payload.setdefault("id", str(uuid.uuid4()))
        payload.setdefault("telemetry_version", SHADOW_TELEMETRY_VERSION)
        payload.setdefault("created_at", payload["updated_at"])
        client = _client()
        if client:
            client.table(_table()).upsert(
                payload, on_conflict="telemetry_key"
            ).execute()
        else:
            previous = _MOCK_ROWS.get(str(payload["telemetry_key"])) or {}
            _MOCK_ROWS[str(payload["telemetry_key"])] = {
                **previous,
                **copy.deepcopy(payload),
                "id": previous.get("id") or payload["id"],
                "created_at": previous.get("created_at") or payload["created_at"],
            }
    except Exception:
        logger.warning("Business QA shadow telemetry write failed", exc_info=True)


def upload_to_cutout_ms(original: Any, cutout: Any) -> Optional[float]:
    """Wall time between persisted original upload and its linked cutout."""

    return _elapsed_ms(getattr(original, "created_at", None), getattr(cutout, "created_at", None))


def start_run(run: Any, *, upload_cutout_ms: Optional[float] = None) -> None:
    """Create the run aggregate without overwriting an existing timeline."""

    if not _enabled():
        return
    telemetry_key = _key(str(run.id), RUN_STAGE)
    try:
        existing = _get(telemetry_key) or {}
        timings = dict(existing.get("timings") or {})
        timings.setdefault("request_started_at", getattr(run, "created_at", None) or _iso())
        if upload_cutout_ms is not None:
            timings.setdefault("upload_to_cutout_ms", upload_cutout_ms)
        stages = dict(timings.get("stages") or {})
        stages.setdefault("QUEUED", {"started_at": timings["request_started_at"]})
        timings["stages"] = stages
        _upsert(
            {
                **existing,
                "telemetry_key": telemetry_key,
                "run_id": str(run.id),
                "candidate_id": None,
                "stage": RUN_STAGE,
                "stage_role": None,
                "user_id": str(run.user_id),
                "pet_id": str(run.pet_id),
                "motion_id": str(run.motion_id),
                "request_kind": str(run.request_kind),
                "timings": timings,
                "candidate_count": int(existing.get("candidate_count") or 0),
                "fallback_used": bool(existing.get("fallback_used")),
                "retry_used": bool(existing.get("retry_used")),
                "metadata": {
                    **dict(existing.get("metadata") or {}),
                    "idempotency_key_present": bool(getattr(run, "idempotency_key", None)),
                    "user_test_cutover": dict(
                        ((getattr(run, "provider_state", None) or {}).get("_business_qa_cutover") or {})
                    ),
                },
            }
        )
    except Exception:
        logger.warning("Business QA shadow telemetry initialization failed", exc_info=True)


def mark_claimed(run: Any, *, claim_time_ms: float) -> None:
    """Record first worker claim and queue wait; later polling claims do not replace it."""

    try:
        start_run(run)
        key = _key(str(run.id), RUN_STAGE)
        row = _get(key) or {}
        timings = dict(row.get("timings") or {})
        now = _iso()
        timings.setdefault("first_claimed_at", now)
        timings.setdefault(
            "queue_wait_ms",
            _elapsed_ms(timings.get("request_started_at") or getattr(run, "created_at", None), now),
        )
        timings["worker_claim_ms"] = round(
            float(timings.get("worker_claim_ms") or 0.0) + max(0.0, claim_time_ms), 3
        )
        timings["worker_claim_count"] = int(timings.get("worker_claim_count") or 0) + 1
        stages = dict(timings.get("stages") or {})
        queued = dict(stages.get("QUEUED") or {})
        queued.setdefault("started_at", timings.get("request_started_at"))
        queued.setdefault("completed_at", timings["first_claimed_at"])
        queued.setdefault(
            "duration_ms", _elapsed_ms(queued.get("started_at"), queued.get("completed_at"))
        )
        stages["QUEUED"] = queued
        timings["stages"] = stages
        _upsert({**row, "telemetry_key": key, "run_id": str(run.id), "stage": RUN_STAGE, "timings": timings})
    except Exception:
        logger.warning("Business QA shadow claim telemetry failed", exc_info=True)


def observe_progress(before: Any, after: Any, fields: Mapping[str, Any]) -> None:
    """Observe orchestration transitions after the authoritative update succeeds."""

    try:
        start_run(after)
        key = _key(str(after.id), RUN_STAGE)
        row = _get(key) or {}
        timings = dict(row.get("timings") or {})
        stages = dict(timings.get("stages") or {})
        now = str(fields.get("completed_at") or _iso())
        previous_stage = str(getattr(before, "current_stage", "") or "QUEUED")
        next_stage = str(fields.get("current_stage") or getattr(after, "current_stage", previous_stage))
        if next_stage != previous_stage:
            previous = dict(stages.get(previous_stage) or {})
            previous.setdefault("started_at", getattr(before, "updated_at", None) or now)
            previous["completed_at"] = now
            previous["duration_ms"] = _elapsed_ms(previous.get("started_at"), now)
            stages[previous_stage] = previous
        current = dict(stages.get(next_stage) or {})
        current.setdefault("started_at", now)
        stages[next_stage] = current
        timings["stages"] = stages
        if fields.get("completed_at"):
            current["completed_at"] = now
            current["duration_ms"] = _elapsed_ms(current.get("started_at"), now)
            timings["completed_at"] = now
            timings["total_request_ms"] = _elapsed_ms(
                timings.get("request_started_at") or getattr(after, "created_at", None), now
            )
        _upsert({**row, "telemetry_key": key, "run_id": str(after.id), "stage": RUN_STAGE, "timings": timings})
    except Exception:
        logger.warning("Business QA shadow stage telemetry failed", exc_info=True)


def _float_env(name: str) -> Optional[float]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _cost_from(value: Any) -> Optional[float]:
    if isinstance(value, Mapping):
        currency = str(value.get("currency") or value.get("currency_code") or "USD").upper()
        for name in ("cost_usd", "estimated_cost_usd", "usd", "amount_usd"):
            try:
                if value.get(name) is not None:
                    return max(0.0, float(value[name]))
            except (TypeError, ValueError):
                pass
        if currency == "USD":
            for name in ("amount", "total", "cost"):
                try:
                    if isinstance(value.get(name), (int, float, str)):
                        return max(0.0, float(value[name]))
                except (TypeError, ValueError):
                    pass
        for child in value.values():
            found = _cost_from(child)
            if found is not None:
                return found
    return None


def _provider_job_metrics(run_id: str, parent_id: str, attempt: int) -> tuple[Optional[float], Optional[float]]:
    try:
        from . import durable_provider_jobs

        matches = [
            row for row in durable_provider_jobs.list_for_run(run_id)
            if str(row.get("phase_version_id") or "") == str(parent_id)
            and int(row.get("attempt") or 0) == int(attempt or 0)
        ]
        if not matches:
            return None, None
        job = matches[-1]
        duration = _elapsed_ms(job.get("submitted_at") or job.get("created_at"), job.get("updated_at"))
        cost = _cost_from(job.get("result_metadata") or {})
        return duration, cost
    except Exception:
        logger.warning("Business QA provider receipt telemetry failed", exc_info=True)
        return None, None


def _candidate_row(
    *, run: Any, stage: str, stage_role: Optional[str], parent_id: str, candidate: Any,
) -> dict[str, Any]:
    qa = dict(getattr(candidate, "qa_result", None) or {})
    business = dict(qa.get("business_qa") or {})
    escalation = dict(qa.get("vlm_escalation") or {})
    reuse = dict(qa.get("qa_evidence_reuse") or {})
    events = list(reuse.get("events") or [])
    tasks = list(escalation.get("called_tasks") or [])
    cache_hits = sum(1 for event in events if event.get("status") == "cache_hit")
    computed_vlm = sum(
        1 for event in events
        if event.get("status") == "computed" and event.get("task")
    )
    if tasks and computed_vlm == 0 and not reuse:
        computed_vlm = max(0, len(tasks) - cache_hits)
    qa_timing = dict(qa.get("shadow_telemetry") or {})
    provider_ms, receipt_cost = _provider_job_metrics(
        str(run.id), parent_id, int(getattr(candidate, "attempt", 0) or 0)
    )
    usage_cost = _cost_from((getattr(candidate, "generation_metadata", None) or {}).get("usage"))
    rate = _float_env(f"BUSINESS_QA_{stage}_CANDIDATE_COST_USD")
    generated = str(getattr(candidate, "provider", "")) != "canonical_reuse" and int(
        getattr(candidate, "attempt", 0) or 0
    ) > 0
    generation_cost = receipt_cost if receipt_cost is not None else usage_cost
    cost_basis = "provider_receipt" if receipt_cost is not None else (
        "candidate_usage" if usage_cost is not None else None
    )
    if generation_cost is None and rate is not None and generated:
        generation_cost, cost_basis = rate, "configured_stage_rate"
    vlm_rate = _float_env("BUSINESS_QA_VLM_TASK_COST_USD")
    vlm_cost = (
        round(computed_vlm * vlm_rate, 6)
        if vlm_rate is not None
        else (0.0 if computed_vlm == 0 else None)
    )
    legacy = str(getattr(candidate, "decision", None) or qa.get("decision") or "UNKNOWN").upper()
    delivery = str(business.get("delivery_action") or "UNKNOWN").upper()
    comparisons = {
        "legacy_nonpass_business_deliver": legacy in {"FAIL", "REVIEW"} and delivery in {
            "DELIVER", "DELIVER_WITH_ADVISORY",
        },
        "legacy_pass_business_block": legacy == "PASS" and delivery == "BLOCK",
    }
    cid = str(getattr(candidate, "id", ""))
    return {
        "telemetry_key": _key(str(run.id), stage, cid),
        "run_id": str(run.id),
        "candidate_id": cid,
        "stage": stage,
        "stage_role": stage_role,
        "user_id": str(run.user_id),
        "pet_id": str(run.pet_id),
        "motion_id": str(run.motion_id),
        "request_kind": str(run.request_kind),
        "legacy_decision": legacy,
        "business_integrity_status": business.get("integrity_status"),
        "business_quality_status": business.get("quality_status"),
        "delivery_action": business.get("delivery_action"),
        "retry_action": business.get("retry_action"),
        "vlm_tasks_called": tasks,
        "vlm_call_count": computed_vlm,
        "vlm_cache_hits": cache_hits,
        "candidate_count": 1,
        "fallback_used": False,
        "retry_used": int(getattr(candidate, "attempt", 0) or 0) > 1,
        "provider_generation_ms": provider_ms,
        "qa_time_ms": qa_timing.get("qa_time_ms"),
        "estimated_generation_cost_usd": generation_cost,
        "estimated_vlm_cost_usd": vlm_cost,
        "comparison_flags": comparisons,
        "metadata": {
            "provider": getattr(candidate, "provider", None),
            "model": getattr(candidate, "model", None),
            "attempt": getattr(candidate, "attempt", None),
            "selected": bool(getattr(candidate, "selected", False)),
            "generated": generated,
            "generation_cost_basis": cost_basis or "unavailable",
            "vlm_cost_basis": "configured_task_rate" if vlm_rate is not None else "unavailable",
            # "VLM returned nothing" metric: requested tasks that produced no
            # answer, with the persisted failure class. Rate = sum(failed) /
            # sum(requested) over candidate rows.
            "vlm_tasks_requested": len(list(escalation.get("requested_tasks") or []))
            if escalation.get("called") else 0,
            "vlm_tasks_returned_nothing": len(list(escalation.get("failed_tasks") or [])),
            "vlm_failure_class": escalation.get("result_failure_class"),
        },
    }


def record_candidates(
    run: Any,
    *,
    stage: str,
    parent_id: str,
    candidates: Sequence[Any],
    stage_role: Optional[str] = None,
) -> None:
    """Persist existing candidate receipts.  This function performs no QA work."""

    if not _enabled():
        return
    for candidate in candidates:
        try:
            _upsert(
                _candidate_row(
                    run=run,
                    stage=str(stage).upper(),
                    stage_role=stage_role,
                    parent_id=parent_id,
                    candidate=candidate,
                )
            )
        except Exception:
            logger.warning("Business QA candidate shadow telemetry failed", exc_info=True)


def finalize_run(run: Any, *, fallback_used: bool, terminal_state: Optional[str]) -> None:
    """Roll candidate observations into one query-friendly per-run aggregate."""

    try:
        start_run(run)
        rows = _rows_for_run(str(run.id))
        candidates = [row for row in rows if row.get("candidate_id")]
        key = _key(str(run.id), RUN_STAGE)
        aggregate = next((row for row in rows if row.get("telemetry_key") == key), {})
        timings = dict(aggregate.get("timings") or {})
        completed = getattr(run, "completed_at", None) or _iso()
        timings["completed_at"] = completed
        timings["total_request_ms"] = _elapsed_ms(
            timings.get("request_started_at") or getattr(run, "created_at", None), completed
        )
        qa_values = [float(row["qa_time_ms"]) for row in candidates if row.get("qa_time_ms") is not None]
        provider_video_values = [
            float(row["provider_generation_ms"])
            for row in candidates
            if row.get("stage") == "MOTION" and row.get("provider_generation_ms") is not None
        ]
        timings["qa_time_ms"] = round(sum(qa_values), 3) if qa_values else None
        timings["provider_video_ms"] = (
            round(sum(provider_video_values), 3) if provider_video_values else None
        )
        timings["canonical_ms"] = (timings.get("stages") or {}).get("CANONICAL", {}).get(
            "duration_ms"
        )
        timings["keyframe_ms"] = (timings.get("stages") or {}).get("KEYFRAMES", {}).get(
            "duration_ms"
        )
        generation_costs = [
            float(row["estimated_generation_cost_usd"])
            for row in candidates if row.get("estimated_generation_cost_usd") is not None
        ]
        vlm_costs = [
            float(row["estimated_vlm_cost_usd"])
            for row in candidates if row.get("estimated_vlm_cost_usd") is not None
        ]
        paid_candidates = [
            row for row in candidates
            if bool((row.get("metadata") or {}).get("generated"))
        ]
        retry_used = any(bool(row.get("retry_used")) for row in paid_candidates) or int(
            getattr(run, "retry_count", 0) or 0
        ) > 0
        selected = [row for row in candidates if bool((row.get("metadata") or {}).get("selected"))]
        selected_priority = {"CANONICAL": 0, "KEYFRAME": 1, "MOTION": 2}
        final_candidate = max(
            selected,
            key=lambda row: selected_priority.get(str(row.get("stage") or ""), -1),
            default=None,
        )
        terminal_decision = dict(
            ((getattr(run, "provider_state", None) or {}).get("_business_qa") or {}).get(
                "decision"
            )
            or {}
        )
        final_business = terminal_decision or {
            "legacy_decision": (final_candidate or {}).get("legacy_decision"),
            "integrity_status": (final_candidate or {}).get("business_integrity_status"),
            "quality_status": (final_candidate or {}).get("business_quality_status"),
            "delivery_action": (final_candidate or {}).get("delivery_action"),
            "retry_action": (final_candidate or {}).get("retry_action"),
        }
        candidate_one_delivered = bool(selected) and all(
            int((row.get("metadata") or {}).get("attempt") or 0) == 1 for row in selected
        ) and not retry_used and not fallback_used
        comparison_flags = {
            "legacy_nonpass_business_deliver": any(
                bool((row.get("comparison_flags") or {}).get("legacy_nonpass_business_deliver"))
                for row in candidates
            ),
            "legacy_pass_business_block": any(
                bool((row.get("comparison_flags") or {}).get("legacy_pass_business_block"))
                for row in candidates
            ),
            "business_deliver_with_fallback": bool(fallback_used) and any(
                str(row.get("delivery_action") or "") in {"DELIVER", "DELIVER_WITH_ADVISORY"}
                for row in candidates
            ),
            "candidate_1_delivered_without_retry": candidate_one_delivered,
        }
        by_stage = {
            stage: sum(1 for row in paid_candidates if row.get("stage") == stage)
            for stage in ("CANONICAL", "KEYFRAME", "MOTION")
        }
        generation_cost_complete = len(generation_costs) == len(paid_candidates)
        vlm_cost_complete = all(
            int(row.get("vlm_call_count") or 0) == 0
            or row.get("estimated_vlm_cost_usd") is not None
            for row in candidates
        )
        _upsert(
            {
                **aggregate,
                "telemetry_key": key,
                "run_id": str(run.id),
                "candidate_id": None,
                "stage": RUN_STAGE,
                "user_id": str(run.user_id),
                "pet_id": str(run.pet_id),
                "motion_id": str(run.motion_id),
                "request_kind": str(run.request_kind),
                "legacy_decision": final_business.get("legacy_decision"),
                "business_integrity_status": final_business.get("integrity_status"),
                "business_quality_status": final_business.get("quality_status"),
                "delivery_action": final_business.get("delivery_action"),
                "retry_action": final_business.get("retry_action"),
                "vlm_tasks_called": [
                    task for row in candidates for task in (row.get("vlm_tasks_called") or [])
                ],
                "vlm_call_count": sum(int(row.get("vlm_call_count") or 0) for row in candidates),
                "vlm_cache_hits": sum(int(row.get("vlm_cache_hits") or 0) for row in candidates),
                "candidate_count": len(paid_candidates),
                "fallback_used": bool(fallback_used),
                "retry_used": retry_used,
                "timings": timings,
                "estimated_generation_cost_usd": (
                    round(sum(generation_costs), 6)
                    if generation_cost_complete and paid_candidates
                    else (0.0 if not paid_candidates else None)
                ),
                "estimated_vlm_cost_usd": (
                    round(sum(vlm_costs), 6) if vlm_cost_complete else None
                ),
                "comparison_flags": comparison_flags,
                "metadata": {
                    **dict(aggregate.get("metadata") or {}),
                    "terminal_state": terminal_state,
                    "orchestration_status": getattr(run, "status", None),
                    "run_retry_count": int(getattr(run, "retry_count", 0) or 0),
                    "candidate_count_by_stage": by_stage,
                    "generation_cost_complete": generation_cost_complete,
                    "vlm_cost_complete": vlm_cost_complete,
                    "user_test_cutover": dict(
                        ((getattr(run, "provider_state", None) or {}).get("_business_qa_cutover") or {})
                    ),
                },
            }
        )
    except Exception:
        logger.warning("Business QA run shadow telemetry finalization failed", exc_info=True)


def rows_for_run(run_id: str) -> list[dict[str, Any]]:
    """Read-only diagnostics/test accessor."""

    try:
        return _rows_for_run(run_id)
    except Exception:
        logger.warning("Business QA shadow telemetry read failed", exc_info=True)
        return []


def aggregate_metrics(
    rows: Optional[Sequence[Mapping[str, Any]]] = None,
    *,
    cohort_only: bool = False,
) -> dict[str, Any]:
    """Compute rollout metrics from RUN rows without changing any runtime state."""

    if rows is not None:
        source = list(rows)
    else:
        try:
            client = _client()
            if client:
                result = client.table(_table()).select("*").eq("stage", RUN_STAGE).execute()
                source = list(getattr(result, "data", None) or [])
            else:
                source = [copy.deepcopy(row) for row in _MOCK_ROWS.values()]
        except Exception:
            logger.warning("Business QA shadow aggregate read failed", exc_info=True)
            source = []
    observed_runs = [row for row in source if row.get("stage") == RUN_STAGE]
    if cohort_only:
        observed_runs = [
            row for row in observed_runs
            if bool(((row.get("metadata") or {}).get("user_test_cutover") or {}).get("enrolled"))
        ]
    runs = [
        row for row in observed_runs if (row.get("timings") or {}).get("completed_at")
    ]
    count = len(runs)
    delivered_count = sum(
        str((row.get("metadata") or {}).get("terminal_state") or "")
        in {"DELIVERED_GENERATED", "DELIVERED_FALLBACK"}
        for row in runs
    )
    timing_names = (
        "upload_to_cutout_ms",
        "queue_wait_ms",
        "worker_claim_ms",
        "canonical_ms",
        "keyframe_ms",
        "provider_video_ms",
        "qa_time_ms",
        "total_request_ms",
    )
    if not count:
        return {
            "observed_run_count": len(observed_runs),
            "run_count": 0,
            "delivered_count": 0,
            "completion_rate": None,
            "average_candidates_per_run": 0.0,
            "average_vlm_calls_per_run": 0.0,
            "retry_rate": 0.0,
            "fallback_rate": 0.0,
            **{f"average_{name}": None for name in timing_names},
            "average_estimated_generation_cost_usd": None,
            "average_estimated_vlm_cost_usd": None,
        }

    def average(values: Sequence[Any]) -> Optional[float]:
        numeric = [float(value) for value in values if value is not None]
        return round(sum(numeric) / len(numeric), 4) if numeric else None

    return {
        "observed_run_count": len(observed_runs),
        "run_count": count,
        "delivered_count": delivered_count,
        "completion_rate": round(delivered_count / count, 4),
        "average_candidates_per_run": round(
            sum(int(row.get("candidate_count") or 0) for row in runs) / count, 4
        ),
        "average_vlm_calls_per_run": round(
            sum(int(row.get("vlm_call_count") or 0) for row in runs) / count, 4
        ),
        "retry_rate": round(sum(bool(row.get("retry_used")) for row in runs) / count, 4),
        "fallback_rate": round(sum(bool(row.get("fallback_used")) for row in runs) / count, 4),
        **{
            f"average_{name}": average([(row.get("timings") or {}).get(name) for row in runs])
            for name in timing_names
        },
        "average_estimated_generation_cost_usd": average(
            [row.get("estimated_generation_cost_usd") for row in runs]
        ),
        "average_estimated_vlm_cost_usd": average(
            [row.get("estimated_vlm_cost_usd") for row in runs]
        ),
    }
