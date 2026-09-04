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
from typing import Optional

from . import owned_assets
from . import pet_generation_run_service as runs
from .premium_motion_finalization import PREMIUM_MOTIONS

logger = logging.getLogger(__name__)


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
    실행 재사용 열쇠.

    크레딧 모드: 예약 원장 id — 같은 구매의 재시도는 같은 실행을 재사용하고,
    환불 후 새 구매는 새 예약 → 새 실행이다(환불된 실패 실행에 갇히지 않는다).
    구독 모드: 펫+액션 고정 — 동시 더블탭이 실행 두 개를 만들지 못한다.
    종료(FAILED)된 기존 실행은 아래 submit 이 retry 로 되살린다.
    """
    scope = (reservation_ledger_id or "").strip() or f"sub:{pet_id}"
    return f"premium:{action_id}:{scope}"


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
    """
    action = (action_id or "").strip().upper()
    if action not in PREMIUM_MOTIONS:
        raise PremiumRunSubmitError(
            "MOTION_NOT_COMMERCIAL", f"{action} 는 상용 이행 대상이 아닙니다.", status=409
        )
    product_key = owned_assets.product_key_for_action(action)
    try:
        run = await runs.start_generation_run(
            user_id=user_id,
            pet_id=pet_id,
            motion_id=action,
            request_kind=runs.REQUEST_PREMIUM_PRODUCT,
            idempotency_key=_idempotency_key(action, pet_id, reservation_ledger_id),
            product_key=product_key,
            reservation_ledger_id=reservation_ledger_id,
            credits_reserved=int(credits_reserved or 0),
        )
        # 같은 열쇠의 기존 실행이 이미 종료(실패)돼 있으면 되살린다 — 구독 모드의
        # 재구매가 대표적이다. PUBLISHED/진행 중이면 retry 는 그대로 돌려준다.
        if run.status in (runs.STATUS_FAILED, runs.STATUS_CANCELLED):
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
