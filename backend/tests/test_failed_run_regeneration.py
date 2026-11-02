"""Failed motion regeneration lifecycle (run b4861dc2, PLAY_WITH_ME, 2026-10-30).

Live symptom: a PREMIUM_PRODUCT run ended FAILED/MOTION_QA_FAILED with its motion
version pinned. Every later request for the same action resolved to the same
subscription-mode idempotency key, revived the same run by retry, and the worker
re-read the pinned (failed) version instead of buying a new video — the same
MOTION_QA_FAILED was reported for the historical candidate each time.

These tests pin the fixed lifecycle:
  * active requests join (idempotent), READY assets are never regenerated
  * a deliberate new request after a terminal motion verdict creates a fresh run
    (new key, new motion version, new provider attempt) and preserves history
  * pre-verdict failures still resume the same run (durable receipts intact)
  * a live provider job blocks replacement (no double billing)
  * QA-only retry stays separate, TIMED_OUT counts as terminal for stale pins
  * pets are independent; reservations are charged/committed exactly once
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services import (
    credit_ledger,
    credit_reservation,
    durable_provider_jobs,
    motion_delivery_service as delivery,
    motion_spec,
    motion_video_service as motions,
    owned_assets,
    pet_generation_run_service as runs,
    pet_reference_service,
    premium_purchase,
    premium_run_fulfillment as fulfillment,
)

from .conftest import make_jpeg_bytes
from .test_phase7h_premium_repoint import (  # noqa: F401 — fixtures are picked up by name
    CANDIDATE_ID,
    PACKED_PATH,
    PET,
    USER,
    VERSION_ID,
    _balance,
    _grant,
    _isolated,
    _premium_harness,
    _run,
    seed_intake,
    storage,
)

MOTION = "PAW_HELLO"
KIND = f"ACTION:{MOTION}"
SUB_KEY = f"premium:{MOTION}:sub:{PET}"
ERROR_CANDIDATE_ID = "7h000000-0000-4000-8000-000000000699"
V2_ID = "7h000000-0000-4000-8000-000000000611"
C2_ID = "7h000000-0000-4000-8000-000000000612"
CID_B = "phase7c-other"
PET_B = f"pet_{CID_B}"


# ── helpers ────────────────────────────────────────────────────────────────


def _buy(kind: str = KIND):
    return _run(
        premium_purchase.purchase(
            user_id=USER, pet_id=PET, kind=kind, pet_image_url=None, api_base="https://api.test",
        )
    )


def _work():
    return _run(runs.process_next_generation_run(worker_id="regen-test-worker"))


def _submit(pet_id: str = PET, **kwargs) -> str:
    return _run(
        fulfillment.submit_premium_run(user_id=USER, pet_id=pet_id, action_id=MOTION, **kwargs)
    )


def _subscribe(monkeypatch):
    """구독 모드 — 예약/과금 없는 고정 열쇠 경로 (라이브 실측과 같은 모드)."""
    monkeypatch.setenv("PREMIUM_REQUIRES_SUBSCRIPTION", "1")
    from backend.services import subscription_store_service as sub_store
    from backend.services.subscription_webhook_service import handle_subscription_webhook

    sub_store._MOCK_SUBS.clear()
    sub_store._MOCK_EVENTS.clear()
    _run(
        handle_subscription_webhook(
            {
                "store_type": "mock",
                "notification_type": "INITIAL_BUY",
                "user_id": USER,
                "plan_id": "standard_subscription",
                "transaction_id": "tx_regen_initial",
            }
        )
    )


def _harness(monkeypatch, *, failing: bool = True):
    """7H 하네스 + 후보별 포장 + 첫 모션 버전을 라이브 실측처럼 ERROR/failed 로."""
    harness = _premium_harness(monkeypatch, MOTION, decision="PASS")

    async def package(**kwargs):
        harness._capture("delivery", kwargs)
        harness.counts["delivery"] += 1
        cand = next(c for c in motions._MOCK_CANDIDATES if c["id"] == kwargs.get("candidate_id"))
        cand["derived_video_path"] = f"{PACKED_PATH}.{cand['id'][-3:]}"
        cand["delivery_format"] = "packed_alpha"
        delivery._MOCK_DELIVERY_OBJECTS.add(cand["derived_video_path"])
        return SimpleNamespace(
            motion_version_id=kwargs.get("motion_version_id"),
            candidate_id=kwargs.get("candidate_id"),
            delivery_format="packed_alpha", deduplicated=False,
        )

    monkeypatch.setattr(delivery, "package_breathing_for_delivery", package)
    if failing:
        _make_motion_fail(harness)
    return harness


def _make_motion_fail(harness):
    """빌더가 돌려주는 버전 = 프로바이더 ERROR 후보 하나, 영상 없음 (run b4861dc2 그대로)."""
    harness.motion.status = motions.STATUS_FAILED
    harness.motion.selected_candidate_id = None
    harness.motion.candidates = [
        SimpleNamespace(
            id=ERROR_CANDIDATE_ID, selected=False, decision="ERROR",
            qa_result=None, raw_video_path=None, generation_metadata={},
        )
    ]
    motions._MOCK_VERSIONS[0].update(status=motions.STATUS_FAILED, selected_candidate_id=None)
    motions._MOCK_CANDIDATES[0].update(
        id=ERROR_CANDIDATE_ID, decision="ERROR", selected=False, raw_video_path=None,
    )


def _make_next_version_pass(harness):
    """다음 빌드는 새 버전(v2) + PASS 후보 — 진짜 새 프로바이더 시도의 결과."""
    passing = SimpleNamespace(
        id=C2_ID, selected=True, decision="PASS",
        qa_result={"identity_similarity": 0.9}, generation_metadata={},
    )
    harness.motion = SimpleNamespace(
        id=V2_ID, pet_id=PET, user_id=USER, motion_id=MOTION,
        motion_spec_version=motion_spec.MOTION_SPEC_VERSION,
        start_keyframe_id=harness.keyframe.id, start_keyframe_version=harness.keyframe.version,
        canonical_version_id=harness.canonical.id, version=2,
        status=motions.STATUS_COMPLETE, selected_candidate_id=C2_ID, candidates=[passing],
    )
    motions._MOCK_VERSIONS.append(
        {
            "id": V2_ID, "pet_id": PET, "user_id": USER, "motion_id": MOTION, "version": 2,
            "status": motions.STATUS_COMPLETE, "selected_candidate_id": C2_ID,
        }
    )
    motions._MOCK_CANDIDATES.append(
        {
            "id": C2_ID, "motion_version_id": V2_ID, "pet_id": PET, "user_id": USER,
            "motion_id": MOTION, "provider": "seedance", "attempt": 1,
            "raw_bucket": "user-assets", "raw_video_path": f"{USER}/v2/raw.mp4",
            "decision": "PASS", "selected": True, "generation_metadata": {},
        }
    )


def _spy_provider_runs(monkeypatch) -> list[str]:
    """모션 프로바이더가 어느 실행 id 로 묶여 만들어졌는가 — 새 시도의 증거."""
    seen: list[str] = []
    original = runs._video_providers

    def spy(run, motion_id):
        seen.append(run.id)
        return original(run, motion_id)

    monkeypatch.setattr(runs, "_video_providers", spy)
    return seen


def _rows():
    return [dict(r) for r in runs._MOCK_RUNS]


def _regen_marker(row: dict) -> dict:
    return dict(((row.get("provider_state") or {}).get("_operator") or {}).get(
        fulfillment.REGENERATION_MARKER
    ) or {})


def _fail_once(monkeypatch) -> dict:
    """구독 모드에서 구매 → 워커 → 최종 판정(MOTION_QA_FAILED). 실패 행 스냅샷."""
    assert _buy().submitted == [MOTION]
    failed = _work()
    assert failed.status == runs.STATUS_FAILED
    assert (failed.last_error or {}).get("code") == "MOTION_QA_FAILED"
    assert failed.motion_version_id == VERSION_ID
    assert len(runs._MOCK_RUNS) == 1
    return dict(runs._MOCK_RUNS[0])


# ══════════════════════════════════════════════════════════════════════════
# 1. FAILED(최종 판정) → 의도적 새 요청 → 새 실행·새 버전·새 프로바이더 시도
# ══════════════════════════════════════════════════════════════════════════


def test_terminal_qa_failure_then_new_request_generates_a_fresh_version(storage, monkeypatch):
    _subscribe(monkeypatch)
    seed_intake()
    harness = _harness(monkeypatch)
    provider_runs = _spy_provider_runs(monkeypatch)

    old = _fail_once(monkeypatch)
    assert old["idempotency_key"] == SUB_KEY
    assert harness.counts["motion_build"] == 1

    # 같은 사용자가 같은 액션을 다시 요청한다 — 라이브에서 같은 실행이 되살아나던 자리.
    again = _buy()
    assert again.status == "processing"
    assert again.submitted == [MOTION]
    assert again.credits_charged == 0
    assert len(runs._MOCK_RUNS) == 2

    fresh = runs._MOCK_RUNS[1]
    assert fresh["id"] != old["id"]
    assert fresh["status"] == runs.STATUS_QUEUED
    assert fresh["idempotency_key"] == f"{SUB_KEY}:regen:{old['id']}"
    assert fresh["motion_version_id"] is None and fresh["selected_candidate_id"] is None
    assert fresh["retry_count"] == 0
    assert fresh["reservation_ledger_id"] is None and fresh["credits_reserved"] == 0
    marker = _regen_marker(fresh)
    assert marker["supersedes_run_id"] == old["id"]
    assert marker["superseded_motion_version_id"] == VERSION_ID
    assert marker["reason"] == "MOTION_QA_FAILED"
    assert marker["reservation_inherited"] is False
    # 역사는 그대로 — 실패 실행·핀·오류·시도 횟수 전부 손대지 않았다.
    assert runs._MOCK_RUNS[0] == old

    _make_next_version_pass(harness)
    done = _work()
    assert done.id == fresh["id"]
    assert done.status == runs.STATUS_PUBLISHED
    assert done.motion_version_id == V2_ID and done.selected_candidate_id == C2_ID
    # 진짜 새 시도: 빌더가 다시 불렸고, 프로바이더는 새 실행 id 로 묶였다.
    assert harness.counts["motion_build"] == 2
    assert provider_runs == [old["id"], fresh["id"]]
    # 옛 실행/버전/후보는 그대로 남아 있다 (삭제·덮어쓰기 없음).
    assert runs._MOCK_RUNS[0] == old
    assert [v["id"] for v in motions._MOCK_VERSIONS] == [VERSION_ID, V2_ID]
    assert motions._MOCK_VERSIONS[0]["status"] == motions.STATUS_FAILED
    assert motions._MOCK_CANDIDATES[0]["decision"] == "ERROR"
    assert [a.product_key for a in owned_assets._MOCK] == [f"action:{MOTION}"]
    state = _run(premium_purchase.asset_state(USER, PET, (MOTION,)))
    assert MOTION in state.ready and not state.active and not state.missing


def test_second_terminal_failure_chains_to_the_latest_failed_run(storage, monkeypatch):
    _subscribe(monkeypatch)
    seed_intake()
    harness = _harness(monkeypatch)

    first = _fail_once(monkeypatch)
    _buy()
    second_id = runs._MOCK_RUNS[1]["id"]
    assert runs._MOCK_RUNS[1]["idempotency_key"] == f"{SUB_KEY}:regen:{first['id']}"

    # 대체 실행도 같은 방식으로 실패한다 (빌더가 여전히 ERROR 버전을 돌려준다).
    again = _work()
    assert again.id == second_id and again.status == runs.STATUS_FAILED
    assert harness.counts["motion_build"] == 2
    second = dict(runs._MOCK_RUNS[1])

    _buy()
    assert len(runs._MOCK_RUNS) == 3
    third = runs._MOCK_RUNS[2]
    assert third["idempotency_key"] == f"{SUB_KEY}:regen:{second_id}"
    assert _regen_marker(third)["supersedes_run_id"] == second_id
    assert runs._MOCK_RUNS[0] == first and runs._MOCK_RUNS[1] == second


# ══════════════════════════════════════════════════════════════════════════
# 2. 활성 중복 요청은 합류한다 / 3. READY 는 재생성하지 않는다
# ══════════════════════════════════════════════════════════════════════════


def test_duplicate_requests_join_the_active_replacement_run(storage, monkeypatch):
    _subscribe(monkeypatch)
    seed_intake()
    _harness(monkeypatch)
    old = _fail_once(monkeypatch)

    first = _submit()
    assert first != old["id"]
    assert _submit() == first
    assert _submit() == first
    dup = _buy()
    assert dup.status == "processing" and dup.submitted == [] and dup.credits_charged == 0
    assert len(runs._MOCK_RUNS) == 2
    assert [r["status"] for r in runs._MOCK_RUNS] == [runs.STATUS_FAILED, runs.STATUS_QUEUED]
    # 실패 실행을 직접 retry 해도 활성 대체 실행에 합류한다 — 활성은 하나뿐이다.
    joined = _run(runs.retry_generation_run(user_id=USER, run_id=old["id"]))
    assert joined.id == first
    assert runs._MOCK_RUNS[0]["status"] == runs.STATUS_FAILED


def test_published_asset_is_reused_never_regenerated(storage, monkeypatch):
    _subscribe(monkeypatch)
    seed_intake()
    harness = _harness(monkeypatch)
    old = _fail_once(monkeypatch)
    _buy()
    _make_next_version_pass(harness)
    done = _work()
    assert done.status == runs.STATUS_PUBLISHED

    ready = _buy()
    assert ready.status == "ready" and ready.submitted == [] and ready.credits_charged == 0
    assert _submit() == done.id
    assert len(runs._MOCK_RUNS) == 2
    assert harness.counts["motion_build"] == 2
    assert runs._MOCK_RUNS[0] == old
    assert [a.product_key for a in owned_assets._MOCK] == [f"action:{MOTION}"]


# ══════════════════════════════════════════════════════════════════════════
# 4. 펫 독립 / 5. 판정 전 실패는 같은 실행 resume / 6. 살아 있는 작업은 대체 금지
# ══════════════════════════════════════════════════════════════════════════


def _seed_other_pet():
    original = _run(
        pet_reference_service.record_original(
            user_id=USER, content_id=CID_B, data=make_jpeg_bytes(), mime_type="image/jpeg",
        )
    )
    _run(
        pet_reference_service.record_derived(
            user_id=USER, content_id=CID_B,
            object_path=f"{USER}/{CID_B}/references/cutout_{original.content_hash[:16]}.png",
            derived_kind="cutout_reference", parent_reference_id=original.id, mime_type="image/png",
        )
    )


def test_failed_run_on_one_pet_never_blocks_or_taints_another_pet(storage, monkeypatch):
    _subscribe(monkeypatch)
    seed_intake()
    _seed_other_pet()
    _harness(monkeypatch)
    old = _fail_once(monkeypatch)

    other_id = _submit(pet_id=PET_B)
    assert other_id != old["id"]
    assert len(runs._MOCK_RUNS) == 2
    other = runs._MOCK_RUNS[1]
    assert other["pet_id"] == PET_B and other["status"] == runs.STATUS_QUEUED
    # 다른 펫의 실패는 이 펫과 무관하다 — 기본 열쇠, 대체 표식 없음, 핀 없음.
    assert other["idempotency_key"] == f"premium:{MOTION}:sub:{PET_B}"
    assert _regen_marker(other) == {}
    assert runs._MOCK_RUNS[0] == old
    scope = dict(user_id=USER, motion_id=MOTION, request_kind=runs.REQUEST_PREMIUM_PRODUCT)
    assert _run(runs.latest_run_for_scope(pet_id=PET_B, **scope)).id == other_id
    assert _run(runs.latest_run_for_scope(pet_id=PET, **scope)).id == old["id"]
    assert _run(fulfillment.active_premium_motion_ids(USER, PET)) == set()
    assert _run(fulfillment.active_premium_motion_ids(USER, PET_B)) == {MOTION}


def test_pre_verdict_failure_still_resumes_the_same_run(storage, monkeypatch):
    _subscribe(monkeypatch)
    seed_intake()
    harness = _harness(monkeypatch, failing=False)
    from backend.services import action_keyframe_service

    real_build = action_keyframe_service.build_keyframe
    boom = {"armed": True}

    async def keyframe_boom(**kwargs):
        if boom["armed"]:
            boom["armed"] = False
            raise runs.PetGenerationRunError("KEYFRAME_BROKEN", "boom", status=503)
        return await real_build(**kwargs)

    monkeypatch.setattr(action_keyframe_service, "build_keyframe", keyframe_boom)

    _buy()
    failed = _work()
    assert failed.status == runs.STATUS_FAILED
    assert (failed.last_error or {}).get("code") == "KEYFRAME_BROKEN"
    assert failed.motion_version_id is None
    assert runs.terminal_motion_verdict(failed) is None

    # 판정 전 실패 → 같은 실행을 되살린다 (durable resume), 새 실행 없음.
    again = _buy()
    assert again.submitted == [MOTION]
    assert len(runs._MOCK_RUNS) == 1
    row = runs._MOCK_RUNS[0]
    assert row["id"] == failed.id and row["status"] == runs.STATUS_QUEUED
    assert row["retry_count"] == 1 and _regen_marker(row) == {}
    done = _work()
    assert done.id == failed.id and done.status == runs.STATUS_PUBLISHED
    assert harness.counts["motion_build"] == 1


def test_live_provider_job_blocks_replacement_and_resumes_instead(storage, monkeypatch):
    _subscribe(monkeypatch)
    seed_intake()
    _harness(monkeypatch)
    old = _fail_once(monkeypatch)
    # 모호한/살아 있는 외부 작업 영수증 — 대체 실행을 만들면 같은 생성에 이중 과금될 수 있다.
    durable_provider_jobs._MOCK_JOBS.append(
        {
            "id": "live-op", "run_id": old["id"], "user_id": USER, "pet_id": PET,
            "provider_operation": durable_provider_jobs.OP_MOTION,
            "phase_version_id": VERSION_ID, "submission_status": durable_provider_jobs.SUBMITTED,
            "external_job_id": "ext-live", "created_at": "2026-10-30T00:00:00+00:00",
        }
    )
    run = runs._to_run(runs._MOCK_RUNS[0])
    assert runs.terminal_motion_verdict(run) == "MOTION_QA_FAILED"
    assert runs.has_live_provider_work(run) is True

    assert _submit() == old["id"]
    assert len(runs._MOCK_RUNS) == 1
    row = runs._MOCK_RUNS[0]
    assert row["status"] == runs.STATUS_QUEUED and row["retry_count"] == old["retry_count"] + 1
    assert row["motion_version_id"] == VERSION_ID  # 핀 보존 — 재제출 없음
    assert _regen_marker(row) == {}


# ══════════════════════════════════════════════════════════════════════════
# 7. QA 전용 재평가(retry)는 별도로 남는다 / TIMED_OUT 은 종료 상태다
# ══════════════════════════════════════════════════════════════════════════


def test_qa_only_retry_keeps_the_pin_and_a_later_request_still_regenerates(storage, monkeypatch):
    _subscribe(monkeypatch)
    seed_intake()
    harness = _harness(monkeypatch)
    old = _fail_once(monkeypatch)

    # 명시적 retry = QA 재평가/durable resume: 핀 유지, 새 실행 없음.
    retried = _run(runs.retry_generation_run(user_id=USER, run_id=old["id"]))
    assert retried.id == old["id"] and retried.status == runs.STATUS_QUEUED
    assert retried.motion_version_id == VERSION_ID
    assert len(runs._MOCK_RUNS) == 1
    # 재평가가 활성인 동안의 새 요청은 합류한다 (대체 실행 없음).
    assert _submit() == old["id"]
    assert len(runs._MOCK_RUNS) == 1
    # 저장된 판정이 그대로면 같은 결과 — 생성은 일어나지 않는다.
    again = _work()
    assert again.id == old["id"] and again.status == runs.STATUS_FAILED
    assert harness.counts["motion_build"] == 1
    # 그 뒤 의도적 요청 → 이제 대체 실행.
    _buy()
    assert len(runs._MOCK_RUNS) == 2
    assert runs._MOCK_RUNS[1]["idempotency_key"] == f"{SUB_KEY}:regen:{old['id']}"


@pytest.mark.parametrize(
    "job_status,unpinned",
    [
        (durable_provider_jobs.TIMED_OUT, True),   # run b4861dc2 — 라이브 상태 그대로
        (durable_provider_jobs.COLLECTED, True),
        (durable_provider_jobs.FAILED, True),
        (durable_provider_jobs.SUBMITTED, False),
        (durable_provider_jobs.AMBIGUOUS, False),
    ],
)
def test_timed_out_provider_job_counts_as_terminal_for_stale_pin_release(monkeypatch, job_status, unpinned):
    """핀된 버전의 프롬프트가 옛 버전(v1 vs 현재)인데 TIMED_OUT 작업 하나가 핀 해제를 막았다."""
    from backend.services import motion_video_prompts

    content_id = PET.removeprefix("pet_")
    row = dict(
        id="b4861dc2-98ca-4329-a35f-1b0eb87b84e8", user_id=USER, pet_id=PET, content_id=content_id,
        motion_id="PLAY_WITH_ME", request_kind=runs.REQUEST_PREMIUM_PRODUCT,
        idempotency_key=f"premium:PLAY_WITH_ME:sub:{PET}", status=runs.STATUS_FAILED,
        current_stage=runs.STAGE_QA, identity_profile_id="identity", identity_profile_version=1,
        reference_set_id="references", reference_set_version=1,
        canonical_version_id="canonical", canonical_version=1,
        keyframes={"STAND_READY": {"id": "kf", "version": 1}},
        motion_spec_version=motion_spec.MOTION_SPEC_VERSION,
        motion_version_id="a6ca5f05-0eb0-4d63-9e17-a023b842072a", motion_version=1,
        selected_candidate_id=None, publication_id=None, provider_state={},
        last_error={"code": "MOTION_QA_FAILED", "stage": "QA"}, retry_count=2,
        created_at="2026-10-29T06:04:04+00:00",
    )
    runs._MOCK_RUNS.append(row)
    durable_provider_jobs._MOCK_JOBS.append(
        {
            "id": "4ea6ebf0", "run_id": row["id"], "provider_operation": durable_provider_jobs.OP_MOTION,
            "phase_version_id": row["motion_version_id"], "submission_status": job_status,
        }
    )
    durable_provider_jobs._MOCK_JOBS.append(
        {
            "id": "0a245f19", "run_id": row["id"], "provider_operation": durable_provider_jobs.OP_KEYFRAME,
            "phase_version_id": "65e0cff2", "submission_status": durable_provider_jobs.COLLECTED,
        }
    )
    current_prompt = motion_video_prompts.prompt_version_for_motion("PLAY_WITH_ME")
    assert current_prompt != "motion-video-prompt-v3-play-with-me-v1"

    async def pinned_version(**kwargs):
        return SimpleNamespace(
            id=row["motion_version_id"], motion_spec_version=motion_spec.MOTION_SPEC_VERSION,
            prompt_version="motion-video-prompt-v3-play-with-me-v1",
        )

    async def intake(*args, **kwargs):
        return content_id

    monkeypatch.setattr(motions, "get_motion_version", pinned_version)
    monkeypatch.setattr(runs, "_validate_intake", intake)

    retried = _run(runs.retry_generation_run(user_id=USER, run_id=row["id"]))
    assert retried.status == runs.STATUS_QUEUED and retried.retry_count == 3
    if unpinned:
        assert retried.motion_version_id is None and retried.motion_version is None
        assert retried.current_stage == runs.STAGE_MOTION_SPEC
    else:
        assert retried.motion_version_id == row["motion_version_id"]
        assert retried.current_stage == runs.STAGE_QA
    assert len(runs._MOCK_RUNS) == 1  # 핀만 비운다 — 행 삭제 없음


@pytest.mark.parametrize(
    "status,code,pinned,expected",
    [
        (runs.STATUS_FAILED, "MOTION_QA_FAILED", True, "MOTION_QA_FAILED"),
        (runs.STATUS_FAILED, "MOTION_QA_REVIEW", True, "MOTION_QA_REVIEW"),
        (runs.STATUS_FAILED, "MOTION_QA_FAILED", False, None),
        (runs.STATUS_FAILED, "KEYFRAME_BROKEN", True, None),
        (runs.STATUS_FAILED, "PROVIDER_RECOVERY_REQUIRED", True, None),
        (runs.STATUS_CANCELLED, "RUN_CANCELLED", True, None),
        (runs.STATUS_PUBLISHED, None, True, None),
        (runs.STATUS_QUEUED, None, True, None),
    ],
)
def test_terminal_motion_verdict_classification(status, code, pinned, expected):
    run = runs._to_run(
        {
            "id": "r", "user_id": USER, "pet_id": PET, "motion_id": MOTION,
            "request_kind": runs.REQUEST_PREMIUM_PRODUCT, "status": status,
            "motion_version_id": ("v" if pinned else None),
            "last_error": ({"code": code} if code else None),
        }
    )
    assert runs.terminal_motion_verdict(run) == expected


# ══════════════════════════════════════════════════════════════════════════
# 8. 크레딧 안전 — 시도당 한 번 과금, 확정 한 번, 해제된 예약은 다시 쓰지 않는다
# ══════════════════════════════════════════════════════════════════════════


def test_credit_mode_regeneration_after_refund_charges_once_per_attempt_and_commits_once(
    storage, monkeypatch
):
    seed_intake()
    harness = _harness(monkeypatch)
    _run(_grant(5))

    first = _buy()
    assert first.credits_charged == 1 and _run(_balance()) == 4
    r1 = runs._MOCK_RUNS[0]["reservation_ledger_id"]
    assert r1

    failed = _work()
    assert failed.status == runs.STATUS_FAILED
    assert (failed.last_error or {}).get("code") == "MOTION_QA_FAILED"
    # 최종 판정 → 예약 **해제**, 구매는 환불 표시, 잔액 복귀.
    assert credit_reservation._MOCK_STATE[r1] == credit_ledger.STATE_RELEASED
    assert _run(_balance()) == 5
    assert _run(premium_purchase.find_active_purchase(USER, PET, KIND)) is None
    old = dict(runs._MOCK_RUNS[0])

    # 새 구매 = 새 예약 = 새 실행 (해제된 예약은 절대 물려받지 않는다).
    second = _buy()
    assert second.credits_charged == 1 and _run(_balance()) == 4
    assert len(runs._MOCK_RUNS) == 2
    fresh = runs._MOCK_RUNS[1]
    r2 = fresh["reservation_ledger_id"]
    assert r2 and r2 != r1
    assert fresh["idempotency_key"] == f"premium:{MOTION}:{r2}"
    assert fresh["credits_reserved"] == 1
    marker = _regen_marker(fresh)
    assert marker["supersedes_run_id"] == old["id"] and marker["reservation_inherited"] is False
    assert runs._MOCK_RUNS[0] == old

    _make_next_version_pass(harness)
    done = _work()
    assert done.status == runs.STATUS_PUBLISHED and done.motion_version_id == V2_ID
    assert credit_reservation._MOCK_STATE[r2] == credit_ledger.STATE_COMMITTED
    assert credit_reservation._MOCK_STATE[r1] == credit_ledger.STATE_RELEASED
    assert _run(_balance()) == 4
    owned = [a for a in owned_assets._MOCK if a.product_key == f"action:{MOTION}"]
    assert len(owned) == 1 and owned[0].ledger_id == r2 and owned[0].credits_spent == 1
    assert harness.counts["motion_build"] == 2

    # READY 뒤 재구매 → 0원, 실행 없음.
    ready = _buy()
    assert ready.credits_charged == 0 and ready.status == "ready"
    assert len(runs._MOCK_RUNS) == 2 and _run(_balance()) == 4


def test_fresh_run_inherits_a_still_open_reservation_when_the_request_carries_none(
    storage, monkeypatch
):
    """환불되지 않은 기존 구매의 재요청(예: 번들의 다른 대상이 READY) — 예약은 한 번만 확정된다."""
    seed_intake()
    harness = _harness(monkeypatch)
    _run(_grant(3))

    async def keep_reservation(stopped):
        return None  # 다른 대상이 READY 라 해제가 생략된 상황을 흉내 낸다

    monkeypatch.setattr(runs, "_reconcile_premium_after_stop", keep_reservation)

    _buy()
    r1 = runs._MOCK_RUNS[0]["reservation_ledger_id"]
    failed = _work()
    assert failed.status == runs.STATUS_FAILED
    assert credit_reservation._MOCK_STATE[r1] == credit_ledger.STATE_RESERVED
    assert _run(_balance()) == 2

    fresh_id = _submit(reservation_ledger_id=None, credits_reserved=0)
    assert fresh_id != failed.id and len(runs._MOCK_RUNS) == 2
    fresh = runs._MOCK_RUNS[1]
    assert fresh["reservation_ledger_id"] == r1 and fresh["credits_reserved"] == 1
    assert _regen_marker(fresh)["reservation_inherited"] is True
    assert fresh["idempotency_key"] == SUB_KEY  # 기본 열쇠가 비어 있으면 접미사 없음

    _make_next_version_pass(harness)
    done = _work()
    assert done.id == fresh_id and done.status == runs.STATUS_PUBLISHED
    assert credit_reservation._MOCK_STATE[r1] == credit_ledger.STATE_COMMITTED
    assert _run(_balance()) == 2  # 이중 과금 없음
    owned = [a for a in owned_assets._MOCK if a.product_key == f"action:{MOTION}"]
    assert len(owned) == 1 and owned[0].ledger_id == r1 and owned[0].credits_spent == 1
