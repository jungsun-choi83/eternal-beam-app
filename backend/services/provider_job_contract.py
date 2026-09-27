"""Provider-neutral durable job primitives used by Phase 7D adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PENDING = "PENDING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
#: A durable job that never resolved within its persisted deadline, or whose
#: poll/collect calls errored too many times in a row. Terminal like FAILED —
#: never re-polled or auto-resubmitted — but distinguishable for monitoring.
TIMED_OUT = "TIMED_OUT"


@dataclass(frozen=True)
class ProviderSubmission:
    external_job_id: str
    provider_status: str = PENDING
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderJobCheck:
    status: str
    provider_status: str
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

