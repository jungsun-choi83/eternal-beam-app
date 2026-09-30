"""
CANONICAL 계보 회귀 테스트 (run 377b6c62, 2026-09-29).

사고: build_canonical() 이 실행이 고정한 레퍼런스 세트를 쓰지 않고 세트를 다시
해석했다. 틱 사이에 PET_VLM_IDENTITY_ENABLED 가 달라진 워커가 같은 실행을
이어받자 분석기 스탬프가 달라져 신원 v3 / 세트 v2 / 정본 v2 가 새로 만들어졌고,
실행은 그 정본을 핀한 뒤에야 계보 검사에서 죽었다 (RUN_LINEAGE_INVALID).

프로바이더는 전부 가짜다 — 실 결제 호출은 없다.
"""

from __future__ import annotations

import anyio
import pytest

from backend.services import canonical_pet_service as svc
from backend.services import durable_provider_jobs as jobs
from backend.services import pet_generation_run_service as runs
from backend.services import pet_identity_service as ids
from backend.services import pet_morphology_service as morph
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry, vlm_identity
from backend.services.canonical_image_providers import CanonicalImageResult
from backend.services.provider_job_contract import (
    PENDING,
    SUCCEEDED,
    ProviderJobCheck,
    ProviderSubmission,
)

from . import test_phase7c_generation_runs as p7c
from .test_canonical_pet_builder import VLM_QA_OK, FakeProvider, install_vlm_qa
from .test_pet_identity_profile import make_pet_cutout_png
from .test_pet_reference_sets import PET, USER, Harness

RUN_ID = "00000000-0000-0000-0000-0000000377b6"


def _run(coro):
    return anyio.run(lambda: coro)


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("SEEDANCE_TRANSPORT", "runway")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("CANONICAL_QA_MIN_RESOLUTION", "100")
    modules = (refs, pet_registry, ids, morph, sets, svc, runs)
    for m in modules:
        m.__reset_for_tests()
    jobs.__reset_for_tests()
    yield
    for m in modules:
        m.__reset_for_tests()
    jobs.__reset_for_tests()


@pytest.fixture
def uploads(monkeypatch) -> list[str]:
    from backend.services import supabase_assets

    paths: list[str] = []

    async def fake_upload(path, data, content_type):
        paths.append(path)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    return paths


# ══════════════════════════════════════════════════════════════════════════
# 빌더: 고정된 세트 + env 변화
# ══════════════════════════════════════════════════════════════════════════


def _seed_vlm_off_refset():
    """VLM 이 꺼진 워커가 만든 세트 v1 — 사고의 첫 틱과 같은 상태."""
    h = Harness()
    h.seed(cutout=make_pet_cutout_png())
    h.seed(cutout=make_pet_cutout_png())
    refset = h.build()
    assert refset.version == 1
    assert refset.analyzer_versions["vlm"] is None
    return h, refset


def _flip_vlm_on(h: Harness, monkeypatch):
    """다음 틱을 VLM 이 켜진 프로세스가 이어받는다."""
    h.install_vlm(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    assert sets.analyzer_versions()["vlm"] == vlm_identity.VLM_ANALYZER_VERSION


def _build(h: Harness, providers, refset=None, **kw):
    if refset is not None:
        kw.setdefault("pinned_reference_set_id", refset.id)
        kw.setdefault("pinned_reference_set_version", refset.version)
    return _run(
        svc.build_canonical(
            user_id=USER,
            pet_id=PET,
            fetch_bytes=h.fetch,
            providers=providers,
            cutout_fn=lambda raw: raw,
            **kw,
        )
    )


def _lineage_counts() -> dict[str, int]:
    return {
        "identity": len([r for r in ids._MOCK_PROFILES if r.get("pet_id") == PET]),
        "refsets": len([r for r in sets._MOCK_SETS if r.get("pet_id") == PET]),
        "canonicals": len(_run(svc._version_rows(PET))),
    }


class AsyncDelegate:
    """Runway 형 비동기 프로바이더 — 제출은 유료, 폴링/수거는 무료."""

    name = "runway"
    supports_durable_jobs = True
    max_prompt_chars = None

    def __init__(self, statuses=(PENDING, SUCCEEDED)):
        self.statuses = list(statuses)
        self.submit_calls = 0
        self.collect_calls = 0

    def available(self):
        return True

    def model_name(self):
        return "gen4_image"

    def submit(self, references, prompt, output_spec, metadata):
        self.submit_calls += 1
        return ProviderSubmission(f"runway-image-job-{self.submit_calls}")

    def check(self, external_job_id):
        status = self.statuses.pop(0) if self.statuses else SUCCEEDED
        return ProviderJobCheck(status, "RUNNING" if status == PENDING else status)

    def collect(self, external_job_id):
        self.collect_calls += 1
        return CanonicalImageResult(
            image_bytes=make_pet_cutout_png(),
            provider=self.name,
            model=self.model_name(),
            external_job_id=external_job_id,
        )


def _worker_provider(delegate):
    """워커 프로세스 하나가 만드는 durable 래퍼 — 재시작마다 새로 만든다."""
    return jobs.DurableImageProvider(
        delegate,
        run_id=RUN_ID,
        user_id=USER,
        pet_id=PET,
        provider_operation=jobs.OP_CANONICAL,
    )


def test_unpinned_build_re_resolves_the_reference_set_when_env_changes(uploads, monkeypatch):
    """사고의 메커니즘 자체 — 핀 없이 부르면 env 변화가 세트 v2 를 만든다."""
    h, refset_v1 = _seed_vlm_off_refset()
    _flip_vlm_on(h, monkeypatch)

    canonical = _build(h, [FakeProvider("gpt_image", [make_pet_cutout_png()])])

    assert canonical.reference_set_version == 2
    assert canonical.reference_set_id != refset_v1.id
    assert _lineage_counts()["refsets"] == 2


def test_pinned_build_uses_run_refset_after_env_change(uploads, monkeypatch):
    h, refset_v1 = _seed_vlm_off_refset()
    before = _lineage_counts()
    _flip_vlm_on(h, monkeypatch)

    canonical = _build(h, [FakeProvider("gpt_image", [make_pet_cutout_png()])], refset_v1)

    assert canonical.reference_set_id == refset_v1.id
    assert canonical.reference_set_version == 1
    assert canonical.identity_profile_version == refset_v1.identity_profile_version
    after = _lineage_counts()
    assert after["refsets"] == before["refsets"] == 1, "세트 v2 가 만들어지면 안 된다"
    assert after["identity"] == before["identity"], "신원 프로필이 다시 분석되면 안 된다"
    assert after["canonicals"] == 1


def test_pinned_build_never_calls_build_reference_set(uploads, monkeypatch):
    h, refset_v1 = _seed_vlm_off_refset()
    _flip_vlm_on(h, monkeypatch)

    async def forbidden(**kwargs):
        raise AssertionError("pinned build must not rebuild the reference set")

    monkeypatch.setattr(sets, "build_reference_set", forbidden)

    canonical = _build(h, [FakeProvider("gpt_image", [make_pet_cutout_png()])], refset_v1)
    assert canonical.reference_set_id == refset_v1.id


def test_recovered_worker_resumes_building_canonical_without_new_version_or_submission(
    uploads, monkeypatch
):
    """
    틱 1(VLM off): 제출 → PENDING 으로 양보. 틱 2(VLM on, 새 워커 프로세스):
    같은 정본 버전을 재개하고, 같은 영수증을 수거한다 — 새 버전도 새 결제도 없다.
    """
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "1")
    h, refset_v1 = _seed_vlm_off_refset()
    delegate = AsyncDelegate(statuses=(SUCCEEDED,))

    with pytest.raises(jobs.ProviderWorkPending):  # 비동기 제출은 항상 다음 틱으로 양보한다
        _build(h, [_worker_provider(delegate)], refset_v1)
    rows = _run(svc._version_rows(PET))
    assert len(rows) == 1 and rows[0]["status"] == svc.STATUS_BUILDING
    building_id = rows[0]["id"]
    assert delegate.submit_calls == 1

    _flip_vlm_on(h, monkeypatch)
    canonical = _build(h, [_worker_provider(delegate)], refset_v1)

    assert canonical.id == building_id, "env 가 달라져도 같은 building 버전을 재개한다"
    assert canonical.version == 1
    assert canonical.reference_set_id == refset_v1.id
    assert canonical.status != svc.STATUS_BUILDING
    assert delegate.submit_calls == 1, "유료 제출은 한 번뿐이다"
    assert delegate.collect_calls == 1
    assert len(jobs._MOCK_JOBS) == 1
    assert jobs._MOCK_JOBS[0]["phase_version_id"] == building_id
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.COLLECTED
    assert _lineage_counts() == {"identity": 1, "refsets": 1, "canonicals": 1}


def test_collected_result_is_reused_after_worker_restart(uploads, monkeypatch):
    """결과 수거 뒤 후처리에서 죽은 워커 — 재시작은 영수증을 재사용할 뿐 재제출하지 않는다."""
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "1")
    h, refset_v1 = _seed_vlm_off_refset()
    delegate = AsyncDelegate(statuses=(SUCCEEDED,))

    def dying_cutout(raw):
        raise MemoryError("worker died in matting")

    with pytest.raises(jobs.ProviderWorkPending):
        _build(h, [_worker_provider(delegate)], refset_v1)
    with pytest.raises(MemoryError):
        _run(
            svc.build_canonical(
                user_id=USER,
                pet_id=PET,
                fetch_bytes=h.fetch,
                providers=[_worker_provider(delegate)],
                cutout_fn=dying_cutout,
                pinned_reference_set_id=refset_v1.id,
                pinned_reference_set_version=refset_v1.version,
            )
        )
    assert delegate.submit_calls == 1
    rows = _run(svc._version_rows(PET))
    assert len(rows) == 1 and rows[0]["status"] == svc.STATUS_BUILDING

    _flip_vlm_on(h, monkeypatch)
    canonical = _build(h, [_worker_provider(delegate)], refset_v1)

    assert canonical.id == rows[0]["id"]
    assert delegate.submit_calls == 1, "재시작 후에도 새 결제는 없다"
    assert len(jobs._MOCK_JOBS) == 1
    assert len(canonical.candidates) == 1
    assert canonical.candidates[0].external_job_id == "runway-image-job-1"
    assert _lineage_counts() == {"identity": 1, "refsets": 1, "canonicals": 1}


def test_completed_canonical_on_pinned_refset_is_reused_after_env_change(uploads, monkeypatch):
    h, refset_v1 = _seed_vlm_off_refset()
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    first = _build(h, [FakeProvider("gpt_image", [make_pet_cutout_png()])], refset_v1)
    assert first.status == svc.STATUS_COMPLETE

    _flip_vlm_on(h, monkeypatch)
    provider = FakeProvider("gpt_image", [make_pet_cutout_png()])
    second = _build(h, [provider], refset_v1)

    assert second.id == first.id and second.deduplicated
    assert provider.calls == 0
    assert _lineage_counts()["canonicals"] == 1


def test_pinned_refset_that_cannot_be_loaded_fails_closed(uploads, monkeypatch):
    h, refset_v1 = _seed_vlm_off_refset()
    provider = FakeProvider("gpt_image", [make_pet_cutout_png()])

    with pytest.raises(svc.CanonicalPetError) as missing_version:
        _build(h, [provider], pinned_reference_set_id=refset_v1.id, pinned_reference_set_version=7)
    assert missing_version.value.code == "PINNED_REFERENCE_SET_NOT_FOUND"

    with pytest.raises(svc.CanonicalPetError) as wrong_id:
        _build(
            h, [provider],
            pinned_reference_set_id="00000000-0000-0000-0000-00000000dead",
            pinned_reference_set_version=1,
        )
    assert wrong_id.value.code == "PINNED_REFERENCE_SET_NOT_FOUND"

    with pytest.raises(svc.CanonicalPetError) as half_pin:
        _build(h, [provider], pinned_reference_set_id=refset_v1.id)
    assert half_pin.value.code == "PINNED_REFERENCE_SET_INVALID"

    assert provider.calls == 0
    assert _lineage_counts() == {"identity": 1, "refsets": 1, "canonicals": 0}


# ══════════════════════════════════════════════════════════════════════════
# 빌더: PASS 가 불가능한 QA 구성에서는 유료 호출 0
# ══════════════════════════════════════════════════════════════════════════


def test_vlm_off_submits_nothing_when_pass_is_impossible(uploads, monkeypatch):
    h, refset_v1 = _seed_vlm_off_refset()
    delegate = AsyncDelegate(statuses=(SUCCEEDED,))
    fallback = FakeProvider("gpt_image", [make_pet_cutout_png()])

    with pytest.raises(svc.CanonicalPetError) as error:
        _build(h, [_worker_provider(delegate), fallback], refset_v1, require_pass_capable_qa=True)

    assert error.value.code == "CANONICAL_QA_NOT_CONFIGURED"
    assert error.value.status == 503
    assert "PET_VLM_IDENTITY_ENABLED" in error.value.message
    assert delegate.submit_calls == 0 and fallback.calls == 0
    assert jobs._MOCK_JOBS == []
    assert _lineage_counts()["canonicals"] == 0, "버전 행도 만들지 않는다"


def test_vlm_enabled_without_credentials_submits_nothing(uploads, monkeypatch):
    h, refset_v1 = _seed_vlm_off_refset()
    monkeypatch.setenv("PET_VLM_IDENTITY_ENABLED", "1")
    provider = FakeProvider("gpt_image", [make_pet_cutout_png()])

    with pytest.raises(svc.CanonicalPetError) as error:
        _build(h, [provider], refset_v1, require_pass_capable_qa=True)

    assert error.value.code == "CANONICAL_QA_NOT_CONFIGURED"
    assert "ANTHROPIC_API_KEY" in error.value.message
    assert provider.calls == 0
    assert _lineage_counts()["canonicals"] == 0


def test_pass_capable_qa_config_generates_normally(uploads, monkeypatch):
    h, refset_v1 = _seed_vlm_off_refset()
    _flip_vlm_on(h, monkeypatch)
    provider = FakeProvider("gpt_image", [make_pet_cutout_png()])

    canonical = _build(h, [provider], refset_v1, require_pass_capable_qa=True)

    assert canonical.status == svc.STATUS_COMPLETE
    assert provider.calls == 1


def test_qa_preflight_does_not_block_free_reuse_of_a_finished_canonical(uploads, monkeypatch):
    h, refset_v1 = _seed_vlm_off_refset()
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    first = _build(h, [FakeProvider("gpt_image", [make_pet_cutout_png()])], refset_v1)

    provider = FakeProvider("gpt_image", [make_pet_cutout_png()])
    reused = _build(h, [provider], refset_v1, require_pass_capable_qa=True)

    assert reused.id == first.id and provider.calls == 0


def test_pass_requirements_are_not_downgraded():
    from backend.services import canonical_qa

    assert canonical_qa.pass_requires_vlm() is True
    checks = {key: canonical_qa.PASS for key in canonical_qa.CORE_CHECKS}
    assert canonical_qa.decide(checks) == canonical_qa.PASS
    for key in ("vlm_anatomy", "vlm_same_pet", "vlm_composition"):
        assert canonical_qa.decide({**checks, key: "unknown"}) == canonical_qa.REVIEW


# ══════════════════════════════════════════════════════════════════════════
# 오케스트레이터: 핀 전달 / 검사 후 핀 / Retry
# ══════════════════════════════════════════════════════════════════════════

OTHER_REFSET = "00000000-0000-0000-0000-000000000399"


def _orchestrator(monkeypatch):
    p7c.seed_intake()
    harness = p7c.PipelineHarness(monkeypatch)
    build_kwargs: list[dict] = []
    original_build = runs.canonical_pet_service.build_canonical

    async def recording_build(**kwargs):
        build_kwargs.append(dict(kwargs))
        return await original_build(**kwargs)

    monkeypatch.setattr(runs.canonical_pet_service, "build_canonical", recording_build)
    monkeypatch.setattr(
        runs, "_image_providers", lambda run, operation: [FakeProvider("gpt_image")]
    )
    return harness, build_kwargs


def _stored(run_id: str) -> dict:
    return next(row for row in runs._MOCK_RUNS if row["id"] == run_id)


def test_orchestrator_passes_the_run_pinned_refset_to_the_builder(uploads, monkeypatch):
    harness, build_kwargs = _orchestrator(monkeypatch)

    p7c.start("pinned-refset")
    result = p7c.work()

    assert result.status == runs.STATUS_PUBLISHED
    assert len(build_kwargs) == 1
    assert build_kwargs[0]["pinned_reference_set_id"] == harness.refset.id
    assert build_kwargs[0]["pinned_reference_set_version"] == harness.refset.version
    assert build_kwargs[0]["require_pass_capable_qa"] is True


def test_mismatched_canonical_fails_before_the_run_pin_is_written(uploads, monkeypatch):
    harness, _ = _orchestrator(monkeypatch)
    harness.canonical.reference_set_id = OTHER_REFSET
    harness.canonical.reference_set_version = 2

    p7c.start("mismatch-no-pin")
    result = p7c.work()

    assert result.status == runs.STATUS_FAILED
    assert result.current_stage == runs.STAGE_CANONICAL
    assert result.last_error["code"] == "RUN_LINEAGE_INVALID"
    assert result.canonical_version_id is None
    assert result.canonical_version is None
    assert result.reference_set_id == harness.refset.id
    assert harness.counts["keyframe_build"] == 0


@pytest.mark.parametrize(
    "field, value",
    [("pet_id", "pet_someone_else"), ("user_id", "mallory@test"), ("identity_profile_version", 9)],
)
def test_canonical_from_another_lineage_is_never_pinned(uploads, monkeypatch, field, value):
    harness, _ = _orchestrator(monkeypatch)
    setattr(harness.canonical, field, value)

    p7c.start(f"foreign-{field}")
    result = p7c.work()

    assert result.status == runs.STATUS_FAILED
    assert result.last_error["code"] == "RUN_LINEAGE_INVALID"
    assert result.canonical_version_id is None


def test_retry_after_lineage_failure_has_no_stale_pin_and_recovers(uploads, monkeypatch):
    harness, build_kwargs = _orchestrator(monkeypatch)
    harness.canonical.reference_set_id = OTHER_REFSET

    p7c.start("retry-clean")
    failed = p7c.work()
    assert failed.last_error["code"] == "RUN_LINEAGE_INVALID"

    retried = _run(runs.retry_generation_run(user_id=p7c.USER, run_id=failed.id))
    assert retried.status == runs.STATUS_QUEUED
    assert retried.canonical_version_id is None
    assert retried.reference_set_id == harness.refset.id

    harness.canonical.reference_set_id = harness.refset.id
    result = p7c.work()

    assert result.status == runs.STATUS_PUBLISHED
    assert result.canonical_version_id == harness.canonical.id
    assert all(k["pinned_reference_set_id"] == harness.refset.id for k in build_kwargs)


def test_retry_clears_a_stale_invalid_pin_written_by_the_old_order(uploads, monkeypatch):
    """예전 순서(핀 → 검사)가 남긴 독성 핀 — Retry 가 핀만 비우고 행은 남긴다."""
    harness, build_kwargs = _orchestrator(monkeypatch)
    harness.canonical.reference_set_id = OTHER_REFSET

    p7c.start("retry-legacy-pin")
    failed = p7c.work()
    row = _stored(failed.id)
    row["canonical_version_id"] = harness.canonical.id
    row["canonical_version"] = harness.canonical.version

    retried = _run(runs.retry_generation_run(user_id=p7c.USER, run_id=failed.id))

    assert retried.status == runs.STATUS_QUEUED
    assert retried.current_stage == runs.STAGE_CANONICAL
    assert retried.canonical_version_id is None
    assert retried.canonical_version is None
    assert retried.reference_set_id == harness.refset.id
    assert retried.identity_profile_id == harness.profile.id

    harness.canonical.reference_set_id = harness.refset.id
    result = p7c.work()
    assert result.status == runs.STATUS_PUBLISHED
    assert build_kwargs[-1]["pinned_reference_set_id"] == harness.refset.id


def test_retry_keeps_a_valid_canonical_pin(uploads, monkeypatch):
    harness, build_kwargs = _orchestrator(monkeypatch)
    harness.canonical.status = svc.STATUS_FAILED

    p7c.start("retry-valid-pin")
    failed = p7c.work()
    assert failed.last_error["code"] == "CANONICAL_NOT_COMPLETE"
    assert failed.canonical_version_id == harness.canonical.id

    retried = _run(runs.retry_generation_run(user_id=p7c.USER, run_id=failed.id))

    assert retried.canonical_version_id == harness.canonical.id
    assert retried.canonical_version == harness.canonical.version


def test_retry_leaves_pin_alone_when_the_pinned_canonical_has_open_provider_work(
    uploads, monkeypatch
):
    harness, _ = _orchestrator(monkeypatch)
    harness.canonical.reference_set_id = OTHER_REFSET

    p7c.start("retry-open-job")
    failed = p7c.work()
    row = _stored(failed.id)
    row["canonical_version_id"] = harness.canonical.id
    row["canonical_version"] = harness.canonical.version
    monkeypatch.setattr(
        runs.durable_provider_jobs,
        "list_for_run",
        lambda run_id: [
            {"phase_version_id": harness.canonical.id, "submission_status": jobs.SUBMITTED}
        ],
    )

    retried = _run(runs.retry_generation_run(user_id=p7c.USER, run_id=failed.id))

    assert retried.canonical_version_id == harness.canonical.id
