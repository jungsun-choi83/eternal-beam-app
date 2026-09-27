"""
정본 펫 빌더 (Phase 4) 계약 테스트.

프로바이더는 전부 가짜다 — 유닛 테스트에서 실 결제 호출은 절대 없다.
QA/선택/버전/근거 로직은 실제 코드로 검증한다 (합성 이미지 + 주입된 VLM 결과).
"""

from __future__ import annotations

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import assets as assets_router
from backend.routers import canonical_v1
from backend.services import canonical_image_providers as providers_mod
from backend.services import canonical_pet_service as svc
from backend.services import canonical_qa
from backend.services import durable_provider_jobs
from backend.services import pet_identity_service as ids
from backend.services import pet_morphology_service as morph
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry, vlm_identity
from backend.services.canonical_image_providers import (
    CanonicalImageProvider,
    CanonicalImageResult,
    CanonicalProviderError,
)

from .conftest import ASGITestClient, make_jpeg_bytes
from .test_pet_identity_profile import make_pet_cutout_png, make_striped_cutout_png
from .test_pet_reference_sets import PET, USER, Harness, cls


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    # 합성 이미지(200×150)가 실사 해상도 게이트에 걸리지 않도록 낮춘다.
    monkeypatch.setenv("CANONICAL_QA_MIN_RESOLUTION", "100")
    for m in (refs, pet_registry, ids, morph, sets, svc):
        m.__reset_for_tests()
    durable_provider_jobs.__reset_for_tests()
    yield
    for m in (refs, pet_registry, ids, morph, sets, svc):
        m.__reset_for_tests()
    durable_provider_jobs.__reset_for_tests()


@pytest.fixture
def uploads(monkeypatch) -> list[str]:
    from backend.services import supabase_assets

    paths: list[str] = []

    async def fake_upload(path, data, content_type):
        paths.append(path)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    return paths


def _run(coro):
    return anyio.run(lambda: coro)


VLM_QA_OK = {
    "same_pet": "yes",
    "same_pet_confidence": "high",
    "anatomy_plausible": "yes",
    "single_pet": "yes",
    "human_present": "no",
    "accessories_present": "no",
    "background_neutral": "yes",
    "pose_neutral": "yes",
    "full_body_visible": "yes",
    "major_occlusion": "no",
    "identity_notes": "",
    "source": vlm_identity.VLM_CANONICAL_QA_VERSION,
    "model": "test-stub",
}


def install_vlm_qa(monkeypatch, result):
    monkeypatch.setattr(
        vlm_identity, "qa_canonical_image", lambda candidate, references, candidate_mime="image/png": result
    )


class FakeProvider(CanonicalImageProvider):
    """결정된 이미지 시퀀스를 돌려주는 가짜 프로바이더. 호출 수를 센다."""

    def __init__(self, name: str, images: list | None = None, model: str = "fake-1"):
        self.name = name
        self._images = list(images or [])
        self._model = model
        self.calls = 0

    def available(self) -> bool:
        return True

    def model_name(self) -> str:
        return self._model

    def generate(self, references, prompt, output_spec, metadata):
        self.calls += 1
        if not self._images:
            raise CanonicalProviderError("PROVIDER_FAILED", "no more fake images")
        item = self._images.pop(0)
        if isinstance(item, Exception):
            raise item
        return CanonicalImageResult(
            image_bytes=item,
            provider=self.name,
            model=self._model,
            external_job_id=f"{self.name}-job-{self.calls}",
        )


def _seed_three_ref_pet(monkeypatch) -> Harness:
    """FACE + FULL_BODY + 3Q 커버리지의 펫 (Phase 3 하네스 재사용)."""
    h = Harness()
    h.seed(cutout=make_pet_cutout_png(), classification=cls(view="FRONT", face_visible="yes"))
    h.seed(
        cutout=make_pet_cutout_png(),
        classification=cls(view="LEFT", full_body_visible="yes", tail_visible="yes"),
    )
    h.seed(
        cutout=make_pet_cutout_png(),
        classification=cls(view="FRONT_RIGHT_3Q", full_body_visible="yes"),
    )
    h.install_vlm(monkeypatch)
    return h


def _build(h: Harness, providers, **kw):
    return _run(
        svc.build_canonical(
            user_id=USER,
            pet_id=PET,
            fetch_bytes=h.fetch,
            providers=providers,
            cutout_fn=lambda raw: raw,  # 가짜 프로바이더가 RGBA PNG 를 내므로 그대로 누끼
            **kw,
        )
    )


GOOD = make_pet_cutout_png  # 시드 누끼와 같은 코트 → 신원 시그니처 일치


def test_durable_builder_resumes_one_building_version(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "1")
    monkeypatch.setenv("CANONICAL_STOP_AFTER_PASSES", "1")

    class YieldOnce(FakeProvider):
        durable_execution = True

        def generate(self, references, prompt, output_spec, metadata):
            self.calls += 1
            if self.calls == 1:
                raise durable_provider_jobs.ProviderWorkPending("operation-1", "PENDING")
            return CanonicalImageResult(
                image_bytes=GOOD(), provider=self.name, model=self.model_name(),
                external_job_id="job-1",
            )

    provider = YieldOnce("runway")
    with pytest.raises(durable_provider_jobs.ProviderWorkPending):
        _build(h, [provider])
    rows = _run(svc._version_rows(PET))
    assert len(rows) == 1 and rows[0]["status"] == svc.STATUS_BUILDING

    completed = _build(h, [provider])
    assert completed.status == svc.STATUS_COMPLETE
    assert len(_run(svc._version_rows(PET))) == 1
    assert len(completed.candidates) == 1


class _DurableJobProvider(FakeProvider):
    """유료 생성은 attempt 당 한 번만 — 재호출은 같은 영수증을 재사용한다.

    실제 DurableImageProvider 는 (phase_version_id, attempt, fingerprint) 로
    중복 제출을 막는다; 이 더블은 그 계약을 `submissions` 카운터로 흉내 낸다
    (calls = generate() 호출 수, submissions = 실제 신규 결제 수).
    """

    durable_execution = True

    def __init__(self, name: str, image: bytes):
        super().__init__(name, [])
        self._image = image
        self.submissions = 0
        self._job_ids: dict[int, str] = {}

    def generate(self, references, prompt, output_spec, metadata):
        self.calls += 1
        attempt = metadata.get("attempt")
        if attempt not in self._job_ids:
            self.submissions += 1
            self._job_ids[attempt] = f"{self.name}-job-{attempt}"
        return CanonicalImageResult(
            image_bytes=self._image, provider=self.name, model=self.model_name(),
            external_job_id=self._job_ids[attempt],
        )


def test_storage_failure_recovers_same_candidate_no_resubmission(uploads, monkeypatch):
    """유료 provider 결과 이후 raw 저장만 실패해도 같은 후보가 재사용된다."""
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "2")
    monkeypatch.setenv("CANONICAL_STOP_AFTER_PASSES", "1")

    from backend.services import supabase_assets

    upload_calls = {"n": 0}

    async def flaky_upload(path, data, content_type):
        upload_calls["n"] += 1
        if upload_calls["n"] == 1:
            raise RuntimeError("storage down")
        uploads.append(path)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", flaky_upload)

    provider = _DurableJobProvider("runway", GOOD())

    with pytest.raises(durable_provider_jobs.ProviderRecoveryRequired):
        _build(h, [provider])

    # 1) 유료 생성은 정확히 한 번 — 저장 실패는 같은 후보에 기록되고, 버전은
    #    BUILDING 으로 남아 재개 가능하다 (다음 유료 후보로 넘어가지 않는다).
    assert provider.calls == 1
    assert provider.submissions == 1
    rows = _run(svc._version_rows(PET))
    assert len(rows) == 1 and rows[0]["status"] == svc.STATUS_BUILDING
    cands = _run(svc._candidate_rows(str(rows[0]["id"])))
    assert len(cands) == 1
    assert cands[0]["decision"] == "ERROR"
    assert cands[0]["error"] == "RAW_STORE_FAILED"
    assert cands[0]["external_job_id"] == "runway-job-1"  # provider job id 보존
    assert not cands[0].get("raw_object_path")

    # 2) 재시작/재개 — 같은 candidate/영수증을 재사용해 저장만 재시도한다.
    completed = _build(h, [provider])
    assert completed.status == svc.STATUS_COMPLETE
    assert provider.calls == 2        # 영수증 재사용을 위한 재호출
    assert provider.submissions == 1  # 새 결제는 없다 — 재제출 없음 증거
    assert len(completed.candidates) == 1
    assert completed.candidates[0].external_job_id == "runway-job-1"
    assert len(_run(svc._version_rows(PET))) == 1  # 새 버전/후보 없음


def test_repeated_storage_failure_ends_recoverable_without_extra_paid_generation(uploads, monkeypatch):
    """저장이 계속 실패해도 재시도마다 유료 후보가 늘지 않고 깨끗하게 recoverable 상태에 머문다."""
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "2")

    from backend.services import supabase_assets

    async def always_fails(path, data, content_type):
        raise RuntimeError("storage down")

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", always_fails)

    provider = _DurableJobProvider("runway", GOOD())

    for _ in range(3):
        with pytest.raises(durable_provider_jobs.ProviderRecoveryRequired):
            _build(h, [provider])

    assert provider.submissions == 1  # 반복 실패해도 새 유료 후보로 넘어가지 않는다
    rows = _run(svc._version_rows(PET))
    assert len(rows) == 1 and rows[0]["status"] == svc.STATUS_BUILDING
    cands = _run(svc._candidate_rows(str(rows[0]["id"])))
    assert len(cands) == 1  # 후보가 늘어나지 않았다


def test_normal_generation_path_unaffected_by_storage_recovery_fix(uploads, monkeypatch):
    """저장이 정상일 때는 기존 성공 경로가 그대로다 — 회귀 없음."""
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    v = _build(h, [FakeProvider("runway", [GOOD()])])
    assert v.status == svc.STATUS_COMPLETE
    sel = next(c for c in v.candidates if c.selected)
    assert sel.error is None
    assert sel.raw_object_path in uploads


# ══════════════════════════════════════════════════════════════════════════
# Canonical postprocessing latency (raw storage / matting / VLM QA overlap)
# ══════════════════════════════════════════════════════════════════════════


def _build_direct(h: Harness, providers, *, cutout_fn, **kw):
    return _run(
        svc.build_canonical(
            user_id=USER,
            pet_id=PET,
            fetch_bytes=h.fetch,
            providers=providers,
            cutout_fn=cutout_fn,
            **kw,
        )
    )


def test_postprocessing_branches_overlap_and_still_completes(uploads, monkeypatch):
    """
    raw storage / matting-cutout / VLM QA prep start together once image bytes
    exist; cutout upload overlaps clean-plate construction after matting.
    Proven by wall-clock: each instrumented step sleeps DELAY, and if they
    ran sequentially the whole build would take >= 5*DELAY (raw, cutout,
    vlm_qa, cutout_upload/plate_build, plate_upload). Overlapped, it must
    take well under that, and the recorded intervals must actually overlap.
    """
    import asyncio
    import time

    from backend.services import clean_plate_service, supabase_assets, vlm_identity

    h = _seed_three_ref_pet(monkeypatch)
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "1")
    monkeypatch.setenv("CANONICAL_STOP_AFTER_PASSES", "1")

    DELAY = 0.12
    intervals: dict[str, list[float]] = {}

    def _start(name: str) -> None:
        intervals.setdefault(name, [None, None])[0] = time.monotonic()

    def _end(name: str) -> None:
        intervals[name][1] = time.monotonic()

    async def slow_upload(path, data, content_type):
        label = (
            "raw" if path.endswith("_raw.png")
            else "cutout" if path.endswith("_cutout.png")
            else "plate"
        )
        _start(f"upload_{label}")
        await asyncio.sleep(DELAY)
        _end(f"upload_{label}")
        uploads.append(path)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", slow_upload)

    def slow_cutout(raw_bytes):
        _start("cutout")
        time.sleep(DELAY)
        _end("cutout")
        return raw_bytes  # 가짜 프로바이더가 이미 RGBA PNG 를 낸다 — 그대로 누끼

    def slow_vlm_qa(candidate, references, candidate_mime="image/png"):
        _start("vlm_qa")
        time.sleep(DELAY)
        _end("vlm_qa")
        return VLM_QA_OK

    monkeypatch.setattr(vlm_identity, "qa_canonical_image", slow_vlm_qa)

    real_build_plate = clean_plate_service.build_clean_plate

    def slow_build_plate(cutout_png):
        _start("plate_build")
        time.sleep(DELAY)
        out = real_build_plate(cutout_png)
        _end("plate_build")
        return out

    monkeypatch.setattr(clean_plate_service, "build_clean_plate", slow_build_plate)

    provider = FakeProvider("runway", [GOOD()])
    t0 = time.monotonic()
    v = _build_direct(h, [provider], cutout_fn=slow_cutout)
    elapsed = time.monotonic() - t0

    # 요구 2: 필요한 모든 분기가 끝나야 완료된다 — 완료됐다는 것 자체가
    # cutout/vlm_qa/uploads 모두 기다렸다는 증거다.
    assert v.status == svc.STATUS_COMPLETE
    sel = next(c for c in v.candidates if c.selected)
    assert sel.error is None

    # 완전 순차라면 >= 5*DELAY (raw, cutout, vlm_qa, cutout_upload 또는
    # plate_build, plate_upload). 겹치면 2~3*DELAY 근방이어야 한다.
    assert elapsed < DELAY * 4, (
        f"postprocessing branches did not overlap: {elapsed:.3f}s "
        f"(sequential would be >= {DELAY * 5:.3f}s)"
    )

    def overlaps(a: str, b: str) -> bool:
        a_start, a_end = intervals[a]
        b_start, b_end = intervals[b]
        return a_start < b_end and b_start < a_end

    assert overlaps("upload_raw", "cutout"), "raw 저장이 매팅과 겹치지 않았다"
    assert overlaps("upload_raw", "vlm_qa"), "raw 저장이 VLM QA 준비와 겹치지 않았다"
    assert overlaps("upload_cutout", "plate_build"), "누끼 업로드가 클린 플레이트 생성과 겹치지 않았다"


def test_postprocessing_reuses_bytes_and_makes_no_duplicate_calls(uploads, monkeypatch):
    """
    요구 5: raw 바이트/누끼 바이트가 재다운로드·재디코딩 없이 재사용되고,
    분기마다 정확히 한 번씩만 일한다 — 중복 제출/중복 업로드가 없다.
    """
    from backend.services import vlm_identity

    h = _seed_three_ref_pet(monkeypatch)
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "1")
    monkeypatch.setenv("CANONICAL_STOP_AFTER_PASSES", "1")

    cutout_calls: list[bytes] = []

    def counting_cutout(raw_bytes):
        cutout_calls.append(raw_bytes)
        return raw_bytes

    vlm_calls: list[tuple[bytes, Any]] = []

    def counting_vlm_qa(candidate, references, candidate_mime="image/png"):
        vlm_calls.append((candidate, references))
        return VLM_QA_OK

    monkeypatch.setattr(vlm_identity, "qa_canonical_image", counting_vlm_qa)

    image = GOOD()
    provider = FakeProvider("runway", [image])
    v = _build_direct(h, [provider], cutout_fn=counting_cutout)

    assert v.status == svc.STATUS_COMPLETE
    assert provider.calls == 1  # 유료 생성은 한 번만

    # 매팅/VLM QA 는 candidate 당 정확히 한 번 — provider 가 이미 돌려준
    # image_bytes 를 그대로 넘겨받았다 (재다운로드/재디코딩 없음).
    assert len(cutout_calls) == 1
    assert cutout_calls[0] == image
    assert len(vlm_calls) == 1
    assert vlm_calls[0][0] == image

    # raw/cutout/plate 업로드도 각각 정확히 한 번.
    raw_uploads = [p for p in uploads if p.endswith("_raw.png")]
    cutout_uploads = [p for p in uploads if p.endswith("_cutout.png")]
    plate_uploads = [p for p in uploads if p.endswith("_plate.png")]
    assert len(raw_uploads) == 1
    assert len(cutout_uploads) == 1
    assert len(plate_uploads) == 1


def test_cutout_upload_failure_does_not_break_plate_or_qa(uploads, monkeypatch):
    """
    요구: 한 분기의 실패는 깔끔하게 격리된다. 누끼 업로드만 실패해도 클린
    플레이트/QA 는 그대로 끝나고 후보는 정상적으로 완료된다(기존 "실패해도
    후보는 남는다" 계약 — 이제는 누끼 업로드가 플레이트 생성과 동시에 도는
    별도 태스크이므로, 그 예외가 gather 의 나머지 태스크를 죽이지 않는지가
    새로운 위험이다).
    """
    from backend.services import supabase_assets

    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "1")
    monkeypatch.setenv("CANONICAL_STOP_AFTER_PASSES", "1")

    real_upload = supabase_assets.upload_asset_to_storage

    async def flaky_cutout_upload(path, data, content_type):
        if path.endswith("_cutout.png"):
            raise RuntimeError("cutout storage down")
        return await real_upload(path, data, content_type)

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", flaky_cutout_upload)

    image = GOOD()
    v = _build(h, [FakeProvider("runway", [image])])

    assert v.status == svc.STATUS_COMPLETE
    sel = next(c for c in v.candidates if c.selected)
    assert sel.error is None  # 누끼 저장 실패는 candidate 를 ERROR 로 만들지 않는다
    assert sel.cutout_object_path is None  # 실패한 분기만 비어있다
    assert sel.plate_object_path is not None  # 나머지 분기는 정상 완료
    assert sel.plate_object_path in uploads
    assert sel.qa_result.get("decision") == canonical_qa.PASS  # QA 도 정상 진행


def test_postprocessing_outputs_identical_to_sequential_shape(uploads, monkeypatch):
    """요구 4: 병렬화 전과 똑같은 raw/cutout/plate 바이트·경로·계보가 저장된다."""
    from backend.services import clean_plate_service

    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "1")
    monkeypatch.setenv("CANONICAL_STOP_AFTER_PASSES", "1")

    image = GOOD()
    _, expected_plate_meta = clean_plate_service.build_clean_plate(image)

    v = _build(h, [FakeProvider("runway", [image])])
    sel = next(c for c in v.candidates if c.selected)

    # 스토리지 목업은 업로드 경로만 기록한다 — 바이트 내용은 clean_plate_service
    # 를 직접 호출해 만든 기대값과, candidate 행의 계보(raw→cutout→plate,
    # provider/attempt)로 검증한다.
    assert sel.raw_object_path.endswith(f"{sel.provider}_a{sel.attempt}_raw.png")
    assert sel.cutout_object_path == sel.raw_object_path.replace("_raw.png", "_cutout.png")
    assert sel.plate_object_path == clean_plate_service.plate_object_path(sel.raw_object_path)
    assert sel.raw_object_path in uploads
    assert sel.cutout_object_path in uploads
    assert sel.plate_object_path in uploads
    assert expected_plate_meta  # 클린 플레이트가 실제로 만들어졌다 (메타 비어있지 않음)


def test_gpt_image_synchronous_result_completes_canonical_in_one_call(uploads, monkeypatch):
    """
    GPT Image 는 submit() 안에서 이미 유료 요청을 끝내고 완료 이미지를 들고
    돌아온다 — build_canonical() 한 번의 호출이 WAITING_PROVIDER 로 새지 않고
    바로 STATUS_COMPLETE 로 끝나야 한다 (ProviderWorkPending 이 전혀 나지 않음
    = 이 테스트가 실패하지 않는 것 자체가 증거다).
    """
    import base64

    import httpx

    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "1")
    monkeypatch.setenv("CANONICAL_STOP_AFTER_PASSES", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    image = GOOD()
    calls = {"post": 0}

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {
                "created": 1720000900,
                "data": [{"b64_json": base64.b64encode(image).decode("ascii")}],
            }

    def post(*args, **kwargs):
        calls["post"] += 1
        return Response()

    monkeypatch.setattr(httpx, "post", post)

    provider = durable_provider_jobs.DurableImageProvider(
        providers_mod.GptImageProvider(),
        run_id="00000000-0000-0000-0000-0000000009f1",
        user_id=USER,
        pet_id=PET,
        provider_operation=durable_provider_jobs.OP_CANONICAL,
    )

    # No pytest.raises(ProviderWorkPending) — the whole build must finish in
    # this one call, unlike an async provider (see test_durable_builder_
    # resumes_one_building_version, which needs two calls for the same shape).
    v = _build(h, [provider])

    assert v.status == svc.STATUS_COMPLETE
    assert calls["post"] == 1, "정확히 한 번의 유료 제출 — 재제출 없음"
    sel = next(c for c in v.candidates if c.selected)
    assert sel.error is None
    assert sel.raw_object_path in uploads

    receipt = durable_provider_jobs._MOCK_JOBS[0]
    assert receipt["submission_status"] == durable_provider_jobs.COLLECTED
    assert receipt["provider_status"] == "SUCCEEDED"


# ══════════════════════════════════════════════════════════════════════════
# 입력 요건 / 프로바이더 구성
# ══════════════════════════════════════════════════════════════════════════


def test_reference_set_is_required(uploads):
    with pytest.raises(svc.CanonicalPetError) as e:
        _run(svc.build_canonical(user_id=USER, pet_id=PET, providers=[FakeProvider("runway", [GOOD()])]))
    assert e.value.code == "NO_ORIGINAL_REFERENCES"


def test_unconfigured_providers_fail_closed_before_any_row(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)

    class Unavailable(CanonicalImageProvider):
        name = "runway"

        def available(self):
            return False

    with pytest.raises(svc.CanonicalPetError) as e:
        _build(h, [Unavailable()])
    assert e.value.code == "PROVIDER_NOT_CONFIGURED" and e.value.status == 503
    assert _run(svc._version_rows(PET)) == []  # 버전 행도, 과금도 없다


def test_provider_registry_and_mock_mode(monkeypatch):
    assert providers_mod.get_provider("runway").name == "runway"
    assert providers_mod.get_provider("gpt_image").name == "gpt_image"
    monkeypatch.setenv("CANONICAL_GENERATION_MOCK", "1")
    resolved = providers_mod.resolve_providers()
    assert [p.name for p in resolved] == ["mock"]


# ══════════════════════════════════════════════════════════════════════════
# 성공 경로 / 레퍼런스 선택 / 프롬프트
# ══════════════════════════════════════════════════════════════════════════


def test_primary_success_with_three_complementary_references(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    primary = FakeProvider("runway", [GOOD(), GOOD(), GOOD()])
    fallback = FakeProvider("gpt_image", [GOOD()])

    v = _build(h, [primary, fallback])

    assert v.status == svc.STATUS_COMPLETE
    assert v.version == 1
    roles = [p["role"] for p in v.output_spec["input_references"]]
    assert roles == ["PRIMARY_FACE", "PRIMARY_FULL_BODY", "PRIMARY_3Q"]
    assert len(v.input_reference_ids) == 3
    assert fallback.calls == 0  # PRIMARY 가 통과하면 FALLBACK 은 호출되지 않는다
    # 점진적 조기 중단: stop_after_passes 기본 1 — 첫 PASS 에서 즉시 멈춘다.
    assert primary.calls == 1
    sel = next(c for c in v.candidates if c.selected)
    assert sel.provider == "runway" and sel.decision == "PASS"
    assert sel.qa_result["identity_similarity"] is not None
    assert v.qa_summary["canonical_confidence"] == "normal"


def test_one_reference_limited_case_lowers_confidence(uploads, monkeypatch):
    h = Harness()
    h.seed(cutout=make_pet_cutout_png(), classification=cls(view="FRONT", face_visible="yes"))
    h.install_vlm(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)

    v = _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])
    assert v.status == svc.STATUS_COMPLETE
    assert len(set(v.input_reference_ids)) == 1
    assert v.qa_summary["canonical_confidence"] == "low"  # 없는 증거를 지어내지 않는다


def test_prompt_uses_known_traits_and_never_invents_unknowns(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    v = _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])

    prompt = v.prompt or ""
    assert v.prompt_version == "canonical-prompt-v2"
    # 그림자 금지는 프롬프트의 계약이다 (알파 억제와 짝을 이룬다).
    assert "No contact shadow under the pet" in prompt
    assert "no cast shadow on the background" in prompt
    # Phase 2 가 실측한 코트 색은 제약으로 들어간다.
    assert "brown" in prompt.lower()
    # UNKNOWN 특성은 문장이 되지 않는다 (귀 모양은 semantic unknown 상태다).
    assert "unknown" not in prompt.lower()
    assert "Ear shape" not in prompt
    # 정면 3/4·중립 배경·펫 단독 사양이 들어 있다.
    assert "three-quarter" in prompt
    assert "No human" in prompt


def test_no_theme_vocabulary_in_prompt(uploads, monkeypatch):
    from backend.services.theme_catalog import ALL_THEME_KEYS

    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    v = _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])
    prompt = (v.prompt or "").lower()
    for key in ALL_THEME_KEYS:
        assert key.replace("_", " ") not in prompt
        assert key not in prompt


# ══════════════════════════════════════════════════════════════════════════
# 폴백 / 실패 구분 / 상한
# ══════════════════════════════════════════════════════════════════════════


def test_primary_provider_failure_falls_back(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    err = CanonicalProviderError("PROVIDER_FAILED", "boom")
    primary = FakeProvider("runway", [err, err, err])
    fallback = FakeProvider("gpt_image", [GOOD(), GOOD()])

    v = _build(h, [primary, fallback])
    assert v.status == svc.STATUS_COMPLETE
    sel = next(c for c in v.candidates if c.selected)
    assert sel.provider == "gpt_image"
    # 프로바이더 실패는 ERROR 로 기록된다 — QA 실패(FAIL)와 구분된다.
    errors = [c for c in v.candidates if c.decision == "ERROR"]
    assert len(errors) == 3 and all(c.error for c in errors)


def test_primary_qa_failure_falls_back(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    # 전혀 다른 코트의 이미지 → 신원 시그니처/코트 계열 FAIL.
    primary = FakeProvider("runway", [make_striped_cutout_png(), make_striped_cutout_png(), make_striped_cutout_png()])
    fallback = FakeProvider("gpt_image", [GOOD()])

    v = _build(h, [primary, fallback])
    assert v.status == svc.STATUS_COMPLETE
    assert next(c for c in v.candidates if c.selected).provider == "gpt_image"
    assert all(c.decision == "FAIL" for c in v.candidates if c.provider == "runway")


def test_candidate_limits_are_enforced_and_configurable(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    # VLM QA 없음 → 후보는 최대 REVIEW → PASS 0 → 상한까지 시도 후 폴백도 상한까지.
    install_vlm_qa(monkeypatch, None)
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "2")
    monkeypatch.setenv("CANONICAL_MAX_FALLBACK", "1")
    primary = FakeProvider("runway", [GOOD()] * 10)
    fallback = FakeProvider("gpt_image", [GOOD()] * 10)

    v = _build(h, [primary, fallback])
    assert primary.calls == 2 and fallback.calls == 1
    assert v.qa_summary["candidate_count"] == 3


def test_review_status_without_vlm_confirmation(uploads, monkeypatch):
    """합성 임계값만으로는 절대 자동 승인되지 않는다 — VLM 확언 없으면 REVIEW."""
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, None)

    v = _build(h, [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])])
    assert v.status == svc.STATUS_REVIEW
    assert v.selected_candidate_id is None
    assert all(c.decision == "REVIEW" for c in v.candidates)
    # 선택되지 않았으므로 generated 대장 기록도 없다.
    ledger = _run(refs.list_references(user_id=USER, pet_id=PET))
    assert not any(r.role == refs.ROLE_GENERATED for r in ledger)


# ══════════════════════════════════════════════════════════════════════════
# 저장 / 근거 / 버전 / 결정론
# ══════════════════════════════════════════════════════════════════════════


def test_candidates_persist_raw_and_cutout(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    v = _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])

    for c in v.candidates:
        assert c.raw_object_path and "_raw.png" in c.raw_object_path
        assert c.cutout_object_path and "_cutout.png" in c.cutout_object_path
        # 하류가 먹는 입력 — raw/cutout 과 별개 객체로 남는다.
        assert c.plate_object_path and "_plate.png" in c.plate_object_path
        assert c.plate_object_path in uploads
        assert c.raw_object_path in uploads and c.cutout_object_path in uploads
    # raw(증거)와 cutout(파생)은 서로 다른 객체다 — raw 는 파괴되지 않는다.
    assert all("canonical/v1/" in p for p in uploads if "canonical" in p)


def test_provenance_chain_to_originals(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    v = _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])

    refset = _run(sets.get_set(user_id=USER, pet_id=PET, version=v.reference_set_version))
    ledger = _run(refs.list_references(user_id=USER, pet_id=PET))
    original_ids = {r.id for r in ledger if r.role == refs.ROLE_ORIGINAL}

    assert set(v.input_reference_ids) <= set(refset.source_reference_ids) <= original_ids
    generated = [r for r in ledger if r.role == refs.ROLE_GENERATED]
    assert {r.derived_kind for r in generated} == {
        "canonical_raw", "canonical_cutout", "canonical_plate",
    }
    for g in generated:
        assert g.diagnostics["canonical_version_id"] == v.id
        assert set(g.diagnostics["input_reference_ids"]) <= original_ids


def test_generated_role_never_becomes_original_evidence(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])

    before_originals = {r.id for r in _run(refs.list_references(user_id=USER, pet_id=PET)) if r.role == refs.ROLE_ORIGINAL}
    # 강제 재빌드된 레퍼런스 세트도 생성물을 근거로 삼지 않는다.
    refset = _run(sets.build_reference_set(user_id=USER, pet_id=PET, fetch_bytes=h.fetch, skip_if_unchanged=False))
    assert set(refset.source_reference_ids) == before_originals
    for item in refset.items:
        assert item["reference_id"] in before_originals


def test_canonical_versioning_is_immutable(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    v1 = _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])
    assert v1.version == 1

    # 새 원본 → 새 레퍼런스 세트 → 새 정본 버전. V1 은 그대로 남는다.
    h.seed(cutout=make_pet_cutout_png(), classification=cls(view="RIGHT", full_body_visible="yes"))
    v2 = _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])
    assert v2.version == 2 and v2.reference_set_version == 2

    old = _run(svc.get_canonical(user_id=USER, pet_id=PET, version=1))
    assert old.id == v1.id and old.reference_set_version == 1
    assert old.selected_candidate_id == v1.selected_candidate_id


def test_idempotent_build_does_not_repay(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    primary = FakeProvider("runway", [GOOD()] * 10)

    first = _build(h, [primary])
    calls_after_first = primary.calls
    second = _build(h, [primary])

    assert second.deduplicated is True and second.version == first.version
    assert primary.calls == calls_after_first  # 중복 과금 없음


def test_deterministic_ranking_given_fixed_qa(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    monkeypatch.setenv("CANONICAL_STOP_AFTER_PASSES", "3")

    a = _build(h, [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])], skip_if_unchanged=False)
    b = _build(h, [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])], skip_if_unchanged=False)

    sa = next(c for c in a.candidates if c.selected)
    sb = next(c for c in b.candidates if c.selected)
    assert (sa.provider, sa.attempt, sa.decision) == (sb.provider, sb.attempt, sb.decision)
    assert a.qa_summary["decisions"] == b.qa_summary["decisions"]


def test_ownership_isolation(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    with pytest.raises(svc.CanonicalPetError) as e:
        _run(svc.build_canonical(user_id="mallory@test", pet_id=PET, providers=[FakeProvider("runway", [GOOD()])]))
    assert e.value.code == "PET_NOT_OWNED"

    install_vlm_qa(monkeypatch, VLM_QA_OK)
    _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])
    with pytest.raises(svc.CanonicalPetError):
        _run(svc.get_canonical(user_id="mallory@test", pet_id=PET))


# ══════════════════════════════════════════════════════════════════════════
# 온보딩 보존 / 평가 하네스 / 라우터
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def assets_client(uploads) -> ASGITestClient:
    app = FastAPI()
    app.include_router(assets_router.router, prefix="/api")
    return ASGITestClient(app)


def test_canonical_failure_does_not_break_onboarding(assets_client, uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    err = CanonicalProviderError("PROVIDER_FAILED", "provider down")
    v = _build(h, [FakeProvider("runway", [err, err, err])])
    assert v.status == svc.STATUS_FAILED  # 정본 실패는 정본에만 머문다

    res = assets_client.post(
        "/api/assets/original",
        files={"file": ("dog.jpg", make_jpeg_bytes(64, 64), "image/jpeg")},
        data={"user_id": USER, "content_id": "cid1"},
    )
    assert res.status_code == 200


def test_evaluation_harness_records_and_summarizes(uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    v = _build(h, [FakeProvider("runway", [GOOD(), GOOD()])])
    sel = next(c for c in v.candidates if c.selected)

    _run(
        svc.record_evaluation(
            user_id=USER, pet_id=PET, canonical_version_id=v.id, candidate_id=sel.id,
            scores={"face_identity": 9, "markings": 8, "body_proportions": 9,
                    "tail_ears_paws": 7, "anatomy": 9, "overall_same_pet": 9},
            verdict="PASS", notes="looks like the same dog",
        )
    )
    summary = _run(svc.evaluation_summary(user_id=USER))
    assert summary["providers"]["runway"]["count"] == 1
    assert summary["providers"]["runway"]["mean_scores"]["overall_same_pet"] == 9.0
    assert summary["providers"]["runway"]["verdicts"]["PASS"] == 1

    with pytest.raises(svc.CanonicalPetError):
        _run(
            svc.record_evaluation(
                user_id=USER, pet_id=PET, canonical_version_id=v.id, candidate_id=sel.id,
                scores={"anatomy": 99}, verdict="PASS",
            )
        )


AUTH = {"Authorization": "Bearer test:alice@test"}


@pytest.fixture
def canonical_client(monkeypatch) -> ASGITestClient:
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    app = FastAPI()
    app.include_router(canonical_v1.router, prefix="/api")
    return ASGITestClient(app)


def test_router_build_get_and_review(canonical_client, uploads, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    monkeypatch.setattr(ids, "_default_fetch_bytes", h.fetch)
    monkeypatch.setattr(
        providers_mod, "resolve_providers", lambda: [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])]
    )
    monkeypatch.setattr(svc, "_default_cutout_fn", lambda raw: raw)

    res = canonical_client.post(f"/api/v1/pet/canonical/{PET}/build", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "complete" and body["version"] == 1
    assert body["selected_candidate_id"]

    res = canonical_client.get(f"/api/v1/pet/canonical/{PET}", headers=AUTH)
    assert res.status_code == 200
    assert res.json()["candidates"]

    res = canonical_client.get(f"/api/v1/pet/canonical/{PET}/review", headers=AUTH)
    assert res.status_code == 200
    review = res.json()
    assert len(review["references"]) == 3
    assert review["references"][0]["role"] == "PRIMARY_FACE"
    assert review["candidates"][0]["qa_result"]["decision"] in ("PASS", "REVIEW", "FAIL")

    res = canonical_client.get(
        f"/api/v1/pet/canonical/{PET}", headers={"Authorization": "Bearer test:mallory@test"}
    )
    assert res.status_code == 403


def test_router_404_without_versions(canonical_client, uploads, monkeypatch):
    _seed_three_ref_pet(monkeypatch)
    res = canonical_client.get(f"/api/v1/pet/canonical/{PET}", headers=AUTH)
    assert res.status_code == 404


# ══════════════════════════════════════════════════════════════════════════
# REVIEW 회복 — QA 재실행 (프로바이더 재호출 없음)
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def object_storage(monkeypatch) -> dict[str, bytes]:
    """경로 → 바이트. QA 재실행이 저장된 후보 raw 를 다시 읽을 수 있어야 한다."""
    from backend.services import supabase_assets

    store: dict[str, bytes] = {}

    async def fake_upload(path, data, content_type):
        store[path] = bytes(data)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    return store


def test_qa_rerun_reuses_existing_candidate_and_flips_review_to_pass(object_storage, monkeypatch):
    """QA 규칙이 업데이트되면(버전 상승) REVIEW 후보를 재구매 없이 재판정한다."""
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, None)  # VLM 확언 없음 → REVIEW
    primary = FakeProvider("runway", [GOOD()])

    def fetch(ref):
        return h.bytes_by_path.get(ref.object_path) or object_storage.get(ref.object_path)

    v = _build(h, [primary])
    assert v.status == svc.STATUS_REVIEW
    calls_after_build = primary.calls
    stale_candidate_id = v.candidates[0].id

    # QA 규칙 튜닝을 흉내낸다 (버전 상승) — 그리고 이번엔 VLM 이 확언한다.
    monkeypatch.setattr(canonical_qa, "CANONICAL_QA_VERSION", "canonical-qa-v999-test")
    install_vlm_qa(monkeypatch, VLM_QA_OK)

    updated = _run(
        svc.reevaluate_canonical_candidate(
            user_id=USER,
            pet_id=PET,
            canonical_version_id=v.id,
            candidate_id=stale_candidate_id,
            fetch_bytes=fetch,
            cutout_fn=lambda raw: raw,
        )
    )

    assert updated.status == svc.STATUS_COMPLETE
    assert updated.selected_candidate_id == stale_candidate_id
    assert next(c for c in updated.candidates if c.id == stale_candidate_id).decision == "PASS"
    # No new provider (image generation) call — this is a pure re-judgment.
    assert primary.calls == calls_after_build


def test_qa_rerun_is_idempotent_and_makes_no_provider_call(object_storage, monkeypatch):
    """같은 QA 버전으로 다시 부르면 아무 것도 다시 계산하지 않는다 (deduplicated)."""
    h = _seed_three_ref_pet(monkeypatch)
    install_vlm_qa(monkeypatch, VLM_QA_OK)
    primary = FakeProvider("runway", [GOOD()])

    def fetch(ref):
        return h.bytes_by_path.get(ref.object_path) or object_storage.get(ref.object_path)

    v = _build(h, [primary])
    assert v.status == svc.STATUS_COMPLETE
    calls_after_build = primary.calls

    def fail_if_called(*args, **kwargs):
        raise AssertionError("reevaluate_canonical_candidate must never call an image provider")

    monkeypatch.setattr(providers_mod, "resolve_providers", fail_if_called)

    again = _run(
        svc.reevaluate_canonical_candidate(
            user_id=USER,
            pet_id=PET,
            canonical_version_id=v.id,
            candidate_id=v.selected_candidate_id,
            fetch_bytes=fetch,
            cutout_fn=lambda raw: raw,
        )
    )
    assert again.deduplicated is True
    assert again.status == svc.STATUS_COMPLETE
    assert primary.calls == calls_after_build
