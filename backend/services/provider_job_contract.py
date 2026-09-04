"""Provider-neutral durable job primitives used by Phase 7D adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PENDING = "PENDING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"


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

