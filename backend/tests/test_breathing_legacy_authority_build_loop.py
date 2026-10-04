"""BREATHING legacy-authority retirement — motion build loop (real builder, fake providers)."""

from __future__ import annotations

import pytest

from backend.services import business_qa
from backend.services import motion_video_service as mv

from .test_motion_video_generation import (
    FakeVideoProvider,
    GOOD,
    VLM_MV_OK,
    _build_motion,
    _prepare_pipeline,
    install_mv_vlm,
)
from .test_motion_video_generation import _mock_backend, storage  # noqa: F401  (fixtures)

BUSINESS, LEGACY = business_qa.QA_AUTHORITY_BUSINESS, business_qa.QA_AUTHORITY_LEGACY


def _mode(monkeypatch, mode: str) -> None:
    monkeypatch.setattr(business_qa, "_PROCESS_QA_AUTHORITY", mode)


def test_build_loop_legacy_review_with_stop_receipt_buys_one_candidate(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    install_mv_vlm(monkeypatch, None)  # VLM returns nothing -> legacy REVIEW
    _mode(monkeypatch, BUSINESS)
    primary = FakeVideoProvider("seedance", [GOOD()] * 5)
    fallback = FakeVideoProvider("kling", [GOOD()] * 5)

    version = _build_motion(h, "BREATHING", [primary, fallback])

    assert primary.calls == 1 and fallback.calls == 0
    candidate = version.candidates[0]
    assert candidate.decision == "REVIEW"
    assert candidate.qa_result["business_qa"]["retry_action"] == "STOP"
    assert version.status == mv.STATUS_COMPLETE and version.selected_candidate_id == candidate.id


def test_business_qa_error_in_build_loop_is_safe_default_without_more_spend(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    install_mv_vlm(monkeypatch, VLM_MV_OK)

    def broken(*args, **kwargs):
        raise RuntimeError("receipt builder crashed")

    monkeypatch.setattr(business_qa, "attach_business_result", broken)
    _mode(monkeypatch, BUSINESS)
    primary = FakeVideoProvider("seedance", [GOOD()] * 5)
    fallback = FakeVideoProvider("kling", [GOOD()] * 5)

    version = _build_motion(h, "BREATHING", [primary, fallback])

    assert primary.calls == 1 and fallback.calls == 0
    receipt = version.candidates[0].qa_result["business_qa"]
    assert receipt["safe_default"] is True
    assert receipt["safe_default_reason"] == business_qa.SAFE_DEFAULT_REASON_ERROR
    assert receipt["delivery_action"] == "BLOCK" and receipt["retry_action"] == "FALLBACK"
    assert version.selected_candidate_id is None
    assert version.qa_summary["business_qa"]["selected"]["safe_default"] is True


def test_flag_off_business_qa_error_propagates_as_before(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    install_mv_vlm(monkeypatch, VLM_MV_OK)

    def broken(*args, **kwargs):
        raise RuntimeError("receipt builder crashed")

    monkeypatch.setattr(business_qa, "attach_business_result", broken)
    _mode(monkeypatch, LEGACY)

    with pytest.raises(RuntimeError):
        _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])


def test_other_motion_build_loop_is_unchanged_under_the_business_flag(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    install_mv_vlm(monkeypatch, VLM_MV_OK)

    def broken(*args, **kwargs):
        raise RuntimeError("receipt builder crashed")

    monkeypatch.setattr(business_qa, "attach_business_result", broken)
    _mode(monkeypatch, BUSINESS)

    # A non-BREATHING motion gets no safe-default receipt: the error propagates as before.
    with pytest.raises(RuntimeError):
        _build_motion(h, "TAIL_WAGGING", [FakeVideoProvider("seedance", [GOOD()])])
