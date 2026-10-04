"""Customer-facing run/playback responses keep Business QA receipts internal."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.routers import generation_runs_v1
from backend.services import motion_publication_service
from backend.services import pet_generation_run_service as runs

from .conftest import ASGITestClient
from .test_phase7c_generation_runs import PET, USER, PipelineHarness, seed_intake, start, work
from .test_phase7g_cutover import _auth, _clean, client, storage  # noqa: F401  (fixtures)

_ADVISORY_RECEIPT = {
    "version": "business-v1",
    "authority_profile": "breathing-v2",
    "integrity_status": "PASS",
    "quality_status": "REVIEW",
    "delivery_action": "DELIVER_WITH_ADVISORY",
    "retry_action": "STOP",
    "legacy_decision": "FAIL",
    "reasons": ["quality_advisory:breathing_global_motion_integrity=REVIEW"],
}


def _published_run(monkeypatch, receipt: dict | None):
    seed_intake()
    PipelineHarness(monkeypatch)
    start()
    run = work()
    assert run.status == runs.STATUS_PUBLISHED
    state = dict(run.provider_state or {})
    state["_business_qa_cutover"] = {"enrolled": True}
    state["_business_qa"] = {
        "version": "business-v1",
        "terminal_state": runs.BUSINESS_DELIVERED_GENERATED,
        "candidate_id": run.selected_candidate_id,
        "candidate_decision": "FAIL",
        "decision": dict(receipt or {}),
    }
    run = replace(run, provider_state=state)

    async def get_run(**kwargs):
        assert kwargs["user_id"] == USER
        return run

    monkeypatch.setattr(runs, "get_generation_run", get_run)
    return run


def _stub_published(monkeypatch, legacy_decision: str | None):
    async def published(**kwargs):
        assert kwargs["pet_id"] == PET
        return SimpleNamespace(
            url="https://storage.test/user-assets/pub_packed.mp4?token=fresh",
            delivery_format="packed_alpha",
            background_baked=False,
            motion_version_id="00000000-0000-0000-0000-000000000601",
            breathing_object_path="pub_packed.mp4",
            qa_decision=legacy_decision,
        )

    monkeypatch.setattr(motion_publication_service, "get_published_breathing", published)


def test_run_status_response_strips_business_receipt(storage, monkeypatch, client: ASGITestClient):
    run = _published_run(monkeypatch, _ADVISORY_RECEIPT)

    response = client.get(f"/api/v1/pet/generation-runs/{run.id}", headers=_auth())

    assert response.status_code == 200, response.text
    body = response.json()
    assert "_business_qa" not in body["provider_state"]
    # The cutover marker the app reads and the derived terminal state remain.
    assert body["provider_state"]["_business_qa_cutover"] == {"enrolled": True}
    assert body["terminal_state"] == runs.BUSINESS_DELIVERED_GENERATED
    text = json.dumps(body)
    for internal in ("DELIVER_WITH_ADVISORY", "delivery_action", "legacy_decision",
                     "candidate_decision", "quality_advisory"):
        assert internal not in text, internal
    # The stored run is untouched.
    assert run.provider_state["_business_qa"]["decision"]["delivery_action"] == "DELIVER_WITH_ADVISORY"


@pytest.mark.parametrize("action", ["DELIVER_WITH_ADVISORY", "DELIVER"])
def test_published_playback_maps_legacy_fail_to_pass_when_receipt_delivers(
    storage, monkeypatch, client: ASGITestClient, action
):
    run = _published_run(monkeypatch, {**_ADVISORY_RECEIPT, "delivery_action": action})
    _stub_published(monkeypatch, "FAIL")

    response = client.get(f"/api/v1/pet/generation-runs/{run.id}/playback", headers=_auth())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["published"] is True
    assert body["qa_decision"] == "PASS"
    assert body["terminal_state"] == runs.BUSINESS_DELIVERED_GENERATED
    assert "ADVISORY" not in json.dumps(body)


def test_published_playback_without_receipt_keeps_legacy_decision(
    storage, monkeypatch, client: ASGITestClient
):
    # Severity-gate publication (no business receipt): previous behavior.
    run = _published_run(monkeypatch, None)
    _stub_published(monkeypatch, "REVIEW")

    response = client.get(f"/api/v1/pet/generation-runs/{run.id}/playback", headers=_auth())

    assert response.json()["qa_decision"] == "REVIEW"


def test_public_qa_decision_rules():
    def run(status, receipt):
        return SimpleNamespace(status=status, provider_state={"_business_qa": {"decision": receipt}})

    decide = generation_runs_v1._public_qa_decision
    assert decide(run(runs.STATUS_PUBLISHED, _ADVISORY_RECEIPT), "FAIL") == "PASS"
    # Not published (dev/review playback) keeps the stored decision.
    assert decide(run(runs.STATUS_FAILED, _ADVISORY_RECEIPT), "REVIEW") == "REVIEW"
    # A blocking or integrity-failed receipt never upgrades the label.
    assert decide(run(runs.STATUS_PUBLISHED, {**_ADVISORY_RECEIPT, "delivery_action": "BLOCK"}), "FAIL") == "FAIL"
    assert decide(run(runs.STATUS_PUBLISHED, {**_ADVISORY_RECEIPT, "integrity_status": "FAIL"}), "FAIL") == "FAIL"
    assert decide(run(runs.STATUS_PUBLISHED, {}), None) == "PASS"
    assert decide(SimpleNamespace(status=runs.STATUS_PUBLISHED, provider_state=None), "PASS") == "PASS"
