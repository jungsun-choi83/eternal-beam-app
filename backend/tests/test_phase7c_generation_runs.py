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
    motion_publication_service,
    motion_spec,
    motion_video_service,
    pet_generation_run_service as runs,
    pet_identity_service,
    pet_reference_service,
    pet_reference_set_service,
    pet_registry,
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
    def __init__(self, monkeypatch, *, motion_status="complete", fail_reference_once=False):
        self.calls = []
        self.expose_latest = False
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
        self.canonical = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000401",
            pet_id=PET,
            user_id=USER,
            version=1,
            status=canonical_pet_service.STATUS_COMPLETE,
            reference_set_id=self.refset.id,
            reference_set_version=1,
        )
        self.keyframe = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000501",
            pet_id=PET,
            user_id=USER,
            keyframe_role="NEUTRAL_IDLE",
            version=1,
            status=action_keyframe_service.STATUS_COMPLETE,
            canonical_version_id=self.canonical.id,
            canonical_version=self.canonical.version,
            selected_candidate_id="00000000-0000-0000-0000-000000000502",
        )
        selected = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000602",
            selected=True,
            decision="PASS",
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
    assert harness.calls.index("identity") < harness.calls.index("reference_set")
    assert harness.calls.index("canonical") < harness.calls.index("keyframe")
    assert harness.calls.index("motion_spec") < harness.calls.index("motion")
    assert harness.calls.index("motion") < harness.calls.index("publication")


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

