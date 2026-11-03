"""Archive-only storage routing.

Archive assets are private and must never inherit the public global bucket.
The scope is carried in a ContextVar owned by ``supabase_assets`` so worker
tasks can route all derived assets without changing normal Eternal Beam code.
"""

from __future__ import annotations

import re
from contextlib import contextmanager, nullcontext
from typing import Any, Iterator

from . import supabase_assets

DEFAULT_ARCHIVE_BUCKET = "user-assets"
# Kept only so already-persisted Phase 5C-2 runs can finish without losing
# lineage. New Archive runs never select this bucket.
LEGACY_ARCHIVE_BUCKET = "user-media"
_BUCKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


def archive_storage_bucket() -> str:
    """Return the single bucket used by all new Archive runs."""

    return DEFAULT_ARCHIVE_BUCKET


@contextmanager
def archive_scope(bucket: str | None = None) -> Iterator[str]:
    target = (bucket or archive_storage_bucket()).strip()
    if target not in {DEFAULT_ARCHIVE_BUCKET, LEGACY_ARCHIVE_BUCKET}:
        raise RuntimeError("Archive storage must use an approved private bucket")
    with supabase_assets.storage_scope(target, require_private=True):
        yield target


def scope_for_run(run: Any):
    """Return a durable scope for an Archive run, or a no-op for legacy runs."""

    state = dict(getattr(run, "provider_state", None) or {})
    archive_state = state.get("_archive_storage")
    if not archive_state:
        # Pre-Phase-5C-2 Archive runs used the normal global bucket. Keep them
        # on the now-private user-assets scope without changing their records.
        if str(getattr(run, "user_id", "") or "").startswith("archive_"):
            return archive_scope()
        return nullcontext()
    if not isinstance(archive_state, dict) or archive_state.get("private") is not True:
        raise RuntimeError("Archive generation run has invalid storage scope metadata")
    return archive_scope(str(archive_state.get("bucket") or ""))

