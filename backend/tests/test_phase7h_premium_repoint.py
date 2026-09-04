"""Phase 7H: 기존 상용 모션을 새 생성 시스템으로 재지향 — 상거래 계약 보존.

BLINKING 을 끝까지 증명한다 (구매 → 실행 → PASS/REVIEW → 이행/되돌림).
프로바이더/포장 실코덱은 부르지 않는다 — 단계 서비스는 7C 하네스로 목업하고,
상거래(지갑·예약·소유·포인터·카탈로그)는 실제 mock 저장소로 검증한다.
"""

from __future__ import annotations

from types import SimpleNamespace

import anyio
import pytest

from backend.services import (
    credit_ledger,
    credit_reservation,
    generated_motions_service as motions_svc,
    motion_delivery_service as delivery,
    motion_spec,
    motion_video_service as motions,
    owned_assets,
    pet_generation_run_service as runs,
    pet_reference_service,
    pet_registry,
    premium_generation,
    premium_motion_finalization as finalization,
    premium_purchase,
    premium_run_fulfillment as fulfillment,
    wallet_service,
)
from backend.scenarios.pet_scenarios import THEME_INDEPENDENT_PLACE_ID

from .test_phase7c_generation_runs import CID, PET, USER, PipelineHarness, seed_intake

VERSION_ID = "7h000000-0000-4000-8000-000000000601"
CANDIDATE_ID = "7h000000-0000-4000-8000-000000000602"
RAW_PATH = f"{USER}/{CID}/motions/blinking/v1/seedance_a1_raw.mp4"
PACKED_PATH = f"{USER}/{CID}/motions/blinking/v1/seedance_a1_packed.mp4"


def _run(awaitable):
    return anyio.run(lambda: awaitable)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("SEEDANCE_TRANSPORT", "runway")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    # 크레딧 모드 — 예약/확정/해제 원장을 그대로 검증한다. 구독 모드는 별도 테스트.
    monkeypatch.setenv("PREMIUM_REQUIRES_SUBSCRIPTION", "0")
    for svc in (
        runs, pet_reference_service, pet_registry, motions, delivery,
        finalization, premium_purchase, owned_assets, credit_reservation,
        credit_ledger,
    ):
        svc.__reset_for_tests()
    wallet_service._MOCK_WALLETS.clear()
    motions_svc._MOCK_MOTIONS.clear()
    yield
    for svc in (
        runs, pet_reference_service, pet_registry, motions, delivery,
        finalization, premium_purchase, owned_assets, credit_reservation,
        credit_ledger,
    ):
        svc.__reset_for_tests()
    wallet_service._MOCK_WALLETS.clear()
    motions_svc._MOCK_MOTIONS.clear()


@pytest.fixture
def storage(monkeypatch):
    from backend.services import supabase_assets

    async def upload(path, data, content_type):
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", upload)
    return None


def _premium_harness(monkeypatch, motion_id: str = "BLINKING", *, decision: str = "PASS"):
    """7C 하네스를 상용 모션으로 돌려 세운다 + 실제 mock 후보 행을 심는다."""
    harness = PipelineHarness(
        monkeypatch, motion_status=("complete" if decision == "PASS" else "review")
    )
    harness.motion.motion_id = motion_id
    harness.motion.id = VERSION_ID
    selected = SimpleNamespace(
        id=CANDIDATE_ID,
        selected=(decision == "PASS"),
        decision=decision,
        qa_result={"identity_similarity": 0.9},
    )
    harness.motion.candidates = [selected]
    harness.motion.selected_candidate_id = CANDIDATE_ID if decision == "PASS" else None
    if decision != "PASS":
        harness.review_candidate = selected

    async def resolve_spec(**kwargs):
        harness._capture("motion_spec", kwargs)
        return {
            "motion_id": motion_id,
            "motion_spec_version": motion_spec.MOTION_SPEC_VERSION,
            "start_keyframe": {"keyframe_id": harness.keyframe.id, "version": 1},
            "canonical_version_id": harness.canonical.id,
        }

    monkeypatch.setattr(motion_spec, "resolve_video_generation_spec", resolve_spec)

    # Phase 6 정본 행 (mock 저장소) — 이행 확정이 실제로 읽는다.
    motions._MOCK_VERSIONS.append(
        {
            "id": VERSION_ID,
            "pet_id": PET,
            "user_id": USER,
            "motion_id": motion_id,
            "version": 1,
            "status": "complete" if decision == "PASS" else "review",
            "selected_candidate_id": (CANDIDATE_ID if decision == "PASS" else None),
        }
    )
    motions._MOCK_CANDIDATES.append(
        {
            "id": CANDIDATE_ID,
            "motion_version_id": VERSION_ID,
            "pet_id": PET,
            "user_id": USER,
            "motion_id": motion_id,
            "provider": "seedance",
            "attempt": 1,
            "raw_bucket": "user-assets",
            "raw_video_path": RAW_PATH,
            "decision": decision,
            "selected": decision == "PASS",
            "generation_metadata": {},
        }
    )

    async def package(**kwargs):
        # 실코덱 없이 "포장 완료" 상태를 실제 mock 행에 남긴다 (Phase 7F 계약).
        harness._capture("delivery", kwargs)
        harness.counts["delivery"] += 1
        cand = motions._MOCK_CANDIDATES[0]
        cand["derived_video_path"] = PACKED_PATH
        cand["delivery_format"] = "packed_alpha"
        delivery._MOCK_DELIVERY_OBJECTS.add(PACKED_PATH)
        return SimpleNamespace(
            motion_version_id=VERSION_ID, candidate_id=kwargs.get("candidate_id"),
            delivery_format="packed_alpha", deduplicated=False,
        )

    monkeypatch.setattr(delivery, "package_breathing_for_delivery", package)

    # 레거시 이행이 불리면 그 자체가 실패다 (재지향 증명의 핵심).
    async def legacy_submit(**kwargs):
        raise AssertionError("legacy premium_generation.submit_premium_action was called")

    monkeypatch.setattr(premium_generation, "submit_premium_action", legacy_submit)
    return harness


async def _grant(credits: int) -> None:
    await wallet_service.add_credits(USER, credits)


async def _balance() -> int:
    return (await wallet_service.get_wallet(USER, create_if_missing=True)).current_credits


def _buy(kind: str = "ACTION:BLINKING"):
    return _run(
        premium_purchase.purchase(
            user_id=USER, pet_id=PET, kind=kind, pet_image_url=None,
            api_base="https://api.test",
        )
    )


def _work():
    return _run(runs.process_next_generation_run(worker_id="phase7h-test-worker"))


# ══════════════════════════════════════════════════════════════════════════
# 1. product_key ↔ motion_id 매핑
# ══════════════════════════════════════════════════════════════════════════


def test_product_key_maps_to_physical_motion_id():
    assert fulfillment.motion_id_for_product("idle:BLINKING") == "BLINKING"
    assert fulfillment.motion_id_for_product("idle:EAR_TWITCHING") == "EAR_TWITCHING"
    assert fulfillment.motion_id_for_product("idle:HEAD_TILTING") == "HEAD_TILTING"
    assert fulfillment.motion_id_for_product("idle:TAIL_WAGGING") == "TAIL_WAGGING"
    assert fulfillment.motion_id_for_product("action:COME_CLOSER") == "COME_CLOSER"
    # 미판매 신모션은 매핑되지 않는다 — 카탈로그 결정 전까지 이행 불가.
    assert fulfillment.motion_id_for_product("idle:PET_HEAD") is None
    assert fulfillment.motion_id_for_product("action:RUN") is None
    # 역방향(모션 → 상품 키)은 기존 규약 하나만 쓴다.
    for motion in fulfillment.PREMIUM_MOTIONS:
        key = owned_assets.product_key_for_action(motion)
        assert fulfillment.motion_id_for_product(key) == motion


def test_unsellable_motions_are_rejected_by_run_validation(storage):
    seed_intake()
    for motion in ("PET_HEAD", "LOOK_UP", "HAPPY", "RUN", "WALK"):
        with pytest.raises(runs.PetGenerationRunError) as e:
            _run(
                runs.start_generation_run(
                    user_id=USER, pet_id=PET, motion_id=motion,
                    request_kind=runs.REQUEST_PREMIUM_PRODUCT,
                    idempotency_key=f"x:{motion}",
                )
            )
        assert e.value.code == "UNSUPPORTED_MOTION"


# ══════════════════════════════════════════════════════════════════════════
# 2–8. BLINKING 끝까지 — 구매 → 실행 → PASS → 이행
# ══════════════════════════════════════════════════════════════════════════


def test_blinking_purchase_creates_run_and_pass_finalizes_commerce(storage, monkeypatch):
    seed_intake()
    harness = _premium_harness(monkeypatch, "BLINKING", decision="PASS")
    _run(_grant(5))

    result = _buy()
    assert result.credits_charged == 1
    assert result.submitted == ["BLINKING"]
    assert result.status == "processing"
    assert _run(_balance()) == 4

    # 실행이 만들어졌고 상거래 맥락이 계보로 실렸다.
    assert len(runs._MOCK_RUNS) == 1
    row = runs._MOCK_RUNS[0]
    assert row["motion_id"] == "BLINKING"
    assert row["request_kind"] == "PREMIUM_PRODUCT"
    assert row["product_key"] == "idle:BLINKING"
    assert row["reservation_ledger_id"]
    reservation_id = row["reservation_ledger_id"]
    assert credit_reservation._MOCK_STATE[reservation_id] == credit_ledger.STATE_RESERVED

    # 워커가 처리한다 → PASS → 포장 → 이행 확정.
    done = _work()
    assert done.status == runs.STATUS_PUBLISHED
    assert harness.counts["delivery"] == 1
    assert harness.counts["publication"] == 0  # BREATHING 발행(7A)은 불리지 않는다

    # 예약이 확정됐다 — 환불도 이중 과금도 없다.
    assert credit_reservation._MOCK_STATE[reservation_id] == credit_ledger.STATE_COMMITTED
    assert _run(_balance()) == 4

    # 소유 원장 + 계보.
    owned = [a for a in owned_assets._MOCK if a.product_key == "idle:BLINKING"]
    assert len(owned) == 1
    asset = owned[0]
    assert asset.ledger_id == reservation_id
    assert asset.credits_spent == 1
    assert asset.lineage["generation_run_id"] == done.id
    assert asset.lineage["pet_motion_version_id"] == VERSION_ID
    assert asset.lineage["selected_candidate_id"] == CANDIDATE_ID
    assert asset.lineage["publication_id"] == done.publication_id
    assert asset.lineage["delivery_object_path"] == PACKED_PATH
    assert asset.lineage["delivery_format"] == "packed_alpha"
    assert asset.lineage["product_key"] == "idle:BLINKING"

    # 발행 원장 (일반화된 publication).
    pubs = finalization._MOCK_PUBLICATIONS
    assert len(pubs) == 1
    assert pubs[0]["motion_id"] == "BLINKING"
    assert pubs[0]["object_path"] == PACKED_PATH

    # 현재 재생 포인터가 packed 파생물을 가리킨다 — Behavior Library 가 읽는 표다.
    motion = _run(
        motions_svc.find_motion_for_key(USER, PET, THEME_INDEPENDENT_PLACE_ID, "BLINKING")
    )
    assert motion is not None
    assert PACKED_PATH in motion.video_url

    # 상태 조회(발견 경로)도 READY 로 본다 → Behavior Library READY.
    state = _run(premium_purchase.asset_state(USER, PET, ("BLINKING",)))
    assert "BLINKING" in state.ready
    assert state.active == [] and state.missing == []


def test_retry_and_second_purchase_do_not_duplicate(storage, monkeypatch):
    seed_intake()
    _premium_harness(monkeypatch, "BLINKING", decision="PASS")
    _run(_grant(5))

    first = _buy()
    assert first.credits_charged == 1
    # 같은 사용자가 생성 완료 전 다시 산다 → 진행 중이므로 0원, 실행도 1개 그대로.
    again = _buy()
    assert again.credits_charged == 0
    assert again.status == "processing"
    assert len(runs._MOCK_RUNS) == 1

    done = _work()
    assert done.status == runs.STATUS_PUBLISHED
    # 워커 재시도 — 더 집을 것이 없다 (실행은 종료됐다).
    assert _work() is None
    # READY 후 재구매 → 0원, 새 실행 없음.
    ready = _buy()
    assert ready.credits_charged == 0
    assert ready.status == "ready"
    assert len(runs._MOCK_RUNS) == 1
    assert _run(_balance()) == 4
    assert len([a for a in owned_assets._MOCK if a.product_key == "idle:BLINKING"]) == 1


def test_finalization_is_idempotent(storage, monkeypatch):
    seed_intake()
    _premium_harness(monkeypatch, "BLINKING", decision="PASS")
    _run(_grant(5))
    _buy()
    done = _work()
    assert done.status == runs.STATUS_PUBLISHED

    # 확정을 직접 재호출 (워커 재시작/재전송 시나리오) — 전부 재사용된다.
    repeat = _run(
        finalization.finalize_premium_motion(
            run_id=done.id, user_id=USER, pet_id=PET, motion_id="BLINKING",
            motion_version_id=VERSION_ID, motion_version=1, candidate_id=CANDIDATE_ID,
            product_key="idle:BLINKING",
            reservation_ledger_id=runs._MOCK_RUNS[0]["reservation_ledger_id"],
            credits_reserved=1,
        )
    )
    assert repeat.deduplicated is True
    assert len(finalization._MOCK_PUBLICATIONS) == 1
    assert len([a for a in owned_assets._MOCK if a.product_key == "idle:BLINKING"]) == 1
    assert _run(_balance()) == 4


# ══════════════════════════════════════════════════════════════════════════
# 5. REVIEW / FAIL — 발행도 확정도 없다
# ══════════════════════════════════════════════════════════════════════════


def test_review_does_not_publish_or_commit_and_releases_reservation(storage, monkeypatch):
    seed_intake()
    harness = _premium_harness(monkeypatch, "BLINKING", decision="REVIEW")
    _run(_grant(5))

    result = _buy()
    assert result.credits_charged == 1
    assert _run(_balance()) == 4
    reservation_id = runs._MOCK_RUNS[0]["reservation_ledger_id"]

    done = _work()
    assert done.status == runs.STATUS_FAILED
    assert (done.last_error or {}).get("code") == "MOTION_QA_REVIEW"

    # QA 상태는 진실 그대로 — 발행/소유/포인터/확정 전부 없음.
    assert finalization._MOCK_PUBLICATIONS == []
    assert [a for a in owned_assets._MOCK if a.product_key == "idle:BLINKING"] == []
    assert motions._MOCK_CANDIDATES[0]["decision"] == "REVIEW"
    assert (
        _run(motions_svc.find_motion_for_key(USER, PET, THEME_INDEPENDENT_PLACE_ID, "BLINKING"))
        is None
    )
    # REVIEW 후보도 포장은 된다 (개발 재생용, 7G 계약 그대로).
    assert harness.counts["delivery"] == 1

    # 예약은 **해제**됐다 (환불이 아니라) — 잔액이 돌아온다.
    assert credit_reservation._MOCK_STATE[reservation_id] == credit_ledger.STATE_RELEASED
    assert _run(_balance()) == 5

    # 되돌림 뒤 재구매는 새로 과금하고 **새 예약 → 새 실행**을 만든다 — 환불된
    # 실패 실행에 갇히지 않는다 (_idempotency_key 의 계약).
    retry = _buy()
    assert retry.credits_charged == 1
    assert len(runs._MOCK_RUNS) == 2
    assert runs._MOCK_RUNS[1]["status"] == runs.STATUS_QUEUED


def test_failed_run_releases_reservation_once(storage, monkeypatch):
    seed_intake()
    harness = _premium_harness(monkeypatch, "BLINKING", decision="PASS")

    # 키프레임 단계에서 터뜨린다 — 프로바이더 이전 실패.
    async def keyframe_boom(**kwargs):
        raise runs.PetGenerationRunError("KEYFRAME_BROKEN", "boom", status=503)

    monkeypatch.setattr(
        __import__("backend.services.action_keyframe_service", fromlist=["x"]),
        "build_keyframe",
        keyframe_boom,
    )
    _run(_grant(3))
    _buy()
    reservation_id = runs._MOCK_RUNS[0]["reservation_ledger_id"]

    done = _work()
    assert done.status == runs.STATUS_FAILED
    assert credit_reservation._MOCK_STATE[reservation_id] == credit_ledger.STATE_RELEASED
    assert _run(_balance()) == 3
    assert finalization._MOCK_PUBLICATIONS == []

    # 종료 판정을 다시 불러도(웹훅류 재전송) 이중 해제/이중 환불이 없다.
    _run(
        fulfillment.reconcile_failed_run(
            user_id=USER, pet_id=PET, motion_id="BLINKING",
            reservation_ledger_id=reservation_id,
        )
    )
    assert _run(_balance()) == 3


# ══════════════════════════════════════════════════════════════════════════
# 3. 구독 모드 — 과금 없음, 이행은 동일
# ══════════════════════════════════════════════════════════════════════════


def test_subscription_mode_charges_nothing_and_still_uses_new_run(
    storage, monkeypatch
):
    monkeypatch.setenv("PREMIUM_REQUIRES_SUBSCRIPTION", "1")
    seed_intake()
    _premium_harness(monkeypatch, "BLINKING", decision="PASS")
    # 활성 구독을 실제 웹훅 경로로 심는다 (test_subscription_entitlement 와 동일).
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
                "transaction_id": "tx_7h_initial",
            }
        )
    )
    _run(_grant(2))
    # INITIAL_BUY 가 플랜 보너스 크레딧을 지급할 수 있다 — 절대값이 아니라
    # **변화 없음**을 검증한다: 구독 모드 이행은 지갑을 건드리지 않는다.
    before = _run(_balance())
    ledger_before = len(credit_ledger.mock_entries())

    result = _buy()
    assert result.credits_charged == 0
    assert result.submitted == ["BLINKING"]
    assert _run(_balance()) == before
    row = runs._MOCK_RUNS[0]
    assert row["reservation_ledger_id"] is None
    assert row["credits_reserved"] == 0

    done = _work()
    assert done.status == runs.STATUS_PUBLISHED
    assert _run(_balance()) == before  # 구독 + 크레딧 이중 과금 없음
    owned = [a for a in owned_assets._MOCK if a.product_key == "idle:BLINKING"]
    assert len(owned) == 1
    assert owned[0].credits_spent == 0
    assert owned[0].source == owned_assets.SOURCE_FREE
    assert len(credit_ledger.mock_entries()) == ledger_before  # 이행이 원장을 만들지 않았다


# ══════════════════════════════════════════════════════════════════════════
# 9. 테마 무진입 (하네스 _capture 가 모든 단계에서 강제하지만, 명시로 한 번 더)
# ══════════════════════════════════════════════════════════════════════════


def test_theme_never_enters_generation(storage, monkeypatch):
    seed_intake()
    harness = _premium_harness(monkeypatch, "BLINKING", decision="PASS")
    _run(_grant(2))
    _buy()
    _work()
    # PipelineHarness._capture 는 매 단계에서 theme/scene/background 키를 금지한다.
    # 여기서는 실행 행 자체에도 테마가 없음을 못박는다.
    row = runs._MOCK_RUNS[0]
    assert not any("theme" in k.lower() or "scene" in k.lower() for k in row)
    assert harness.counts["delivery"] == 1


# ══════════════════════════════════════════════════════════════════════════
# 나머지 아이들 모션 + COME_CLOSER — 같은 어댑터
# ══════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════
# 10. Behavior Library 발견 경로 — 기존 상품 키/가격/상태 그대로
# ══════════════════════════════════════════════════════════════════════════


def test_behavior_library_discovery_shows_legacy_keys_and_run_progress(
    storage, monkeypatch
):
    from fastapi import FastAPI

    from backend.routers import premium_v1

    from .conftest import ASGITestClient

    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    seed_intake()
    _premium_harness(monkeypatch, "BLINKING", decision="PASS")
    _run(_grant(3))
    _buy()  # BLINKING 실행이 진행 중이다 (워커 미실행)

    app = FastAPI()
    app.include_router(premium_v1.router, prefix="/api")
    client = ASGITestClient(app)
    response = client.get(
        f"/api/v1/pet/premium/assets?pet_id={PET}",
        headers={"Authorization": f"Bearer test:{USER}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # 레거시 카탈로그 키가 그대로 보인다 — 상품/가격 결정은 바뀌지 않았다.
    assert body["idle_events"] == ["BLINKING", "EAR_TWITCHING", "HEAD_TILTING", "TAIL_WAGGING"]
    assert body["action_events"] == ["COME_CLOSER"]
    assert body["prices"]["idle:BLINKING"] == 1
    assert body["prices"]["action:COME_CLOSER"] == 1
    # 새 실행의 진행 상태가 기존 'generating' 계약으로 흘러나온다.
    assert "BLINKING" in body["generating"]

    # 워커 완료 후에는 READY 로 옮겨 간다.
    done = _work()
    assert done.status == runs.STATUS_PUBLISHED
    after = client.get(
        f"/api/v1/pet/premium/assets?pet_id={PET}",
        headers={"Authorization": f"Bearer test:{USER}"},
    ).json()
    assert "BLINKING" in after["ready"]
    assert PACKED_PATH in after["ready"]["BLINKING"]
    assert "BLINKING" not in after["generating"]
    # Phase 7I.1 — 명시 전달 포맷이 실린다: 새 시스템 자산은 packed_alpha.
    assert after["ready_assets"]["BLINKING"]["delivery_format"] == "packed_alpha"
    assert after["ready_assets"]["BLINKING"]["url"] == after["ready"]["BLINKING"]


def test_discovery_resigns_urls_and_reports_mixed_formats(storage, monkeypatch):
    """Phase 7I.1 — 발견은 호출 시점 재서명 + 모션별 포맷. 레거시는 None 포맷."""
    from fastapi import FastAPI

    from backend.routers import premium_v1
    from backend.services import asset_url_refresh

    from .conftest import ASGITestClient

    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    seed_intake()
    # 소유권 귀속 (trust-on-first-use) — 포인터만 직접 심는 경로라 먼저 만진다.
    _run(premium_purchase.assert_pet_owned(USER, PET))

    # 새 시스템 packed 자산 + 레거시(블랙 플레이트) 자산이 섞여 있다.
    packed_stored = (
        f"https://old.supabase.co/storage/v1/object/sign/user-assets/{PACKED_PATH}?token=stale"
    )
    legacy_stored = (
        "https://old.supabase.co/storage/v1/object/sign/user-assets/"
        f"{USER}/{CID}/library/COME_CLOSER_abc123.mp4?token=stale"
    )
    _run(
        motions_svc.record_pointer(
            user_id=USER, pet_id=PET, place_id=THEME_INDEPENDENT_PLACE_ID,
            action_id="BLINKING", video_url=packed_stored,
        )
    )
    _run(
        motions_svc.record_pointer(
            user_id=USER, pet_id=PET, place_id=THEME_INDEPENDENT_PLACE_ID,
            action_id="COME_CLOSER", video_url=legacy_stored,
        )
    )
    signed: list[str] = []

    def fresh_sign(obj, **kwargs):
        signed.append(obj.path)
        return f"https://storage.test/{obj.bucket}/{obj.path}?token=fresh-{len(signed)}"

    monkeypatch.setattr(asset_url_refresh, "sign_object", fresh_sign)

    app = FastAPI()
    app.include_router(premium_v1.router, prefix="/api")
    client = ASGITestClient(app)
    body = client.get(
        f"/api/v1/pet/premium/assets?pet_id={PET}",
        headers={"Authorization": f"Bearer test:{USER}"},
    ).json()

    # 저장된 만료 서명이 아니라 **이번 요청의** 서명이 나간다.
    blink = body["ready_assets"]["BLINKING"]
    closer = body["ready_assets"]["COME_CLOSER"]
    assert "token=stale" not in blink["url"] and "token=fresh-" in blink["url"]
    assert "token=stale" not in closer["url"] and "token=fresh-" in closer["url"]
    # 혼합 포맷: 새 시스템 → packed_alpha, 레거시 → None (브라우저 기존 규칙).
    assert blink["delivery_format"] == "packed_alpha"
    assert closer["delivery_format"] is None
    # 구클라이언트 호환 필드(ready)도 같은 새 서명을 싣는다.
    assert body["ready"]["BLINKING"] == blink["url"]
    assert body["ready"]["COME_CLOSER"] == closer["url"]


@pytest.mark.parametrize("motion", ["EAR_TWITCHING", "HEAD_TILTING", "TAIL_WAGGING", "COME_CLOSER"])
def test_other_commercial_motions_flow_through_same_adapter(storage, monkeypatch, motion):
    seed_intake()
    _premium_harness(monkeypatch, motion, decision="PASS")
    _run(_grant(2))

    result = _buy(kind=f"ACTION:{motion}")
    assert result.credits_charged == 1
    assert result.submitted == [motion]

    done = _work()
    assert done.status == runs.STATUS_PUBLISHED
    key = owned_assets.product_key_for_action(motion)
    assert [a.product_key for a in owned_assets._MOCK] == [key]
    pointer = _run(
        motions_svc.find_motion_for_key(USER, PET, THEME_INDEPENDENT_PLACE_ID, motion)
    )
    assert pointer is not None
