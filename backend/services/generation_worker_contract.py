"""Versioned contract for consumers of the Supabase generation queue."""

from __future__ import annotations


# This value is duplicated in the claim-RPC migration and pinned by tests. A
# worker with any other value must fail before the database selects a run.
WORKER_CONTRACT_VERSION = "pet-generation-worker-v2"

