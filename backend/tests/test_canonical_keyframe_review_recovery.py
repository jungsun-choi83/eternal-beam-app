"""Canonical (Phase 4) + Keyframe (Phase 5) REVIEW-loop fix.

Bug: `_canonical()`/`_keyframe()` reused an existing REVIEW version exactly like
a COMPLETE one (by design — skip_if_unchanged must not repay for an unchanged
input), but `_execute()` then required STATUS_COMPLETE unconditionally. A REVIEW
version therefore raised the same generic *_NOT_COMPLETE error every retry,
forever, with no distinction from an actual failure and no path back to
progress — not a billing bug (no version was ever rebuilt on retry), but a
permanent dead end.

Fix (mirrors the already-shipped Phase 6/motion REVIEW-recovery pattern in
`test_review_state_recovery.py`):
  * `_execute()` now pins canonical_version_id/keyframes[role] *before* judging
    status (so a REVIEW version is remembered across retries and never
    rebuilt), and raises a distinct CANONICAL_QA_REVIEW / KEYFRAME_QA_REVIEW
    instead of the generic NOT_COMPLETE code.
  * `request_canonical_replacement_generation()` /
    `request_keyframe_replacement_generation()` are the only way to force a
    new paid version from a REVIEW state, gated on that distinct error code
    and the matching stage — same shape as `request_replacement_generation()`.
  * `canonical_pet_service.reevaluate_canonical_candidate()` /
    `action_keyframe_service.reevaluate_keyframe_candidate()` let a REVIEW
    candidate be rejudged against the *current* QA implementation with zero
    provider calls (see test_canonical_pet_builder.py /
    test_action_keyframes.py for the QA-rerun tests using the real QA code).
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import anyio
import pytest

from backend.services import (
    action_keyframe_service,
    canonical_pet_service,
    motion_spec,
    pet_generation_run_service as runs,
    pet_reference_service,
    pet_registry,
)

from .test_phase7c_generation_runs import (
    CID,
    PET,
    USER,
    PipelineHarness,
    seed_intake,
    start,
    work,
)


def _run(awaitable):
    return anyio.run(lambda: awaitable)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("SEEDANCE_TRANSPORT", "runway")
    runs.__reset_for_tests()
    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()
    yield
    runs.__reset_for_tests()
    pet_reference_service.__reset_for_tests()
    pet_registry.__reset_for_tests()


@pytest.fixture
def storage(monkeypatch):
    from backend.services import supabase_assets

    async def upload(path, data, content_type):
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", upload)
    return None


# 1. REVIEW canonical stays recoverable, never loops into a repeated build ──


def test_review_canonical_does_not_loop(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch, canonical_status=canonical_pet_service.STATUS_REVIEW)

    started = start(key="canonical-review:no-loop")
    first = work()

    assert first.status == runs.STATUS_FAILED
    assert first.current_stage == runs.STAGE_CANONICAL  # not KEYFRAMES/MOTION_* — never advanced
    assert first.last_error["code"] == "CANONICAL_QA_REVIEW"
    assert first.canonical_version_id == harness.canonical.id  # pinned, so retries don't re-derive
    assert harness.counts["canonical_build"] == 1
    assert harness.counts["keyframe_build"] == 0  # never reached — no wasted downstream work

    resumed = _run(runs.retry_generation_run(user_id=started.user_id, run_id=started.id))
    assert resumed.status == runs.STATUS_QUEUED
    assert resumed.canonical_version_id == harness.canonical.id  # pin preserved, not cleared

    second = work()
    assert second.current_stage == runs.STAGE_CANONICAL
    assert second.last_error["code"] == "CANONICAL_QA_REVIEW"
    assert harness.counts["canonical_build"] == 1  # retry never re-derives/re-buys


# 2. REVIEW keyframe stays recoverable, never loops into a repeated build ───


def test_review_keyframe_does_not_loop(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch, keyframe_status=action_keyframe_service.STATUS_REVIEW)

    started = start(key="keyframe-review:no-loop")
    first = work()

    assert first.status == runs.STATUS_FAILED
    assert first.current_stage == runs.STAGE_KEYFRAMES
    assert first.last_error["code"] == "KEYFRAME_QA_REVIEW"
    assert first.last_error["keyframe_role"] == "NEUTRAL_IDLE"
    assert first.keyframes["NEUTRAL_IDLE"]["id"] == harness.keyframe.id
    assert harness.counts["canonical_build"] == 1  # canonical (upstream) still completed once
    assert harness.counts["keyframe_build"] == 1
    assert harness.counts["motion_build"] == 0  # never reached

    resumed = _run(runs.retry_generation_run(user_id=started.user_id, run_id=started.id))
    assert resumed.status == runs.STATUS_QUEUED
    assert resumed.keyframes["NEUTRAL_IDLE"]["id"] == harness.keyframe.id  # pin preserved

    second = work()
    assert second.current_stage == runs.STAGE_KEYFRAMES
    assert second.last_error["code"] == "KEYFRAME_QA_REVIEW"
    assert harness.counts["canonical_build"] == 1  # upstream not re-derived either
    assert harness.counts["keyframe_build"] == 1  # retry never re-derives/re-buys


# 5. Explicit replacement builds exactly one new paid canonical version ────


def test_canonical_replacement_request_is_queued_once_and_api_does_not_generate(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch, canonical_status=canonical_pet_service.STATUS_REVIEW)
    started = start(key="canonical-review:replace")
    failed = work()
    assert failed.last_error["code"] == "CANONICAL_QA_REVIEW"
    assert harness.counts["canonical_build"] == 1

    queued = _run(
        runs.request_canonical_replacement_generation(
            user_id=USER,
            run_id=started.id,
            idempotency_key="canonical-replace:one",
            reason="coat pattern REVIEW persists — requesting a fresh candidate",
        )
    )
    assert queued.status == runs.STATUS_QUEUED
    assert queued.canonical_version_id is None
    request = queued.provider_state["_operator"]["canonical_replacement_request"]
    assert request["source_canonical_version_id"] == harness.canonical.id
    assert request["status"] == "QUEUED"
    assert harness.counts["canonical_build"] == 1  # API only persisted intent, no spend yet

    duplicate = _run(
        runs.request_canonical_replacement_generation(
            user_id=USER,
            run_id=started.id,
            idempotency_key="canonical-replace:one",
            reason="same request",
        )
    )
    assert duplicate.provider_state == queued.provider_state
    assert harness.counts["canonical_build"] == 1

    with pytest.raises(runs.PetGenerationRunError) as second_key:
        _run(
            runs.request_canonical_replacement_generation(
                user_id=USER,
                run_id=started.id,
                idempotency_key="canonical-replace:two",
                reason="must not buy twice",
            )
        )
    assert second_key.value.code == "REPLACEMENT_ALREADY_REQUESTED"


def test_canonical_replacement_worker_builds_exactly_one_new_version_then_reuses_it(monkeypatch):
    source = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000401",
        pet_id=PET,
        user_id=USER,
        version=1,
        status=canonical_pet_service.STATUS_REVIEW,
        reference_set_id="00000000-0000-0000-0000-000000000301",
        reference_set_version=1,
    )
    replacement = SimpleNamespace(
        **{**source.__dict__, "id": "00000000-0000-0000-0000-000000000403", "version": 2}
    )
    build_calls = []

    async def get_canonical(**kwargs):
        version = kwargs.get("version")
        if version is None:
            # An unversioned "latest" lookup genuinely returns the REVIEW
            # source in production — it really is the current latest row.
            return source
        if version == replacement.version:
            return replacement
        if version == source.version:
            return source
        return None

    async def build_canonical(**kwargs):
        build_calls.append(kwargs)
        return replacement

    monkeypatch.setattr(canonical_pet_service, "get_canonical", get_canonical)
    monkeypatch.setattr(canonical_pet_service, "build_canonical", build_canonical)
    monkeypatch.setattr(runs, "_image_providers", lambda *args: [object()])

    run = runs.PetGenerationRun(
        id="00000000-0000-0000-0000-000000000900",
        user_id=USER,
        pet_id=PET,
        content_id=CID,
        motion_id="BREATHING",
        request_kind="FREE_HOME",
        idempotency_key="canonical-replacement-worker",
        status=runs.STATUS_RUNNING,
        current_stage=runs.STAGE_CANONICAL,
        reference_set_id=source.reference_set_id,
        reference_set_version=source.reference_set_version,
        provider_state={
            "_operator": {"canonical_replacement_request": {"source_canonical_version_id": source.id}}
        },
    )

    built, run = _run(runs._canonical(run))
    assert built.id == replacement.id
    assert len(build_calls) == 1  # exactly one new paid candidate submitted
    assert build_calls[0]["skip_if_unchanged"] is False

    # simulate `_execute` pinning the run to the freshly built version
    run = replace(run, canonical_version_id=replacement.id, canonical_version=replacement.version)
    again, run = _run(runs._canonical(run))
    assert again.id == replacement.id
    assert len(build_calls) == 1  # resuming never buys a second candidate


def test_keyframe_replacement_worker_builds_exactly_one_new_version_then_reuses_it(monkeypatch):
    source = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000501",
        pet_id=PET,
        user_id=USER,
        keyframe_role="NEUTRAL_IDLE",
        version=1,
        status=action_keyframe_service.STATUS_REVIEW,
        canonical_version_id="00000000-0000-0000-0000-000000000401",
        canonical_version=1,
    )
    replacement = SimpleNamespace(
        **{**source.__dict__, "id": "00000000-0000-0000-0000-000000000503", "version": 2}
    )
    build_calls = []

    async def get_keyframe(**kwargs):
        version = kwargs.get("version")
        if version is None:
            return source
        if version == replacement.version:
            return replacement
        if version == source.version:
            return source
        return None

    async def build_keyframe(**kwargs):
        build_calls.append(kwargs)
        return replacement

    monkeypatch.setattr(action_keyframe_service, "get_keyframe", get_keyframe)
    monkeypatch.setattr(action_keyframe_service, "build_keyframe", build_keyframe)
    monkeypatch.setattr(runs, "_image_providers", lambda *args: [object()])

    run = runs.PetGenerationRun(
        id="00000000-0000-0000-0000-000000000901",
        user_id=USER,
        pet_id=PET,
        content_id=CID,
        motion_id="BREATHING",
        request_kind="FREE_HOME",
        idempotency_key="keyframe-replacement-worker",
        status=runs.STATUS_RUNNING,
        current_stage=runs.STAGE_KEYFRAMES,
        canonical_version_id=source.canonical_version_id,
        canonical_version=source.canonical_version,
        provider_state={
            "_operator": {
                "keyframe_replacement_requests": {
                    "NEUTRAL_IDLE": {"source_keyframe_id": source.id}
                }
            }
        },
    )

    built, run = _run(runs._keyframe(run, "NEUTRAL_IDLE"))
    assert built.id == replacement.id
    assert len(build_calls) == 1
    assert build_calls[0]["skip_if_unchanged"] is False

    run = replace(run, keyframes={"NEUTRAL_IDLE": {"id": replacement.id, "version": replacement.version}})
    again, run = _run(runs._keyframe(run, "NEUTRAL_IDLE"))
    assert again.id == replacement.id
    assert len(build_calls) == 1


def test_keyframe_replacement_bypasses_canonical_reuse_even_when_requested(monkeypatch):
    """A pending replacement must always win over Canonical reuse.

    Simulates the exact call shape BREATHING's start-keyframe call makes
    (allow_canonical_reuse=True passed in by _execute()), but with an active
    keyframe_replacement_requests["NEUTRAL_IDLE"] entry pinned to the current
    latest version — an operator explicitly asked for a fresh generated
    candidate, so the alias-Canonical shortcut must not fire even though the
    caller asked for it.
    """
    source = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000501",
        pet_id=PET,
        user_id=USER,
        keyframe_role="NEUTRAL_IDLE",
        version=1,
        status=action_keyframe_service.STATUS_REVIEW,
        canonical_version_id="00000000-0000-0000-0000-000000000401",
        canonical_version=1,
    )
    replacement = SimpleNamespace(
        **{**source.__dict__, "id": "00000000-0000-0000-0000-000000000504", "version": 2}
    )
    build_calls = []

    async def get_keyframe(**kwargs):
        version = kwargs.get("version")
        if version is None:
            return source
        if version == replacement.version:
            return replacement
        if version == source.version:
            return source
        return None

    async def build_keyframe(**kwargs):
        build_calls.append(kwargs)
        return replacement

    monkeypatch.setattr(action_keyframe_service, "get_keyframe", get_keyframe)
    monkeypatch.setattr(action_keyframe_service, "build_keyframe", build_keyframe)
    monkeypatch.setattr(runs, "_image_providers", lambda *args: [object()])

    run = runs.PetGenerationRun(
        id="00000000-0000-0000-0000-000000000902",
        user_id=USER,
        pet_id=PET,
        content_id=CID,
        motion_id="BREATHING",
        request_kind="FREE_HOME",
        idempotency_key="keyframe-replacement-bypasses-reuse",
        status=runs.STATUS_RUNNING,
        current_stage=runs.STAGE_KEYFRAMES,
        canonical_version_id=source.canonical_version_id,
        canonical_version=source.canonical_version,
        provider_state={
            "_operator": {
                "keyframe_replacement_requests": {
                    "NEUTRAL_IDLE": {"source_keyframe_id": source.id}
                }
            }
        },
    )

    # Mirrors _execute(): BREATHING's NEUTRAL_IDLE start call always asks
    # for reuse — the replacement pin must still win.
    built, run = _run(runs._keyframe(run, "NEUTRAL_IDLE", allow_canonical_reuse=True))
    assert built.id == replacement.id
    assert len(build_calls) == 1
    assert build_calls[0]["skip_if_unchanged"] is False
    assert build_calls[0]["allow_canonical_reuse"] is False


# 6. PASS canonical/keyframe versions still reuse normally, unaffected ─────


def test_pass_canonical_and_keyframe_still_reuse_across_runs(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)  # defaults: canonical/keyframe/motion all COMPLETE

    start(key="canonical-keyframe-pass:first")
    first = work()
    assert first.status == runs.STATUS_PUBLISHED

    harness.expose_latest = True
    start(key="canonical-keyframe-pass:second")
    second = work()

    assert second.status == runs.STATUS_PUBLISHED
    assert second.canonical_version_id == first.canonical_version_id
    assert second.keyframes == first.keyframes
    assert harness.counts["canonical_build"] == 1
    assert harness.counts["keyframe_build"] == 1


def test_fail_canonical_and_keyframe_remain_terminal_and_not_replacement_eligible(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch, canonical_status=canonical_pet_service.STATUS_FAILED)

    started = start(key="canonical-fail:terminal")
    result = work()

    assert result.status == runs.STATUS_FAILED
    assert result.current_stage == runs.STAGE_CANONICAL
    assert result.last_error["code"] == "CANONICAL_NOT_COMPLETE"  # not CANONICAL_QA_REVIEW

    with pytest.raises(runs.PetGenerationRunError) as error:
        _run(
            runs.request_canonical_replacement_generation(
                user_id=started.user_id,
                run_id=started.id,
                idempotency_key="canonical-fail:replace",
                reason="should be rejected",
            )
        )
    assert error.value.code == "REPLACEMENT_NOT_JUSTIFIED"
