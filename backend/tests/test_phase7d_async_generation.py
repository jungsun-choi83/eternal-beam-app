"""Phase 7D async provider receipts, polling, and worker recovery tests."""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

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
    GptImageProvider,
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
    logical_model_id = "seedance"
    vendor_id = "runway"
    adapter_id = "RunwaySeedanceProvider"
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


def _gpt_image_call(wrapper, operation):
    phase_key = "keyframe_id" if operation == jobs.OP_KEYFRAME else "canonical_version_id"
    return wrapper.generate(
        [
            CanonicalReference(
                "ref-1",
                "PRIMARY",
                data=make_jpeg_bytes(),
                mime_type="image/jpeg",
            )
        ],
        "pet-only prompt",
        {"size": "1024x1024"},
        {
            "pet_id": PET,
            phase_key: "00000000-0000-0000-0000-000000000822",
            "attempt": 1,
        },
    )


@pytest.mark.parametrize("operation", [jobs.OP_CANONICAL, jobs.OP_KEYFRAME])
def test_gpt_completed_result_is_persisted_before_collection(
    monkeypatch, operation
):
    """
    GPT Image already ran the paid request and returned the completed image
    inside submit() — the durable wrapper must collect it in this SAME call,
    not yield ProviderWorkPending and wait for a second worker tick/poll.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    image = make_jpeg_bytes()
    calls = {"post": 0}
    submission_status_during_post = {}

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {
                "created": 1720000000,
                "data": [{"b64_json": base64.b64encode(image).decode("ascii")}],
                "usage": {"images": 1},
            }

    def post(*args, **kwargs):
        calls["post"] += 1
        # The provider-job receipt exists before the paid request starts.
        assert len(jobs._MOCK_JOBS) == 1
        submission_status_during_post["value"] = jobs._MOCK_JOBS[0]["submission_status"]
        return Response()

    import httpx

    monkeypatch.setattr(httpx, "post", post)
    provider = GptImageProvider()
    wrapper = jobs.DurableImageProvider(
        provider,
        run_id="00000000-0000-0000-0000-000000000821",
        user_id=USER,
        pet_id=PET,
        provider_operation=operation,
    )

    # Same-tick collection: no ProviderWorkPending, no second call, no
    # WAITING_PROVIDER/poll cycle — the result comes back directly.
    result = _gpt_image_call(wrapper, operation)

    assert submission_status_during_post["value"] == jobs.SUBMITTING
    assert result.image_bytes == image
    assert result.provider == "gpt_image"
    assert result.external_job_id == "1720000000"
    assert calls["post"] == 1, "정확히 한 번의 유료 제출 — 재제출 없음"

    receipt = jobs._MOCK_JOBS[0]
    assert receipt["provider"] == "gpt_image"
    assert receipt["submission_status"] == jobs.COLLECTED
    assert receipt["provider_status"] == SUCCEEDED
    persisted = receipt["result_metadata"]["completed_image_result"]
    assert persisted["image_b64"]

    # A later call against the same receipt (e.g. an operator re-driving the
    # same attempt) must still never resubmit — the durable guard is
    # unchanged, only the first-call latency improved.
    replay = jobs.DurableImageProvider(
        provider,
        run_id="00000000-0000-0000-0000-000000000821",
        user_id=USER,
        pet_id=PET,
        provider_operation=operation,
    )
    result_again = _gpt_image_call(replay, operation)
    assert result_again.image_bytes == image
    assert calls["post"] == 1


def test_gpt_restart_between_succeeded_and_collected_never_resubmits(monkeypatch):
    """
    Same-tick collection still writes SUBMITTED → SUCCEEDED → COLLECTED as
    separate durable steps. A crash between SUCCEEDED and COLLECTED (after the
    paid request already ran) must recover on a fresh wrapper without a
    second paid call — restart-safety must survive the new fallthrough path.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    image = make_jpeg_bytes()
    calls = {"post": 0}

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {
                "created": 1720000555,
                "data": [{"b64_json": base64.b64encode(image).decode("ascii")}],
            }

    def post(*args, **kwargs):
        calls["post"] += 1
        return Response()

    import httpx

    monkeypatch.setattr(httpx, "post", post)
    real_update = jobs._update
    state = {"crashed": False}

    def crash_before_collected(operation_id, fields):
        if fields.get("submission_status") == jobs.COLLECTED and not state["crashed"]:
            state["crashed"] = True
            raise RuntimeError("worker died before writing COLLECTED")
        return real_update(operation_id, fields)

    monkeypatch.setattr(jobs, "_update", crash_before_collected)
    provider = GptImageProvider()
    run_id = "00000000-0000-0000-0000-000000000831"

    def wrapper():
        return jobs.DurableImageProvider(
            provider,
            run_id=run_id,
            user_id=USER,
            pet_id=PET,
            provider_operation=jobs.OP_CANONICAL,
        )

    with pytest.raises(RuntimeError, match="worker died"):
        _gpt_image_call(wrapper(), jobs.OP_CANONICAL)

    receipt = jobs._MOCK_JOBS[0]
    assert receipt["submission_status"] == jobs.SUCCEEDED  # last write before the crash
    assert calls["post"] == 1

    # Fresh wrapper (simulating a worker restart) resumes from the receipt —
    # must reuse the already-persisted result, not call OpenAI again.
    result = _gpt_image_call(wrapper(), jobs.OP_CANONICAL)

    assert calls["post"] == 1
    assert result.image_bytes == image
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.COLLECTED


def test_gpt_ambiguous_submission_is_never_repeated(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls = {"post": 0}

    def post(*args, **kwargs):
        calls["post"] += 1
        raise RuntimeError("connection lost after request write")

    import httpx

    monkeypatch.setattr(httpx, "post", post)
    provider = GptImageProvider()
    wrapper = _image_wrapper(provider)

    with pytest.raises(jobs.ProviderRecoveryRequired):
        _gpt_image_call(wrapper, jobs.OP_CANONICAL)
    with pytest.raises(jobs.ProviderRecoveryRequired):
        _gpt_image_call(_image_wrapper(provider), jobs.OP_CANONICAL)

    assert calls["post"] == 1
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.AMBIGUOUS


def test_gpt_result_receipt_write_failure_is_never_resubmitted(monkeypatch):
    """Paid response arrived, but its durable write failed: leave SUBMITTING."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    image = make_jpeg_bytes()
    calls = {"post": 0, "failed_write": False}

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {
                "created": 1720000001,
                "data": [{"b64_json": base64.b64encode(image).decode("ascii")}],
            }

    def post(*args, **kwargs):
        calls["post"] += 1
        return Response()

    import httpx

    monkeypatch.setattr(httpx, "post", post)
    real_update = jobs._update

    def fail_completed_result_write(operation_id, fields):
        if (
            fields.get("submission_status") == jobs.SUBMITTED
            and not calls["failed_write"]
        ):
            calls["failed_write"] = True
            raise RuntimeError("receipt write unavailable")
        return real_update(operation_id, fields)

    monkeypatch.setattr(jobs, "_update", fail_completed_result_write)
    provider = GptImageProvider()

    with pytest.raises(RuntimeError, match="receipt write unavailable"):
        _gpt_image_call(_image_wrapper(provider), jobs.OP_CANONICAL)
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.SUBMITTING

    with pytest.raises(jobs.ProviderRecoveryRequired):
        _gpt_image_call(_image_wrapper(provider), jobs.OP_CANONICAL)
    assert calls["post"] == 1


def test_worker_keeps_gpt_primary_for_both_image_operations(monkeypatch):
    monkeypatch.setenv("CANONICAL_IMAGE_PROVIDER", "gpt_image")
    monkeypatch.setenv("CANONICAL_IMAGE_FALLBACK_PROVIDER", "runway")
    monkeypatch.setenv("KEYFRAME_IMAGE_PROVIDER", "gpt_image")
    monkeypatch.setenv("KEYFRAME_IMAGE_FALLBACK_PROVIDER", "runway")
    run = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000823",
        user_id=USER,
        pet_id=PET,
    )

    canonical = runs._image_providers(run, jobs.OP_CANONICAL)
    keyframe = runs._image_providers(run, jobs.OP_KEYFRAME)

    assert [provider.name for provider in canonical] == ["gpt_image", "runway"]
    assert [provider.name for provider in keyframe] == ["gpt_image", "runway"]


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


class FailingCheckProvider:
    """A provider whose poll (check) always errors — never resolves."""

    name = "runway"
    supports_durable_jobs = True
    max_prompt_chars = 1000

    def __init__(self):
        self.submit_calls = 0
        self.check_calls = 0

    def available(self):
        return True

    def model_name(self):
        return "gen4_image"

    def submit(self, references, prompt, output_spec, metadata):
        self.submit_calls += 1
        return ProviderSubmission("runway-image-job-poll-timeout")

    def check(self, external_job_id):
        self.check_calls += 1
        raise RuntimeError("provider unreachable")

    def collect(self, external_job_id):
        raise AssertionError("collect must never run without a successful check")


class FailingCollectProvider:
    """A provider whose check always succeeds but collect always errors."""

    name = "runway"
    supports_durable_jobs = True
    max_prompt_chars = 1000

    def __init__(self):
        self.submit_calls = 0
        self.check_calls = 0
        self.collect_calls = 0

    def available(self):
        return True

    def model_name(self):
        return "gen4_image"

    def submit(self, references, prompt, output_spec, metadata):
        self.submit_calls += 1
        return ProviderSubmission("runway-image-job-collect-timeout")

    def check(self, external_job_id):
        self.check_calls += 1
        return ProviderJobCheck(SUCCEEDED, SUCCEEDED)

    def collect(self, external_job_id):
        self.collect_calls += 1
        raise RuntimeError("storage fetch failed")


class ReconcilingProvider:
    """A provider whose external job may still be alive past the receipt's
    deadline. Used to test the final-reconciliation safety fix: a durable
    receipt must never treat an expired deadline as an automatic terminal
    timeout without first asking the provider one last time whether the
    original paid job actually finished, is still running, or is dead."""

    name = "runway"
    supports_durable_jobs = True
    max_prompt_chars = 1000

    def __init__(self, *, reconcile=None, reconcile_error=None):
        self.submit_calls = 0
        self.check_calls = 0
        self.collect_calls = 0
        self._reconcile = reconcile  # (status, provider_status, error)
        self._reconcile_error = reconcile_error

    def available(self):
        return True

    def model_name(self):
        return "gen4_image"

    def submit(self, references, prompt, output_spec, metadata):
        self.submit_calls += 1
        return ProviderSubmission("runway-image-job-1")

    def check(self, external_job_id):
        self.check_calls += 1
        if self._reconcile_error is not None:
            raise self._reconcile_error
        status, provider_status, error = self._reconcile
        return ProviderJobCheck(status, provider_status, error=error)

    def collect(self, external_job_id):
        self.collect_calls += 1
        return CanonicalImageResult(
            image_bytes=make_jpeg_bytes(),
            provider=self.name,
            model=self.model_name(),
            external_job_id=external_job_id,
        )


def _submit_then_expire_deadline(provider):
    """Submit (still PENDING, no poll needed yet), then rewind the receipt's
    deadline into the past so the next call must go through final
    reconciliation instead of an immediate terminal timeout."""
    wrapper = _image_wrapper(provider)
    with pytest.raises(jobs.ProviderWorkPending):
        _image_call(wrapper)  # submit only — provider_status PENDING

    operation = jobs._MOCK_JOBS[0]
    assert operation["deadline_at"] is not None
    operation["deadline_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    return operation


def test_expired_deadline_with_provider_completed_collects_existing_result():
    """expired + COMPLETED -> collect the existing result, do not time out."""
    provider = ReconcilingProvider(reconcile=(SUCCEEDED, "COMPLETED", None))
    _submit_then_expire_deadline(provider)

    result = _image_call(_image_wrapper(provider))

    assert result.external_job_id == "runway-image-job-1"
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.COLLECTED
    assert jobs._MOCK_JOBS[0]["provider_status"] == "COMPLETED"
    assert provider.submit_calls == 1  # never resubmitted
    assert provider.collect_calls == 1


def test_expired_deadline_with_provider_in_queue_stays_recoverable_no_fallback():
    """expired + IN_QUEUE -> no fallback submission; external_job_id preserved."""
    provider = ReconcilingProvider(reconcile=(PENDING, "IN_QUEUE", None))
    operation = _submit_then_expire_deadline(provider)
    external_job_id_before = operation["external_job_id"]

    # Must raise the recoverable/waiting exception, never a terminal provider
    # error a caller could react to by starting a second paid fallback.
    with pytest.raises(jobs.ProviderWorkPending):
        _image_call(_image_wrapper(provider))

    operation = jobs._MOCK_JOBS[0]
    assert operation["submission_status"] == jobs.SUBMITTED  # not TIMED_OUT
    assert operation["provider_status"] == "IN_QUEUE"
    assert operation["external_job_id"] == external_job_id_before
    assert provider.submit_calls == 1  # no fallback / no resubmission


def test_expired_deadline_with_provider_in_progress_stays_recoverable_no_fallback():
    """expired + IN_PROGRESS -> no fallback submission; external_job_id preserved."""
    provider = ReconcilingProvider(reconcile=(PENDING, "IN_PROGRESS", None))
    operation = _submit_then_expire_deadline(provider)
    external_job_id_before = operation["external_job_id"]

    with pytest.raises(jobs.ProviderWorkPending):
        _image_call(_image_wrapper(provider))

    operation = jobs._MOCK_JOBS[0]
    assert operation["submission_status"] == jobs.SUBMITTED  # not TIMED_OUT
    assert operation["provider_status"] == "IN_PROGRESS"
    assert operation["external_job_id"] == external_job_id_before
    assert provider.submit_calls == 1


def test_expired_deadline_with_provider_failed_allows_normal_fallback_policy():
    """expired + FAILED -> normal fallback policy may continue."""
    provider = ReconcilingProvider(reconcile=(FAILED, "FAILED", "fal reported the job failed"))
    _submit_then_expire_deadline(provider)

    # A definitive FAILED verdict must raise the SAME exception/code as any
    # other provider failure, so the caller's existing fallback policy runs
    # exactly like it would for a non-timeout failure.
    with pytest.raises(CanonicalProviderError) as exc:
        _image_call(_image_wrapper(provider))

    assert exc.value.code == "PROVIDER_FAILED"
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.FAILED  # not TIMED_OUT
    assert provider.submit_calls == 1

    # Terminal like FAILED: never re-polled, never resubmitted. The early
    # FAILED guard in _execute short-circuits before another check() call.
    with pytest.raises(CanonicalProviderError):
        _image_call(_image_wrapper(provider))
    assert provider.check_calls == 1
    assert provider.submit_calls == 1


def test_expired_deadline_with_unreachable_provider_enters_recovery_required():
    """expired + status cannot be determined -> RECOVERY_REQUIRED."""
    provider = ReconcilingProvider(reconcile_error=RuntimeError("provider unreachable"))
    operation = _submit_then_expire_deadline(provider)
    external_job_id_before = operation["external_job_id"]

    with pytest.raises(jobs.ProviderRecoveryRequired):
        _image_call(_image_wrapper(provider))

    operation = jobs._MOCK_JOBS[0]
    assert operation["submission_status"] == jobs.AMBIGUOUS
    assert operation["external_job_id"] == external_job_id_before
    assert provider.submit_calls == 1

    # The ambiguous guard applies on every later attempt too — never resolved
    # automatically, never resubmitted, and the early guard short-circuits
    # before another check() call is ever made.
    with pytest.raises(jobs.ProviderRecoveryRequired):
        _image_call(_image_wrapper(provider))
    assert provider.submit_calls == 1
    assert provider.check_calls == 1


def test_no_duplicate_paid_submission_across_all_uncertain_post_deadline_states():
    """Across every non-definitive post-deadline outcome, submit() fires once."""
    scenarios = [
        ("IN_QUEUE", ReconcilingProvider(reconcile=(PENDING, "IN_QUEUE", None)), jobs.ProviderWorkPending),
        ("IN_PROGRESS", ReconcilingProvider(reconcile=(PENDING, "IN_PROGRESS", None)), jobs.ProviderWorkPending),
        (
            "UNREACHABLE",
            ReconcilingProvider(reconcile_error=RuntimeError("provider unreachable")),
            jobs.ProviderRecoveryRequired,
        ),
    ]
    for index, (label, provider, expected_exc) in enumerate(scenarios):
        run_id = f"00000000-0000-0000-0000-0000009800{index:02d}"
        wrapper = _image_wrapper(provider, run_id=run_id)

        with pytest.raises(jobs.ProviderWorkPending):
            _image_call(wrapper)  # submit only — provider_status PENDING

        operation = next(row for row in jobs._MOCK_JOBS if row["run_id"] == run_id)
        operation["deadline_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()

        for _ in range(3):  # repeated worker ticks past the deadline
            with pytest.raises(expected_exc):
                _image_call(_image_wrapper(provider, run_id=run_id))

        assert provider.submit_calls == 1, f"{label}: paid job must never be submitted twice"


def test_repeated_poll_errors_become_terminal_timeout(monkeypatch):
    monkeypatch.setenv("PROVIDER_JOB_MAX_CONSECUTIVE_ERRORS", "3")
    provider = FailingCheckProvider()

    with pytest.raises(jobs.ProviderWorkPending):
        _image_call(_image_wrapper(provider))  # submit
    for _ in range(2):
        with pytest.raises(jobs.ProviderWorkPending):
            _image_call(_image_wrapper(provider))  # poll errors 1, 2 — still retryable

    with pytest.raises(CanonicalProviderError) as exc:
        _image_call(_image_wrapper(provider))  # poll error 3 hits the bound — terminal

    assert provider.check_calls == 3
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.TIMED_OUT
    assert jobs._MOCK_JOBS[0]["consecutive_poll_errors"] == 3
    assert "failed 3 times in a row" in str(exc.value)

    with pytest.raises(CanonicalProviderError):
        _image_call(_image_wrapper(provider))
    assert provider.check_calls == 3  # never polled again once terminal
    assert provider.submit_calls == 1  # never resubmitted


def test_repeated_collect_errors_become_terminal_timeout(monkeypatch):
    monkeypatch.setenv("PROVIDER_JOB_MAX_CONSECUTIVE_ERRORS", "3")
    provider = FailingCollectProvider()

    with pytest.raises(jobs.ProviderWorkPending):
        _image_call(_image_wrapper(provider))  # submit
    for _ in range(2):
        with pytest.raises(jobs.ProviderWorkPending):
            _image_call(_image_wrapper(provider))  # collect errors 1, 2 — still retryable

    with pytest.raises(CanonicalProviderError) as exc:
        _image_call(_image_wrapper(provider))  # collect error 3 hits the bound — terminal

    assert provider.collect_calls == 3
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.TIMED_OUT
    assert jobs._MOCK_JOBS[0]["consecutive_collect_errors"] == 3
    assert "failed 3 times in a row" in str(exc.value)

    with pytest.raises(CanonicalProviderError):
        _image_call(_image_wrapper(provider))
    assert provider.collect_calls == 3  # never collected again once terminal
    assert provider.submit_calls == 1  # never resubmitted


def test_ambiguous_submission_is_never_auto_resubmitted_after_deadline_logic_added():
    """Timeout handling must not weaken the pre-existing ambiguous-submission guard."""
    provider = FakeDurableImageProvider(
        submit_error=CanonicalProviderError("PROVIDER_TRANSPORT", "connection lost")
    )

    with pytest.raises(jobs.ProviderRecoveryRequired):
        _image_call(_image_wrapper(provider))
    with pytest.raises(jobs.ProviderRecoveryRequired):
        _image_call(_image_wrapper(provider))

    assert provider.submit_calls == 1
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.AMBIGUOUS


def test_normal_provider_completion_still_succeeds_within_deadline():
    provider = FakeDurableImageProvider()

    with pytest.raises(jobs.ProviderWorkPending):
        _image_call(_image_wrapper(provider))

    operation = jobs._MOCK_JOBS[0]
    assert operation["deadline_at"] is not None
    assert operation["submission_status"] == jobs.SUBMITTED

    with pytest.raises(jobs.ProviderWorkPending):
        _image_call(_image_wrapper(provider))  # still PENDING, well within the deadline

    result = _image_call(_image_wrapper(provider))

    assert result.external_job_id == "runway-image-job-1"
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.COLLECTED
    assert jobs._MOCK_JOBS[0]["consecutive_poll_errors"] == 0
    assert jobs._MOCK_JOBS[0]["consecutive_collect_errors"] == 0


def test_restart_resume_respects_persisted_deadline():
    provider = FakeDurableImageProvider(statuses=(PENDING, SUCCEEDED))

    with pytest.raises(jobs.ProviderWorkPending):
        _image_call(_image_wrapper(provider))  # submit — deadline_at fixed now

    operation = jobs._MOCK_JOBS[0]
    original_deadline = operation["deadline_at"]

    # Simulate a worker restart: a brand-new wrapper instance with no
    # in-memory state, picking the persisted receipt back up.
    with pytest.raises(jobs.ProviderWorkPending):
        _image_call(_image_wrapper(provider))  # poll — still PENDING

    assert jobs._MOCK_JOBS[0]["deadline_at"] == original_deadline  # never reset by resume

    # Time passes past the persisted deadline before the next resume. The
    # provider's external job actually finished (SUCCEEDED) in the meantime —
    # the final reconciliation must collect it, not discard it as a timeout.
    jobs._MOCK_JOBS[0]["deadline_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()

    result = _image_call(_image_wrapper(provider))

    assert result.external_job_id == "runway-image-job-1"
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.COLLECTED
    assert provider.submit_calls == 1  # never resubmitted despite the restart


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
    state = next(
        iter(
            jobs.summary_for_run(
                "00000000-0000-0000-0000-000000000811"
            ).values()
        )
    )
    assert state["logical_model"] == "seedance"
    assert state["vendor"] == "runway"
    assert state["adapter"] == "RunwaySeedanceProvider"
    assert state["vendor_model"] == "seedance2_5"


def test_worker_polls_existing_submission_then_finishes_pipeline(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    provider = FakeDurableImageProvider()
    monkeypatch.setattr(canonical_image_providers, "resolve_providers", lambda: [provider])
    monkeypatch.setattr(canonical_image_providers, "resolve_keyframe_providers", lambda: [provider])

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


def test_canonical_wait_resume_skips_identity_and_reference_stages(storage, monkeypatch):
    """
    확인된 지연 병목의 회귀 가드: WAITING_PROVIDER 로 CANONICAL 에서 멈춘 뒤
    깨어나면 _execute() 는 IDENTITY 부터 다시 돌지 않고 CANONICAL 에서 재개
    한다 — 이미 핀된 Identity/Reference Set 은 다시 읽지도 검증하지도 않는다.
    """
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    provider = FakeDurableImageProvider()
    monkeypatch.setattr(canonical_image_providers, "resolve_providers", lambda: [provider])
    monkeypatch.setattr(canonical_image_providers, "resolve_keyframe_providers", lambda: [provider])

    async def canonical_build(**kwargs):
        harness.counts["canonical_build"] += 1
        wrapped = kwargs["providers"][0]
        _image_call(wrapped)
        return harness.canonical

    monkeypatch.setattr(runs.canonical_pet_service, "build_canonical", canonical_build)

    _run(
        runs.start_generation_run(
            user_id=USER, pet_id=PET, idempotency_key="canonical-resume-skip"
        )
    )

    submitted = _run(runs.process_next_generation_run(worker_id="worker-a"))
    assert submitted.status == runs.STATUS_WAITING_PROVIDER
    assert submitted.current_stage == runs.STAGE_CANONICAL
    # 첫 틱은 IDENTITY/REFERENCE_SET/CANONICAL 을 정상적으로 한 번씩 돈다.
    assert harness.calls.count("identity") == 1
    assert harness.calls.count("reference_set") == 1
    assert harness.counts["canonical_build"] == 1
    calls_after_submit = list(harness.calls)

    # 재개(poll) — CANONICAL provider 는 아직 PENDING, 다시 대기한다.
    polled = _run(runs.process_next_generation_run(worker_id="worker-b"))
    assert polled.status == runs.STATUS_WAITING_PROVIDER
    assert polled.current_stage == runs.STAGE_CANONICAL

    new_calls = harness.calls[len(calls_after_submit):]
    # 재개 틱에서 IDENTITY/REFERENCE_SET 은 한 번도 다시 불리지 않는다 —
    # 읽기(get)든 빌드든.
    assert "identity" not in new_calls and "identity_get" not in new_calls
    assert "reference_set" not in new_calls and "reference_get" not in new_calls
    # 멈춘 단계(CANONICAL) 자체는 재개 시 다시 시도된다 — provider 재제출은
    # 아니다(provider.submit_calls 로 아래에서 확인).
    assert harness.counts["canonical_build"] == 2
    assert provider.submit_calls == 1

    completed = _run(runs.process_next_generation_run(worker_id="worker-c"))
    assert completed.status == runs.STATUS_PUBLISHED
    assert provider.submit_calls == 1
    assert harness.counts["publication"] == 1
    final_new_calls = harness.calls[len(calls_after_submit):]
    assert "identity" not in final_new_calls and "identity_get" not in final_new_calls
    assert "reference_set" not in final_new_calls and "reference_get" not in final_new_calls


def test_provider_failure_is_durable_and_never_publishes(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    provider = FakeDurableImageProvider(statuses=(FAILED,))
    monkeypatch.setattr(canonical_image_providers, "resolve_providers", lambda: [provider])
    monkeypatch.setattr(canonical_image_providers, "resolve_keyframe_providers", lambda: [provider])

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


def test_waiting_run_is_reclaimed_only_when_poll_is_due(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    provider = FakeDurableImageProvider()
    monkeypatch.setattr(canonical_image_providers, "resolve_providers", lambda: [provider])
    monkeypatch.setattr(canonical_image_providers, "resolve_keyframe_providers", lambda: [provider])

    async def canonical_build(**kwargs):
        _image_call(kwargs["providers"][0])
        return harness.canonical

    monkeypatch.setattr(runs.canonical_pet_service, "build_canonical", canonical_build)
    _run(runs.start_generation_run(user_id=USER, pet_id=PET, idempotency_key="poll-due"))
    submitted = _run(runs.process_next_generation_run(worker_id="worker-a"))
    assert submitted.status == runs.STATUS_WAITING_PROVIDER

    row = next(item for item in runs._MOCK_RUNS if item["id"] == submitted.id)
    row["next_attempt_at"] = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()

    not_due = _run(runs.process_next_generation_run(worker_id="worker-b"))
    assert not_due is None
    assert provider.check_calls == 0

    row["next_attempt_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    reclaimed = _run(runs.process_next_generation_run(worker_id="worker-b"))
    assert reclaimed.status == runs.STATUS_WAITING_PROVIDER
    assert provider.check_calls == 1


def test_no_duplicate_polling_of_the_same_waiting_run(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    provider = FakeDurableImageProvider()
    monkeypatch.setattr(canonical_image_providers, "resolve_providers", lambda: [provider])
    monkeypatch.setattr(canonical_image_providers, "resolve_keyframe_providers", lambda: [provider])

    async def canonical_build(**kwargs):
        _image_call(kwargs["providers"][0])
        return harness.canonical

    monkeypatch.setattr(runs.canonical_pet_service, "build_canonical", canonical_build)
    _run(runs.start_generation_run(user_id=USER, pet_id=PET, idempotency_key="no-dup-poll"))
    submitted = _run(runs.process_next_generation_run(worker_id="worker-a"))
    assert submitted.status == runs.STATUS_WAITING_PROVIDER

    row = next(item for item in runs._MOCK_RUNS if item["id"] == submitted.id)
    row["next_attempt_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()

    # Two workers racing to reclaim the same due run: only one may win the
    # lease, so the same provider job is never polled concurrently.
    claimed_a = _run(runs._claim_next("worker-x"))
    claimed_b = _run(runs._claim_next("worker-y"))

    assert claimed_a is not None and claimed_a.id == submitted.id
    assert claimed_b is None
    assert provider.check_calls == 0


def test_no_duplicate_paid_submission_across_rapid_ticks(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    provider = FakeDurableImageProvider()
    monkeypatch.setattr(canonical_image_providers, "resolve_providers", lambda: [provider])
    monkeypatch.setattr(canonical_image_providers, "resolve_keyframe_providers", lambda: [provider])

    async def canonical_build(**kwargs):
        _image_call(kwargs["providers"][0])
        return harness.canonical

    monkeypatch.setattr(runs.canonical_pet_service, "build_canonical", canonical_build)
    _run(runs.start_generation_run(user_id=USER, pet_id=PET, idempotency_key="no-dup-submit"))

    # Drive rapid back-to-back ticks, mirroring the worker loop's immediate
    # re-claim with no sleep in between, through to completion.
    last = None
    for worker_id in ("worker-a", "worker-b", "worker-c", "worker-d"):
        result = _run(runs.process_next_generation_run(worker_id=worker_id))
        if result is None:
            break
        last = result

    assert last is not None
    assert last.status == runs.STATUS_PUBLISHED
    assert provider.submit_calls == 1


def test_active_lease_is_not_reclaimed_by_a_rapid_next_tick(storage, monkeypatch):
    seed_intake()
    PipelineHarness(monkeypatch)
    queued = _run(
        runs.start_generation_run(user_id=USER, pet_id=PET, idempotency_key="active-lease-safe")
    )
    claimed = _run(runs._claim_next("worker-a"))
    assert claimed.id == queued.id
    assert claimed.lease_expires_at is not None

    # Simulate the loop immediately trying to claim more work on the very
    # next tick (no sleep in between): a still-leased run must not be handed
    # to another worker.
    again = _run(runs._claim_next("worker-b"))
    assert again is None


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
