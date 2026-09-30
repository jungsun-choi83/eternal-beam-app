"""
KEYFRAME 계보 회귀 테스트 (2026-09-29, CANONICAL 계보 수정의 후속).

build_keyframe() 은 실행이 고정한 정본이 아니라 "최신 정본"을 해석했고, 멱등/재개
판정은 현재 프로세스 env 의 분석기 스탬프를 썼다. 틱 사이에 다른 정본 버전이
생기거나 PET_VLM_IDENTITY_ENABLED 가 달라지면 키프레임이 다른 정본에 붙거나
새 (유료) 버전이 만들어졌다.

프로바이더는 전부 가짜다 — 실 결제 호출은 없다.
"""

from __future__ import annotations

import anyio
import pytest

from backend.services import action_keyframe_service as kf
from backend.services import canonical_pet_service as canon
from backend.services import durable_provider_jobs as jobs
from backend.services import pet_generation_run_service as runs
from backend.services import pet_identity_service as ids
from backend.services import pet_morphology_service as morph
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry
from backend.services.provider_job_contract import SUCCEEDED

from . import test_phase7c_generation_runs as p7c
from .test_action_keyframes import VLM_KF_OK, install_kf_vlm
from .test_canonical_pet_builder import VLM_QA_OK, FakeProvider, install_vlm_qa
from .test_canonical_pinned_lineage import AsyncDelegate
from .test_pet_identity_profile import make_pet_cutout_png
from .test_pet_reference_sets import PET, USER, Harness

RUN_ID = "00000000-0000-0000-0000-0000000377b6"
ROLE = "NEUTRAL_IDLE"


def _run(coro):
    return anyio.run(lambda: coro)


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("SEEDANCE_TRANSPORT", "runway")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    monkeypatch.delenv("KEYFRAME_ALLOW_REVIEW_CANONICAL", raising=False)
    monkeypatch.setenv("CANONICAL_QA_MIN_RESOLUTION", "100")
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "1")
    modules = (refs, pet_registry, ids, morph, sets, canon, kf, runs)
    for m in modules:
        m.__reset_for_tests()
    jobs.__reset_for_tests()
    yield
    for m in modules:
        m.__reset_for_tests()
    jobs.__reset_for_tests()


@pytest.fixture
def storage(monkeypatch) -> dict[str, bytes]:
    from backend.services import supabase_assets

    store: dict[str, bytes] = {}

    async def fake_upload(path, data, content_type):
        store[path] = bytes(data)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    return store


# ══════════════════════════════════════════════════════════════════════════
# 빌더
# ══════════════════════════════════════════════════════════════════════════


def _prepare(monkeypatch, storage):
    """VLM 이 꺼진 워커가 만든 세트 v1 + 정본 v1(complete)."""
    h = Harness()
    h.seed(cutout=make_pet_cutout_png())
    h.seed(cutout=make_pet_cutout_png())
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    install_kf_vlm(monkeypatch, VLM_KF_OK)

    def fetch(ref):
        return h.bytes_by_path.get(ref.object_path) or storage.get(ref.object_path)

    h.kf_fetch = fetch
    canonical = _build_canonical(h)
    assert canonical.status == canon.STATUS_COMPLETE and canonical.version == 1
    assert canonical.analyzer_versions["vlm"] is None
    return h, canonical


def _build_canonical(h, **kw):
    return _run(
        canon.build_canonical(
            user_id=USER, pet_id=PET, fetch_bytes=h.kf_fetch,
            providers=[FakeProvider("gpt_image", [make_pet_cutout_png()])],
            cutout_fn=lambda raw: raw, **kw,
        )
    )


def _flip_vlm_on(h, monkeypatch):
    h.install_vlm(monkeypatch)
    assert kf.analyzer_versions()["vlm"] is not None


def _build_kf(h, providers, canonical=None, **kw):
    if canonical is not None:
        kw.setdefault("pinned_canonical_version_id", canonical.id)
        kw.setdefault("pinned_canonical_version", canonical.version)
    return _run(
        kf.build_keyframe(
            user_id=USER, pet_id=PET, keyframe_role=ROLE,
            fetch_bytes=h.kf_fetch, providers=providers,
            cutout_fn=lambda raw: raw, **kw,
        )
    )


def _worker_provider(delegate):
    """워커 프로세스 하나가 만드는 durable 래퍼 — 재시작마다 새로 만든다."""
    return jobs.DurableImageProvider(
        delegate, run_id=RUN_ID, user_id=USER, pet_id=PET,
        provider_operation=jobs.OP_KEYFRAME,
    )


def _counts() -> dict[str, int]:
    return {
        "refsets": len([r for r in sets._MOCK_SETS if r.get("pet_id") == PET]),
        "canonicals": len(_run(canon._version_rows(PET))),
        "keyframes": len(_run(kf._keyframe_rows(PET, ROLE))),
    }


def test_unpinned_build_follows_the_latest_canonical(storage, monkeypatch):
    """버그의 메커니즘 자체 — 핀 없이 부르면 나중에 생긴 정본 v2 에 붙는다."""
    h, canonical_v1 = _prepare(monkeypatch, storage)
    canonical_v2 = _build_canonical(h, skip_if_unchanged=False)
    assert canonical_v2.version == 2

    keyframe = _build_kf(h, [FakeProvider("gpt_image", [make_pet_cutout_png()])])

    assert keyframe.canonical_version_id == canonical_v2.id != canonical_v1.id


def test_pinned_build_uses_run_canonical_not_the_latest(storage, monkeypatch):
    h, canonical_v1 = _prepare(monkeypatch, storage)
    canonical_v2 = _build_canonical(h, skip_if_unchanged=False)
    before = _counts()

    provider = FakeProvider("gpt_image", [make_pet_cutout_png()])
    keyframe = _build_kf(h, [provider], canonical_v1)

    assert keyframe.canonical_version_id == canonical_v1.id
    assert keyframe.canonical_version == 1
    assert keyframe.canonical_version_id != canonical_v2.id
    anchor = next(c for c in canonical_v1.candidates if c.selected)
    assert keyframe.candidates[0].input_canonical_candidate_id == anchor.id
    after = _counts()
    assert after["canonicals"] == before["canonicals"] == 2, "정본은 만들어지지도 바뀌지도 않는다"
    assert after["refsets"] == before["refsets"]
    stored_v1 = _run(canon.get_canonical(user_id=USER, pet_id=PET, version=1))
    assert stored_v1 == canonical_v1


def test_pinned_build_survives_env_change_between_ticks(storage, monkeypatch):
    h, canonical_v1 = _prepare(monkeypatch, storage)
    first = _build_kf(h, [FakeProvider("gpt_image", [make_pet_cutout_png()])], canonical_v1)
    assert first.status == kf.STATUS_COMPLETE

    _flip_vlm_on(h, monkeypatch)
    provider = FakeProvider("gpt_image", [make_pet_cutout_png()])
    second = _build_kf(h, [provider], canonical_v1)

    assert second.id == first.id and second.deduplicated
    assert provider.calls == 0, "env 가 달라졌다는 이유만으로 새 유료 키프레임을 만들지 않는다"
    assert _counts() == {"refsets": 1, "canonicals": 1, "keyframes": 1}


def test_recovered_worker_resumes_building_keyframe_without_new_version_or_submission(
    storage, monkeypatch
):
    """틱 1(VLM off): 제출 → 양보. 틱 2(VLM on, 새 워커): 같은 버전·같은 영수증."""
    h, canonical_v1 = _prepare(monkeypatch, storage)
    delegate = AsyncDelegate(statuses=(SUCCEEDED,))

    with pytest.raises(jobs.ProviderWorkPending):
        _build_kf(h, [_worker_provider(delegate)], canonical_v1)
    rows = _run(kf._keyframe_rows(PET, ROLE))
    assert len(rows) == 1 and rows[0]["status"] == kf.STATUS_BUILDING
    assert delegate.submit_calls == 1

    _flip_vlm_on(h, monkeypatch)
    keyframe = _build_kf(h, [_worker_provider(delegate)], canonical_v1)

    assert keyframe.id == rows[0]["id"] and keyframe.version == 1
    assert keyframe.canonical_version_id == canonical_v1.id
    assert keyframe.status != kf.STATUS_BUILDING
    assert delegate.submit_calls == 1, "유료 제출은 한 번뿐이다"
    assert delegate.collect_calls == 1
    assert len(jobs._MOCK_JOBS) == 1
    assert jobs._MOCK_JOBS[0]["phase_version_id"] == rows[0]["id"]
    assert _counts() == {"refsets": 1, "canonicals": 1, "keyframes": 1}


def test_collected_keyframe_result_is_reused_after_worker_crash(storage, monkeypatch):
    """결과 수거 뒤 후처리에서 죽은 워커 — 재시작은 영수증을 재사용한다."""
    h, canonical_v1 = _prepare(monkeypatch, storage)
    delegate = AsyncDelegate(statuses=(SUCCEEDED,))

    with pytest.raises(jobs.ProviderWorkPending):
        _build_kf(h, [_worker_provider(delegate)], canonical_v1)

    def dying_cutout(raw):
        raise MemoryError("worker died in matting")

    with pytest.raises(MemoryError):
        _run(
            kf.build_keyframe(
                user_id=USER, pet_id=PET, keyframe_role=ROLE,
                fetch_bytes=h.kf_fetch, providers=[_worker_provider(delegate)],
                cutout_fn=dying_cutout,
                pinned_canonical_version_id=canonical_v1.id,
                pinned_canonical_version=canonical_v1.version,
            )
        )
    rows = _run(kf._keyframe_rows(PET, ROLE))
    assert len(rows) == 1 and rows[0]["status"] == kf.STATUS_BUILDING

    _flip_vlm_on(h, monkeypatch)
    keyframe = _build_kf(h, [_worker_provider(delegate)], canonical_v1)

    assert keyframe.id == rows[0]["id"]
    assert delegate.submit_calls == 1, "재시작 후에도 새 결제는 없다"
    assert len(jobs._MOCK_JOBS) == 1
    assert len(keyframe.candidates) == 1
    assert _counts() == {"refsets": 1, "canonicals": 1, "keyframes": 1}


def test_recovery_ignores_a_newer_canonical_created_mid_run(storage, monkeypatch):
    h, canonical_v1 = _prepare(monkeypatch, storage)
    delegate = AsyncDelegate(statuses=(SUCCEEDED,))
    with pytest.raises(jobs.ProviderWorkPending):
        _build_kf(h, [_worker_provider(delegate)], canonical_v1)
    building_id = _run(kf._keyframe_rows(PET, ROLE))[0]["id"]

    _build_canonical(h, skip_if_unchanged=False)  # 다른 경로가 정본 v2 를 만들었다
    keyframe = _build_kf(h, [_worker_provider(delegate)], canonical_v1)

    assert keyframe.id == building_id
    assert keyframe.canonical_version_id == canonical_v1.id
    assert delegate.submit_calls == 1
    assert _counts()["keyframes"] == 1


def test_pinned_canonical_that_cannot_be_loaded_fails_closed(storage, monkeypatch):
    h, canonical_v1 = _prepare(monkeypatch, storage)
    provider = FakeProvider("gpt_image", [make_pet_cutout_png()])

    with pytest.raises(kf.ActionKeyframeError) as missing:
        _build_kf(h, [provider], pinned_canonical_version_id=canonical_v1.id, pinned_canonical_version=7)
    assert missing.value.code == "PINNED_CANONICAL_NOT_FOUND"

    with pytest.raises(kf.ActionKeyframeError) as wrong_id:
        _build_kf(
            h, [provider],
            pinned_canonical_version_id="00000000-0000-0000-0000-00000000dead",
            pinned_canonical_version=1,
        )
    assert wrong_id.value.code == "PINNED_CANONICAL_NOT_FOUND"

    with pytest.raises(kf.ActionKeyframeError) as half_pin:
        _build_kf(h, [provider], pinned_canonical_version_id=canonical_v1.id)
    assert half_pin.value.code == "PINNED_CANONICAL_INVALID"

    assert provider.calls == 0
    assert _counts() == {"refsets": 1, "canonicals": 1, "keyframes": 0}


# ══════════════════════════════════════════════════════════════════════════
# 오케스트레이터: 핀 전달 / 검사 후 핀
# ══════════════════════════════════════════════════════════════════════════

OTHER_CANONICAL = "00000000-0000-0000-0000-000000000499"


def _orchestrator(monkeypatch):
    p7c.seed_intake()
    harness = p7c.PipelineHarness(monkeypatch)
    monkeypatch.setattr(
        runs, "_image_providers", lambda run, operation: [FakeProvider("gpt_image")]
    )
    return harness


def test_orchestrator_passes_the_run_pinned_canonical_to_the_builder(storage, monkeypatch):
    harness = _orchestrator(monkeypatch)

    p7c.start("kf-pinned")
    result = p7c.work()

    assert result.status == runs.STATUS_PUBLISHED
    assert len(harness.keyframe_build_calls) == 1
    call = harness.keyframe_build_calls[0]
    assert call["pinned_canonical_version_id"] == harness.canonical.id == result.canonical_version_id
    assert call["pinned_canonical_version"] == harness.canonical.version


def test_mismatched_keyframe_fails_before_the_run_pin_is_written(storage, monkeypatch):
    harness = _orchestrator(monkeypatch)
    harness.keyframe.canonical_version_id = OTHER_CANONICAL
    harness.keyframe.canonical_version = 2

    p7c.start("kf-mismatch")
    result = p7c.work()

    assert result.status == runs.STATUS_FAILED
    assert result.current_stage == runs.STAGE_KEYFRAMES
    assert result.last_error["code"] == "RUN_LINEAGE_INVALID"
    assert result.keyframes == {}, "계보가 다른 키프레임은 실행에 핀되지 않는다"
    assert result.canonical_version_id == harness.canonical.id, "정본 핀은 그대로다"
    assert result.canonical_version == harness.canonical.version
    assert harness.counts["motion_build"] == 0


@pytest.mark.parametrize("field, value", [("pet_id", "pet_someone_else"), ("user_id", "mallory@test")])
def test_keyframe_from_another_owner_is_never_pinned(storage, monkeypatch, field, value):
    harness = _orchestrator(monkeypatch)
    setattr(harness.keyframe, field, value)

    p7c.start(f"kf-foreign-{field}")
    result = p7c.work()

    assert result.status == runs.STATUS_FAILED
    assert result.last_error["code"] == "RUN_LINEAGE_INVALID"
    assert result.keyframes == {}


def test_lineage_failure_does_not_overwrite_an_existing_keyframe_pin(storage, monkeypatch):
    """전이 모션: 시작 역할은 이미 핀됐고, 목표 역할의 계보가 틀렸다."""
    harness = _orchestrator(monkeypatch)
    p7c.start("kf-keep-pin")
    published = p7c.work()
    pinned = dict(published.keyframes[ROLE])

    run = runs._to_run(next(r for r in runs._MOCK_RUNS if r["id"] == published.id))
    harness.keyframe.canonical_version_id = OTHER_CANONICAL
    with pytest.raises(runs.PetGenerationRunError) as error:
        _run(runs._advance_keyframe_stage(run, ROLE, harness.keyframe, dict(run.keyframes)))

    assert error.value.code == "RUN_LINEAGE_INVALID"
    stored = next(r for r in runs._MOCK_RUNS if r["id"] == published.id)
    assert stored["keyframes"][ROLE] == pinned


def test_retry_after_keyframe_lineage_failure_recovers(storage, monkeypatch):
    harness = _orchestrator(monkeypatch)
    harness.keyframe.canonical_version_id = OTHER_CANONICAL

    p7c.start("kf-retry")
    failed = p7c.work()
    assert failed.last_error["code"] == "RUN_LINEAGE_INVALID" and failed.keyframes == {}

    retried = _run(runs.retry_generation_run(user_id=p7c.USER, run_id=failed.id))
    assert retried.keyframes == {}
    assert retried.canonical_version_id == harness.canonical.id

    harness.keyframe.canonical_version_id = harness.canonical.id
    result = p7c.work()

    assert result.status == runs.STATUS_PUBLISHED
    assert result.keyframes[ROLE]["id"] == harness.keyframe.id
    assert harness.counts["canonical_build"] == 1, "정본은 다시 만들어지지 않는다"
