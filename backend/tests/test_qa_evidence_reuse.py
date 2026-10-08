from types import SimpleNamespace

from backend.services import business_qa, qa_evidence_reuse


def _approved_qa(version="canonical-qa-v5"):
    qa = {
        "qa_version": version,
        "decision": "PASS",
        "checks": {
            "vlm_same_pet": "PASS",
            "vlm_anatomy": "PASS",
            "vlm_composition": "PASS",
        },
        "business_signals": {
            "canonical_face_head_identity": "PASS",
            "canonical_distinctive_markings_identity": "PASS",
        },
    }
    business_qa.attach_business_result(
        qa, attempt_number=1, request_kind="CANONICAL"
    )
    return qa


def test_profile_and_reference_evidence_is_inherited_when_pins_match():
    profile = SimpleNamespace(
        id="identity-1",
        version=3,
        status="complete",
        analyzer_versions={"identity": "v3"},
    )
    refset = SimpleNamespace(
        id="refs-1",
        version=5,
        status="complete",
        identity_profile_id="identity-1",
        identity_profile_version=3,
        morphology_profile_id="morph-1",
        morphology_profile_version=2,
        source_reference_ids=["r1", "r2"],
        analyzer_versions={"reference_set": "v5"},
    )
    evidence = qa_evidence_reuse.profile_evidence(
        identity_profile=profile,
        reference_set=refset,
        expected_identity_profile_version=3,
        expected_reference_set_version=5,
    )
    assert evidence["valid"] is True
    assert evidence["status"] == qa_evidence_reuse.INHERITED
    assert {"identity_profile", "markings", "morphology_profile", "reference_set"} <= set(
        evidence["domains"]
    )


def test_partial_or_mismatched_profile_evidence_is_stale():
    profile = SimpleNamespace(
        id="identity-2", version=3, status="partial", analyzer_versions={}
    )
    refset = SimpleNamespace(
        id="refs-1",
        version=5,
        status="complete",
        identity_profile_id="identity-1",
        identity_profile_version=3,
        source_reference_ids=["r1"],
        analyzer_versions={},
    )
    evidence = qa_evidence_reuse.profile_evidence(
        identity_profile=profile,
        reference_set=refset,
        expected_identity_profile_version=3,
        expected_reference_set_version=5,
    )
    assert evidence["valid"] is False
    assert evidence["domains"] == []
    assert "identity_profile_not_complete" in evidence["stale_reasons"]
    assert "reference_set_identity_profile_changed" in evidence["stale_reasons"]


def test_approved_canonical_identity_is_reusable_downstream():
    lineage = {
        "canonical_version_id": "canonical-1",
        "canonical_version": 4,
        "identity_profile_version": 3,
        "reference_set_version": 5,
    }
    evidence = qa_evidence_reuse.approved_asset_evidence(
        source_stage="CANONICAL",
        qa_result=_approved_qa(),
        lineage=lineage,
        expected_lineage=lineage,
        expected_qa_version="canonical-qa-v5",
    )
    assert evidence["valid"] is True
    assert evidence["status"] == qa_evidence_reuse.INHERITED
    assert {"identity", "markings", "anatomy"} <= set(evidence["domains"])


def test_changed_profile_or_qa_version_is_stale_and_not_reused():
    lineage = {
        "canonical_version_id": "canonical-1",
        "identity_profile_version": 3,
        "reference_set_version": 5,
    }
    evidence = qa_evidence_reuse.approved_asset_evidence(
        source_stage="CANONICAL",
        qa_result=_approved_qa("canonical-qa-v4"),
        lineage=lineage,
        expected_lineage={**lineage, "identity_profile_version": 4},
        expected_qa_version="canonical-qa-v5",
    )
    assert evidence["valid"] is False
    assert evidence["evidence"] == {}
    assert "qa_version_changed" in evidence["stale_reasons"]
    assert "lineage_changed:identity_profile_version" in evidence["stale_reasons"]


def test_receipt_persists_inherited_escalated_and_cache_hit_states():
    qa = {}
    inherited = {
        "valid": True,
        "status": "inherited",
        "source_stage": "CANONICAL",
        "domains": ["identity"],
        "fingerprint": "abc",
    }
    vlm = {
        "targeted_vlm_evidence": {
            "POSE_VLM": {
                "cache_receipt": {"status": "cache_hit", "cache_key": "key-1"}
            }
        }
    }
    qa_evidence_reuse.attach_receipt(
        qa,
        stage="KEYFRAME",
        escalation={"requested_tasks": ["POSE_VLM"]},
        vlm_result=vlm,
        inherited=[inherited],
    )
    assert qa["qa_evidence_reuse"]["summary"] == {
        "computed": 1,
        "cache_hit": 1,
        "inherited": 1,
        "escalated": 1,
    }
