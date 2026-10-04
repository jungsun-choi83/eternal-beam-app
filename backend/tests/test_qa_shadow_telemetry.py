from __future__ import annotations

import copy
from types import SimpleNamespace

from backend.services import qa_shadow_telemetry as telemetry


def _run(*, completed=False):
    return SimpleNamespace(
        id="run-1",
        user_id="user-1",
        pet_id="pet-1",
        motion_id="BREATHING",
        request_kind="FREE_HOME",
        idempotency_key="request-1",
        status="PUBLISHED" if completed else "QUEUED",
        current_stage="PUBLISHED" if completed else "QUEUED",
        retry_count=0,
        created_at="2026-10-02T00:00:00+00:00",
        updated_at="2026-10-02T00:00:01+00:00",
        completed_at="2026-10-02T00:00:10+00:00" if completed else None,
    )


def _candidate(*, attempt=1, selected=True, decision="REVIEW", delivery="DELIVER"):
    return SimpleNamespace(
        id=f"candidate-{attempt}",
        provider="seedance",
        model="test-model",
        attempt=attempt,
        selected=selected,
        decision=decision,
        generation_metadata={"usage": {}},
        qa_result={
            "decision": decision,
            "business_qa": {
                "integrity_status": "PASS",
                "quality_status": "REVIEW",
                "delivery_action": delivery,
                "retry_action": "STOP",
            },
            "vlm_escalation": {
                "called_tasks": ["IDENTITY_VLM", "ANATOMY_VLM"],
            },
            "qa_evidence_reuse": {
                "events": [
                    {"status": "escalated", "task": "IDENTITY_VLM"},
                    {"status": "cache_hit", "task": "IDENTITY_VLM"},
                    {"status": "escalated", "task": "ANATOMY_VLM"},
                    {"status": "computed", "task": "ANATOMY_VLM"},
                ]
            },
            "shadow_telemetry": {"qa_time_ms": 12.5},
        },
    )


def setup_function():
    telemetry.__reset_for_tests()


def test_candidate_shadow_records_old_new_qa_vlm_and_cache_without_mutation(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("BUSINESS_QA_VLM_TASK_COST_USD", "0.02")
    run = _run()
    candidate = _candidate()
    before = copy.deepcopy(candidate.qa_result)

    telemetry.start_run(run, upload_cutout_ms=125.0)
    telemetry.record_candidates(
        run, stage="MOTION", parent_id="motion-1", candidates=[candidate]
    )
    completed = _run(completed=True)
    telemetry.finalize_run(
        completed, fallback_used=False, terminal_state="DELIVERED_GENERATED"
    )

    rows = telemetry.rows_for_run(run.id)
    row = next(item for item in rows if item.get("candidate_id") == candidate.id)
    assert candidate.qa_result == before
    assert row["legacy_decision"] == "REVIEW"
    assert row["business_integrity_status"] == "PASS"
    assert row["business_quality_status"] == "REVIEW"
    assert row["delivery_action"] == "DELIVER"
    assert row["retry_action"] == "STOP"
    assert row["vlm_tasks_called"] == ["IDENTITY_VLM", "ANATOMY_VLM"]
    assert row["vlm_call_count"] == 1
    assert row["vlm_cache_hits"] == 1
    assert row["qa_time_ms"] == 12.5
    assert row["estimated_vlm_cost_usd"] == 0.02
    assert row["comparison_flags"]["legacy_nonpass_business_deliver"] is True
    aggregate = next(item for item in rows if item["stage"] == "RUN")
    assert aggregate["legacy_decision"] == "REVIEW"
    assert aggregate["business_integrity_status"] == "PASS"
    assert aggregate["delivery_action"] == "DELIVER"
    assert aggregate["comparison_flags"]["candidate_1_delivered_without_retry"] is True


def test_shadow_comparison_flags_cover_legacy_pass_block_and_deliver_fallback(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    run = _run(completed=True)
    blocked = _candidate(attempt=1, selected=False, decision="PASS", delivery="BLOCK")
    delivered = _candidate(attempt=2, selected=False, decision="REVIEW", delivery="DELIVER")
    telemetry.start_run(run)
    telemetry.record_candidates(
        run,
        stage="MOTION",
        parent_id="motion-1",
        candidates=[blocked, delivered],
    )
    telemetry.finalize_run(run, fallback_used=True, terminal_state="DELIVERED_FALLBACK")

    aggregate = next(
        item for item in telemetry.rows_for_run(run.id) if item["stage"] == "RUN"
    )
    assert aggregate["comparison_flags"]["legacy_pass_business_block"] is True
    assert aggregate["comparison_flags"]["business_deliver_with_fallback"] is True


def test_stage_timing_and_run_totals_are_recorded(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    queued = _run()
    telemetry.start_run(queued, upload_cutout_ms=50.0)
    telemetry.mark_claimed(queued, claim_time_ms=4.25)
    running = SimpleNamespace(**{**vars(queued), "status": "RUNNING", "current_stage": "CANONICAL"})
    telemetry.observe_progress(
        queued,
        running,
        {"current_stage": "CANONICAL"},
    )
    completed = _run(completed=True)
    telemetry.observe_progress(
        running,
        completed,
        {"current_stage": "PUBLISHED", "completed_at": completed.completed_at},
    )
    telemetry.finalize_run(
        completed, fallback_used=False, terminal_state="DELIVERED_GENERATED"
    )

    row = next(item for item in telemetry.rows_for_run(queued.id) if item["stage"] == "RUN")
    assert row["timings"]["upload_to_cutout_ms"] == 50.0
    assert row["timings"]["worker_claim_ms"] >= 4.25
    assert row["timings"]["queue_wait_ms"] is not None
    assert row["timings"]["total_request_ms"] == 10000.0
    assert "CANONICAL" in row["timings"]["stages"]


def test_fallback_retry_and_rollout_aggregate(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    run = _run(completed=True)
    telemetry.start_run(run)
    telemetry.record_candidates(
        run,
        stage="MOTION",
        parent_id="motion-1",
        candidates=[
            _candidate(attempt=1, selected=False, decision="FAIL", delivery="BLOCK"),
            _candidate(attempt=2, selected=False, decision="FAIL", delivery="BLOCK"),
        ],
    )
    telemetry.finalize_run(
        run, fallback_used=True, terminal_state="DELIVERED_FALLBACK"
    )

    aggregate = next(item for item in telemetry.rows_for_run(run.id) if item["stage"] == "RUN")
    assert aggregate["candidate_count"] == 2
    assert aggregate["retry_used"] is True
    assert aggregate["fallback_used"] is True
    assert aggregate["metadata"]["terminal_state"] == "DELIVERED_FALLBACK"
    metrics = telemetry.aggregate_metrics([aggregate])
    assert metrics["run_count"] == 1
    assert metrics["completion_rate"] == 1.0
    assert metrics["average_candidates_per_run"] == 2.0
    assert metrics["average_vlm_calls_per_run"] == 2.0
    assert metrics["retry_rate"] == 1.0
    assert metrics["fallback_rate"] == 1.0
    assert metrics["average_total_request_ms"] == 10000.0


def test_telemetry_storage_failure_is_fail_open(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "1")

    class BrokenClient:
        def table(self, _name):
            raise RuntimeError("telemetry unavailable")

    monkeypatch.setattr(telemetry, "_client", lambda: BrokenClient())
    run = _run()
    candidate = _candidate()
    before = copy.deepcopy(candidate.qa_result)

    telemetry.start_run(run)
    telemetry.record_candidates(
        run, stage="MOTION", parent_id="motion-1", candidates=[candidate]
    )
    telemetry.finalize_run(run, fallback_used=False, terminal_state=None)

    assert candidate.qa_result == before


def test_rollout_metrics_can_be_limited_to_enrolled_user_test_runs():
    enrolled = {
        "stage": "RUN",
        "candidate_count": 1,
        "vlm_call_count": 0,
        "fallback_used": False,
        "retry_used": False,
        "timings": {"completed_at": "2026-10-02T00:00:10+00:00"},
        "metadata": {"user_test_cutover": {"enrolled": True}},
    }
    control = {
        **copy.deepcopy(enrolled),
        "candidate_count": 2,
        "metadata": {"user_test_cutover": {"enrolled": False}},
    }

    metrics = telemetry.aggregate_metrics([enrolled, control], cohort_only=True)
    assert metrics["run_count"] == 1
    assert metrics["average_candidates_per_run"] == 1.0
