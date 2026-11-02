"""
Phase 7H — 프리미엄 상품 이행을 새 생성 실행(Phase 1–7)으로 보낸다.

기존 상거래(카탈로그·가격·구독·예약·소유·Behavior Library)는 그대로다.
바뀌는 것은 **이행 기술 하나**: premium_generation(레거시 블랙 플레이트
키프레임 + 레거시 프롬프트 + 웹훅 승격) 대신 durable generation run 이
Phase 2–6 → 새 모션 스펙/프롬프트/프로바이더 → QA → packed 포장 → 이행 확정
(premium_motion_finalization)을 수행한다.

product_key 는 판매/가격의 권위, motion_id 는 생성/런타임의 권위다 —
매핑은 기존 규약(owned_assets.product_key_for_action) 하나로만 한다:

    idle:BLINKING        → BLINKING
    idle:EAR_TWITCHING   → EAR_TWITCHING
    idle:HEAD_TILTING    → HEAD_TILTING
    idle:TAIL_WAGGING    → TAIL_WAGGING
    action:COME_CLOSER   → COME_CLOSER

레거시 경로는 PREMIUM_FULFILLMENT=legacy 로만 돌아간다(명시 회귀 스위치).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from . import owned_assets
from . import pet_generation_run_service as runs
from .premium_motion_finalization import PREMIUM_MOTIONS

logger = logging.getLogger(__name__)


#: 유료 액션 → 무료 시스템 동반 모션. 유료 접근(COME_CLOSER)을 만든 펫에게는 그
#: 접근을 홈으로 되돌리는 복귀(RETURN_HOME)도 필요하다. 동반 모션은 시스템 소유라
#: 크레딧 0·구매 불가(REQUEST_FREE_HOME 경로)로 생성되고, 시작 이미지는 COME_CLOSER
#: 영상에서 파생한 CLOSE_POSITION 이다(close_position_service → start_image_override).
#:
#: ⚠️ 의존성 규칙: 동반은 **제출 시점이 아니라 유료 액션이 QA 를 통과해 실제
#: 생성물로 발행된 뒤에만** 큐에 들어간다(pet_generation_run_service 의
#: _maybe_enqueue_system_companion, DELIVERED_GENERATED 경로). 그래서 COME_CLOSER 가
#: 실패/폴백이면 RETURN_HOME 은 애초에 만들어지지 않고, RETURN_HOME 이 실패해도 이미
#: 발행된 COME_CLOSER 는 그대로다(별개 실행).
SYSTEM_COMPANIONS: dict[str, str] = {"COME_CLOSER": "RETURN_HOME"}


async def ensure_system_companion(
    *,
    user_id: str,
    pet_id: str,
    action_id: str,
    source_run_id: str,
    start_image_override: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """이 액션에 무료 시스템 동반 모션이 있으면 그 생성 실행을 보장한다 (run_id).

    멱등: 펫+동반모션+원본 실행 열쇠. 같은 원본의 재시도는 재사용하지만,
    새 COME_CLOSER 실행은 새 CLOSE_POSITION 을 쓰는 별개 동반 실행을 만든다.
    동반 모션이 없으면 None. 과금 없음(credits_reserved=0, product_key 없음).
    start_image_override(RETURN_HOME 의 런별 파생 CLOSE_POSITION)는 실행 생성 시점에
    provider_state 로 박혀, 모션 단계가 그것을 시작 이미지로 쓴다.

    **호출 시점**: 유료 액션이 QA 통과·발행된 직후에만 부른다(의존성 규칙)."""
    companion = SYSTEM_COMPANIONS.get((action_id or "").strip().upper())
    if not companion:
        return None
    source_run_id = (source_run_id or "").strip()
    if not source_run_id:
        raise ValueError("A source generation run is required for a system companion")
    provider_state_extra = (
        {"close_position_override": dict(start_image_override)} if start_image_override else None
    )
    run = await runs.start_generation_run(
        user_id=user_id,
        pet_id=pet_id,
        motion_id=companion,
        request_kind=runs.REQUEST_FREE_HOME,
        idempotency_key=f"system-companion:{companion}:{pet_id}:{source_run_id}",
        credits_reserved=0,
        provider_state_extra=provider_state_extra,
    )
    if (
        run.status == runs.STATUS_FAILED
        and (run.last_error or {}).get("code") == "DERIVED_START_MISSING"
        and start_image_override
    ):
        run = await runs.retry_generation_run(
            user_id=user_id, run_id=run.id,
            close_position_override=start_image_override,
        )
    return run.id


class PremiumRunSubmitError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def phase7_premium_enabled() -> bool:
    """새 이행이 기본이다. 'legacy' 는 명시적 개발 회귀 스위치일 뿐이다."""
    return (os.getenv("PREMIUM_FULFILLMENT") or "phase7").strip().lower() != "legacy"


def motion_id_for_product(product_key: str) -> Optional[str]:
    """product_key → 물리 motion_id. 상용 5종 밖이면 None (판매 불가 = 이행 불가)."""
    key = (product_key or "").strip()
    for motion in PREMIUM_MOTIONS:
        if owned_assets.product_key_for_action(motion) == key:
            return motion
    return None


def _idempotency_key(action_id: str, pet_id: str, reservation_ledger_id: Optional[str]) -> str:
    """
    실행 재사용 열쇠(기본 열쇠).

    크레딧 모드: 예약 원장 id — 같은 구매의 재시도는 같은 실행을 재사용하고,
    환불 후 새 구매는 새 예약 → 새 실행이다(환불된 실패 실행에 갇히지 않는다).
    구독 모드: 펫+액션 고정 — 동시 더블탭이 실행 두 개를 만들지 못한다.

    종료(FAILED)된 기존 실행은 submit 이 두 갈래로 다룬다:
      * 판정 전 실패(프로바이더/인프라/취소) → retry 로 되살린다(durable resume).
      * 핀된 모션 버전에 대한 **최종 판정**(MOTION_QA_FAILED 등) → 이 열쇠에
        `:regen:<실패 실행 id>` 를 붙인 새 열쇠로 **새 실행**을 만든다
        (_regeneration_key). 옛 실행·버전·후보·QA 영수증은 그대로 남는다.
    """
    scope = (reservation_ledger_id or "").strip() or f"sub:{pet_id}"
    return f"premium:{action_id}:{scope}"


def _regeneration_key(base_key: str, superseded_run_id: str) -> str:
    """
    최종 판정으로 끝난 실행을 **대체**하는 새 실행의 열쇠.

    결정적이다(같은 실패 실행을 대체하는 동시 요청 둘은 같은 열쇠를 계산해
    하나의 실행에 합류한다) 그리고 사슬이다(대체 실행이 또 최종 판정으로 끝나면
    다음 요청은 그 실행의 id 로 새 열쇠를 만든다). 실행 한 번의 실패당 새 실행
    하나 — 무한 재생성이 아니라 사용자의 명시적 요청 한 번당 하나다.
    """
    return f"{base_key}:regen:{superseded_run_id}"


#: provider_state._operator 아래의 재생성 감사 표식 키.
REGENERATION_MARKER = "regeneration"


async def _start_fresh_run(
    *,
    user_id: str,
    pet_id: str,
    action: str,
    product_key: str,
    idempotency_key: str,
    reservation_ledger_id: Optional[str],
    credits_reserved: int,
    superseded: runs.PetGenerationRun,
    verdict: str,
) -> runs.PetGenerationRun:
    """
    최종 판정으로 끝난 `superseded` 를 대체하는 새 실행을 만든다.

    상거래 맥락: 호출자가 예약을 들고 왔으면(새 구매) 그것을 싣는다. 들고 오지
    않았으면(구독 모드, 또는 환불되지 않은 기존 구매의 재요청) 대체되는 실행의
    예약을 **그대로 물려받는다** — retry 로 되살렸을 때 그 실행이 들고 있었을
    바로 그 예약이다. 확정은 멱등(commit 은 여러 번 안전)이라 같은 구매가 두 번
    확정되지 않고, 예약 없는 실행(구독)은 아무것도 물려받지 않는다.
    """
    inherit = not (reservation_ledger_id or "").strip() and bool(superseded.reservation_ledger_id)
    marker = {
        "supersedes_run_id": superseded.id,
        "superseded_motion_version_id": superseded.motion_version_id,
        "reason": verdict,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "reservation_inherited": inherit,
    }
    return await runs.start_generation_run(
        user_id=user_id,
        pet_id=pet_id,
        motion_id=action,
        request_kind=runs.REQUEST_PREMIUM_PRODUCT,
        idempotency_key=idempotency_key,
        product_key=product_key,
        reservation_ledger_id=(
            superseded.reservation_ledger_id if inherit else reservation_ledger_id
        ),
        credits_reserved=(
            int(superseded.credits_reserved or 0) if inherit else int(credits_reserved or 0)
        ),
        provider_state_extra={"_operator": {REGENERATION_MARKER: marker}},
    )


async def _start_base_run(
    *,
    user_id: str,
    pet_id: str,
    action: str,
    product_key: str,
    base_key: str,
    reservation_ledger_id: Optional[str],
    credits_reserved: int,
) -> runs.PetGenerationRun:
    """기본 열쇠로 실행을 만들거나 재사용한다 (예전 submit 과 동일)."""
    return await runs.start_generation_run(
        user_id=user_id,
        pet_id=pet_id,
        motion_id=action,
        request_kind=runs.REQUEST_PREMIUM_PRODUCT,
        idempotency_key=base_key,
        product_key=product_key,
        reservation_ledger_id=reservation_ledger_id,
        credits_reserved=int(credits_reserved or 0),
    )


async def submit_premium_run(
    *,
    user_id: str,
    pet_id: str,
    action_id: str,
    reservation_ledger_id: Optional[str] = None,
    credits_reserved: int = 0,
) -> str:
    """
    상용 액션 하나를 새 생성 실행으로 제출/재사용한다. 반환: run_id.

    과금은 여기서 일어나지 않는다 — 예약은 구매(premium_purchase)가 이미 잡았고,
    확정/환불은 실행의 종료 경로(finalization / reconcile)가 판정한다.

    실행 수명주기(사용자·펫·액션·요청 종류 범위에서):
      1) 활성 실행이 있으면 합류한다 — 열쇠가 달라도 (start_generation_run 의
         활성 범위 합류 + DB 부분 unique 인덱스). 이중 생성 없음.
      2) 가장 최근 실행이 핀된 모션 버전에 대한 **최종 판정**(MOTION_QA_FAILED /
         MOTION_QA_REVIEW / 폴백 전달)으로 끝났고 살아 있는 프로바이더 작업이
         없으면, 이 요청은 의도적인 새 생성이다 → 새 열쇠로 새 실행(새 모션
         버전, 새 프로바이더 시도). 옛 실행·버전·후보·QA 는 보존된다.
         retry(=QA 재평가/durable resume)는 별도 엔드포인트로 그대로 남는다.
      3) 그 밖에는 예전 그대로: 기본 열쇠로 만들거나 재사용하고, 판정 전
         실패(프로바이더/인프라/취소)면 retry 로 되살린다 — durable 핀과
         프로바이더 영수증을 보존하므로 살아 있는 외부 작업을 재제출하지 않는다.
    """
    action = (action_id or "").strip().upper()
    if action not in PREMIUM_MOTIONS:
        raise PremiumRunSubmitError(
            "MOTION_NOT_COMMERCIAL", f"{action} 는 상용 이행 대상이 아닙니다.", status=409
        )
    product_key = owned_assets.product_key_for_action(action)
    base_key = _idempotency_key(action, pet_id, reservation_ledger_id)
    scope = dict(
        user_id=user_id, pet_id=pet_id, motion_id=action,
        request_kind=runs.REQUEST_PREMIUM_PRODUCT,
    )
    try:
        latest = await runs.latest_run_for_scope(**scope)
        base_run = await runs.find_run_by_key(**scope, idempotency_key=base_key)
        if latest is None:
            run = await _start_base_run(
                user_id=user_id, pet_id=pet_id, action=action, product_key=product_key,
                base_key=base_key, reservation_ledger_id=reservation_ledger_id,
                credits_reserved=credits_reserved,
            )
        elif latest.status in runs.ACTIVE_RUN_STATUSES:
            # 1) 활성 실행에 합류 — 열쇠가 달라도 이 범위의 활성 실행은 하나뿐이다.
            run = latest
        elif latest.status == runs.STATUS_PUBLISHED and not runs.is_delivered_fallback(latest):
            # READY — 생성할 것이 없다. 기본 열쇠가 이미 쓰였으면(구독 고정 열쇠 /
            # 같은 예약) 발행된 실행을 돌려주고, 옛 실패 실행을 되살리지 않는다.
            # 새 예약(크레딧 모드)이면 예전처럼 그 예약을 실은 실행을 만든다 —
            # 빌더가 완성 버전을 재사용하고 이행이 포인터/예약을 다시 맞춘다.
            run = latest if base_run is not None else await _start_base_run(
                user_id=user_id, pet_id=pet_id, action=action, product_key=product_key,
                base_key=base_key, reservation_ledger_id=reservation_ledger_id,
                credits_reserved=credits_reserved,
            )
        elif (verdict := runs.terminal_motion_verdict(latest)) and not runs.has_live_provider_work(latest):
            # 2) 의도적인 새 생성 — 기본 열쇠가 비어 있으면(크레딧 모드의 새
            #    예약) 그대로, 이미 쓰였으면(구독 모드 고정 열쇠) 대체 열쇠.
            key = base_key if base_run is None else _regeneration_key(base_key, latest.id)
            logger.info(
                "프리미엄 재생성 — %s 실행 %s(%s)을 새 실행으로 대체 (user=%s pet=%s key=%s)",
                action, latest.id, verdict, user_id, pet_id, key,
            )
            run = await _start_fresh_run(
                user_id=user_id, pet_id=pet_id, action=action, product_key=product_key,
                idempotency_key=key, reservation_ledger_id=reservation_ledger_id,
                credits_reserved=int(credits_reserved or 0),
                superseded=latest, verdict=verdict,
            )
        elif base_run is None:
            # 3a) 판정 전 종료 + 새 예약(크레딧 모드) — 예전 그대로 새 실행.
            run = await _start_base_run(
                user_id=user_id, pet_id=pet_id, action=action, product_key=product_key,
                base_key=base_key, reservation_ledger_id=reservation_ledger_id,
                credits_reserved=credits_reserved,
            )
        else:
            # 3b) 판정 전 종료(프로바이더/인프라/취소) — **가장 최근** 실행을
            #     되살린다(durable resume: 핀·프로바이더 영수증 보존, 살아 있는
            #     외부 작업 재제출 없음). 기본 열쇠의 옛 실행이 아니라 최근 실행이다.
            run = await runs.retry_generation_run(user_id=user_id, run_id=latest.id)
        # 같은 열쇠의 기존 실행이 판정 전 실패/취소로 종료돼 있으면 되살린다 —
        # durable resume. PUBLISHED/진행 중이면 retry 는 그대로 돌려준다.
        if run.status in (
            runs.STATUS_FAILED, runs.STATUS_CANCELLED
        ) or runs.is_delivered_fallback(run):
            run = await runs.retry_generation_run(user_id=user_id, run_id=run.id)
        return run.id
    except runs.PetGenerationRunError as exc:
        raise PremiumRunSubmitError(exc.code, exc.message, status=exc.status) from exc


#: "진행 중"으로 보는 실행 상태. RECOVERY_REQUIRED 를 포함한다 — 운영자 재시도
#: 대기 중이지 종료가 아니며, 이때 재제출/환불이 돌면 이중 생성/이중 판정이 된다.
_ACTIVE_RUN_STATUSES = (
    runs.STATUS_QUEUED,
    runs.STATUS_RUNNING,
    runs.STATUS_WAITING_PROVIDER,
    runs.STATUS_RECOVERY_REQUIRED,
)


async def reconcile_failed_run(
    *,
    user_id: str,
    pet_id: str,
    motion_id: str,
    reservation_ledger_id: Optional[str],
) -> bool:
    """
    프리미엄 실행이 실패로 종료됐다 → 예약을 되돌려야 하는가.

    레거시 세션의 예약 분기와 같은 원리다: 예약 기반 과금의 되돌림은 환불이
    아니라 **해제**다(제공된 적이 없다). 정책도 레거시 reconcile 과 같다 —
    같은 구매(kind)의 대상 중 하나라도 READY 면 되돌리지 않고, 아직 진행 중이면
    판정을 미룬다. 도장(purchase refunded 표시)을 먼저 찍어 동시 종료에서도
    해제가 한 번만 일어난다.

    구독 모드(예약 없음)에서는 아무것도 하지 않는다.
    """
    from . import credit_reservation, premium_purchase

    if not reservation_ledger_id:
        return False
    action = (motion_id or "").strip().upper()
    from ..scenarios.pet_scenarios import IDLE_EVENTS

    kinds = (
        [premium_purchase.KIND_IDLE_BUNDLE, premium_purchase.action_kind(action)]
        if action in IDLE_EVENTS
        else [premium_purchase.action_kind(action)]
    )
    released = False
    for kind in kinds:
        purchase_row = await premium_purchase.find_active_purchase(user_id, pet_id, kind)
        if not purchase_row:
            continue
        actions = premium_purchase.target_actions(kind)
        state = await premium_purchase.asset_state(user_id, pet_id, actions)
        if state.active:
            continue  # 아직 진행 중 — 마지막 종료가 판정한다
        if state.ready:
            continue  # 가치가 나왔다 — 되돌리지 않는다
        if not await premium_purchase._mark_purchase_refunded(purchase_row):
            continue  # 동시 종료가 이미 처리했다
        from . import generation_credits

        if await generation_credits.release_quietly(reservation_ledger_id):
            released = True
        else:
            restored = await premium_purchase._unmark_purchase_refunded(purchase_row)
            logger.error(
                "프리미엄 실행 예약 해제 실패 — 표시_되돌림=%s (user=%s pet=%s kind=%s)",
                restored, user_id, pet_id, kind,
            )
    return released


async def active_premium_motion_ids(user_id: str, pet_id: str) -> set[str]:
    """
    이 펫의 진행 중인 프리미엄 실행의 motion_id 집합. 읽기 전용.

    premium_purchase.asset_state 가 레거시 작업 표와 OR 로 합친다 — 구매 중복
    방지와 Behavior Library 의 'generating' 표시가 같은 값을 본다.
    """
    active: set[str] = set()
    client = runs._supabase() if runs._use_db() else None
    if client:
        try:
            result = (
                client.table(runs._table())
                .select("motion_id,status")
                .eq("user_id", user_id)
                .eq("pet_id", pet_id)
                .eq("request_kind", runs.REQUEST_PREMIUM_PRODUCT)
                .in_("status", list(_ACTIVE_RUN_STATUSES))
                .execute()
            )
            for row in getattr(result, "data", None) or []:
                mid = str(row.get("motion_id") or "").upper()
                if mid in PREMIUM_MOTIONS:
                    active.add(mid)
        except Exception:
            # 조회 실패를 "없음"으로 답하면 구매가 이중 제출된다. fail-closed 로
            # 올린다 — 구매 쪽이 자기 오류 규약(503)으로 바꾼다.
            logger.exception("프리미엄 실행 조회 실패 (user=%s pet=%s)", user_id, pet_id)
            raise PremiumRunSubmitError(
                "RUNS_UNAVAILABLE", "생성 실행 상태를 확인하지 못했습니다.", status=503
            )
        return active
    for row in runs._MOCK_RUNS:
        if (
            str(row.get("user_id")) == user_id
            and str(row.get("pet_id")) == pet_id
            and str(row.get("request_kind")) == runs.REQUEST_PREMIUM_PRODUCT
            and str(row.get("status")) in _ACTIVE_RUN_STATUSES
        ):
            mid = str(row.get("motion_id") or "").upper()
            if mid in PREMIUM_MOTIONS:
                active.add(mid)
    return active
