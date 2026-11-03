from __future__ import annotations

import signal
import threading
from pathlib import Path

import yaml

from backend.workers import generation_worker_supervisor as supervisor
from backend.workers import archive_intake_preparation_worker as archive_worker


class FakeProcess:
    _next_pid = 1000

    def __init__(self, *, exit_code=None):
        self.pid = FakeProcess._next_pid
        FakeProcess._next_pid += 1
        self.exit_code = exit_code
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminated = True
        if self.exit_code is None:
            self.exit_code = -signal.SIGTERM

    def kill(self):
        self.killed = True
        self.exit_code = -signal.SIGKILL

    def wait(self, timeout=None):
        self.waited = True
        if self.exit_code is None:
            raise supervisor.subprocess.TimeoutExpired("fake-worker", timeout)
        return self.exit_code


def test_supervisor_starts_both_expected_worker_modules_and_shuts_down(monkeypatch):
    shutdown = threading.Event()
    commands = []
    processes = []

    def spawn(command):
        commands.append(command)
        process = FakeProcess()
        processes.append(process)
        if len(processes) == len(supervisor.WORKER_MODULES):
            shutdown.set()
        return process

    monkeypatch.setattr(supervisor.sys, "executable", "python-test")

    result = supervisor.run_supervisor(
        shutdown_event=shutdown,
        popen_factory=spawn,
        sleep=lambda _seconds: None,
    )

    assert commands == [
        ["python-test", "-m", "backend.workers.archive_intake_preparation_worker"],
        ["python-test", "-m", "backend.workers.pet_generation_worker"],
    ]
    assert result == 0
    assert all(process.terminated and process.waited for process in processes)


def test_unexpected_worker_failure_stops_sibling_and_returns_failure():
    shutdown = threading.Event()
    processes = []

    def spawn(_command):
        process = FakeProcess(exit_code=7 if not processes else None)
        processes.append(process)
        return process

    result = supervisor.run_supervisor(
        shutdown_event=shutdown,
        popen_factory=spawn,
        sleep=lambda _seconds: None,
    )

    assert result == 1
    assert len(processes) == 2
    assert processes[1].terminated and processes[1].waited


def test_even_clean_unexpected_worker_exit_fails_service():
    processes = []

    def spawn(_command):
        process = FakeProcess(exit_code=0 if not processes else None)
        processes.append(process)
        return process

    result = supervisor.run_supervisor(
        shutdown_event=threading.Event(),
        popen_factory=spawn,
        sleep=lambda _seconds: None,
    )

    assert result == 1
    assert processes[1].terminated


def test_sigterm_or_sigint_requests_graceful_child_shutdown(monkeypatch):
    captured_handlers = {}
    shutdown_events = []

    def fake_signal(signum, handler):
        captured_handlers[signum] = handler
        return None

    def fake_run(*, shutdown_event):
        shutdown_events.append(shutdown_event)
        captured_handlers[signal.SIGTERM](signal.SIGTERM, None)
        assert shutdown_event.is_set()
        captured_handlers[signal.SIGINT](signal.SIGINT, None)
        return 0

    monkeypatch.setattr(supervisor.signal, "signal", fake_signal)
    monkeypatch.setattr(supervisor, "run_supervisor", fake_run)

    assert supervisor.main() == 0
    assert set(captured_handlers) == {signal.SIGTERM, signal.SIGINT}
    assert len(shutdown_events) == 1


def test_archive_preparation_worker_signal_stops_after_current_intake(monkeypatch):
    monkeypatch.setattr(archive_worker, "_STOP", False)

    archive_worker._stop(signal.SIGTERM, None)

    assert archive_worker._STOP is True


def test_render_generation_worker_uses_supervisor_and_enables_both_workers():
    root = Path(__file__).resolve().parents[2]
    blueprint = yaml.safe_load((root / "render.yaml").read_text(encoding="utf-8"))
    services = {service["name"].strip(): service for service in blueprint["services"]}
    assert set(services) == {
        "eternal-beam-video-api",
        "eternal-beam-generation-worker",
        "eternal-beam-exhibition-worker",
    }
    worker = services["eternal-beam-generation-worker"]
    exhibition = services["eternal-beam-exhibition-worker"]
    env = {entry["key"]: entry for entry in worker["envVars"]}

    assert worker["dockerCommand"] == "python -m backend.workers.generation_worker_supervisor"
    assert env["ARCHIVE_PREPARATION_WORKER_ENABLED"]["value"] == "1"
    assert env["PET_GENERATION_WORKER_ENABLED"]["value"] == "1"
    assert env["SUPABASE_URL"]["sync"] is False
    assert env["SUPABASE_SERVICE_ROLE_KEY"]["sync"] is False
    assert exhibition["dockerCommand"] == "python -m backend.workers.exhibition_prep_worker"


def test_second_worker_start_failure_stops_first_and_returns_failure():
    processes = []

    def spawn(_command):
        if processes:
            raise OSError("cannot start second worker")
        process = FakeProcess()
        processes.append(process)
        return process

    result = supervisor.run_supervisor(
        shutdown_event=threading.Event(),
        popen_factory=spawn,
        sleep=lambda _seconds: None,
    )

    assert result == 1
    assert processes[0].terminated and processes[0].waited
