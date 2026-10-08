from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
import yaml

from backend.services import (
    business_qa,
    business_qa_user_test as user_test,
    canonical_image_providers,
    customer_fallback_service,
    pet_generation_run_service as runs,
    shaker_ops,
    video_motion_providers,
    vlm_identity,
)


def _run(awaitable):
    return anyio.run(lambda: awaitable)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("BUSINESS_QA_USER_TEST_MODE", "off")
    monkeypatch.delenv("BUSINESS_QA_USER_TEST_PET_IDS", raising=False)
    runs.__reset_for_tests()
    yield
    runs.__reset_for_tests()


def test_allowlist_admits_only_enrolled_pets(monkeypatch):
    monkeypatch.setenv("BUSINESS_QA_USER_TEST_MODE", "allowlist")
    monkeypatch.setenv("BUSINESS_QA_USER_TEST_PET_IDS", "pet-a,pet-b")

    receipt = user_test.cutover_receipt(user_id="user-1", pet_id="pet-a")
    assert receipt["enrolled"] is True
    assert receipt["authority"] == business_qa.BUSINESS_QA_VERSION
    assert receipt["candidate_budget"]["max_paid_candidates"] == 2

    with pytest.raises(user_test.UserTestError) as caught:
        user_test.require_admission(user_id="user-1", pet_id="pet-c")
    assert caught.value.code == "BUSINESS_QA_USER_TEST_NOT_ENROLLED"


def test_noncohort_start_is_rejected_before_intake_or_paid_work(monkeypatch):
    monkeypatch.setenv("BUSINESS_QA_USER_TEST_MODE", "allowlist")
    monkeypatch.setenv("BUSINESS_QA_USER_TEST_PET_IDS", "pet-enrolled")

    async def should_not_read_intake(**_kwargs):
        raise AssertionError("cohort rejection must happen before pipeline work")

    monkeypatch.setattr(runs.pet_reference_service, "list_references", should_not_read_intake)
    with pytest.raises(runs.PetGenerationRunError) as caught:
        _run(
            runs.start_generation_run(
                user_id="user-1",
                pet_id="pet-not-enrolled",
                idempotency_key="phase12-rejected",
            )
        )
    assert caught.value.code == "BUSINESS_QA_USER_TEST_NOT_ENROLLED"
    assert runs._MOCK_RUNS == []


def _queued_row(run_id: str, pet_id: str) -> dict:
    return {
        "id": run_id,
        "user_id": "user-1",
        "pet_id": pet_id,
        "content_id": f"content-{pet_id}",
        "motion_id": "BREATHING",
        "request_kind": "FREE_HOME",
        "idempotency_key": run_id,
        "status": runs.STATUS_QUEUED,
        "current_stage": runs.STAGE_QUEUED,
        "keyframes": {},
        "provider_state": {},
        "retry_count": 0,
        "lease_recoveries": 0,
        "created_at": "2026-10-02T00:00:00+00:00",
        "updated_at": "2026-10-02T00:00:00+00:00",
    }


def test_worker_claims_only_allowlisted_cohort(monkeypatch):
    monkeypatch.setenv("BUSINESS_QA_USER_TEST_MODE", "allowlist")
    monkeypatch.setenv("BUSINESS_QA_USER_TEST_PET_IDS", "pet-allowed")
    runs._MOCK_RUNS.extend(
        [_queued_row("run-blocked", "pet-blocked"), _queued_row("run-allowed", "pet-allowed")]
    )

    claimed = _run(runs._claim_next("worker-1"))
    assert claimed is not None and claimed.id == "run-allowed"
    assert next(row for row in runs._MOCK_RUNS if row["id"] == "run-blocked")["status"] == runs.STATUS_QUEUED


def test_provider_refresh_preserves_cutover_receipt(monkeypatch):
    receipt = {"version": user_test.USER_TEST_VERSION, "enrolled": True}
    run = SimpleNamespace(id="run-1", provider_state={"_business_qa_cutover": receipt})
    monkeypatch.setattr(runs.durable_provider_jobs, "summary_for_run", lambda _run_id: {"job": {}})

    refreshed = runs._provider_state(run)
    assert refreshed["job"] == {}
    assert refreshed["_business_qa_cutover"] == receipt


class _AvailableProvider:
    name = "configured-live-provider"

    def available(self):
        return True


def test_live_readiness_requires_cohort_worker_telemetry_vlm_and_providers(monkeypatch):
    monkeypatch.setenv("BUSINESS_QA_USER_TEST_MODE", "allowlist")
    monkeypatch.setenv("BUSINESS_QA_USER_TEST_PET_IDS", "pet-a")
    monkeypatch.setenv("PHASE6_LIVE_MODE", "allowlist")
    monkeypatch.setenv("PHASE6_LIVE_ALLOWLIST", "pet-a")
    monkeypatch.setenv("PET_GENERATION_WORKER_ENABLED", "1")
    monkeypatch.setenv("BUSINESS_QA_SHADOW_TELEMETRY", "1")
    provider = _AvailableProvider()
    monkeypatch.setattr(canonical_image_providers, "resolve_providers", lambda: [provider])
    monkeypatch.setattr(canonical_image_providers, "resolve_keyframe_providers", lambda: [provider])
    monkeypatch.setattr(canonical_image_providers, "_mock_enabled", lambda: False)
    monkeypatch.setattr(canonical_image_providers, "_keyframe_mock_enabled", lambda: False)
    monkeypatch.setattr(video_motion_providers, "resolve_provider_order", lambda _order: [provider])
    monkeypatch.setattr(video_motion_providers, "_mock_enabled", lambda: False)
    monkeypatch.setattr(vlm_identity, "unavailable_reason", lambda: None)

    report = user_test.readiness_report()
    assert report["ready_to_enable"] is True
    assert report["blockers"] == []
    assert report["checks"]["max_automatic_paid_candidates"] == 2
    assert report["checks"]["publication_ownership_behavior"] == "unchanged"


def _delivered_run(*, terminal="DELIVERED_GENERATED"):
    return SimpleNamespace(
        id="run-feedback",
        user_id="user-1",
        pet_id="pet-1",
        motion_id="BREATHING",
        provider_state={
            "_business_qa": {
                "terminal_state": terminal,
                "decision": {"delivery_action": "DELIVER"},
            },
            "_business_qa_cutover": {
                "version": user_test.USER_TEST_VERSION,
                "enrolled": True,
            },
        },
    )


def test_feedback_tracks_acceptance_and_complaints_without_changing_run():
    delivered = _delivered_run()
    before = copy.deepcopy(delivered.provider_state)
    row = user_test.record_feedback(
        run=delivered,
        user_id="user-1",
        accepted=False,
        complaints=["identity", "motion"],
        comment="Face drifted during the turn.",
    )

    assert delivered.provider_state == before
    assert row["complaints"] == ["IDENTITY", "MOTION"]
    metrics = user_test.feedback_metrics()
    assert metrics["response_count"] == 1
    assert metrics["acceptance_rate"] == 0.0
    assert metrics["complaint_counts"]["IDENTITY"] == 1
    assert metrics["complaint_counts"]["MOTION"] == 1


def test_feedback_rejects_undelivered_run():
    with pytest.raises(user_test.UserTestError) as caught:
        user_test.record_feedback(
            run=_delivered_run(terminal="TRUE_INFRASTRUCTURE_FAILURE"),
            user_id="user-1",
            accepted=False,
        )
    assert caught.value.code == "BUSINESS_QA_FEEDBACK_NOT_READY"


def test_read_only_fallback_drill_resolves_every_cohort_pet_without_candidates(monkeypatch):
    monkeypatch.setenv("BUSINESS_QA_USER_TEST_PET_IDS", "pet-b,pet-a")
    calls = []

    async def owner(pet_id):
        return f"owner-{pet_id}"

    async def resolve(**kwargs):
        calls.append(kwargs)
        return customer_fallback_service.CustomerFallbackAsset(
            tier=customer_fallback_service.TIER_CANONICAL_STILL,
            asset_kind="canonical_image",
            user_id=kwargs["user_id"],
            pet_id=kwargs["pet_id"],
            requested_motion_id=kwargs["motion_id"],
            delivery_format=customer_fallback_service.FORMAT_CANONICAL_STILL,
            canonical_version_id=f"canonical-{kwargs['pet_id']}",
        )

    monkeypatch.setattr(shaker_ops, "resolve_pet_owner", owner)
    monkeypatch.setattr(customer_fallback_service, "resolve_best_safe_fallback", resolve)

    report = _run(user_test.read_only_fallback_drill())

    assert report["read_only"] is True
    assert report["ready"] is True
    assert [row["pet_id"] for row in report["results"]] == ["pet-a", "pet-b"]
    assert all(call["current_candidates"] == () for call in calls)


def test_phase12_claim_migration_is_cohort_scoped():
    root = Path(__file__).resolve().parents[2]
    sql = (
        root / "supabase/migrations/20261102000000_business_qa_user_test_claims.sql"
    ).read_text()
    assert "p_pet_allowlist text[] default null" in sql
    assert "pet_id = any(p_pet_allowlist)" in sql
    assert "for update skip locked" in sql
    assert "p_max_lease_recoveries int default 2" in sql
    assert "business_qa_user_test_claim_contract" in sql
    assert "select 'business-user-test-v1'::text" in sql


def test_render_blueprint_keeps_web_and_worker_on_the_same_fail_closed_cohort_contract():
    root = Path(__file__).resolve().parents[2]
    blueprint = yaml.safe_load((root / "render.yaml").read_text())
    services = {service["name"]: service for service in blueprint["services"]}
    web = services["eternal-beam-video-api"]
    worker = services["eternal-beam-generation-worker"]

    for service in (web, worker):
        env = {row["key"]: row for row in service["envVars"]}
        assert env["BUSINESS_QA_USER_TEST_MODE"]["value"] == "allowlist"
        assert env["BUSINESS_QA_USER_TEST_PET_IDS"]["sync"] is False
        assert env["PHASE6_LIVE_MODE"]["value"] == "allowlist"
        assert env["PHASE6_LIVE_ALLOWLIST"]["sync"] is False
        assert env["BUSINESS_QA_SHADOW_TELEMETRY"]["value"] == "1"
        assert env["PET_VLM_IDENTITY_ENABLED"]["value"] == "1"
        assert env["ANTHROPIC_API_KEY"]["sync"] is False

    assert worker["dockerCommand"] == "exec python -m backend.workers.pet_generation_worker"
    requirements = (root / "backend/requirements-render.txt").read_text()
    assert "anthropic>=1.0.0" in requirements
