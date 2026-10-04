"""
QA VLM 캐시 — 단계 빌더를 **진짜** qa_* 함수로 통과시키는 계약 테스트.

여기서는 단계 테스트들이 쓰는 VLM 스텁(install_vlm_qa / install_kf_vlm / install_mv_vlm)
을 걷어내고, 가짜 anthropic 모듈 위에서 실제 qa_canonical_image / qa_action_keyframe /
qa_motion_video 가 캐시를 거치게 한다. 확인하는 것:

  * raw 저장 뒤 QA 기록 전 크래시 / RAW_STORE_FAILED 복구 / QA 전용 버전 범프 뒤
    qa-rerun 은 새 VLM 호출을 내지 않는다.
  * durable 재개(양보 → 재개)는 이미 판정된 후보를 다시 묻지 않는다.
  * 캐시 on/off 판정은 동일하다.
프로바이더는 전부 가짜 — 실 결제 호출 없음.
"""

from __future__ import annotations

import sys
import types

import anyio
import pytest

from backend.services import action_keyframe_service as kf
from backend.services import canonical_pet_service as canon
from backend.services import canonical_qa
from backend.services import durable_provider_jobs as jobs
from backend.services import motion_video_qa
from backend.services import motion_video_service as mv
from backend.services import pet_identity_service as ids
from backend.services import pet_morphology_service as morph
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry, supabase_assets, vlm_identity as vlm

from .test_action_keyframes import _build_kf, _prepare_canonical
from .test_canonical_pet_builder import GOOD, FakeProvider, _DurableJobProvider, _seed_three_ref_pet
from .test_motion_video_generation import (
    FakeVideoProvider,
    _DurableJobVideoProvider,
    _build_motion,
    _prepare_pipeline,
    conformance_ok,
    sampler_identical,
)
from .test_pet_reference_sets import PET, USER
from .test_vlm_qa_cache import install_fake_anthropic

REAL_QA_CANONICAL = vlm.qa_canonical_image
REAL_QA_KEYFRAME = vlm.qa_action_keyframe
REAL_QA_MOTION = vlm.qa_motion_video


def _run(coro):
    return anyio.run(lambda: coro)


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_VLM_IDENTITY_ENABLED", "1")
    monkeypatch.setenv(vlm.VLM_QA_CACHE_ENV, "on")
    monkeypatch.setenv("CANONICAL_QA_MIN_RESOLUTION", "100")
    monkeypatch.setenv("PHASE6_LIVE_MODE", "all")
    monkeypatch.setenv("PHASE6_VIDEO_ANCHOR", "0")
    monkeypatch.delenv("KEYFRAME_ALLOW_REVIEW_CANONICAL", raising=False)
    monkeypatch.delenv("VIDEO_GENERATION_MOCK", raising=False)
    monkeypatch.delenv(motion_video_qa.MOTION_VIDEO_QA_RULESET_ENV, raising=False)
    # 레퍼런스 분석기의 VLM 은 이 테스트의 관심사가 아니다 — 꺼진 것과 같은 결과(None).
    monkeypatch.setattr(vlm, "analyze_semantic_traits", lambda images: None)
    monkeypatch.setattr(vlm, "classify_reference", lambda data, mime_type="image/jpeg": None)
    vlm.clear_semantic_cache()
    for m in (refs, pet_registry, ids, morph, sets, canon, kf, mv):
        m.__reset_for_tests()
    jobs.__reset_for_tests()
    yield
    vlm.clear_semantic_cache()
    for m in (refs, pet_registry, ids, morph, sets, canon, kf, mv):
        m.__reset_for_tests()
    jobs.__reset_for_tests()


@pytest.fixture
def storage(monkeypatch) -> dict[str, bytes]:
    store: dict[str, bytes] = {}

    async def fake_upload(path, data, content_type):
        store[path] = bytes(data)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    return store


def _use_real_qa(monkeypatch, *names):
    """단계 헬퍼가 설치한 스텁을 걷어내고 진짜 qa_* 를 되돌린다."""
    real = {"canonical": REAL_QA_CANONICAL, "keyframe": REAL_QA_KEYFRAME, "motion": REAL_QA_MOTION}
    attr = {"canonical": "qa_canonical_image", "keyframe": "qa_action_keyframe", "motion": "qa_motion_video"}
    for n in names:
        monkeypatch.setattr(vlm, attr[n], real[n])


def _kind_calls(calls, schema):
    return [c for c in calls if c["output_config"]["format"]["schema"] is schema]


def _targeted_calls(calls, task=None):
    selected = []
    for call in calls:
        text = call["messages"][0]["content"][-1].get("text", "")
        if "_VLM only" not in text:
            continue
        if task is None or f"{task} only" in text:
            selected.append(call)
    return selected


def _flaky_first_upload(monkeypatch, storage):
    state = {"n": 0}

    async def flaky(path, data, content_type):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("storage down")
        storage[path] = bytes(data)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", flaky)


def _fetch_for(h, storage):
    """레퍼런스 원본(하네스) + 업로드된 산출물(storage) 둘 다 읽는 fetch — 재판정은 저장된 raw 를 읽는다."""
    return lambda ref: h.bytes_by_path.get(ref.object_path) or storage.get(ref.object_path)


def _build_canonical(h, providers, storage, **kw):
    return _run(canon.build_canonical(user_id=USER, pet_id=PET, fetch_bytes=_fetch_for(h, storage),
                                      providers=providers, cutout_fn=kw.pop("cutout_fn", lambda raw: raw), **kw))


# ══════════════════════════════════════════════════════════════════════════
# CANONICAL
# ══════════════════════════════════════════════════════════════════════════


def test_canonical_raw_store_failed_recovery_makes_no_second_vlm_call(storage, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    calls = install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "2")
    monkeypatch.setenv("CANONICAL_STOP_AFTER_PASSES", "1")
    _flaky_first_upload(monkeypatch, storage)
    provider = _DurableJobProvider("runway", GOOD())

    with pytest.raises(jobs.ProviderRecoveryRequired):
        _build_canonical(h, [provider], storage)
    # Phase 8은 raw/cutout 결정론 QA 전에는 VLM을 시작하지 않는다.
    assert len(_kind_calls(calls, vlm.CANONICAL_QA_SCHEMA)) == 0

    completed = _build_canonical(h, [provider], storage)
    assert completed.status == canon.STATUS_COMPLETE
    assert provider.submissions == 1
    assert len(_kind_calls(calls, vlm.CANONICAL_QA_SCHEMA)) == 0
    assert completed.candidates[0].qa_result["checks"]["vlm_same_pet"] == "unknown"
    assert completed.candidates[0].qa_result["vlm_escalation"]["decision"] == "SKIP"


def test_canonical_crash_between_raw_upload_and_qa_persist_makes_no_second_vlm_call(storage, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    calls = install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "1")
    provider = _DurableJobProvider("runway", GOOD())

    def dying_cutout(raw):
        raise MemoryError("worker died in matting")

    with pytest.raises(MemoryError):
        _build_canonical(h, [provider], storage, cutout_fn=dying_cutout)
    rows = _run(canon._version_rows(PET))
    assert len(rows) == 1 and rows[0]["status"] == canon.STATUS_BUILDING
    n_after_crash = len(_kind_calls(calls, vlm.CANONICAL_QA_SCHEMA))
    assert n_after_crash == 0  # 결정론 QA 전 crash — VLM은 시작되지 않는다

    completed = _build_canonical(h, [provider], storage)
    assert completed.status == canon.STATUS_COMPLETE
    assert len(_run(canon._version_rows(PET))) == 1
    assert provider.submissions == 1
    assert len(_kind_calls(calls, vlm.CANONICAL_QA_SCHEMA)) == n_after_crash


def test_canonical_qa_only_bump_then_rerun_makes_no_vlm_call(storage, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    calls = install_fake_anthropic(monkeypatch)
    v = _build_canonical(h, [FakeProvider("runway", [GOOD()])], storage)
    assert v.status == canon.STATUS_COMPLETE and len(calls) == 0
    before = v.candidates[0].qa_result

    monkeypatch.setattr(canonical_qa, "CANONICAL_QA_VERSION", "canonical-qa-v999-test")
    rerun = _run(canon.reevaluate_canonical_candidate(
        user_id=USER, pet_id=PET, canonical_version_id=v.id, candidate_id=v.candidates[0].id,
        fetch_bytes=_fetch_for(h, storage), cutout_fn=lambda raw: raw,
    ))
    after = next(c for c in rerun.candidates if c.id == v.candidates[0].id).qa_result
    assert after["qa_version"] == "canonical-qa-v999-test"
    assert after["decision"] == before["decision"] and after["checks"] == before["checks"]
    assert len(calls) == 0, "clear deterministic QA remains VLM-free after a QA-only bump"


def test_canonical_operator_refresh_asks_the_vlm_again_and_overwrites(storage, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    calls = install_fake_anthropic(monkeypatch)
    v = _build_canonical(h, [FakeProvider("runway", [GOOD()])], storage)
    assert len(calls) == 0

    # 같은 QA 버전 — 평소라면 dedup 으로 아무것도 안 한다. refresh 는 강제로 다시 묻는다.
    rerun = _run(canon.reevaluate_canonical_candidate(
        user_id=USER, pet_id=PET, canonical_version_id=v.id, candidate_id=v.candidates[0].id,
        fetch_bytes=_fetch_for(h, storage), cutout_fn=lambda raw: raw, vlm_cache_mode="refresh",
    ))
    assert rerun.deduplicated is False
    assert len(_targeted_calls(calls)) == 2  # explicit full review: identity + anatomy
    assert len([r for r in vlm._MOCK_DURABLE_CACHE.values() if r["kind"].startswith(vlm.KIND_TARGETED_QA)]) == 2
    # 이후 일반 재사용은 새 항목을 읽는다.
    _build_canonical(h, [FakeProvider("runway", [GOOD()])], storage, skip_if_unchanged=False)
    assert len(_targeted_calls(calls)) == 2


def test_canonical_decisions_identical_with_cache_on_and_off(storage, monkeypatch):
    h = _seed_three_ref_pet(monkeypatch)
    calls = install_fake_anthropic(monkeypatch)
    monkeypatch.setenv(vlm.VLM_QA_CACHE_ENV, "off")
    off = _build_canonical(h, [FakeProvider("runway", [GOOD()])], storage)
    monkeypatch.setenv(vlm.VLM_QA_CACHE_ENV, "on")
    on_miss = _build_canonical(h, [FakeProvider("runway", [GOOD()])], storage, skip_if_unchanged=False)
    on_hit = _build_canonical(h, [FakeProvider("runway", [GOOD()])], storage, skip_if_unchanged=False)
    assert len(calls) == 0  # clear evidence skips VLM regardless of cache mode
    for a, b in ((off, on_miss), (on_miss, on_hit)):
        assert a.status == b.status == canon.STATUS_COMPLETE
        assert a.candidates[0].qa_result["decision"] == b.candidates[0].qa_result["decision"]
        assert a.candidates[0].qa_result["checks"] == b.candidates[0].qa_result["checks"]
        assert a.candidates[0].qa_result["vlm"] == b.candidates[0].qa_result["vlm"]


# ══════════════════════════════════════════════════════════════════════════
# KEYFRAME
# ══════════════════════════════════════════════════════════════════════════


def test_keyframe_qa_only_bump_invalidates_targeted_vlm_cache(storage, monkeypatch):
    h, _canonical = _prepare_canonical(monkeypatch, storage)   # 정본 QA 는 스텁, 키프레임 QA 는 진짜
    _use_real_qa(monkeypatch, "keyframe")
    calls = install_fake_anthropic(monkeypatch)
    k = _build_kf(h, [FakeProvider("runway", [GOOD()])])
    assert k.status == kf.STATUS_COMPLETE
    assert len(_targeted_calls(calls, "POSE_VLM")) == 1
    before = k.candidates[0].qa_result

    monkeypatch.setattr(kf, "KEYFRAME_QA_VERSION", "keyframe-qa-v999-test")
    rerun = _run(kf.reevaluate_keyframe_candidate(
        user_id=USER, pet_id=PET, keyframe_id=k.id, candidate_id=k.candidates[0].id,
        fetch_bytes=h.kf_fetch, cutout_fn=lambda raw: raw,
    ))
    after = next(c for c in rerun.candidates if c.id == k.candidates[0].id).qa_result
    assert after["qa_version"] == "keyframe-qa-v999-test"
    assert after["decision"] == before["decision"] == "REVIEW" and after["checks"] == before["checks"]
    assert after["business_qa"]["delivery_action"] in {"DELIVER", "DELIVER_WITH_ADVISORY"}
    assert len(_targeted_calls(calls, "POSE_VLM")) == 2


def test_keyframe_durable_resume_and_rebuild_reuse_the_cached_answer(storage, monkeypatch):
    """양보 → 재개(1회 호출) → 같은 바이트로 강제 재생성(0회) → dedup(0회)."""
    h, _canonical = _prepare_canonical(monkeypatch, storage)
    _use_real_qa(monkeypatch, "keyframe")
    calls = install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "1")
    monkeypatch.setenv("CANONICAL_STOP_AFTER_PASSES", "1")
    from backend.services.canonical_image_providers import CanonicalImageResult

    class YieldOnce(FakeProvider):
        durable_execution = True

        def generate(self, references, prompt, output_spec, metadata):
            self.calls += 1
            if self.calls == 1:
                raise jobs.ProviderWorkPending("operation-1", "PENDING")
            return CanonicalImageResult(image_bytes=GOOD(), provider=self.name, model=self.model_name(),
                                        external_job_id="job-1")

    provider = YieldOnce("runway")
    with pytest.raises(jobs.ProviderWorkPending):
        _build_kf(h, [provider])
    assert len(_targeted_calls(calls)) == 0   # 결과가 없으니 QA 도 없다

    resumed = _build_kf(h, [provider])
    assert resumed.status == kf.STATUS_COMPLETE
    assert len(_targeted_calls(calls, "POSE_VLM")) == 1

    forced = _build_kf(h, [FakeProvider("runway", [GOOD()])], skip_if_unchanged=False)
    assert forced.version == 2 and forced.candidates[0].decision == resumed.candidates[0].decision
    assert len(_targeted_calls(calls, "POSE_VLM")) == 1, "같은 입력 → 캐시"

    again = _build_kf(h, [FakeProvider("runway", [GOOD()])])
    assert again.deduplicated is True
    assert len(_targeted_calls(calls, "POSE_VLM")) == 1


def test_keyframe_operator_refresh_forces_one_new_call(storage, monkeypatch):
    h, _canonical = _prepare_canonical(monkeypatch, storage)
    _use_real_qa(monkeypatch, "keyframe")
    calls = install_fake_anthropic(monkeypatch)
    k = _build_kf(h, [FakeProvider("runway", [GOOD()])])
    n = len(_targeted_calls(calls))
    rerun = _run(kf.reevaluate_keyframe_candidate(
        user_id=USER, pet_id=PET, keyframe_id=k.id, candidate_id=k.candidates[0].id,
        fetch_bytes=h.kf_fetch, cutout_fn=lambda raw: raw, vlm_cache_mode="refresh",
    ))
    assert rerun.deduplicated is False
    assert len(_targeted_calls(calls)) == n + 3  # full refresh: identity + anatomy + pose


# ══════════════════════════════════════════════════════════════════════════
# MOTION
# ══════════════════════════════════════════════════════════════════════════


def test_motion_raw_store_failed_recovery_then_qa_bump_recomputes_vlm(storage, monkeypatch):
    # 빌드는 (플레이트가 켜져 있으면) 클린 플레이트를, 재판정은 raw 키프레임을 VLM 레퍼런스로
    # 보낸다 — 아래 별도 테스트가 그 사실을 고정한다. 여기서는 플레이트를 꺼서 두 경로가
    # 같은 입력을 보내게 하고, 캐시가 재판정을 공짜로 만드는지 본다.
    monkeypatch.setenv("CLEAN_PLATE_ENABLED", "0")
    h, _canonical = _prepare_pipeline(monkeypatch, storage)   # 정본/키프레임 QA 는 스텁
    _use_real_qa(monkeypatch, "motion")
    calls = install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("PHASE6_MAX_PRIMARY", "2")
    monkeypatch.setenv("PHASE6_STOP_AFTER_PASSES", "1")
    _flaky_first_upload(monkeypatch, storage)
    provider = _DurableJobVideoProvider("seedance", GOOD())

    with pytest.raises(jobs.ProviderRecoveryRequired):
        _build_motion(h, "BREATHING", [provider])
    # 모션은 raw 저장 **뒤에** QA 를 돈다 — 첫 시도에서는 VLM 이 아직 호출되지 않았다.
    assert len(_targeted_calls(calls)) == 0

    completed = _build_motion(h, "BREATHING", [provider])
    assert completed.status == mv.STATUS_COMPLETE and provider.submissions == 1
    assert len(_targeted_calls(calls, "MOTION_VLM")) == 1
    before = completed.candidates[0].qa_result
    assert before["qa_version"] == "motion-video-qa-v9"

    # QA 전용 범프 (v10 규칙 집합) → qa-rerun: QA 버전이 달라 캐시는 의도적으로 miss 한다.
    monkeypatch.setenv(motion_video_qa.MOTION_VIDEO_QA_RULESET_ENV, "v10")
    rerun = _run(mv.reevaluate_motion_candidate(
        user_id=USER, pet_id=PET, motion_id="BREATHING", motion_version_id=completed.id,
        candidate_id=completed.candidates[0].id, video_bytes=GOOD(), fetch_bytes=h.kf_fetch,
        frame_sampler=sampler_identical, conformance_fn=conformance_ok,
    ))
    after = next(c for c in rerun.candidates if c.id == completed.candidates[0].id).qa_result
    assert after["qa_version"] == "motion-video-qa-v10"
    # The combined identity+anatomy call now answers what used to stay unknown.
    assert after["decision"] == before["decision"] == "PASS"
    assert after["business_qa"]["delivery_action"] in {"DELIVER", "DELIVER_WITH_ADVISORY"}
    assert after["vlm"]["requested_motion_occurs"] == before["vlm"]["requested_motion_occurs"]
    before_task = before["vlm"]["targeted_vlm_evidence"]["MOTION_VLM"]
    after_task = after["vlm"]["targeted_vlm_evidence"]["MOTION_VLM"]
    assert before_task["cache_receipt"]["status"] == "computed"
    assert after_task["cache_receipt"]["status"] == "computed"
    assert len(_targeted_calls(calls, "MOTION_VLM")) == 2
    assert provider.submissions == 1


def test_motion_rerun_with_clean_plate_sends_a_different_reference_than_the_build(storage, monkeypatch):
    """
    기존 불일치의 고정: CLEAN_PLATE_ENABLED=1(기본) 이면 빌드는 시작 키프레임의 **클린
    플레이트**를 VLM 레퍼런스로 보내고(motion_video_service.build_motion_video), 재판정은
    **raw** 키프레임을 보낸다(reevaluate_motion_candidate._obj → payload["raw"]). 입력이
    다르므로 캐시는 올바르게 miss 한다 — 캐시 버그가 아니라 재판정 경로의 증거 불일치다.
    이 테스트는 그 사실을 문서화한다; 고치려면 재판정이 빌드와 같은 레퍼런스를 보내야 한다.
    """
    h, _canonical = _prepare_pipeline(monkeypatch, storage)
    _use_real_qa(monkeypatch, "motion")
    calls = install_fake_anthropic(monkeypatch)
    v = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])
    assert v.status == mv.STATUS_COMPLETE and len(_targeted_calls(calls, "MOTION_VLM")) == 1
    build_ref = [b for b in calls[-1]["messages"][0]["content"] if b["type"] == "image"][-1]["source"]["data"]

    monkeypatch.setenv(motion_video_qa.MOTION_VIDEO_QA_RULESET_ENV, "v10")
    _run(mv.reevaluate_motion_candidate(
        user_id=USER, pet_id=PET, motion_id="BREATHING", motion_version_id=v.id,
        candidate_id=v.candidates[0].id, video_bytes=GOOD(), fetch_bytes=h.kf_fetch,
        frame_sampler=sampler_identical, conformance_fn=conformance_ok,
    ))
    assert len(_targeted_calls(calls, "MOTION_VLM")) == 2
    rerun_ref = [b for b in calls[-1]["messages"][0]["content"] if b["type"] == "image"][-1]["source"]["data"]
    assert rerun_ref != build_ref, "miss 의 원인은 레퍼런스 이미지(plate vs raw) 차이다"


def test_motion_operator_refresh_forces_one_new_call_and_keeps_history(storage, monkeypatch):
    h, _canonical = _prepare_pipeline(monkeypatch, storage)
    _use_real_qa(monkeypatch, "motion")
    calls = install_fake_anthropic(monkeypatch)
    v = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])
    assert v.status == mv.STATUS_COMPLETE and len(_targeted_calls(calls, "MOTION_VLM")) == 1

    same = _run(mv.reevaluate_motion_candidate(
        user_id=USER, pet_id=PET, motion_id="BREATHING", motion_version_id=v.id,
        candidate_id=v.candidates[0].id, video_bytes=GOOD(), fetch_bytes=h.kf_fetch,
        frame_sampler=sampler_identical, conformance_fn=conformance_ok,
    ))
    assert same.deduplicated is True and len(_targeted_calls(calls, "MOTION_VLM")) == 1

    refreshed = _run(mv.reevaluate_motion_candidate(
        user_id=USER, pet_id=PET, motion_id="BREATHING", motion_version_id=v.id,
        candidate_id=v.candidates[0].id, video_bytes=GOOD(), fetch_bytes=h.kf_fetch,
        frame_sampler=sampler_identical, conformance_fn=conformance_ok, vlm_cache_mode="refresh",
    ))
    assert refreshed.deduplicated is False
    # prior motion + identity/anatomy calls, then the full three-task refresh
    assert len(_targeted_calls(calls)) == 5
    cand = next(c for c in refreshed.candidates if c.id == v.candidates[0].id)
    assert cand.generation_metadata["qa_history"], "이전 판정은 감사 기록으로 남는다"


def test_motion_decisions_identical_with_cache_on_and_off(storage, monkeypatch):
    h, _canonical = _prepare_pipeline(monkeypatch, storage)
    _use_real_qa(monkeypatch, "motion")
    calls = install_fake_anthropic(monkeypatch)
    monkeypatch.setenv(vlm.VLM_QA_CACHE_ENV, "off")
    off = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])
    monkeypatch.setenv(vlm.VLM_QA_CACHE_ENV, "on")
    on_miss = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])], skip_if_unchanged=False)
    on_hit = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])], skip_if_unchanged=False)
    assert len(_targeted_calls(calls, "MOTION_VLM")) == 2
    for a, b in ((off, on_miss), (on_miss, on_hit)):
        assert a.candidates[0].qa_result["decision"] == b.candidates[0].qa_result["decision"] == "PASS"
        assert a.candidates[0].qa_result["business_qa"]["delivery_action"] in {
            "DELIVER", "DELIVER_WITH_ADVISORY"
        }
        assert a.candidates[0].qa_result["checks"] == b.candidates[0].qa_result["checks"]
