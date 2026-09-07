"""Phase 7G 테스트 보조 — REVIEW 후보가 있는 파이프라인 하네스."""

from __future__ import annotations

from types import SimpleNamespace

from .test_phase7c_generation_runs import PipelineHarness


def review_harness(
    monkeypatch,
    *,
    version_id: str | None = None,
    candidate_id: str | None = None,
) -> PipelineHarness:
    """QA REVIEW 로 끝나는 모션 + REVIEW 후보 1개. 결정은 절대 PASS 로 가공되지 않는다."""
    harness = PipelineHarness(monkeypatch, motion_status="review")
    review = SimpleNamespace(
        id=candidate_id or "00000000-0000-0000-0000-000000000698",
        selected=False,
        decision="REVIEW",
        qa_result={"identity_similarity": 0.61},
    )
    harness.review_candidate = review
    harness.motion.candidates = [review]
    harness.motion.selected_candidate_id = None
    if version_id:
        harness.motion.id = version_id
    return harness
