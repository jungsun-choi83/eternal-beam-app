"""Phase 7D async provider receipts, polling, and worker recovery tests."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anyio
import pytest

from backend.services import (
    canonical_image_providers,
    durable_provider_jobs as jobs,
    pet_generation_run_service as runs,
    pet_reference_service,
)
from backend.services.canonical_image_providers import (
    CanonicalImageResult,
    CanonicalProviderError,
    CanonicalReference,
)
from backend.services.provider_job_contract import (
    FAILED,
    PENDING,
    SUCCEEDED,
    ProviderJobCheck,
    ProviderSubmission,
)
from backend.services.video_motion_providers import MotionVideoResult, MotionVideoRequest

from .conftest import make_jpeg_bytes
from .test_phase7c_generation_runs import CID, PET, USER, PipelineHarness, seed_intake


def _run(awaitable):
    return anyio.run(lambda: awaitable)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("GENERATION_PROVIDER_POLL_SECONDS", "0")
    monkeypatch.setenv("SEEDANCE_TRANSPORT", "runway")
    runs.__reset_for_tests()
    pet_reference_service.__reset_for_tests()
    yield
    runs.__reset_for_tests()
    pet_reference_service.__reset_for_tests()


@pytest.fixture
def storage(monkeypatch):
    from backend.services import supabase_assets

    async def upload(path, data, content_type):
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", upload)


class FakeDurableImageProvider:
    name = "runway"
    supports_durable_jobs = True
    max_prompt_chars = 1000

    def __init__(self, statuses=(PENDING, SUCCEEDED), *, submit_error=None):
        self.statuses = list(statuses)
        self.submit_error = submit_error
        self.submit_calls = 0
        self.check_calls = 0
        self.collect_calls = 0

    def available(self):
        return True

    def model_name(self):
        return "gen4_image"

    def submit(self, references, prompt, output_spec, metadata):
        self.submit_calls += 1
        if self.submit_error:
            raise self.submit_error
        return ProviderSubmission("runway-image-job-1")

    def check(self, external_job_id):
        self.check_calls += 1
        status = self.statuses.pop(0) if self.statuses else SUCCEEDED
        return ProviderJobCheck(
            status,
            "RUNNING" if status == PENDING else status,
            error=("provider failed" if status == FAILED else None),
        )

    def collect(self, external_job_id):
        self.collect_calls += 1
        return CanonicalImageResult(
            image_bytes=make_jpeg_bytes(),
            provider=self.name,
            model=self.model_name(),
            external_job_id=external_job_id,
        )


class FakeDurableVideoProvider:
    name = "seedance"
    supports_durable_jobs = True
    supports_end_frame = True
    supports_motion_reference = False
    reference_budget = 0

    def __init__(self):
        self.submit_calls = 0

    def available(self):
        return True

    def model_name(self):
        return "seedance2_5"

    def submit(self, request):
        self.submit_calls += 1
        return ProviderSubmission("runway-video-job-1")

    def check(self, external_job_id):
        return ProviderJobCheck(SUCCEEDED, SUCCEEDED)

    def collect(self, external_job_id):
        return MotionVideoResult(
            video_bytes=b"video",
            provider=self.name,
            model=self.model_name(),
            external_job_id=external_job_id,
        )


def _image_wrapper(provider, run_id="00000000-0000-0000-0000-000000000801"):
    return jobs.DurableImageProvider(
        provider,
        run_id=run_id,
        user_id=USER,
        pet_id=PET,
        provider_operation=jobs.OP_CANONICAL,
    )


def _image_call(wrapper):
    return wrapper.generate(
        [CanonicalReference("ref-1", "PRIMARY", url="https://storage.test/ref.jpg")],
        "pet-only prompt",
        {"ratio": "1024:1024"},
        {
            "pet_id": PET,
            "canonical_version_id": "00000000-0000-0000-0000-000000000802",
            "attempt": 1,
        },
    )


def test_submission_id_is_persisted_and_restart_reuses_it():
    provider = FakeDurableImageProvider()
    wrapper = _image_wrapper(provider)

    with pytest.raises(jobs.ProviderWorkPending):
        _image_call(wrapper)
    operation = jobs._MOCK_JOBS[0]
    assert operation["external_job_id"] == "runway-image-job-1"
    assert operation["submission_status"] == jobs.SUBMITTED
    assert provider.submit_calls == 1

    restarted = _image_wrapper(provider)
    with pytest.raises(jobs.ProviderWorkPending):
        _image_call(restarted)
    result = _image_call(restarted)

    assert result.external_job_id == "runway-image-job-1"
    assert provider.submit_calls == 1
    assert provider.collect_calls == 1
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.COLLECTED


def test_ambiguous_submission_is_never_repeated():
    provider = FakeDurableImageProvider(
        submit_error=CanonicalProviderError("PROVIDER_TRANSPORT", "connection lost")
    )
    wrapper = _image_wrapper(provider)

    with pytest.raises(jobs.ProviderRecoveryRequired):
        _image_call(wrapper)
    with pytest.raises(jobs.ProviderRecoveryRequired):
        _image_call(_image_wrapper(provider))

    assert provider.submit_calls == 1
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.AMBIGUOUS


def test_terminal_provider_failure_is_not_repolled_or_resubmitted():
    provider = FakeDurableImageProvider(statuses=(FAILED,))
    wrapper = _image_wrapper(provider)

    with pytest.raises(jobs.ProviderWorkPending):
        _image_call(wrapper)
    with pytest.raises(CanonicalProviderError):
        _image_call(wrapper)
    with pytest.raises(CanonicalProviderError):
        _image_call(_image_wrapper(provider))

    assert provider.submit_calls == 1
    assert provider.check_calls == 1
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.FAILED


def test_video_contract_uses_same_durable_receipt_model():
    provider = FakeDurableVideoProvider()
    wrapper = jobs.DurableVideoProvider(
        provider,
        run_id="00000000-0000-0000-0000-000000000811",
        user_id=USER,
        pet_id=PET,
        provider_operation=jobs.OP_MOTION,
    )
    request = MotionVideoRequest(
        prompt="breathing",
        start_image_url="https://storage.test/start.png",
        start_image_bytes=b"image",
        metadata={
            "motion_version_id": "00000000-0000-0000-0000-000000000812",
            "start_keyframe_id": "keyframe-1",
            "attempt": 1,
        },
    )

    with pytest.raises(jobs.ProviderWorkPending):
        wrapper.generate(request)
    result = wrapper.generate(request)

    assert result.external_job_id == "runway-video-job-1"
    assert provider.submit_calls == 1


def test_worker_polls_existing_submission_then_finishes_pipeline(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    provider = FakeDurableImageProvider()
    monkeypatch.setattr(canonical_image_providers, "resolve_providers", lambda: [provider])

    async def canonical_build(**kwargs):
        wrapped = kwargs["providers"][0]
        _image_call(wrapped)
        harness.counts["canonical_build"] += 1
        return harness.canonical

    monkeypatch.setattr(runs.canonical_pet_service, "build_canonical", canonical_build)

    queued = _run(
        runs.start_generation_run(
            user_id=USER, pet_id=PET, idempotency_key="async-worker-happy"
        )
    )
    duplicate = _run(
        runs.start_generation_run(
            user_id=USER, pet_id=PET, idempotency_key="async-worker-happy"
        )
    )
    assert queued.status == runs.STATUS_QUEUED
    assert duplicate.id == queued.id
    assert provider.submit_calls == 0

    submitted = _run(runs.process_next_generation_run(worker_id="worker-a"))
    assert submitted.status == runs.STATUS_WAITING_PROVIDER
    operation = next(iter(submitted.provider_state.values()))
    assert operation["external_job_id"] == "runway-image-job-1"
    after_submit_duplicate = _run(
        runs.start_generation_run(
            user_id=USER, pet_id=PET, idempotency_key="async-worker-happy"
        )
    )
    assert after_submit_duplicate.id == queued.id
    assert provider.submit_calls == 1

    pending = _run(runs.process_next_generation_run(worker_id="worker-b"))
    assert pending.status == runs.STATUS_WAITING_PROVIDER
    completed = _run(runs.process_next_generation_run(worker_id="worker-c"))

    assert completed.status == runs.STATUS_PUBLISHED
    assert provider.submit_calls == 1
    assert completed.publication_id == harness.publication.publication_id
    assert harness.counts["publication"] == 1


def test_provider_failure_is_durable_and_never_publishes(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    provider = FakeDurableImageProvider(statuses=(FAILED,))
    monkeypatch.setattr(canonical_image_providers, "resolve_providers", lambda: [provider])

    async def canonical_build(**kwargs):
        _image_call(kwargs["providers"][0])
        return harness.canonical

    monkeypatch.setattr(runs.canonical_pet_service, "build_canonical", canonical_build)
    _run(runs.start_generation_run(user_id=USER, pet_id=PET, idempotency_key="provider-fail"))
    first = _run(runs.process_next_generation_run(worker_id="worker-a"))
    assert first.status == runs.STATUS_WAITING_PROVIDER

    failed = _run(runs.process_next_generation_run(worker_id="worker-b"))
    assert failed.status == runs.STATUS_FAILED
    assert failed.current_stage == runs.STAGE_CANONICAL
    assert failed.publication_id is None
    assert harness.counts["publication"] == 0
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.FAILED


def test_expired_lease_is_reclaimed_by_another_worker(storage, monkeypatch):
    seed_intake()
    PipelineHarness(monkeypatch)
    queued = _run(
        runs.start_generation_run(user_id=USER, pet_id=PET, idempotency_key="stale-lease")
    )
    claimed = _run(runs._claim_next("dead-worker"))
    assert claimed.id == queued.id
    assert _run(runs.process_next_generation_run(worker_id="live-worker")) is None

    row = next(item for item in runs._MOCK_RUNS if item["id"] == queued.id)
    row["lease_expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    completed = _run(runs.process_next_generation_run(worker_id="live-worker"))

    assert completed.status == runs.STATUS_PUBLISHED
    assert completed.worker_id is None
    assert completed.execution_token is None


def test_phase7d_migration_has_provider_receipts_and_fenced_worker_claims():
    root = Path(__file__).resolve().parents[2]
    sql = (
        root / "supabase/migrations/20261019000000_async_generation_provider_jobs.sql"
    ).read_text()

    assert "create table if not exists public.pet_generation_provider_jobs" in sql
    assert "external_job_id text" in sql
    assert "submission_status text" in sql
    assert "unique (run_id, provider_operation, phase_version_id, provider, attempt)" in sql
    assert "for update skip locked" in sql
    assert "execution_token = gen_random_uuid()" in sql
    assert "create or replace function public.heartbeat_pet_generation_run" in sql
    assert "to service_role" in sql


def test_worker_dotenv_cannot_override_injected_environment(monkeypatch):
    from backend.workers import pet_generation_worker as worker

    monkeypatch.setenv("PHASE6_LIVE_MODE", "allowlist")
    monkeypatch.setenv("PET_GENERATION_WORKER_ENABLED", "1")

    def overwrite_like_local_dotenv(path, override=False):  # noqa: ARG001
        os.environ["PHASE6_LIVE_MODE"] = "off"
        os.environ["PET_GENERATION_WORKER_ENABLED"] = "0"
        return True

    monkeypatch.setattr(worker, "load_dotenv", overwrite_like_local_dotenv)
    worker._load_environment()

    assert os.environ["PHASE6_LIVE_MODE"] == "allowlist"
    assert os.environ["PET_GENERATION_WORKER_ENABLED"] == "1"
