"""Phase 7C durable orchestration tests. Every paid/provider boundary is mocked."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import generation_runs_v1
from backend.services import (
    action_keyframe_service,
    canonical_pet_service,
    durable_provider_jobs,
    motion_publication_service,
    motion_spec,
    motion_video_service,
    pet_generation_run_service as runs,
    pet_identity_service,
    pet_reference_service,
    pet_reference_set_service,
    pet_registry,
    qa_shadow_telemetry,
)

from .conftest import ASGITestClient, make_jpeg_bytes

USER = "alice@test"
CID = "phase7c"
PET = f"pet_{CID}"


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

    uploads = []

    async def upload(path, data, content_type):
        uploads.append(path)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", upload)
    return uploads


def seed_intake(*, cutout=True):
    original = _run(
        pet_reference_service.record_original(
            user_id=USER,
            content_id=CID,
            data=make_jpeg_bytes(),
            mime_type="image/jpeg",
        )
    )
    if cutout:
        _run(
            pet_reference_service.record_derived(
                user_id=USER,
                content_id=CID,
                object_path=f"{USER}/{CID}/references/cutout_{original.content_hash[:16]}.png",
                derived_kind="cutout_reference",
                parent_reference_id=original.id,
                mime_type="image/png",
            )
        )
    return original


class PipelineHarness:
    def __init__(
        self,
        monkeypatch,
        *,
        motion_status="complete",
        canonical_status="complete",
        keyframe_status="complete",
        fail_reference_once=False,
    ):
        self.calls = []
        self.expose_latest = False
        #: keyframe_build 로 넘어간 kwargs 전부 — allow_canonical_reuse 같은
        #: 호출부 전용 플래그를 검증하려면 calls(문자열 목록)로는 부족하다.
        self.keyframe_build_calls: list[dict] = []
        self.counts = {
            "identity_build": 0,
            "reference_build": 0,
            "canonical_build": 0,
            "keyframe_build": 0,
            "motion_build": 0,
            "delivery": 0,
            "publication": 0,
        }
        self.fail_reference_once = fail_reference_once
        self.profile = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000201",
            pet_id=PET,
            user_id=USER,
            version=1,
            status=pet_identity_service.STATUS_COMPLETE,
        )
        self.refset = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000301",
            pet_id=PET,
            user_id=USER,
            version=1,
            status=pet_reference_set_service.STATUS_COMPLETE,
            identity_profile_id=self.profile.id,
            identity_profile_version=1,
        )
        canonical_candidate = SimpleNamespace(id="00000000-0000-0000-0000-000000000402", selected=(canonical_status == "complete"))
        self.canonical = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000401",
            pet_id=PET,
            user_id=USER,
            version=1,
            status=canonical_status,
            reference_set_id=self.refset.id,
            reference_set_version=1,
            candidates=[canonical_candidate],
        )
        keyframe_candidate = SimpleNamespace(id="00000000-0000-0000-0000-000000000502", selected=(keyframe_status == "complete"))
        self.keyframe = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000501",
            pet_id=PET,
            user_id=USER,
            keyframe_role="NEUTRAL_IDLE",
            version=1,
            status=keyframe_status,
            canonical_version_id=self.canonical.id,
            canonical_version=self.canonical.version,
            selected_candidate_id=(keyframe_candidate.id if keyframe_status == "complete" else None),
            candidates=[keyframe_candidate],
        )
        selected = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000602",
            selected=True,
            decision="PASS",
            generation_metadata={
                "provider_identity": {
                    "logical_model": "minimax_h3_max_turbo",
                    "vendor": "fal",
                    "vendor_model": "minimax/h3-max-turbo/image-to-video",
                    "adapter": "FalMinimaxH3MaxTurboProvider",
                }
            },
        )
        self.motion = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000601",
            pet_id=PET,
            user_id=USER,
            motion_id="BREATHING",
            motion_spec_version=motion_spec.MOTION_SPEC_VERSION,
            start_keyframe_id=self.keyframe.id,
            start_keyframe_version=self.keyframe.version,
            canonical_version_id=self.canonical.id,
            version=1,
            status=motion_status,
            selected_candidate_id=(selected.id if motion_status == "complete" else None),
            candidates=([selected] if motion_status == "complete" else []),
        )
        self.publication = SimpleNamespace(
            publication_id="00000000-0000-0000-0000-000000000701",
            selected_candidate_id=selected.id,
        )

        async def identity_build(**kwargs):
            self._capture("identity", kwargs)
            self.counts["identity_build"] += 1
            return self.profile

        async def identity_get(**kwargs):
            self._capture("identity_get", kwargs)
            return self.profile

        async def reference_build(**kwargs):
            self._capture("reference_set", kwargs)
            self.counts["reference_build"] += 1
            if self.fail_reference_once and self.counts["reference_build"] == 1:
                raise pet_reference_set_service.PetReferenceSetError(
                    "REFERENCE_TEMPORARY", "temporary", status=503
                )
            return self.refset

        async def reference_get(**kwargs):
            self._capture("reference_get", kwargs)
            return self.refset

        async def canonical_get(**kwargs):
            self._capture("canonical_get", kwargs)
            return self.canonical if kwargs.get("version") or self.expose_latest else None

        async def canonical_build(**kwargs):
            self._capture("canonical", kwargs)
            self.counts["canonical_build"] += 1
            return self.canonical

        async def keyframe_get(**kwargs):
            self._capture("keyframe_get", kwargs)
            return self.keyframe if kwargs.get("version") or self.expose_latest else None

        async def keyframe_build(**kwargs):
            self._capture("keyframe", kwargs)
            self.keyframe_build_calls.append(dict(kwargs))
            self.counts["keyframe_build"] += 1
            return self.keyframe

        async def resolve_spec(**kwargs):
            self._capture("motion_spec", kwargs)
            return {
                "motion_id": "BREATHING",
                "motion_spec_version": motion_spec.MOTION_SPEC_VERSION,
                "start_keyframe": {"keyframe_id": self.keyframe.id, "version": 1},
                "canonical_version_id": self.canonical.id,
            }

        async def motion_get(**kwargs):
            self._capture("motion_get", kwargs)
            return self.motion if kwargs.get("version") or self.expose_latest else None

        async def motion_build(**kwargs):
            self._capture("motion", kwargs)
            self.counts["motion_build"] += 1
            return self.motion

        async def publish(**kwargs):
            self._capture("publication", kwargs)
            self.counts["publication"] += 1
            return self.publication

        async def package(**kwargs):
            # Phase 7G — 발행 전 packed-alpha 포장 (Phase 7F). 테마 금지 계약은
            # 다른 단계와 동일하게 검사된다.
            self._capture("delivery", kwargs)
            self.counts["delivery"] += 1
            return SimpleNamespace(
                motion_version_id=kwargs.get("motion_version_id"),
                candidate_id=kwargs.get("candidate_id"),
                delivery_format="packed_alpha",
                deduplicated=False,
            )

        monkeypatch.setattr(pet_identity_service, "build_identity_profile", identity_build)
        monkeypatch.setattr(pet_identity_service, "get_profile", identity_get)
        monkeypatch.setattr(pet_reference_set_service, "build_reference_set", reference_build)
        monkeypatch.setattr(pet_reference_set_service, "get_set", reference_get)
        monkeypatch.setattr(canonical_pet_service, "get_canonical", canonical_get)
        monkeypatch.setattr(canonical_pet_service, "build_canonical", canonical_build)
        monkeypatch.setattr(action_keyframe_service, "get_keyframe", keyframe_get)
        monkeypatch.setattr(action_keyframe_service, "build_keyframe", keyframe_build)
        monkeypatch.setattr(motion_spec, "resolve_video_generation_spec", resolve_spec)
        monkeypatch.setattr(motion_video_service, "get_motion_version", motion_get)
        monkeypatch.setattr(motion_video_service, "build_motion_video", motion_build)
        monkeypatch.setattr(motion_publication_service, "publish_breathing", publish)
        from backend.services import motion_delivery_service

        monkeypatch.setattr(
            motion_delivery_service, "package_breathing_for_delivery", package
        )

    def _capture(self, stage, kwargs):
        assert kwargs.get("user_id") == USER
        assert kwargs.get("pet_id") == PET
        forbidden = {"theme_id", "theme", "background", "background_image", "scene", "baked_scene"}
        assert forbidden.isdisjoint(kwargs)
        self.calls.append(stage)


def start(key="upload:phase7c"):
    return _run(
        runs.start_generation_run(
            user_id=USER,
            pet_id=PET,
            motion_id="BREATHING",
            request_kind="FREE_HOME",
            idempotency_key=key,
        )
    )


def work():
    return _run(runs.process_next_generation_run(worker_id="phase7c-test-worker"))


def test_incomplete_intake_is_rejected_without_a_run(storage):
    seed_intake(cutout=False)
    with pytest.raises(runs.PetGenerationRunError) as error:
        start()
    assert error.value.code == "PHASE1_INTAKE_INCOMPLETE"
    assert runs._MOCK_RUNS == []


def test_happy_path_persists_full_lineage_and_reaches_mocked_phase7a(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)

    queued = start()
    assert queued.status == runs.STATUS_QUEUED
    result = work()

    assert result.status == runs.STATUS_PUBLISHED
    assert result.current_stage == runs.STAGE_PUBLISHED
    assert result.content_id == CID
    assert result.identity_profile_id == harness.profile.id
    assert result.reference_set_id == harness.refset.id
    assert result.canonical_version_id == harness.canonical.id
    assert result.keyframes["NEUTRAL_IDLE"]["id"] == harness.keyframe.id
    assert result.motion_spec_version == motion_spec.MOTION_SPEC_VERSION
    assert result.motion_version_id == harness.motion.id
    assert result.motion_version == 1
    assert result.selected_candidate_id == harness.motion.selected_candidate_id
    assert result.publication_id == harness.publication.publication_id
    assert result.provider_state["_business_qa"]["version"] == "business-v1"
    assert result.provider_state["_business_qa"]["terminal_state"] == "DELIVERED_GENERATED"
    assert result.provider_state["_business_qa"]["provider_identity"] == {
        "logical_model": "minimax_h3_max_turbo",
        "vendor": "fal",
        "vendor_model": "minimax/h3-max-turbo/image-to-video",
        "adapter": "FalMinimaxH3MaxTurboProvider",
    }
    assert harness.calls.index("identity") < harness.calls.index("reference_set")
    assert harness.calls.index("canonical") < harness.calls.index("keyframe")
    assert harness.calls.index("motion_spec") < harness.calls.index("motion")
    assert harness.calls.index("motion") < harness.calls.index("publication")


def test_shadow_telemetry_observes_pipeline_without_extra_paid_work(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)

    queued = start()
    result = work()

    assert result.status == runs.STATUS_PUBLISHED
    assert harness.counts["canonical_build"] == 1
    assert harness.counts["keyframe_build"] == 1
    assert harness.counts["motion_build"] == 1
    assert harness.counts["publication"] == 1

    rows = qa_shadow_telemetry.rows_for_run(queued.id)
    stages = {row["stage"] for row in rows}
    assert {"RUN", "CANONICAL", "KEYFRAME", "MOTION"} <= stages
    aggregate = next(row for row in rows if row["stage"] == "RUN")
    assert aggregate["fallback_used"] is False
    assert aggregate["metadata"]["terminal_state"] == "DELIVERED_GENERATED"
    assert aggregate["timings"]["upload_to_cutout_ms"] is not None
    assert aggregate["timings"]["queue_wait_ms"] is not None
    assert aggregate["timings"]["worker_claim_ms"] >= 0
    assert aggregate["timings"]["total_request_ms"] is not None
    assert {
        "CANONICAL", "KEYFRAMES", "MOTION_GENERATION", "QA", "PUBLICATION"
    } <= set(aggregate["timings"]["stages"])
    assert aggregate["vlm_call_count"] == 0


def test_motion_build_reuses_stage_motion_spec_contract(storage, monkeypatch):
    """
    STAGE_MOTION_SPEC 에서 이미 해석/검증한 Phase 5.1 계약이 build_motion_video
    에 그대로 전달된다 — 모션 준비 지연의 확인된 병목(같은 계약을 두 번 해석)을
    없앤 회귀 가드. resolve_video_generation_spec("motion_spec" 단계)은 런당
    정확히 한 번만 호출된다.
    """
    seed_intake()
    h = PipelineHarness(monkeypatch)

    captured: dict = {}

    async def motion_build_capture(**kwargs):
        h._capture("motion", kwargs)
        captured.update(kwargs)
        h.counts["motion_build"] += 1
        return h.motion

    monkeypatch.setattr(motion_video_service, "build_motion_video", motion_build_capture)

    start()
    result = work()

    assert result.status == runs.STATUS_PUBLISHED
    assert h.calls.count("motion_spec") == 1
    assert captured.get("precomputed_contract") is not None
    assert captured["precomputed_contract"]["start_keyframe"]["keyframe_id"] == h.keyframe.id
    assert captured["precomputed_contract"]["motion_id"] == "BREATHING"


def test_motion_wait_resume_skips_upstream_and_survives_lease_loss(storage, monkeypatch):
    """
    확인된 문제의 회귀 가드: MOTION_GENERATION 에서 WAITING_PROVIDER 로 멈춘
    실행이 (다른 워커에게) 재개되면 _execute() 는 IDENTITY 부터 다시 돌지
    않는다 — 이미 핀된 Identity/Reference Set/Canonical/Keyframe/Motion Spec
    은 다시 읽지도 검증하지도 않고 곧장 Motion 단계에서 재개한다. 재개가
    다른 worker_id 로 일어나도(리스 유실 복구) 동일하다.
    """
    monkeypatch.setenv("GENERATION_PROVIDER_POLL_SECONDS", "0")
    seed_intake()
    h = PipelineHarness(monkeypatch)

    motion_attempts = {"n": 0}

    async def motion_build_pending_once(**kwargs):
        h._capture("motion", kwargs)
        motion_attempts["n"] += 1
        h.counts["motion_build"] += 1
        if motion_attempts["n"] == 1:
            raise durable_provider_jobs.ProviderWorkPending("motion-op-1", "PENDING")
        return h.motion

    monkeypatch.setattr(motion_video_service, "build_motion_video", motion_build_pending_once)

    queued = start()
    assert queued.status == runs.STATUS_QUEUED

    waiting = _run(runs.process_next_generation_run(worker_id="worker-a"))
    assert waiting.status == runs.STATUS_WAITING_PROVIDER
    assert waiting.current_stage == runs.STAGE_MOTION_GENERATION
    # 첫 틱은 파이프라인 전체를 정상 순서로 한 번씩 돈다 (fresh run 동작 불변).
    for name in ("identity", "reference_set", "canonical", "keyframe", "motion_spec"):
        assert h.calls.count(name) == 1
    assert motion_attempts["n"] == 1
    calls_after_wait = list(h.calls)

    # 다른 워커가 재개한다 — 리스 유실/워커 교체 복구 경로와 동일한 모양.
    resumed = _run(runs.process_next_generation_run(worker_id="worker-b"))
    assert resumed.status == runs.STATUS_PUBLISHED
    assert motion_attempts["n"] == 2  # motion 만 다시 시도된다 (재제출이 아니라 재시도)

    new_calls = h.calls[len(calls_after_wait):]
    # 재개 틱에서 상류 4단계는 전혀 다시 불리지 않는다 — 읽기(get)든 빌드든.
    for name in (
        "identity", "identity_get",
        "reference_set", "reference_get",
        "canonical", "canonical_get",
        "keyframe", "keyframe_get",
    ):
        assert name not in new_calls, f"{name} should not re-run on resume, saw {new_calls}"
    # MOTION_SPEC 계약은 build_motion_video 에 넘기려고 한 번 더 해석되지만
    # (motion_spec_version 자체는 이미 핀돼 있어 재검증/재기록은 하지 않는다),
    # 이는 전체 계보 재검증이 아니라 모션 재개에 필요한 값 하나를 다시
    # 만드는 것뿐이다.
    assert new_calls.count("motion_spec") == 1
    assert h.counts["publication"] == 1
    assert h.counts["delivery"] == 1


def test_inconsistent_persisted_stage_falls_back_to_safe_recovery(storage, monkeypatch):
    """
    안전 요구사항: current_stage 가 상류 단계를 지났다고 말하는데 그 단계의
    핀 필드가 실제로는 비어 있으면(비정상 상태), 그 값을 그대로 믿고 건너뛰지
    않는다 — 처음부터 안전하게 다시 유도한다.
    """
    seed_intake()
    h = PipelineHarness(monkeypatch)

    queued = start()
    assert queued.status == runs.STATUS_QUEUED

    # current_stage 만 MOTION_GENERATION 으로 앞서가게 조작하고, 그 이전 단계
    # 핀은 전부 비워 둔다 — 재현 불가능한/손상된 영속 상태를 흉내낸다.
    _run(
        runs._update(
            queued.id,
            {
                "current_stage": runs.STAGE_MOTION_GENERATION,
                "identity_profile_id": None,
                "identity_profile_version": None,
                "reference_set_id": None,
                "reference_set_version": None,
                "canonical_version_id": None,
                "canonical_version": None,
                "keyframes": {},
                "motion_spec_version": None,
            },
        )
    )

    result = work()

    # 손상된 current_stage 힌트에도 불구하고, 없는 핀은 안전하게 처음부터
    # 다시 유도되어 정상 완주한다 — 조용히 빈 계보로 진행하지 않는다.
    assert result.status == runs.STATUS_PUBLISHED
    assert h.calls.index("identity") < h.calls.index("reference_set")
    assert h.calls.index("reference_set") < h.calls.index("canonical")
    assert h.calls.index("canonical") < h.calls.index("keyframe")
    assert h.calls.index("keyframe") < h.calls.index("motion_spec")
    assert h.calls.index("motion_spec") < h.calls.index("motion")
    assert result.identity_profile_id == h.profile.id
    assert result.canonical_version_id == h.canonical.id


def test_breathing_start_keyframe_requests_canonical_reuse(storage, monkeypatch):
    """BREATHING 의 NEUTRAL_IDLE 시작 키프레임 호출만 Canonical 재사용을 켠다.

    build_keyframe() 자체는 스텁이라 여기서는 재사용 판정 로직을 증명하지
    않는다 — _execute() 가 BREATHING 의 start_keyframe_role 호출에 정확히
    allow_canonical_reuse=True 를 실어 보내는 배선만 증명한다(요구 6의 짝:
    non-BREATHING 은 test_unsellable... / 아래 BLINKING 테스트에서 False).
    """
    seed_intake()
    harness = PipelineHarness(monkeypatch)

    start()
    result = work()

    assert result.status == runs.STATUS_PUBLISHED
    assert len(harness.keyframe_build_calls) == 1
    assert harness.keyframe_build_calls[0]["keyframe_role"] == "NEUTRAL_IDLE"
    assert harness.keyframe_build_calls[0]["allow_canonical_reuse"] is True


def test_same_idempotency_key_returns_same_run_without_duplicate_work(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)

    first = start()
    second = start()
    completed = work()

    assert second.id == first.id
    assert completed.id == first.id
    assert harness.counts["canonical_build"] == 1
    assert harness.counts["keyframe_build"] == 1
    assert harness.counts["motion_build"] == 1
    assert harness.counts["publication"] == 1
    assert len(runs._MOCK_RUNS) == 1


def test_different_idempotency_key_same_scope_joins_existing_active_run(storage, monkeypatch):
    """Duplicate-paid-run protection: two *different* client idempotency_keys
    for the same pet/motion/request_kind must not each create their own run —
    the caller's key is not the locking key, (pet, motion, request_kind) is."""
    seed_intake()
    harness = PipelineHarness(monkeypatch)

    first = start(key="device-a")
    second = start(key="device-b")
    completed = work()

    assert second.id == first.id
    assert second.idempotency_key == first.idempotency_key  # the first writer's key wins
    assert completed.id == first.id
    assert harness.counts["canonical_build"] == 1
    assert harness.counts["keyframe_build"] == 1
    assert harness.counts["motion_build"] == 1
    assert harness.counts["publication"] == 1
    assert len(runs._MOCK_RUNS) == 1


def test_two_simultaneous_requests_produce_one_run_and_one_paid_submission(storage, monkeypatch):
    """Genuine concurrency, not just sequential calls: the second request's
    start_generation_run() call is forced to block on the per-scope lock
    while the first is still mid-insert, proving the lock (not luck) is what
    prevents two rows — and therefore two workers each paying for the same
    canonical/keyframe/motion build — from ever existing."""
    seed_intake()
    harness = PipelineHarness(monkeypatch)

    entered = anyio.Event()
    hold = anyio.Event()
    original_insert_or_get = runs._insert_or_get
    attempts: list[str] = []

    async def guarded_insert_or_get(row):
        attempts.append(row["idempotency_key"])
        if len(attempts) == 1:
            entered.set()
            await hold.wait()
        return await original_insert_or_get(row)

    monkeypatch.setattr(runs, "_insert_or_get", guarded_insert_or_get)

    results: dict[str, runs.PetGenerationRun] = {}

    async def go(key: str) -> None:
        results[key] = await runs.start_generation_run(
            user_id=USER, pet_id=PET, motion_id="BREATHING",
            request_kind="FREE_HOME", idempotency_key=key,
        )

    async def scenario() -> None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(go, "device-a")
            await entered.wait()
            # device-a is now blocked *inside* the locked section — device-b
            # must queue on the lock, not race ahead of it.
            tg.start_soon(go, "device-b")
            await anyio.sleep(0.01)
            hold.set()

    anyio.run(scenario)

    assert attempts == ["device-a", "device-b"]  # device-b only ran after device-a released the lock
    assert results["device-a"].id == results["device-b"].id
    assert len(runs._MOCK_RUNS) == 1

    completed = work()
    assert completed.status == runs.STATUS_PUBLISHED
    assert harness.counts["canonical_build"] == 1
    assert harness.counts["keyframe_build"] == 1
    assert harness.counts["motion_build"] == 1
    assert harness.counts["publication"] == 1


def test_different_pet_runs_independently(storage, monkeypatch):
    seed_intake()
    other_cid = f"{CID}-second-pet"
    other_pet = f"pet_{other_cid}"
    other_original = _run(
        pet_reference_service.record_original(
            user_id=USER, content_id=other_cid, data=make_jpeg_bytes(), mime_type="image/jpeg",
        )
    )
    _run(
        pet_reference_service.record_derived(
            user_id=USER,
            content_id=other_cid,
            object_path=f"{USER}/{other_cid}/references/cutout.png",
            derived_kind="cutout_reference",
            parent_reference_id=other_original.id,
            mime_type="image/png",
        )
    )
    PipelineHarness(monkeypatch)

    first = start(key="pet-one")
    second = _run(
        runs.start_generation_run(
            user_id=USER, pet_id=other_pet, motion_id="BREATHING",
            request_kind="FREE_HOME", idempotency_key="pet-two",
        )
    )

    assert second.id != first.id
    assert second.pet_id == other_pet
    assert {row["pet_id"] for row in runs._MOCK_RUNS} == {PET, other_pet}
    assert len(runs._MOCK_RUNS) == 2


def test_failed_terminal_run_does_not_block_a_new_active_run(storage, monkeypatch):
    seed_intake()
    PipelineHarness(monkeypatch, motion_status=motion_video_service.STATUS_FAILED)

    first = start(key="attempt-1")
    failed = work()
    assert failed.status == runs.STATUS_FAILED

    second = start(key="attempt-2")
    assert second.id != first.id
    assert second.status == runs.STATUS_QUEUED
    assert len(runs._MOCK_RUNS) == 2


def test_retry_of_a_superseded_failed_run_joins_the_newer_active_run(storage, monkeypatch):
    """A stale run_id (e.g. an old browser tab) must never resurrect a FAILED
    run into QUEUED once a fresher run already owns this pet's active work —
    that would put two active rows in the same scope back in play."""
    seed_intake()
    PipelineHarness(monkeypatch, motion_status=motion_video_service.STATUS_FAILED)

    stale = start(key="attempt-1")
    failed = work()
    assert failed.status == runs.STATUS_FAILED

    fresh = start(key="attempt-2")
    assert fresh.status == runs.STATUS_QUEUED

    retried = _run(runs.retry_generation_run(user_id=USER, run_id=stale.id))

    assert retried.id == fresh.id
    stale_row = next(r for r in runs._MOCK_RUNS if r["id"] == stale.id)
    assert stale_row["status"] == runs.STATUS_FAILED  # never resurrected
    assert len(runs._MOCK_RUNS) == 2


def test_published_terminal_run_does_not_block_a_new_active_run(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)

    first = start(key="attempt-1")
    published = work()
    assert published.status == runs.STATUS_PUBLISHED

    harness.expose_latest = True
    second = start(key="attempt-2")
    assert second.id != first.id
    assert second.status == runs.STATUS_QUEUED
    assert len(runs._MOCK_RUNS) == 2


def test_new_run_reuses_valid_existing_provider_outputs(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch)

    start(key="first-logical-run")
    first = work()
    harness.expose_latest = True
    start(key="second-logical-run")
    second = work()

    assert second.id != first.id
    assert second.status == runs.STATUS_PUBLISHED
    assert second.canonical_version_id == first.canonical_version_id
    assert second.keyframes == first.keyframes
    assert second.motion_version_id == first.motion_version_id
    assert harness.counts["canonical_build"] == 1
    assert harness.counts["keyframe_build"] == 1
    assert harness.counts["motion_build"] == 1


@pytest.mark.parametrize(
    ("motion_status", "error_code"),
    [(motion_video_service.STATUS_REVIEW, "MOTION_QA_REVIEW"),
     (motion_video_service.STATUS_FAILED, "MOTION_QA_FAILED")],
)
def test_qa_review_or_fail_never_publishes(storage, monkeypatch, motion_status, error_code):
    seed_intake()
    harness = PipelineHarness(monkeypatch, motion_status=motion_status)

    start(key=f"qa:{motion_status}")
    result = work()

    assert result.status == runs.STATUS_FAILED
    assert result.current_stage == runs.STAGE_QA
    assert result.last_error["code"] == error_code
    assert result.motion_version_id == harness.motion.id
    assert result.publication_id is None
    assert harness.counts["publication"] == 0


def test_review_replacement_request_is_queued_once_and_api_does_not_generate(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch, motion_status=motion_video_service.STATUS_REVIEW)
    started = start(key="review-replacement")
    failed = work()
    assert failed.last_error["code"] == "MOTION_QA_REVIEW"
    assert harness.counts["motion_build"] == 1

    queued = _run(
        runs.request_replacement_generation(
            user_id=USER,
            run_id=started.id,
            idempotency_key="replacement:one",
            reason="v2 QA confirms loop but breathing remains unrecognizable",
        )
    )
    assert queued.status == runs.STATUS_QUEUED
    assert queued.motion_version_id is None
    request = queued.provider_state["_operator"]["replacement_request"]
    assert request["source_motion_version_id"] == harness.motion.id
    assert request["status"] == "QUEUED"
    assert harness.counts["motion_build"] == 1  # API only persisted intent.

    duplicate = _run(
        runs.request_replacement_generation(
            user_id=USER,
            run_id=started.id,
            idempotency_key="replacement:one",
            reason="same request",
        )
    )
    assert duplicate.provider_state == queued.provider_state
    assert harness.counts["motion_build"] == 1

    with pytest.raises(runs.PetGenerationRunError) as second:
        _run(
            runs.request_replacement_generation(
                user_id=USER,
                run_id=started.id,
                idempotency_key="replacement:two",
                reason="must not buy twice",
            )
        )
    assert second.value.code == "REPLACEMENT_ALREADY_REQUESTED"


def test_replacement_worker_refuses_to_reuse_review_source(monkeypatch):
    source = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000901",
        pet_id=PET,
        user_id=USER,
        motion_id="BREATHING",
        motion_spec_version=motion_spec.MOTION_SPEC_VERSION,
        start_keyframe_id="00000000-0000-0000-0000-000000000501",
        start_keyframe_version=1,
        canonical_version_id="00000000-0000-0000-0000-000000000401",
        version=1,
        status=motion_video_service.STATUS_REVIEW,
    )
    replacement = SimpleNamespace(
        **{**source.__dict__, "id": "00000000-0000-0000-0000-000000000902", "version": 2}
    )
    captured = {}

    async def get_motion(**kwargs):
        return source

    async def build_motion(**kwargs):
        captured.update(kwargs)
        return replacement

    monkeypatch.setattr(motion_video_service, "get_motion_version", get_motion)
    monkeypatch.setattr(motion_video_service, "build_motion_video", build_motion)
    monkeypatch.setattr(runs, "_video_providers", lambda *args: [object()])
    run = runs.PetGenerationRun(
        id="00000000-0000-0000-0000-000000000900",
        user_id=USER,
        pet_id=PET,
        content_id=CID,
        motion_id="BREATHING",
        request_kind="FREE_HOME",
        idempotency_key="replacement-worker",
        status=runs.STATUS_RUNNING,
        current_stage=runs.STAGE_MOTION_GENERATION,
        canonical_version_id=source.canonical_version_id,
        keyframes={"NEUTRAL_IDLE": {"id": source.start_keyframe_id, "version": 1}},
        motion_spec_version=motion_spec.MOTION_SPEC_VERSION,
        provider_state={"_operator": {"replacement_request": {"source_motion_version_id": source.id}}},
    )
    result, _ = _run(runs._motion(run))
    assert result.id == replacement.id
    assert captured["skip_if_unchanged"] is False


def test_stage_failure_is_durable_and_retry_reuses_earlier_lineage(storage, monkeypatch):
    seed_intake()
    harness = PipelineHarness(monkeypatch, fail_reference_once=True)

    start(key="retryable")
    failed = work()
    assert failed.status == runs.STATUS_FAILED
    assert failed.current_stage == runs.STAGE_REFERENCE_SET
    assert failed.identity_profile_id == harness.profile.id
    assert failed.reference_set_id is None

    retried_queued = _run(runs.retry_generation_run(user_id=USER, run_id=failed.id))
    assert retried_queued.status == runs.STATUS_QUEUED
    retried = work()
    assert retried.status == runs.STATUS_PUBLISHED
    assert retried.retry_count == 1
    assert retried.identity_profile_id == failed.identity_profile_id
    assert harness.counts["identity_build"] == 1
    assert harness.counts["reference_build"] == 2
    assert harness.counts["canonical_build"] == 1
    assert harness.counts["motion_build"] == 1
    assert harness.counts["publication"] == 1


def test_wrong_user_is_rejected_before_orchestration(storage, monkeypatch):
    seed_intake()
    PipelineHarness(monkeypatch)
    with pytest.raises(runs.PetGenerationRunError) as error:
        _run(
            runs.start_generation_run(
                user_id="mallory@test",
                pet_id=PET,
                idempotency_key="wrong-user",
            )
        )
    assert error.value.code == "PET_NOT_OWNED"
    assert runs._MOCK_RUNS == []


def test_authenticated_single_run_api(storage, monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    seed_intake()
    PipelineHarness(monkeypatch)
    app = FastAPI()
    app.include_router(generation_runs_v1.router, prefix="/api")
    client = ASGITestClient(app)
    auth = {"Authorization": f"Bearer test:{USER}"}

    created = client.post(
        "/api/v1/pet/generation-runs",
        headers=auth,
        json={"pet_id": PET, "idempotency_key": "api-one"},
    )
    assert created.status_code == 202
    assert created.json()["status"] == "QUEUED"
    run_id = created.json()["run_id"]

    fetched = client.get(f"/api/v1/pet/generation-runs/{run_id}", headers=auth)
    assert fetched.status_code == 200
    assert fetched.json()["run_id"] == run_id
    assert fetched.json()["status"] == "QUEUED"

    themed = client.post(
        "/api/v1/pet/generation-runs",
        headers=auth,
        json={"pet_id": PET, "idempotency_key": "theme-rejected", "theme_id": "forest"},
    )
    assert themed.status_code == 422


def test_only_breathing_free_home_are_supported(storage):
    seed_intake()
    with pytest.raises(runs.PetGenerationRunError) as motion_error:
        _run(
            runs.start_generation_run(
                user_id=USER, pet_id=PET, motion_id="RUN", idempotency_key="unsupported-motion"
            )
        )
    assert motion_error.value.code == "UNSUPPORTED_MOTION"

    with pytest.raises(runs.PetGenerationRunError) as kind_error:
        _run(
            runs.start_generation_run(
                user_id=USER,
                pet_id=PET,
                request_kind="PREMIUM",
                idempotency_key="unsupported-kind",
            )
        )
    assert kind_error.value.code == "UNSUPPORTED_REQUEST_KIND"


def test_migration_persists_lineage_and_uses_an_atomic_claim():
    root = Path(__file__).resolve().parents[2]
    migration = root / "supabase/migrations/20261018000000_pet_generation_runs.sql"
    sql = migration.read_text()

    for field in (
        "identity_profile_id",
        "reference_set_id",
        "canonical_version_id",
        "keyframes jsonb",
        "motion_spec_version",
        "motion_version_id",
        "selected_candidate_id",
        "publication_id",
        "provider_state jsonb",
        "last_error jsonb",
        "retry_count int",
    ):
        assert field in sql
    assert "unique (user_id, pet_id, motion_id, request_kind, idempotency_key)" in sql
    assert "create or replace function public.claim_pet_generation_run" in sql
    assert "for update" in sql
    assert "to service_role" in sql
