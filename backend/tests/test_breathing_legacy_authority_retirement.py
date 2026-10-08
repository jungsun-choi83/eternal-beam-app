"""BREATHING: legacy QA authority retired — the business-v1 receipt is the only authority.

Legacy PASS/REVIEW/FAIL stays stored telemetry. With BREATHING_QA_AUTHORITY=business
it cannot change delivery, retry, fallback, selection, publication or run status.
The flag defaults to legacy; every case is checked in both modes where relevant.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services import business_qa
from backend.services import business_qa_user_test
from backend.services import customer_fallback_service
from backend.services import motion_delivery_service as delivery
from backend.services import motion_publication_service as publication
from backend.services import motion_video_service as mv
from backend.services import pet_generation_run_service as runs

from . import test_phase7a_breathing_publication as p7a
from .test_canonical_keyframe_review_recovery import _install_resolved_fallback
from .test_phase7c_generation_runs import PipelineHarness, seed_intake, start, work
from .test_phase7c_generation_runs import _clean, storage  # noqa: F401  (fixtures; _clean is autouse)
from .test_phase7g_helpers import review_harness

BUSINESS, LEGACY = business_qa.QA_AUTHORITY_BUSINESS, business_qa.QA_AUTHORITY_LEGACY


def _mode(monkeypatch, mode: str) -> None:
    """Set the process-start flag value (read once in production)."""

    monkeypatch.setattr(business_qa, "_PROCESS_QA_AUTHORITY", mode)


def _receipt(delivery: str, retry: str, integrity: str = "PASS", attempt: int = 1) -> dict:
    terminal = {
        "DELIVER": business_qa.DELIVERED_GENERATED,
        "DELIVER_WITH_ADVISORY": business_qa.DELIVERED_GENERATED,
    }.get(delivery, business_qa.DELIVERED_FALLBACK if retry == "FALLBACK" else None)
    return {
        "version": business_qa.BUSINESS_QA_VERSION,
        "authority_profile": "breathing-v2",
        "integrity_status": integrity,
        "quality_status": "REVIEW",
        "delivery_action": delivery,
        "retry_action": retry,
        "attempt_number": attempt,
        "terminal_state": terminal,
    }


def _candidate(cid: str, decision: str, receipt: dict | None, *, selected: bool = False, attempt: int = 1):
    qa_result = {"decision": decision}
    if receipt is not None:
        qa_result["business_qa"] = receipt
    return SimpleNamespace(
        id=f"00000000-0000-0000-0000-0000000006{cid}", decision=decision,
        qa_result=qa_result, selected=selected, attempt=attempt,
    )


def _set_motion(harness: PipelineHarness, candidates, *, status: str, selected=None) -> None:
    harness.motion.candidates = list(candidates)
    harness.motion.status = status
    harness.motion.selected_candidate_id = selected.id if selected else None
    if selected:
        harness.publication.selected_candidate_id = selected.id


# ── flag, stamp, scope ───────────────────────────────────────────────────


def test_flag_defaults_to_legacy_and_only_business_enables_retirement(monkeypatch):
    _mode(monkeypatch, LEGACY)
    monkeypatch.delenv("BREATHING_QA_AUTHORITY", raising=False)
    assert business_qa.breathing_qa_authority() == LEGACY
    monkeypatch.setenv("BREATHING_QA_AUTHORITY", "nonsense")
    assert business_qa.breathing_qa_authority() == LEGACY
    monkeypatch.setenv("BREATHING_QA_AUTHORITY", "business")
    assert business_qa.breathing_qa_authority() == BUSINESS
    assert business_qa.legacy_authority_retired("BREATHING") is False  # process value unchanged


def test_run_is_stamped_at_creation_and_keeps_its_mode(storage, monkeypatch):
    seed_intake()
    PipelineHarness(monkeypatch)
    _mode(monkeypatch, BUSINESS)
    stamped = start(key="stamp:business")
    assert stamped.provider_state["_business_qa_cutover"]["breathing_qa_authority"] == BUSINESS
    assert business_qa.run_qa_authority(stamped.provider_state) == BUSINESS
    # Unstamped (pre-deploy) and legacy-stamped runs resolve to legacy.
    assert business_qa.run_qa_authority({}) == LEGACY
    assert business_qa.run_qa_authority({"_business_qa_cutover": {"enrolled": True}}) == LEGACY
    with business_qa.qa_authority_scope(LEGACY):
        assert business_qa.legacy_authority_retired("BREATHING") is False
    assert business_qa.legacy_authority_retired("BREATHING") is True


def test_in_flight_run_created_under_legacy_stays_legacy_after_the_flip(storage, monkeypatch):
    seed_intake()
    review_harness(monkeypatch)
    _mode(monkeypatch, LEGACY)
    start(key="inflight:legacy")
    _mode(monkeypatch, BUSINESS)  # flag flipped while the run is queued

    result = work()

    assert result.status == runs.STATUS_FAILED
    assert (result.last_error or {}).get("code") == "MOTION_QA_REVIEW"


# ── 1. legacy FAIL + Business QA DELIVER -> delivered and published ───────


@pytest.mark.parametrize("delivery_action", ["DELIVER", "DELIVER_WITH_ADVISORY"])
@pytest.mark.parametrize("version_status", ["complete", "review", "failed"])
def test_legacy_fail_with_delivering_receipt_publishes(storage, monkeypatch, delivery_action, version_status):
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    chosen = _candidate("10", "FAIL", _receipt(delivery_action, "STOP"), selected=True)
    _set_motion(harness, [chosen], status=version_status, selected=chosen)
    _mode(monkeypatch, BUSINESS)

    start(key=f"deliver:{delivery_action}:{version_status}")
    result = work()

    assert result.status == runs.STATUS_PUBLISHED
    assert harness.counts["publication"] == 1 and harness.counts["motion_build"] == 1
    receipt = result.provider_state["_business_qa"]
    assert receipt["terminal_state"] == business_qa.DELIVERED_GENERATED
    assert receipt["candidate_decision"] == "FAIL"  # legacy value kept as telemetry


def test_not_enrolled_run_is_still_governed_by_the_receipt(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    chosen = _candidate("11", "FAIL", _receipt("DELIVER_WITH_ADVISORY", "STOP"), selected=True)
    _set_motion(harness, [chosen], status="complete", selected=chosen)
    monkeypatch.setenv("BUSINESS_QA_USER_TEST_MODE", business_qa_user_test.MODE_OFF)
    _mode(monkeypatch, BUSINESS)

    created = start(key="not-enrolled")
    result = work()

    assert created.provider_state["_business_qa_cutover"]["enrolled"] is False
    assert result.status == runs.STATUS_PUBLISHED and harness.counts["publication"] == 1


# ── 2 + 6. paid retry only per the Business QA contract ──────────────────


@pytest.mark.parametrize("legacy", ["PASS", "REVIEW", "FAIL"])
def test_retry_follows_receipt_never_the_legacy_decision(legacy):
    stop = [{"id": "a", "decision": legacy, "qa_result": {"business_qa": _receipt("DELIVER_WITH_ADVISORY", "STOP")}}]
    regenerate = [{"id": "a", "decision": legacy,
                   "qa_result": {"business_qa": _receipt("BLOCK", "REGENERATE", "FAIL")}}]
    missing = [{"id": "a", "decision": legacy, "qa_result": {"decision": legacy}}]

    for fallback in (True, False):
        assert business_qa.may_generate_next_candidate(stop, legacy_fallback=fallback) is False
        assert business_qa.may_generate_next_candidate(regenerate, legacy_fallback=fallback) is True
    # No receipt: legacy mode buys another candidate on REVIEW/FAIL; retired never does.
    assert business_qa.may_generate_next_candidate(missing, legacy_fallback=True) is (legacy != "PASS")
    assert business_qa.may_generate_next_candidate(missing, legacy_fallback=False) is False
    # The budget is still two paid candidates.
    exhausted = regenerate + [{**regenerate[0], "id": "b"}]
    assert business_qa.may_generate_next_candidate(exhausted, legacy_fallback=False) is False


# ── 3. legacy PASS + Business QA integrity FAIL -> block / retry / fallback ─


def test_legacy_pass_with_integrity_fail_receipts_ends_in_fallback(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    first = _candidate("20", "PASS", _receipt("BLOCK", "REGENERATE", "FAIL", 1), selected=True)
    second = _candidate("21", "PASS", _receipt("BLOCK", "FALLBACK", "FAIL", 2), attempt=2)
    _set_motion(harness, [first, second], status="complete", selected=first)
    _install_resolved_fallback(monkeypatch)
    _mode(monkeypatch, BUSINESS)

    start(key="pass-but-integrity-fail")
    result = work()

    assert harness.counts["publication"] == 0
    receipt = result.provider_state["_business_qa"]
    assert receipt["terminal_state"] == business_qa.DELIVERED_FALLBACK
    assert receipt["decision"]["retry_action"] == "FALLBACK"
    for mode in (LEGACY, BUSINESS):
        with business_qa.qa_authority_scope(mode):
            assert mv.candidate_is_publishable(first, motion_id="BREATHING") is False


# ── 4. DELIVERED_FALLBACK cannot be turned back into FAILED by legacy ─────


def test_delivered_fallback_is_not_overridden_by_legacy_review(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch, motion_status="review")
    blocked = _candidate("30", "REVIEW", _receipt("BLOCK", "REGENERATE", "FAIL", 1))
    exhausted = _candidate("31", "REVIEW", _receipt("BLOCK", "FALLBACK", "FAIL", 2), attempt=2)
    _set_motion(harness, [blocked, exhausted], status="review")
    _install_resolved_fallback(monkeypatch)
    _mode(monkeypatch, BUSINESS)

    start(key="fallback-vs-legacy-review")
    result = work()

    assert result.status == runs.STATUS_PUBLISHED and result.last_error is None
    assert result.provider_state["_business_qa"]["terminal_state"] == business_qa.DELIVERED_FALLBACK
    assert runs.is_delivered_fallback(result)
    assert harness.counts["publication"] == 0 and harness.counts["delivery"] == 0


# ── 5. publication (Python) uses Business QA only ────────────────────────


def _seed_publication(decision: str, receipt: dict | None, created_at: str):
    p7a._seed(decision=decision)
    row = mv._MOCK_CANDIDATES[-1]
    row["created_at"] = created_at
    row["qa_result"] = {"decision": decision, **({"business_qa": receipt} if receipt else {})}


def _publish(**kwargs):
    return p7a._run(publication.publish_breathing(
        user_id=p7a.USER, pet_id=p7a.PET, motion_version_id=p7a.VERSION_ID, **kwargs
    ))


AFTER_CUTOVER, BEFORE_CUTOVER = "2026-10-03T00:00:00+00:00", "2026-09-03T00:00:00+00:00"


@pytest.fixture
def publication_store(monkeypatch):
    for _ in p7a._isolated.__wrapped__(monkeypatch):
        yield


def test_python_publication_gate_is_receipt_only_when_retired(publication_store, monkeypatch):
    _mode(monkeypatch, BUSINESS)

    _seed_publication("FAIL", _receipt("DELIVER_WITH_ADVISORY", "STOP"), AFTER_CUTOVER)
    assert _publish().publication_id

    for decision, receipt in (
        ("PASS", None),                                   # missing receipt
        ("PASS", _receipt("BLOCK", "REGENERATE", "FAIL")),  # legacy PASS, Business blocks
        ("PASS", _receipt("DELIVER", "STOP", "FAIL")),      # integrity FAIL always wins
    ):
        mv.__reset_for_tests(); publication.__reset_for_tests()
        _seed_publication(decision, receipt, AFTER_CUTOVER)
        with pytest.raises(publication.MotionPublicationError) as error:
            _publish(allow_grandfathered_legacy_pass=True)
        assert error.value.code == "CANDIDATE_NOT_PASS"


def test_direct_route_grandfathers_only_pre_cutover_legacy_pass(publication_store, monkeypatch):
    _mode(monkeypatch, BUSINESS)
    _seed_publication("PASS", None, BEFORE_CUTOVER)

    with pytest.raises(publication.MotionPublicationError):
        _publish()  # run path: no grandfathering
    assert _publish(allow_grandfathered_legacy_pass=True).publication_id

    mv.__reset_for_tests(); publication.__reset_for_tests()
    _seed_publication("REVIEW", None, BEFORE_CUTOVER)  # only legacy PASS is grandfathered
    with pytest.raises(publication.MotionPublicationError):
        _publish(allow_grandfathered_legacy_pass=True)


def test_packaging_and_playback_follow_the_receipt_when_retired(monkeypatch):
    deliver = {"motion_id": "BREATHING", "decision": "FAIL",
               "qa_result": {"business_qa": _receipt("DELIVER_WITH_ADVISORY", "STOP")}}
    legacy_review = {"motion_id": "BREATHING", "decision": "REVIEW", "qa_result": {"decision": "REVIEW"}}
    legacy_pass = {"motion_id": "BREATHING", "decision": "PASS", "qa_result": {"decision": "PASS"}}

    with business_qa.qa_authority_scope(BUSINESS):
        assert mv.candidate_is_publishable(deliver) is True
        assert mv.candidate_is_publishable(legacy_review) is False
        assert mv.candidate_is_publishable(legacy_pass) is False
    with business_qa.qa_authority_scope(LEGACY):
        assert mv.candidate_is_publishable(legacy_pass) is True
    assert delivery.motions is mv


# ── 7. missing receipt / Business QA error -> safe default, never legacy ──


@pytest.mark.parametrize("legacy", ["PASS", "REVIEW", "FAIL"])
def test_missing_receipt_resolves_to_fallback_not_legacy(storage, monkeypatch, legacy):
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    orphan = _candidate("40", legacy, None, selected=(legacy == "PASS"))
    _set_motion(harness, [orphan], status=("complete" if legacy == "PASS" else "review"),
                selected=(orphan if legacy == "PASS" else None))
    _install_resolved_fallback(monkeypatch)
    _mode(monkeypatch, BUSINESS)

    start(key=f"missing-receipt:{legacy}")
    result = work()

    assert harness.counts["publication"] == 0
    assert result.status == runs.STATUS_PUBLISHED and result.last_error is None
    receipt = result.provider_state["_business_qa"]
    assert receipt["terminal_state"] == business_qa.DELIVERED_FALLBACK
    assert receipt["decision"]["safe_default"] is True
    assert receipt["decision"]["safe_default_reason"] == business_qa.SAFE_DEFAULT_REASON_MISSING
    assert receipt["fallback_asset"]["tier"] == customer_fallback_service.TIER_CANONICAL_STILL


def test_missing_receipt_without_any_fallback_fails_with_a_distinct_code(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    _set_motion(harness, [_candidate("41", "PASS", None)], status="review")

    async def nothing(**kwargs):
        return None

    monkeypatch.setattr(customer_fallback_service, "resolve_best_safe_fallback", nothing)
    _mode(monkeypatch, BUSINESS)

    start(key="missing-receipt:no-fallback")
    result = work()

    assert result.status == runs.STATUS_FAILED
    assert result.last_error["code"] == "NO_SAFE_FALLBACK"
    assert harness.counts["publication"] == 0


def test_provider_failure_without_evaluated_candidate_stays_a_retryable_failure(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    _set_motion(harness, [_candidate("42", "ERROR", None)], status="failed")
    _mode(monkeypatch, BUSINESS)

    start(key="provider-error")
    result = work()

    assert result.status == runs.STATUS_FAILED
    assert result.last_error["code"] == "MOTION_QA_FAILED"
    assert "_business_qa" not in result.provider_state


# ── 8. flag off -> legacy behavior restored ──────────────────────────────


def test_flag_off_legacy_review_ends_in_motion_qa_review(storage, monkeypatch):
    seed_intake()
    harness = review_harness(monkeypatch)
    _mode(monkeypatch, LEGACY)

    start(key="legacy:review")
    result = work()

    assert result.status == runs.STATUS_FAILED
    assert result.last_error["code"] == "MOTION_QA_REVIEW"
    assert harness.counts["delivery"] == 1 and harness.counts["publication"] == 0


def test_flag_off_legacy_pass_without_receipt_still_publishes(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)  # default: legacy PASS, no receipt
    _mode(monkeypatch, LEGACY)

    start(key="legacy:pass")
    result = work()

    assert result.status == runs.STATUS_PUBLISHED and harness.counts["publication"] == 1


def test_flag_off_python_publication_gate_accepts_legacy_pass(publication_store, monkeypatch):
    _mode(monkeypatch, LEGACY)
    _seed_publication("PASS", None, AFTER_CUTOVER)

    assert _publish().publication_id


# ── selection ────────────────────────────────────────────────────────────


def test_selection_ignores_legacy_decision_and_similarity_when_retired():
    receipt = _receipt("DELIVER_WITH_ADVISORY", "STOP")
    first = {"id": "a", "attempt": 1, "decision": "FAIL",
             "qa_result": {"business_qa": receipt, "identity_similarity": 0.60}}
    second = {"id": "b", "attempt": 2, "decision": "PASS",
              "qa_result": {"business_qa": receipt, "identity_similarity": 0.99}}

    assert business_qa.best_available_candidate([first, second])["id"] == "b"  # legacy tie-break
    assert business_qa.best_available_candidate([first, second], legacy_fallback=False)["id"] == "a"
    assert [c["id"] for c in mv._rank([second, first], legacy_order=False)] == ["a", "b"]


# ── 9. other motions unchanged ───────────────────────────────────────────


@pytest.mark.parametrize("motion_id", ["BLINKING", "TAIL_WAGGING", "LIE_DOWN", "RUN", "PET_HEAD"])
def test_other_motions_keep_legacy_behavior_under_the_business_flag(monkeypatch, motion_id):
    _mode(monkeypatch, BUSINESS)
    legacy_pass = {"motion_id": motion_id, "decision": "PASS", "qa_result": {"decision": "PASS"}}
    legacy_review = {"motion_id": motion_id, "decision": "REVIEW", "qa_result": {"decision": "REVIEW"}}

    assert business_qa.legacy_authority_retired(motion_id) is False
    assert mv.candidate_is_publishable(legacy_pass) is True
    assert mv.candidate_is_publishable(legacy_review) is False
    assert business_qa.legacy_authority_retired("BREATHING") is True




def test_hydration_of_an_already_published_asset_is_not_regated(publication_store, monkeypatch):
    _mode(monkeypatch, LEGACY)
    _seed_publication("PASS", None, AFTER_CUTOVER)
    assert _publish().publication_id  # published while legacy authority was active
    _mode(monkeypatch, BUSINESS)

    hydrated = p7a._run(publication.get_published_breathing(user_id=p7a.USER, pet_id=p7a.PET))

    assert hydrated.motion_version_id == p7a.VERSION_ID
    assert hydrated.url.endswith("token=fresh")
