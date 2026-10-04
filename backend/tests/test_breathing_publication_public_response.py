"""Publication responses: customer QA label follows Business QA; receipts stay internal."""

from __future__ import annotations

import json

import pytest

from backend.services import business_qa
from backend.services import motion_publication_service as publication
from backend.services import motion_video_service as motions

from .conftest import ASGITestClient
from .test_phase7a_breathing_publication import (  # noqa: F401  (fixtures)
    CANDIDATE_ID,
    PET,
    USER,
    VERSION_ID,
    _auth,
    _isolated,
    _post,
    _run,
    _seed,
    client,
)

_INTERNAL_MARKERS = (
    "qa_decision", "qa_reasons", "severity_gate", "business_qa", "delivery_action",
    "DELIVER_WITH_ADVISORY", "legacy_decision", "authority_evidence",
)


def _receipt(delivery: str, integrity: str = "PASS") -> dict:
    return {
        "version": business_qa.BUSINESS_QA_VERSION,
        "authority_profile": "breathing-v2",
        "integrity_status": integrity,
        "quality_status": "REVIEW",
        "delivery_action": delivery,
        "retry_action": "STOP" if delivery != "BLOCK" else "REGENERATE",
        "legacy_decision": "FAIL",
        "reasons": ["quality_advisory:breathing_global_motion_integrity=REVIEW"],
    }


def _seed_with_receipt(delivery: str, *, decision: str = "FAIL", integrity: str = "PASS"):
    _seed(decision=decision)
    motions._MOCK_CANDIDATES[-1]["qa_result"] = {
        "decision": decision,
        "business_qa": _receipt(delivery, integrity),
    }


@pytest.mark.parametrize("delivery", ["DELIVER_WITH_ADVISORY", "DELIVER"])
def test_published_label_is_pass_when_receipt_delivers_and_stored_decision_is_kept(delivery):
    _seed_with_receipt(delivery)

    published = _run(
        publication.publish_breathing(user_id=USER, pet_id=PET, motion_version_id=VERSION_ID)
    )
    hydrated = _run(publication.get_published_breathing(user_id=USER, pet_id=PET))

    # The publish result keeps the actual decision as an internal audit field
    # (never serialized by the HTTP model); the hydration label is customer-facing.
    assert published.qa_decision == "FAIL"
    assert hydrated.qa_decision == "PASS"
    stored = next(c for c in motions._MOCK_CANDIDATES if c["id"] == CANDIDATE_ID)
    assert stored["decision"] == "FAIL"
    assert stored["qa_result"]["business_qa"]["legacy_decision"] == "FAIL"


def test_legacy_pass_without_receipt_is_unchanged():
    _seed(decision="PASS")

    published = _run(
        publication.publish_breathing(user_id=USER, pet_id=PET, motion_version_id=VERSION_ID)
    )

    assert published.qa_decision == "PASS"
    assert _run(publication.get_published_breathing(user_id=USER, pet_id=PET)).qa_decision == "PASS"


def test_customer_qa_decision_rules():
    decide = business_qa.customer_qa_decision
    assert decide({"business_qa": _receipt("DELIVER_WITH_ADVISORY")}, "FAIL") == "PASS"
    assert decide({"business_qa": _receipt("DELIVER", "REVIEW")}, "REVIEW") == "PASS"
    # Blocking / integrity-failed / foreign-version receipts never upgrade the label.
    assert decide({"business_qa": _receipt("BLOCK", "FAIL")}, "FAIL") == "FAIL"
    assert decide({"business_qa": _receipt("DELIVER", "FAIL")}, "FAIL") == "FAIL"
    assert decide({"business_qa": {**_receipt("DELIVER"), "version": "business-v0"}}, "FAIL") == "FAIL"
    assert decide({}, "REVIEW") == "REVIEW"
    assert decide(None, None) is None


def test_publish_and_published_http_responses_expose_no_qa_internals(client: ASGITestClient):
    _seed_with_receipt("DELIVER_WITH_ADVISORY")

    posted = _post(client)
    fetched = client.get(f"/api/v1/pet/motions/{PET}/BREATHING/published", headers=_auth())

    assert posted.status_code == 200, posted.text
    assert fetched.status_code == 200, fetched.text
    for response in (posted, fetched):
        text = json.dumps(response.json())
        for marker in _INTERNAL_MARKERS:
            assert marker not in text, marker
