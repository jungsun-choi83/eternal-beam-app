"""
키프레임 멱등성 — QA 전용 버전 범프는 생성물을 바꾸지 않는다 (2026-09-30).

build_keyframe() 의 재사용/재개 판정이 analyzer_versions 스탬프 **전체**를
비교했다. keyframe_qa / canonical_qa 는 "이 그림을 어떻게 볼 것인가"만 바꾸는데,
그 값이 오르면 완료된 키프레임이 "달라진 것"으로 보여 새 버전(유료)이 만들어지고,
building 중이던 키프레임은 재개되지 못하고 중복 버전이 생겼다. canonical / motion
은 이미 QA 전용 키를 비교에서 뺀다(generation_versions) — 키프레임도 같은 계약.

프로바이더는 전부 가짜 — 실 결제 호출 없음.
"""

from __future__ import annotations

import anyio
import pytest

from backend.services import action_keyframe_service as kf
from backend.services import action_keyframe_spec as spec_mod
from backend.services import canonical_pet_service as canon
from backend.services import canonical_qa
from backend.services import durable_provider_jobs as jobs
from backend.services import motion_video_service as mv
from backend.services import pet_generation_run_service as runs
from backend.services import pet_identity_service as ids
from backend.services import pet_reference_set_service as sets
from backend.services.canonical_image_providers import CanonicalImageResult
from backend.services.provider_job_contract import SUCCEEDED

from .test_action_keyframes import (  # noqa: F401  (fixtures: _mock_backend, storage)
    VLM_KF_OK,
    _build_kf,
    _mock_backend,
    _prepare_canonical,
    install_kf_vlm,
    storage,
)
from .test_canonical_pet_builder import GOOD, FakeProvider
from .test_canonical_pinned_lineage import AsyncDelegate
from . import test_phase7c_generation_runs as p7c
from .test_pet_reference_sets import PET, USER

ROLE = "NEUTRAL_IDLE"
RUN_ID = "00000000-0000-0000-0000-00000000qa10"


def _run(coro):
    return anyio.run(lambda: coro)


def _rows():
    return _run(kf._keyframe_rows(PET, ROLE))


def _complete_keyframe(monkeypatch, storage):
    h, canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    provider = FakeProvider("runway", [GOOD()] * 10)
    first = _build_kf(h, [provider])
    assert first.status == kf.STATUS_COMPLETE and first.version == 1
    return h, canonical, provider, first


def _bump_keyframe_qa(monkeypatch):
    monkeypatch.setattr(kf, "KEYFRAME_QA_VERSION", "keyframe-qa-v999-test")
    assert kf.analyzer_versions()["keyframe_qa"] == "keyframe-qa-v999-test"


def _bump_canonical_qa(monkeypatch):
    monkeypatch.setattr(canonical_qa, "CANONICAL_QA_VERSION", "canonical-qa-v999-test")
    assert kf.analyzer_versions()["canonical_qa"] == "canonical-qa-v999-test"


# ── 계약: 세 단계가 같은 방식으로 QA 전용 키를 뺀다 ───────────────────────


def test_keyframe_projection_matches_canonical_and_motion_contract():
    assert hasattr(kf, "QA_ONLY_VERSION_KEYS") and hasattr(kf, "generation_versions")
    # 키프레임 스탬프는 정본 스탬프를 포함하므로 정본의 QA 전용 키도 빠져야 한다.
    assert set(canon.QA_ONLY_VERSION_KEYS) <= set(kf.QA_ONLY_VERSION_KEYS)
    assert "keyframe_qa" in kf.QA_ONLY_VERSION_KEYS
    # 생성에 영향을 주는 키는 하나도 빠지지 않는다.
    stamp = kf.analyzer_versions()
    projected = kf.generation_versions(stamp)
    assert set(stamp) - set(projected) == set(kf.QA_ONLY_VERSION_KEYS) & set(stamp)
    for key in ("keyframe_builder", "keyframe_spec", "keyframe_prompt", "canonical_builder",
                "canonical_prompt", "canonical_providers", "selection", "view_classifier",
                "signature", "embedding", "vlm", "vlm_model"):
        assert key in projected, key
    # motion 의 헬퍼와 같은 모양 (None/빈 스탬프 안전).
    assert kf.generation_versions(None) == {} == mv.generation_versions(None)


# ── 완료된 키프레임 + QA 전용 범프 → 재사용, 생성 없음 ─────────────────────


@pytest.mark.parametrize("bump", [_bump_keyframe_qa, _bump_canonical_qa])
def test_complete_keyframe_is_reused_after_qa_only_bump(storage, monkeypatch, bump):
    h, _canonical, provider, first = _complete_keyframe(monkeypatch, storage)
    calls = provider.calls

    bump(monkeypatch)
    again = _build_kf(h, [provider])

    assert again.deduplicated is True
    assert again.id == first.id and again.version == 1
    assert provider.calls == calls, "QA 규칙이 바뀌었다고 이미지를 다시 사지 않는다"
    assert len(_rows()) == 1
    # 재사용된 키프레임은 저장된 QA 판정을 그대로 가진다 — 새 QA 버전으로
    # 재평가되지 않는다 (canonical/motion 도 같다; 재평가는 qa-rerun 의 몫).
    assert again.candidates[0].qa_result["qa_version"] == "keyframe-qa-v1"
    assert again.candidates[0].decision == first.candidates[0].decision


def test_canonical_reuse_keyframe_is_reused_after_qa_only_bump(storage, monkeypatch):
    """BREATHING NEUTRAL_IDLE 정본 재사용 경로 — 재사용 키프레임도 같은 규칙."""
    h, _canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    provider = FakeProvider("runway", [GOOD()] * 10)
    first = _build_kf(h, [provider], allow_canonical_reuse=True)
    assert first.status == kf.STATUS_COMPLETE
    assert first.candidates[0].provider == kf.CANONICAL_REUSE_PROVIDER
    assert provider.calls == 0

    _bump_keyframe_qa(monkeypatch)
    again = _build_kf(h, [provider], allow_canonical_reuse=True)

    assert again.deduplicated is True and again.id == first.id
    assert provider.calls == 0 and len(_rows()) == 1
    assert again.candidates[0].provider == kf.CANONICAL_REUSE_PROVIDER


# ── building 키프레임 + QA 전용 범프 → 재개, 중복/좌초 없음 ────────────────


def _worker_provider(delegate):
    return jobs.DurableImageProvider(
        delegate, run_id=RUN_ID, user_id=USER, pet_id=PET, provider_operation=jobs.OP_KEYFRAME,
    )


@pytest.mark.parametrize("bump", [_bump_keyframe_qa, _bump_canonical_qa])
def test_building_keyframe_resumes_after_qa_only_bump(storage, monkeypatch, bump):
    """틱 1: 제출 → 양보(building). 배포로 QA 버전만 오른 뒤 틱 2: 같은 행을 재개한다."""
    h, canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "1")
    jobs.__reset_for_tests()
    delegate = AsyncDelegate(statuses=(SUCCEEDED,))
    pinned = {"pinned_canonical_version_id": canonical.id, "pinned_canonical_version": canonical.version}

    with pytest.raises(jobs.ProviderWorkPending):
        _build_kf(h, [_worker_provider(delegate)], **pinned)
    rows = _rows()
    assert len(rows) == 1 and rows[0]["status"] == kf.STATUS_BUILDING
    assert delegate.submit_calls == 1

    bump(monkeypatch)
    keyframe = _build_kf(h, [_worker_provider(delegate)], **pinned)  # 새 워커 프로세스

    assert keyframe.id == rows[0]["id"] and keyframe.version == 1
    assert keyframe.status == kf.STATUS_COMPLETE
    assert delegate.submit_calls == 1, "유료 제출은 한 번뿐이다"
    assert len(_rows()) == 1, "building 버전이 좌초되거나 중복되지 않는다"
    assert len(jobs._MOCK_JOBS) == 1
    jobs.__reset_for_tests()


def test_lease_recovery_after_crash_in_postprocessing_with_qa_bump_adds_no_keyframe(storage, monkeypatch):
    """결과 수거 뒤 죽은 워커 + 그 사이 QA 버전 범프 — 재시작은 영수증과 행을 재사용한다."""
    h, canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "1")
    jobs.__reset_for_tests()
    delegate = AsyncDelegate(statuses=(SUCCEEDED,))
    pinned = {"pinned_canonical_version_id": canonical.id, "pinned_canonical_version": canonical.version}

    with pytest.raises(jobs.ProviderWorkPending):
        _build_kf(h, [_worker_provider(delegate)], **pinned)

    def dying_cutout(raw):
        raise MemoryError("worker died in matting")

    with pytest.raises(MemoryError):
        _run(
            kf.build_keyframe(
                user_id=USER, pet_id=PET, keyframe_role=ROLE, fetch_bytes=h.kf_fetch,
                providers=[_worker_provider(delegate)], cutout_fn=dying_cutout, **pinned,
            )
        )
    building_id = _rows()[0]["id"]

    _bump_keyframe_qa(monkeypatch)
    keyframe = _build_kf(h, [_worker_provider(delegate)], **pinned)

    assert keyframe.id == building_id
    assert delegate.submit_calls == 1 and len(jobs._MOCK_JOBS) == 1
    assert len(_rows()) == 1 and len(keyframe.candidates) == 1
    jobs.__reset_for_tests()


# ── 생성에 영향을 주는 키는 여전히 "달라진 것"이다 — 클래스별 1개 ──────────


def _bump_attr(module, name):
    def _apply(monkeypatch):
        monkeypatch.setattr(module, name, f"{name.lower()}-v999-test")
    _apply.__name__ = f"bump_{name}"
    return _apply


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(_bump_attr(spec_mod, "KEYFRAME_PROMPT_VERSION"), id="keyframe_prompt"),
        pytest.param(_bump_attr(spec_mod, "KEYFRAME_SPEC_VERSION"), id="keyframe_spec"),
        pytest.param(_bump_attr(kf, "KEYFRAME_BUILDER_VERSION"), id="keyframe_builder"),
        pytest.param(_bump_attr(canon, "CANONICAL_BUILDER_VERSION"), id="canonical_builder"),
        pytest.param(_bump_attr(sets, "SELECTION_ANALYZER_VERSION"), id="reference_set_analyzer"),
        pytest.param(_bump_attr(ids, "SIGNATURE_VERSION"), id="identity_analyzer"),
    ],
)
def test_generation_affecting_version_change_still_builds_a_new_keyframe(storage, monkeypatch, change):
    h, _canonical, provider, first = _complete_keyframe(monkeypatch, storage)
    calls = provider.calls

    change(monkeypatch)
    again = _build_kf(h, [provider])

    assert again.deduplicated is False
    assert again.id != first.id and again.version == 2
    assert provider.calls > calls
    assert len(_rows()) == 2


def test_provider_change_still_builds_a_new_keyframe(storage, monkeypatch):
    h, _canonical, provider, first = _complete_keyframe(monkeypatch, storage)
    other = FakeProvider("gpt_image", [GOOD()] * 10)
    again = _build_kf(h, [other])
    assert again.deduplicated is False and again.version == 2
    assert other.calls >= 1 and len(_rows()) == 2


def test_new_canonical_version_still_builds_a_new_keyframe(storage, monkeypatch):
    h, _canonical, provider, first = _complete_keyframe(monkeypatch, storage)
    canonical_v2 = _run(
        canon.build_canonical(
            user_id=USER, pet_id=PET, fetch_bytes=h.kf_fetch,
            providers=[FakeProvider("runway", [GOOD()])], cutout_fn=lambda raw: raw,
            skip_if_unchanged=False,
        )
    )
    assert canonical_v2.version == 2
    again = _build_kf(h, [provider])
    assert again.deduplicated is False and again.version == 2
    assert again.canonical_version_id == canonical_v2.id and len(_rows()) == 2


def test_stamp_with_unknown_extra_key_is_treated_as_changed(storage, monkeypatch):
    """모르는 키는 생성에 영향을 준다고 본다 — 조용히 낡은 자산을 재사용하지 않는다."""
    h, _canonical, provider, first = _complete_keyframe(monkeypatch, storage)
    row = next(r for r in kf._MOCK_KEYFRAMES if r["id"] == first.id)
    row["analyzer_versions"] = {**row["analyzer_versions"], "future_generation_key": "x1"}
    again = _build_kf(h, [provider])
    assert again.deduplicated is False and again.version == 2


# ── 레거시 저장 스탬프 (QA 키 포함, 투영 필드 없음) ─────────────────────────


def test_legacy_stored_stamp_with_old_qa_keys_is_reused(storage, monkeypatch):
    h, _canonical, provider, first = _complete_keyframe(monkeypatch, storage)
    row = next(r for r in kf._MOCK_KEYFRAMES if r["id"] == first.id)
    legacy = dict(row["analyzer_versions"])
    assert "keyframe_qa" in legacy and "canonical_qa" in legacy and "generation_versions" not in legacy
    legacy["keyframe_qa"] = "keyframe-qa-v0"
    legacy["canonical_qa"] = "canonical-qa-v3"
    row["analyzer_versions"] = legacy  # 예전 배포가 저장한 그대로 — 마이그레이션 없음
    calls = provider.calls

    again = _build_kf(h, [provider])

    assert again.deduplicated is True and again.id == first.id
    assert provider.calls == calls and len(_rows()) == 1
    # 저장된 스탬프는 손대지 않는다 (읽기 시점 투영).
    assert row["analyzer_versions"] == legacy


# ── QA 판정은 수정 전후 동일 ───────────────────────────────────────────────


def test_qa_decision_for_same_inputs_is_unchanged_by_the_fix(storage, monkeypatch):
    h, _canonical, provider, first = _complete_keyframe(monkeypatch, storage)
    before = first.candidates[0].qa_result

    _bump_keyframe_qa(monkeypatch)
    forced = _build_kf(h, [provider], skip_if_unchanged=False)  # 같은 입력으로 강제 재생성
    after = forced.candidates[0].qa_result

    assert after["decision"] == before["decision"] == "PASS"
    assert after["checks"] == before["checks"]
    assert after["identity_decision"] == before["identity_decision"]
    assert after["qa_version"] == "keyframe-qa-v999-test" and before["qa_version"] == "keyframe-qa-v1"


# ── 실행 오케스트레이터: 재시도 / 새 실행은 핀·최신 완료 키프레임을 그대로 쓴다 ──
# pet_generation_run_service._keyframe() 은 저장된 핀(run.keyframes[role]) 또는 같은
# 정본의 최신 완료 키프레임을 스탬프 비교 없이 재사용한다 — 빌더는 building 이거나
# 없을 때만 불린다. QA 버전 범프가 그 사이에 끼어도 키프레임 빌드 수는 늘지 않는다.

def _bump_all_qa(monkeypatch):
    _bump_keyframe_qa(monkeypatch)
    _bump_canonical_qa(monkeypatch)


def test_run_retry_after_qa_only_bump_reuses_pinned_keyframe(storage, monkeypatch):
    runs.__reset_for_tests()
    p7c.seed_intake()
    harness = p7c.PipelineHarness(monkeypatch, motion_status=mv.STATUS_FAILED)

    p7c.start(key="qa-bump-retry")
    failed = p7c.work()
    assert failed.status == runs.STATUS_FAILED and failed.last_error["code"] == "MOTION_QA_FAILED"
    pinned = dict(failed.keyframes)
    assert pinned and harness.counts["keyframe_build"] == 1

    _bump_all_qa(monkeypatch)
    queued = _run(runs.retry_generation_run(user_id=p7c.USER, run_id=failed.id))
    assert queued.status == runs.STATUS_QUEUED
    retried = p7c.work()

    assert retried.keyframes == pinned, "재시도는 핀된 키프레임을 그대로 쓴다"
    assert retried.canonical_version_id == failed.canonical_version_id
    assert harness.counts["keyframe_build"] == 1, "QA 범프로 키프레임이 다시 만들어지지 않는다"
    assert harness.counts["canonical_build"] == 1
    runs.__reset_for_tests()


def test_new_run_after_qa_only_bump_reuses_complete_keyframe(storage, monkeypatch):
    runs.__reset_for_tests()
    p7c.seed_intake()
    harness = p7c.PipelineHarness(monkeypatch)

    p7c.start(key="qa-bump-first")
    first = p7c.work()
    assert first.status == runs.STATUS_PUBLISHED
    harness.expose_latest = True

    _bump_all_qa(monkeypatch)
    p7c.start(key="qa-bump-second")
    second = p7c.work()

    assert second.id != first.id and second.status == runs.STATUS_PUBLISHED
    assert second.keyframes == first.keyframes
    assert harness.counts["keyframe_build"] == 1
    assert harness.counts["canonical_build"] == 1
    runs.__reset_for_tests()
