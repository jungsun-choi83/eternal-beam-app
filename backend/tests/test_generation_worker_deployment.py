"""Production deployment/liveness tests for the pet generation worker.

pet_generation_runs could get stuck in WAITING_PROVIDER forever because the
Render blueprint only ever declared the web (FastAPI) service — nothing
guaranteed a persistent process actually ran
backend/workers/pet_generation_worker.py. These tests pin down the fix:

1. render.yaml declares the worker as its own restart-on-crash service,
   independent of the web service.
2. The worker process has no import-time dependency on the FastAPI app, so
   it can start/stop without the web process and vice versa.
3. Shutting the worker down (SIGTERM/SIGINT) stops it from claiming new work
   without touching the DB from the signal handler itself, so an in-flight
   lease is never corrupted by shutdown.
"""

from __future__ import annotations

import ast
import signal
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
import yaml

from backend.workers import pet_generation_worker as worker

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(awaitable):
    return anyio.run(lambda: awaitable)


def _fake_run(status: str, run_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=run_id, current_stage="MOTION_GENERATION", status=status)


@pytest.fixture(autouse=True)
def _reset_stop_flag():
    worker._STOP = False
    yield
    worker._STOP = False


def _render_config() -> dict:
    return yaml.safe_load((REPO_ROOT / "render.yaml").read_text())


def _services_by_name(config: dict) -> dict:
    return {svc["name"]: svc for svc in config["services"]}


def _env(service: dict) -> dict:
    return {var["key"]: var for var in service.get("envVars", [])}


# ---------------------------------------------------------------------------
# 1. Independent, restart-on-crash worker service in the deployment config.
# ---------------------------------------------------------------------------


def test_render_yaml_declares_a_persistent_generation_worker_service():
    services = _services_by_name(_render_config())
    worker_svc = services["eternal-beam-generation-worker"]

    # `type: worker` (not `web`/`cron`) is what makes Render restart the
    # process automatically after a crash or a deploy, independent of the
    # web service's lifecycle.
    assert worker_svc["type"] == "worker"
    assert worker_svc["autoDeploy"] is True
    assert "backend.workers.pet_generation_worker" in worker_svc["dockerCommand"]

    # `exec` replaces the shell with the python process (PID 1) so Render's
    # SIGTERM reaches the worker directly instead of being swallowed by an
    # un-trapping `sh -c`.
    assert "exec python -m backend.workers.pet_generation_worker" in worker_svc["dockerCommand"]


def test_worker_service_is_explicitly_enabled_and_web_service_is_not():
    services = _services_by_name(_render_config())
    worker_env = _env(services["eternal-beam-generation-worker"])
    web_env = _env(services["eternal-beam-video-api"])

    assert worker_env["PET_GENERATION_WORKER_ENABLED"]["value"] == "1"
    # The web service must never carry this flag: pet_generation_worker.main()
    # is only ever invoked as its own process (see run command above), but an
    # accidental copy of this flag onto the web service would signal that
    # someone intended a second, undeployed polling loop to run there.
    assert "PET_GENERATION_WORKER_ENABLED" not in web_env


def test_worker_and_web_share_the_same_generation_pipeline_config():
    services = _services_by_name(_render_config())
    worker_env = _env(services["eternal-beam-generation-worker"])
    web_env = _env(services["eternal-beam-video-api"])

    # Values (not just key presence) must match: a drift here means the
    # worker could process a run the web API queued under a different
    # provider/mock mode than the one it was queued under.
    shared_keys = (
        "HYBRID_USE_SUPABASE",
        "GENERATION_MOCK",
        "LUMA_MOCK",
        "MOCK_LUMA_USE_KEYFRAME",
        "MOCK_LUMA_VIDEO_URL",
        "SUPABASE_STORAGE_BUCKET",
    )
    for key in shared_keys:
        assert key in worker_env, f"worker service is missing shared config {key}"
        assert worker_env[key]["value"] == web_env[key]["value"], key

    # Secrets are dashboard-managed per service (Render does not fan a
    # sync:false value out across services), but both services must at least
    # declare the same keys so they get set for both.
    for key in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "LUMA_API_KEY"):
        assert worker_env[key].get("sync") is False
        assert web_env[key].get("sync") is False


def test_web_service_command_never_runs_the_generation_worker():
    services = _services_by_name(_render_config())
    web_svc = services["eternal-beam-video-api"]
    # The web service has no dockerCommand override -> it runs the
    # Dockerfile's default CMD (uvicorn). Assert that default never mentions
    # the worker module, and that if a future edit adds an explicit
    # dockerCommand to the web service, it still can't be the worker.
    assert "pet_generation_worker" not in web_svc.get("dockerCommand", "")
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    assert "pet_generation_worker" not in dockerfile


# ---------------------------------------------------------------------------
# 2. The worker process is independent of the FastAPI app/process.
# ---------------------------------------------------------------------------


def test_worker_module_has_no_import_time_dependency_on_the_web_app():
    """Statically verify the worker never imports backend.main or fastapi at
    module scope, so it can be started, restarted, or crash without the web
    process (and vice versa) — a real subprocess-boundary check, not just a
    style rule, since a shared import would make the two processes share
    startup failures.
    """
    source = (REPO_ROOT / "backend/workers/pet_generation_worker.py").read_text()
    tree = ast.parse(source)
    module_level_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_level_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_level_names.add(node.module)

    assert not any("fastapi" in name for name in module_level_names)
    assert not any(name.endswith("main") for name in module_level_names)


def test_run_once_only_touches_the_generation_run_service():
    """run_once's import is deferred (inside the function) and points only at
    pet_generation_run_service — never backend.main — so importing/running
    the worker module never boots the FastAPI app as a side effect.
    """
    source = (REPO_ROOT / "backend/workers/pet_generation_worker.py").read_text()
    tree = ast.parse(source)
    run_once = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_once"
    )
    imports = [n for n in ast.walk(run_once) if isinstance(n, ast.ImportFrom)]
    assert len(imports) == 1
    assert imports[0].level == 2  # `from ..services import ...`
    assert imports[0].module == "services"
    assert [a.name for a in imports[0].names] == ["pet_generation_run_service"]


# ---------------------------------------------------------------------------
# 3. Graceful shutdown never corrupts an in-flight lease.
# ---------------------------------------------------------------------------


def test_sigterm_and_sigint_are_wired_to_the_pure_stop_handler(monkeypatch):
    registered = {}

    def fake_signal(sig, handler):
        registered[sig] = handler

    monkeypatch.setattr(worker.signal, "signal", fake_signal)
    monkeypatch.setattr(worker, "_load_environment", lambda: None)
    # Avoid actually driving the infinite poll loop: close the coroutine
    # asyncio.run() would otherwise run to completion (or forever).
    monkeypatch.setattr(worker.asyncio, "run", lambda coro: coro.close())

    worker.main()

    assert registered[signal.SIGINT] is worker._stop
    assert registered[signal.SIGTERM] is worker._stop


def test_stop_handler_body_only_sets_the_flag_and_logs():
    """Statically verify `_stop` performs no calls other than logging: it
    must be safe to invoke at any time (including re-entrantly, or while the
    main loop holds a lease) because it can never itself reach the DB or the
    provider clients — proving shutdown cannot race or corrupt a lease write.
    """
    source = (REPO_ROOT / "backend/workers/pet_generation_worker.py").read_text()
    tree = ast.parse(source)
    stop_fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_stop"
    )
    calls = [
        n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None)
        for n in ast.walk(stop_fn)
        if isinstance(n, ast.Call)
    ]
    assert calls == ["info"], f"unexpected call(s) in _stop: {calls}"


def test_stop_handler_flips_the_flag(monkeypatch):
    monkeypatch.setattr(worker, "_STOP", False)
    worker._stop(signal.SIGTERM, None)
    assert worker._STOP is True
    worker._STOP = False
    worker._stop(signal.SIGINT, None)
    assert worker._STOP is True


def test_shutdown_mid_loop_stops_claiming_new_work_after_the_current_tick(monkeypatch):
    """Simulates SIGTERM arriving while the worker is idle-sleeping: the loop
    must not perform another claim afterwards, leaving whatever it already
    holds to be released/expired by the existing DB lease machinery rather
    than by ad-hoc shutdown code.
    """
    monkeypatch.setenv("PET_GENERATION_WORKER_ENABLED", "1")
    monkeypatch.setenv("PET_GENERATION_WORKER_ID", "shutdown-test-worker")
    monkeypatch.setenv("PET_GENERATION_WORKER_POLL_SEC", "5")

    ticks = []

    async def fake_run_once(worker_id):
        ticks.append(worker_id)
        return None

    async def fake_sleep(seconds):
        # Simulate the operator/Render sending SIGTERM while the worker is
        # parked in its idle backoff sleep.
        worker._stop(signal.SIGTERM, None)

    monkeypatch.setattr(worker, "run_once", fake_run_once)
    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)

    _run(worker._run())

    # Exactly one claim attempt: the loop must exit right after the sleep
    # observes _STOP, never looping back in for a second claim.
    assert ticks == ["shutdown-test-worker"]
