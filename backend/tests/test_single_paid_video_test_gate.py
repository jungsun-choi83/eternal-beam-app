"""TEST-ONLY single paid video submission gate tests.

These tests never contact Supabase or an AI provider.  The provider doubles
only count submit/check/collect calls and the durable job store is in-memory.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
import time

import pytest

from backend.services import durable_provider_jobs as jobs
from backend.services.provider_job_contract import (
    PENDING,
    SUCCEEDED,
    ProviderJobCheck,
    ProviderSubmission,
)
from backend.services.video_motion_providers import MotionVideoRequest, MotionVideoResult


PET = "pet_synthetic_single_video"
RUN = "00000000-0000-0000-0000-000000000901"
MOTION_VERSION = "00000000-0000-0000-0000-000000000902"


class FakeVideoProvider:
    supports_durable_jobs = True
    supports_end_frame = True
    supports_motion_reference = False
    reference_budget = 0
    logical_model_id = "test-video-model"
    vendor_id = "test"
    adapter_id = "FakeVideoProvider"

    def __init__(
        self,
        name: str,
        *,
        statuses=(PENDING, SUCCEEDED),
        delay=0.0,
        submit_status=PENDING,
        check_error=None,
    ):
        self.name = name
        self.logical_model_id = name
        self._statuses = list(statuses)
        self.delay = delay
        self.submit_status = submit_status
        self.check_error = check_error
        self.submit_calls = 0
        self.check_calls = 0
        self.collect_calls = 0
        self._lock = threading.Lock()

    def available(self):
        return True

    def model_name(self):
        return self.name

    def submit(self, request):  # noqa: ARG002
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            self.submit_calls += 1
        return ProviderSubmission(
            f"external-{self.name}", provider_status=self.submit_status
        )

    def check(self, external_job_id):  # noqa: ARG002
        if self.check_error:
            raise self.check_error
        with self._lock:
            self.check_calls += 1
            status = self._statuses.pop(0) if self._statuses else SUCCEEDED
        return ProviderJobCheck(status, status)

    def collect(self, external_job_id):  # noqa: ARG002
        with self._lock:
            self.collect_calls += 1
        return MotionVideoResult(
            video_bytes=b"mock-mp4",
            provider=self.name,
            model=self.model_name(),
            external_job_id=external_job_id,
        )


def _request(attempt=1):
    return MotionVideoRequest(
        prompt="synthetic pet test",
        start_image_url="https://storage.test/start.png",
        start_image_bytes=b"start",
        output_spec={"duration_sec": 4},
        metadata={
            "pet_id": PET,
            "motion_version_id": MOTION_VERSION,
            "attempt": attempt,
        },
    )


def _wrapper(provider, *, run_id=RUN):
    return jobs.DurableVideoProvider(
        provider,
        run_id=run_id,
        user_id="archive_synthetic",
        pet_id=PET,
        provider_operation=jobs.OP_MOTION,
    )


@pytest.fixture(autouse=True)
def _mock_store(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    jobs.__reset_for_tests()
    yield
    jobs.__reset_for_tests()


def _enable_gate(monkeypatch):
    monkeypatch.setenv("PHASE6_SINGLE_PAID_VIDEO_TEST", "1")
    monkeypatch.setenv("PHASE6_SINGLE_PAID_VIDEO_TEST_PET_ID", PET)
    monkeypatch.setenv("BUSINESS_QA_USER_TEST_MODE", "allowlist")
    monkeypatch.setenv("BUSINESS_QA_USER_TEST_PET_IDS", PET)
    monkeypatch.setenv("PHASE6_LIVE_MODE", "allowlist")
    monkeypatch.setenv("PHASE6_LIVE_ALLOWLIST", PET)


def test_gate_fails_closed_without_exact_test_allowlist(monkeypatch):
    _enable_gate(monkeypatch)
    monkeypatch.delenv("PHASE6_LIVE_ALLOWLIST")
    provider = FakeVideoProvider("first")

    with pytest.raises(Exception) as exc_info:
        _wrapper(provider).generate(_request())

    assert exc_info.value.code == "SINGLE_PAID_VIDEO_TEST_CONFIG_INVALID"
    assert provider.submit_calls == 0
    assert jobs.list_for_run(RUN) == []


def test_duplicate_clicks_and_failed_qa_cannot_start_a_second_candidate(monkeypatch):
    _enable_gate(monkeypatch)
    first = FakeVideoProvider("first", statuses=(SUCCEEDED,))
    second = FakeVideoProvider("fallback")
    first_wrapper = _wrapper(first)

    with pytest.raises(jobs.ProviderWorkPending):
        first_wrapper.generate(_request())

    # A second click resumes the same durable operation and only polls it.
    assert _wrapper(first).generate(_request()).video_bytes == b"mock-mp4"

    # A failed-QA fallback would use a new attempt/provider.  The test gate
    # rejects it before the second provider's submit() can run.
    with pytest.raises(jobs.SinglePaidVideoTestGateError):
        _wrapper(second).generate(_request(attempt=2))

    assert first.submit_calls == 1
    assert second.submit_calls == 0


def test_timeout_and_worker_restart_never_resubmit(monkeypatch):
    _enable_gate(monkeypatch)
    provider = FakeVideoProvider(
        "first", statuses=(PENDING,), check_error=RuntimeError("poll timeout")
    )
    with pytest.raises(jobs.ProviderWorkPending):
        _wrapper(provider).generate(_request())

    actual = next(
        row
        for row in jobs.list_for_run(RUN)
        if row["provider"] != "__TEST_SINGLE_PAID_VIDEO_GATE__"
    )
    jobs._update(
        actual["id"],
        {
            "deadline_at": (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
        },
    )

    # The fresh wrapper represents a restarted worker.  The provider poll is
    # attempted once, but the ambiguous outcome never causes a new submit().
    with pytest.raises(jobs.ProviderRecoveryRequired):
        _wrapper(provider).generate(_request())
    with pytest.raises(jobs.ProviderRecoveryRequired):
        _wrapper(provider).generate(_request())

    assert provider.submit_calls == 1


def test_restart_fails_closed_if_test_configuration_disappears(monkeypatch):
    _enable_gate(monkeypatch)
    provider = FakeVideoProvider("first")
    with pytest.raises(jobs.ProviderWorkPending):
        _wrapper(provider).generate(_request())

    monkeypatch.delenv("BUSINESS_QA_USER_TEST_PET_IDS")
    with pytest.raises(Exception) as exc_info:
        _wrapper(provider).generate(_request())

    assert exc_info.value.code == "SINGLE_PAID_VIDEO_TEST_CONFIG_INVALID"
    assert provider.submit_calls == 1


def test_concurrent_workers_share_one_durable_test_slot(monkeypatch):
    _enable_gate(monkeypatch)
    first = FakeVideoProvider("first", delay=0.05)
    second = FakeVideoProvider("second", delay=0.05)

    def call(provider):
        try:
            _wrapper(provider).generate(_request())
        except Exception as exc:  # both outcomes are expected to be non-terminal
            return type(exc)
        return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(call, (first, second)))

    assert sum(provider.submit_calls for provider in (first, second)) == 1
    assert sum(outcome is jobs.ProviderWorkPending for outcome in outcomes) == 1
    assert sum(outcome is jobs.SinglePaidVideoTestGateError for outcome in outcomes) == 1


def test_normal_generation_is_unchanged_when_gate_is_disabled(monkeypatch):
    monkeypatch.delenv("PHASE6_SINGLE_PAID_VIDEO_TEST", raising=False)
    first = FakeVideoProvider(
        "first", statuses=(SUCCEEDED,), submit_status=SUCCEEDED
    )

    result = _wrapper(first).generate(_request())

    assert result.video_bytes == b"mock-mp4"
    assert first.submit_calls == 1
    assert not any(
        row["provider"] == "__TEST_SINGLE_PAID_VIDEO_GATE__"
        for row in jobs.list_for_run(RUN)
    )
