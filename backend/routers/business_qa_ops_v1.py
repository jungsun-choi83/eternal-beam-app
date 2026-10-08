"""Read-only Phase 12 operator readiness and rollout metrics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..auth import AuthedUser
from ..services import business_qa_user_test, qa_shadow_telemetry, shaker_ops


router = APIRouter(prefix="/v1/ops/business-qa", tags=["business-qa-ops"])


@router.get("/readiness")
async def readiness(
    _user: AuthedUser = Depends(shaker_ops.require_ops),
) -> dict[str, Any]:
    """Read-only preflight; never submits generation or VLM work."""

    return business_qa_user_test.readiness_report()


@router.get("/metrics")
async def metrics(
    _user: AuthedUser = Depends(shaker_ops.require_ops),
) -> dict[str, Any]:
    """Phase 11 runtime telemetry plus Phase 12 customer feedback."""

    return {
        "cutover_version": business_qa_user_test.USER_TEST_VERSION,
        "runtime": qa_shadow_telemetry.aggregate_metrics(cohort_only=True),
        "customer_feedback": business_qa_user_test.feedback_metrics(),
    }


@router.get("/fallback-drill")
async def fallback_drill(
    _user: AuthedUser = Depends(shaker_ops.require_ops),
) -> dict[str, Any]:
    """Resolve cohort fallbacks without generation, publication, or pointer writes."""

    return await business_qa_user_test.read_only_fallback_drill()
