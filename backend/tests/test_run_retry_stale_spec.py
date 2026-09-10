"""FAILED 실행 재시도 vs 모션 스펙 버전 격차 — 스테일 계보 해소.

배경(라이브 실측, run ebbc11f5): 실행이 실패한 뒤 모션 스펙이 올라가면(v5→v6),
재시도는 같은 실행을 재사용하지만 고정된 옛 motion_version 이 현재 스펙과 영원히
어긋나 RUN_LINEAGE_INVALID 로 죽는다. 구독 모드 구매는 멱등 키가 고정이라
사용자의 [생성] 버튼이 조용히 영원히 실패한다.

계약 (retry_generation_run + _stale_motion_pin):
  * 같은 스펙        → 기존 durable 재사용 그대로 (핀 유지)
  * 스펙 격차 + 안전 → 하류 모션 계보만 비우고 MOTION_SPEC 부터 재개,
                       현재 스펙으로 **다음** 버전을 만든다
  * 안전 증명 실패   → 절대 핀을 풀지 않는다 (fail-closed):
      - 종료되지 않은 프로바이더 작업 (SUBMITTING/SUBMITTED/AMBIGUOUS/PREPARED)
      - 발행/이행 존재, 운영자 교체 요청 존재, 버전 행 조회 불능
  * 옛 버전/후보 행은 역사로 남는다 — 삭제 없음
  * 상류 Phase 1–5 핀(신원/레퍼런스/canonical/키프레임)은 재사용된다
"""

from __future__ import annotations

from types import SimpleNamespace

import anyio
import pytest

from backend.services import (
    durable_provider_jobs,
    motion_spec,
    motion_video_service,
    pet_reference_service,
    pet_registry,
)
from backend.services import pet_generation_run_service as runs

from .test_phase7c_generation_runs import PET, USER, PipelineHarness, seed_intake

OLD_SPEC = "motion-spec-v5-old-for-test"


def _run(awaitable):
    return anyio.run(lambda: awaitable)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("SEEDANCE_TRANSPORT", "runway")
    for svc in (runs, pet_reference_service, pet_registry, durable_provider_jobs, motion_video_service):
        svc.__reset_for_tests()
    yield
    for svc in (runs, pet_reference_service, pet_registry, durable_provider_jobs, motion_video_service):
        svc.__reset_for_tests()


@pytest.fixture
def storage(monkeypatch):
    from backend.services import supabase_assets

    async def upload(path, data, content_type):
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", upload)
    return None


def _start(key="retry-stale:1"):
    return _run(
        runs.start_generation_run(
            user_id=USER, pet_id=PET, motion_id="BREATHING",
            request_kind="FREE_HOME", idempotency_key=key,
        )
    )


def _work():
    return _run(runs.process_next_generation_run(worker_id="retry-stale-test-worker"))


def _retry(run_id):
    return _run(runs.retry_generation_run(user_id=USER, run_id=run_id))


def _fail_pinned_to_old_spec(monkeypatch):
    """QA REVIEW 로 FAILED 된 실행 — 고정된 모션 버전은 **옛 스펙** 도장이다."""
    seed_intake()
    harness = PipelineHarness(monkeypatch, motion_status=motion_video_service.STATUS_REVIEW)
    harness.motion.motion_spec_version = OLD_SPEC
    started = _start()
    failed = _work()
    assert failed.status == runs.STATUS_FAILED
    assert failed.motion_version_id == harness.motion.id
    return harness, started, failed


def _swap_in_v2(harness):
    """다음 시도의 빌드 산출물 — 현재 스펙의 v2, QA PASS."""
    selected = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000702", selected=True, decision="PASS"
    )
    harness.motion = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000701",
        pet_id=PET, user_id=USER, motion_id="BREATHING",
        motion_spec_version=motion_spec.MOTION_SPEC_VERSION,
        start_keyframe_id=harness.keyframe.id, start_keyframe_version=1,
        canonical_version_id=harness.canonical.id,
        version=2, status="complete",
        selected_candidate_id=selected.id, candidates=[selected],
    )
    harness.publication = SimpleNamespace(
        publication_id="00000000-0000-0000-0000-000000000703",
        selected_candidate_id=selected.id,
    )
    return harness.motion


# ══════════════════════════════════════════════════════════════════════════
# 1. 스펙 격차 재시도 — 하류만 비우고 같은 실행으로 새 버전을 만든다
# ══════════════════════════════════════════════════════════════════════════


def test_stale_spec_retry_unpins_downstream_and_builds_new_version(storage, monkeypatch):
    harness, started, failed = _fail_pinned_to_old_spec(monkeypatch)
    old_motion = harness.motion

    requeued = _retry(started.id)
    # 하류 모션 계보만 비었다.
    assert requeued.status == runs.STATUS_QUEUED
    assert requeued.motion_version_id is None
    assert requeued.motion_version is None
    assert requeued.selected_candidate_id is None
    assert requeued.publication_id is None
    assert requeued.last_error is None
    assert requeued.current_stage == runs.STAGE_MOTION_SPEC
    assert requeued.retry_count == 1
    # 상류 핀은 그대로다 — 같은 실행, 같은 계보.
    assert requeued.id == started.id
    assert requeued.identity_profile_id == failed.identity_profile_id
    assert requeued.canonical_version_id == failed.canonical_version_id
    assert requeued.keyframes == failed.keyframes

    _swap_in_v2(harness)
    published = _work()
    assert published.status == runs.STATUS_PUBLISHED
    assert published.id == started.id  # 새 실행이 아니라 같은 실행이다
    assert published.motion_version_id == harness.motion.id
    assert published.motion_version == 2
    assert published.motion_spec_version == motion_spec.MOTION_SPEC_VERSION

    # 새 **빌드**였다 — 옛 버전을 고치거나 지운 것이 아니다.
    assert harness.counts["motion_build"] == 2
    assert old_motion.id != harness.motion.id
    assert old_motion.motion_spec_version == OLD_SPEC  # 역사 그대로
    # 상류 Phase 2–5 는 재생성되지 않았다.
    assert harness.counts["identity_build"] == 1
    assert harness.counts["canonical_build"] == 1
    assert harness.counts["keyframe_build"] == 1


# ══════════════════════════════════════════════════════════════════════════
# 2. 같은 스펙 — 기존 durable 재사용 그대로
# ══════════════════════════════════════════════════════════════════════════


def test_same_spec_retry_keeps_pin_and_reuses_motion_version(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch, motion_status=motion_video_service.STATUS_REVIEW)
    started = _start()
    failed = _work()
    assert failed.status == runs.STATUS_FAILED
    assert harness.motion.motion_spec_version == motion_spec.MOTION_SPEC_VERSION

    requeued = _retry(started.id)
    assert requeued.status == runs.STATUS_QUEUED
    assert requeued.motion_version_id == harness.motion.id  # 핀 유지
    assert requeued.motion_version == 1
    assert requeued.current_stage == failed.current_stage  # 스테이지 되감기 없음

    again = _work()
    # 고정된 버전을 재사용했다 — 재빌드 없음 = 프로바이더 이중 지출 없음.
    assert harness.counts["motion_build"] == 1
    assert again.status == runs.STATUS_FAILED  # 모션은 여전히 REVIEW — 판정 정직
    assert again.motion_version_id == harness.motion.id


# ══════════════════════════════════════════════════════════════════════════
# 3. 종료되지 않은 프로바이더 작업 — 핀을 풀지 않는다
# ══════════════════════════════════════════════════════════════════════════


def _plant_job(run_id: str, submission_status: str):
    durable_provider_jobs._MOCK_JOBS.append(
        {
            "id": f"job-{submission_status}",
            "run_id": run_id,
            "user_id": USER,
            "pet_id": PET,
            "provider_operation": durable_provider_jobs.OP_MOTION,
            "phase_version_id": "00000000-0000-0000-0000-000000000601",
            "provider": "seedance",
            "attempt": 1,
            "submission_status": submission_status,
            "provider_status": "PENDING",
        }
    )


@pytest.mark.parametrize(
    "submission_status",
    [
        durable_provider_jobs.PREPARED,
        durable_provider_jobs.SUBMITTING,
        durable_provider_jobs.SUBMITTED,
        durable_provider_jobs.AMBIGUOUS,
    ],
)
def test_inflight_provider_job_blocks_unpin(storage, monkeypatch, submission_status):
    harness, started, failed = _fail_pinned_to_old_spec(monkeypatch)
    _plant_job(started.id, submission_status)

    requeued = _retry(started.id)
    # 재큐는 되지만 (기존 계약) 핀은 절대 풀리지 않는다 — 이중 지출 금지.
    assert requeued.status == runs.STATUS_QUEUED
    assert requeued.motion_version_id == harness.motion.id
    assert requeued.motion_version == 1


def test_terminal_provider_jobs_do_not_block_unpin(storage, monkeypatch):
    harness, started, failed = _fail_pinned_to_old_spec(monkeypatch)
    _plant_job(started.id, durable_provider_jobs.COLLECTED)
    _plant_job(started.id, "FAILED")

    requeued = _retry(started.id)
    assert requeued.motion_version_id is None  # 전부 종료 → 스테일 해소 진행


def test_published_run_is_never_unpinned(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)
    started = _start()
    published = _work()
    assert published.status == runs.STATUS_PUBLISHED

    unchanged = _retry(started.id)
    assert unchanged.status == runs.STATUS_PUBLISHED
    assert unchanged.motion_version_id == harness.motion.id
    assert unchanged.publication_id == published.publication_id


def test_operator_replacement_request_blocks_unpin(storage, monkeypatch):
    harness, started, failed = _fail_pinned_to_old_spec(monkeypatch)
    row = next(r for r in runs._MOCK_RUNS if r["id"] == started.id)
    state = dict(row.get("provider_state") or {})
    state["_operator"] = {"replacement_request": {"idempotency_key": "op:1", "status": "QUEUED"}}
    row["provider_state"] = state

    requeued = _retry(started.id)
    assert requeued.motion_version_id == harness.motion.id  # 운영자 흐름에 맡긴다


# ══════════════════════════════════════════════════════════════════════════
# 4. 반복 재시도 멱등
# ══════════════════════════════════════════════════════════════════════════


def test_repeated_retry_is_idempotent(storage, monkeypatch):
    harness, started, failed = _fail_pinned_to_old_spec(monkeypatch)

    first = _retry(started.id)
    assert first.motion_version_id is None
    assert first.retry_count == 1

    # QUEUED 상태의 재시도는 아무것도 바꾸지 않는다 (기존 단락 유지).
    second = _retry(started.id)
    assert second.status == runs.STATUS_QUEUED
    assert second.retry_count == 1
    assert second.motion_version_id is None
    assert second.current_stage == runs.STAGE_MOTION_SPEC


# ══════════════════════════════════════════════════════════════════════════
# 5. 역사 보존 — 실제 mock 저장소의 옛 버전/후보 행은 그대로다
# ══════════════════════════════════════════════════════════════════════════


def test_unpin_preserves_old_version_and_candidates_in_store(storage, monkeypatch):
    """하네스 없이 실제 mock 저장소로 — retry 는 실행 행 밖을 일절 만지지 않는다."""
    seed_intake()
    started = _start(key="retry-stale:history")

    old_version_row = {
        "id": "7fix0000-0000-4000-8000-000000000601",
        "pet_id": PET,
        "user_id": USER,
        "motion_id": "BREATHING",
        "version": 1,
        "status": "failed",
        "motion_spec_version": OLD_SPEC,
        "selected_candidate_id": None,
    }
    old_candidate_row = {
        "id": "7fix0000-0000-4000-8000-000000000602",
        "motion_version_id": old_version_row["id"],
        "pet_id": PET,
        "user_id": USER,
        "motion_id": "BREATHING",
        "provider": "seedance",
        "attempt": 1,
        "decision": "ERROR",
        "selected": False,
    }
    motion_video_service._MOCK_VERSIONS.append(dict(old_version_row))
    motion_video_service._MOCK_CANDIDATES.append(dict(old_candidate_row))

    row = next(r for r in runs._MOCK_RUNS if r["id"] == started.id)
    row.update(
        {
            "status": runs.STATUS_FAILED,
            "current_stage": runs.STAGE_QA,
            "motion_version_id": old_version_row["id"],
            "motion_version": 1,
            "selected_candidate_id": None,
            "last_error": {"code": "MOTION_QA_FAILED", "stage": "QA"},
        }
    )

    requeued = _retry(started.id)
    assert requeued.motion_version_id is None
    assert requeued.current_stage == runs.STAGE_MOTION_SPEC

    # 옛 계보는 바이트 단위로 그대로다 — 삭제도 수정도 없다.
    stored_versions = [v for v in motion_video_service._MOCK_VERSIONS if v["id"] == old_version_row["id"]]
    stored_candidates = [c for c in motion_video_service._MOCK_CANDIDATES if c["id"] == old_candidate_row["id"]]
    assert stored_versions == [old_version_row]
    assert stored_candidates == [old_candidate_row]
