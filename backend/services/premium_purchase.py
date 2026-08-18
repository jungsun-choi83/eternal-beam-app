"""
프리미엄 구매/생성 — **크레딧이 나가는 유일한 프리미엄 경로**.

가격 모델(확정):
    IDLE_BUNDLE        1 크레딧 — 등록된 아이들 이벤트 **전체**를 잠금 해제한다.
                       개수와 무관하다. 5번째 아이들 모션이 레지스트리에 추가되면
                       같은 1 크레딧 번들에 자동으로 포함된다.
    ACTION:<ACTION_ID> 1 크레딧 — 액션 이벤트 1건 (현재 COME_CLOSER).

핵심 원칙 두 가지:

  1) **크레딧은 생성/잠금 해제에만 쓴다.** 재생은 언제나 0원이다. 이미 승격된
     canonical 자산은 잔액이 0이 되어도 계속 재생된다 — 재생 접근권을 정하는 것은
     generated_motions 의 canonical 행이지 지갑이 아니다.

  2) **멱등성은 서버가 쥔다.** premium_purchases 의 부분 unique 인덱스
     (user_id, pet_id, kind) WHERE refunded_at IS NULL 하나로 끝난다. 새로고침,
     다중 탭, Preview/Memorial 중복, 재시도가 동시에 들어와도 insert 는 하나만
     성공한다. 클라이언트 가드는 왕복을 줄일 뿐 정확성에 관여하지 않는다.

생성 인프라는 **재사용만 한다**: canonical 조회 → 진행중 조회 → 큐 판정 →
premium_generation.submit_premium_action → 후보 검증 → 승격 → 스토리지.
이 파일에는 프로바이더 호출도, 프롬프트도, 재시도 로직도 없다.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from ..scenarios.pet_scenarios import IDLE_EVENTS, PET_ACTIONS, PREMIUM_ACTIONS
from . import generated_motions_service as motions_svc
from . import generation_queue
from . import premium_generation
from .wallet_service import (
    InsufficientCreditsError,
    WalletUnavailableError,
    deduct_credits,
    refund_credits,
)

logger = logging.getLogger(__name__)

#: 아이들 번들 — 등록된 아이들 이벤트 전체가 1 크레딧.
KIND_IDLE_BUNDLE = "IDLE_BUNDLE"

#: 액션 이벤트 접두사. kind = "ACTION:COME_CLOSER".
ACTION_KIND_PREFIX = "ACTION:"

#: 번들 가격. **개수와 무관하다** — 상수 4 를 쓰지 않는 이유가 이것이다.
IDLE_BUNDLE_CREDITS = int(os.getenv("IDLE_BUNDLE_CREDITS", "1"))

#: 액션 이벤트 1건 가격.
ACTION_EVENT_CREDITS = int(os.getenv("ACTION_EVENT_CREDITS", "1"))


def _table() -> str:
    return os.getenv("PREMIUM_PURCHASES_TABLE", "premium_purchases")


#: DB 가 없을 때의 인메모리 원장 (로컬/테스트).
_MOCK_PURCHASES: dict[str, dict[str, Any]] = {}


def __reset_for_tests() -> None:
    _MOCK_PURCHASES.clear()


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


class PurchaseError(Exception):
    """구매를 진행할 수 없다. code 로 HTTP 변환을 구분한다."""

    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def action_kind(action_id: str) -> str:
    return f"{ACTION_KIND_PREFIX}{(action_id or '').strip().upper()}"


def credits_for_kind(kind: str) -> int:
    if kind == KIND_IDLE_BUNDLE:
        return IDLE_BUNDLE_CREDITS
    if kind.startswith(ACTION_KIND_PREFIX):
        return ACTION_EVENT_CREDITS
    raise PurchaseError("UNKNOWN_KIND", f"알 수 없는 구매 종류: {kind}")


def target_actions(kind: str) -> tuple[str, ...]:
    """
    이 구매가 잠금 해제하는 액션 집합.

    번들은 **레지스트리에서** 가져온다(IDLE_EVENTS). 하드코딩된 목록이나 개수 4를
    쓰지 않으므로, 5번째 아이들 모션이 추가되면 같은 1 크레딧 번들에 자동 포함된다.
    BREATHING 은 IDLE_EVENTS 에 없다 — 무료 기본 모션이라 번들 밖이다.
    """
    if kind == KIND_IDLE_BUNDLE:
        return tuple(IDLE_EVENTS)
    if kind.startswith(ACTION_KIND_PREFIX):
        action = kind[len(ACTION_KIND_PREFIX) :].strip().upper()
        if action not in PET_ACTIONS:
            raise PurchaseError(
                "ACTION_NOT_SUPPORTED",
                f"{action} 는 구매 가능한 액션 이벤트가 아닙니다.",
            )
        return (action,)
    raise PurchaseError("UNKNOWN_KIND", f"알 수 없는 구매 종류: {kind}")


def resolve_kind(raw: str | None) -> str:
    """요청된 kind 문자열 → 검증된 kind. 액션은 PREMIUM_ACTIONS 안에 있어야 한다."""
    k = (raw or "").strip().upper()
    if not k:
        raise PurchaseError("KIND_REQUIRED", "구매 종류(kind)가 필요합니다.")
    if k == KIND_IDLE_BUNDLE:
        return k
    if k.startswith(ACTION_KIND_PREFIX):
        target_actions(k)  # 검증만 — 실패하면 예외
        return k
    # 편의: 액션 id 를 그대로 준 경우 ACTION: 을 붙여 준다.
    if k in PREMIUM_ACTIONS and k in PET_ACTIONS:
        return action_kind(k)
    raise PurchaseError("UNKNOWN_KIND", f"알 수 없는 구매 종류: {raw!r}")


# ── 소유권 ────────────────────────────────────────────────────────────────────


async def assert_pet_owned(user_id: str, pet_id: str) -> None:
    """
    이 펫이 이 사용자의 것인가.

    소유권 테이블이 없다(펫은 프론트에서 content_id 로 파생된다). 그래서 **최초
    사용 시 귀속(trust on first use)** 으로 판정한다: 다른 사용자 아래에 같은
    pet_id 의 자산/작업/구매가 이미 있으면 거절하고, 없으면 이 사용자 것으로 본다.

    이것이 막는 것은 실제 위협이다 — 남의 pet_id 를 넣어 남의 자산을 조회하거나
    남의 크레딧으로 생성하는 것. 막지 못하는 것은 "아직 아무도 쓰지 않은 pet_id 를
    선점하는 것"인데, 그건 자기 크레딧을 쓰는 행위라 피해가 없다.
    """
    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    if not uid or not pid:
        raise PurchaseError("PET_REQUIRED", "user_id 와 pet_id 가 필요합니다.", status=400)

    if not (_use_db() and _supabase()):
        owner = _MOCK_PURCHASES.get(f"owner:{pid}")
        if owner and owner.get("user_id") != uid:
            raise PurchaseError("PET_NOT_OWNED", "이 펫에 접근할 권한이 없습니다.", status=403)
        _MOCK_PURCHASES.setdefault(f"owner:{pid}", {"user_id": uid})
        return

    sb = _supabase()
    for table_env, default in (
        ("GENERATED_MOTIONS_TABLE", "generated_motions"),
        ("PREMIUM_PURCHASES_TABLE", "premium_purchases"),
    ):
        try:
            r = (
                sb.table(os.getenv(table_env, default))
                .select("user_id")
                .eq("pet_id", pid)
                .neq("user_id", uid)
                .limit(1)
                .execute()
            )
        except Exception:
            # 조회 실패를 통과로 해석하지 않는다 — 소유권 검사는 fail closed 다.
            logger.exception("소유권 조회 실패 (user=%s pet=%s table=%s)", uid, pid, default)
            raise PurchaseError(
                "OWNERSHIP_CHECK_UNAVAILABLE",
                "소유권을 확인할 수 없어 요청을 거절합니다.",
                status=503,
            )
        if getattr(r, "data", None):
            raise PurchaseError("PET_NOT_OWNED", "이 펫에 접근할 권한이 없습니다.", status=403)


# ── 원장 ──────────────────────────────────────────────────────────────────────


async def find_active_purchase(user_id: str, pet_id: str, kind: str) -> Optional[dict[str, Any]]:
    """환불되지 않은 구매가 있으면 그 행. 없으면 None."""
    if _use_db() and _supabase():
        r = (
            _supabase()
            .table(_table())
            .select("*")
            .eq("user_id", user_id)
            .eq("pet_id", pet_id)
            .eq("kind", kind)
            .is_("refunded_at", "null")
            .limit(1)
            .execute()
        )
        return (getattr(r, "data", None) or [None])[0]
    key = f"{user_id}|{pet_id}|{kind}"
    row = _MOCK_PURCHASES.get(key)
    return row if row and not row.get("refunded_at") else None


async def _claim_purchase(user_id: str, pet_id: str, kind: str, credits: int) -> Optional[str]:
    """
    구매 원장에 자리를 **선점**한다. 이미 활성 구매가 있으면 None.

    부분 unique 인덱스가 동시성 판정을 대신한다 — insert 가 성공한 요청만
    과금하고, unique 위반으로 떨어진 요청은 "이미 구매함"으로 처리된다.
    """
    purchase_id = str(uuid.uuid4())
    row = {
        "purchase_id": purchase_id,
        "user_id": user_id,
        "pet_id": pet_id,
        "kind": kind,
        "credits_charged": credits,
        "created_at": datetime.utcnow().isoformat(),
    }

    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).insert(row).execute()
        except Exception as e:
            msg = f"{e}".lower()
            if "duplicate" in msg or "unique" in msg or "23505" in msg:
                return None  # 다른 요청이 먼저 선점했다 — 과금하지 않는다
            raise PurchaseError(
                "PURCHASE_LEDGER_UNAVAILABLE",
                "구매 기록을 저장하지 못해 과금하지 않았습니다.",
                status=503,
            ) from e
        return purchase_id

    key = f"{user_id}|{pet_id}|{kind}"
    existing = _MOCK_PURCHASES.get(key)
    if existing and not existing.get("refunded_at"):
        return None
    _MOCK_PURCHASES[key] = row
    return purchase_id


async def _release_purchase(user_id: str, pet_id: str, kind: str, purchase_id: str) -> None:
    """선점 취소 — 과금 실패/제출 실패로 되돌릴 때. 재구매가 가능해진다."""
    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).delete().eq("purchase_id", purchase_id).execute()
        except Exception:
            logger.exception("구매 원장 롤백 실패 (purchase_id=%s)", purchase_id)
        return
    key = f"{user_id}|{pet_id}|{kind}"
    if (_MOCK_PURCHASES.get(key) or {}).get("purchase_id") == purchase_id:
        _MOCK_PURCHASES.pop(key, None)


async def _mark_purchase_refunded(purchase: dict[str, Any]) -> bool:
    """환불 표시를 **한 번만** 성공시킨다. 영향 행 수로 판정한다."""
    stamp = datetime.utcnow().isoformat()
    if _use_db() and _supabase():
        r = (
            _supabase()
            .table(_table())
            .update({"refunded_at": stamp})
            .eq("purchase_id", purchase["purchase_id"])
            .is_("refunded_at", "null")
            .execute()
        )
        return bool(getattr(r, "data", None))
    key = f"{purchase['user_id']}|{purchase['pet_id']}|{purchase['kind']}"
    row = _MOCK_PURCHASES.get(key)
    if not row or row.get("refunded_at") or row.get("purchase_id") != purchase["purchase_id"]:
        return False
    row["refunded_at"] = stamp
    return True


# ── 상태 조회 (과금 없음, 생성 없음) ──────────────────────────────────────────


@dataclass
class AssetState:
    ready: dict[str, str] = field(default_factory=dict)
    active: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


async def asset_state(user_id: str, pet_id: str, actions: tuple[str, ...]) -> AssetState:
    """
    이 액션들의 현재 상태. **읽기 전용** — 절대 생성하지도 과금하지도 않는다.
    """
    motions = await motions_svc.list_motions_for_pet(user_id, pet_id)
    ready = {
        (m.action_id or "").upper(): m.video_url
        for m in motions
        if (m.action_id or "").upper() in actions and m.video_url
    }
    active_ids = {
        a.upper() for a in await motions_svc.list_active_action_ids_for_pet(user_id, pet_id)
    }
    state = AssetState()
    state.ready = ready
    for a in actions:
        if a in ready:
            continue
        (state.active if a in active_ids else state.missing).append(a)
    return state


# ── 구매 ──────────────────────────────────────────────────────────────────────


@dataclass
class PurchaseResult:
    kind: str
    status: str          # "ready" | "processing" | "partial"
    credits_charged: int
    credits_remaining: Optional[int]
    ready: dict[str, str]
    generating: list[str]
    submitted: list[str]
    already_owned: bool


async def purchase(
    *,
    user_id: str,
    pet_id: str,
    kind: str,
    pet_image_url: Optional[str],
    api_base: str,
) -> PurchaseResult:
    """
    구매 + 누락 자산 생성 착수.

    순서가 계약이다. 과금은 **맨 마지막 관문**이고, 그 앞의 세 검사가 전부
    "0 크레딧" 경로다:

        1) 대상 전부 READY            → charged 0, 재생성 없음
        2) 활성 구매가 이미 있음      → charged 0, 누락분만 생성 착수
        3) 일부 진행 중               → charged 0(구매가 있으면), 중복 제출 없음
        4) 그 외                      → 원장 선점 → 1 크레딧 차감 → 제출

    제출이 하나도 성사되지 않으면 즉시 환불하고 선점을 푼다 — 생성 작업 없이
    크레딧만 잃는 경우가 없어야 한다.
    """
    actions = target_actions(kind)
    credits = credits_for_kind(kind)

    await assert_pet_owned(user_id, pet_id)

    state = await asset_state(user_id, pet_id, actions)

    # ① 만들 것이 없으면 **절대 과금하지 않는다.**
    #
    # 두 경우를 함께 덮는다:
    #   전부 READY  → status=ready
    #   남은 게 전부 생성 중 → status=processing (확정 규칙: 진행 중은 0원)
    #
    # 과금은 "새로 만들 자산이 있을 때"만 의미가 있다. 이 가드가 없으면 이미
    # 생성 중인 액션에 구매 기록이 없을 때(예: 개발 모드로 만든 작업) 사용자가
    # 이미 진행 중인 것에 돈을 내게 된다.
    if not state.missing:
        return PurchaseResult(
            kind=kind,
            status="ready" if not state.active else "processing",
            credits_charged=0,
            credits_remaining=None,
            ready=state.ready,
            generating=sorted(state.active),
            submitted=[],
            already_owned=True,
        )

    # ② 이미 구매한 사용자인가. 있으면 절대 다시 과금하지 않는다.
    existing = await find_active_purchase(user_id, pet_id, kind)
    charged = 0
    remaining: Optional[int] = None
    purchase_id: Optional[str] = None

    if existing is None:
        # ③ 원장 선점 — 여기서 동시 요청이 하나로 좁혀진다.
        purchase_id = await _claim_purchase(user_id, pet_id, kind, credits)
        if purchase_id is None:
            # 경합에서 졌다 = 다른 요청이 이미 구매했다 → 과금 없음.
            existing = await find_active_purchase(user_id, pet_id, kind)
        else:
            try:
                wallet = await deduct_credits(user_id, credits, strict=True)
            except InsufficientCreditsError:
                await _release_purchase(user_id, pet_id, kind, purchase_id)
                raise PurchaseError(
                    "INSUFFICIENT_CREDITS", "크레딧이 부족합니다.", status=402
                )
            except WalletUnavailableError as e:
                # 지갑을 신뢰성 있게 갱신하지 못했다 → 과금도 생성도 하지 않는다.
                await _release_purchase(user_id, pet_id, kind, purchase_id)
                raise PurchaseError(
                    "WALLET_UNAVAILABLE", e.message, status=503
                ) from e
            charged = credits
            remaining = wallet.current_credits

    # ④ 누락분 제출. 큐 상한은 generation_queue 가 쥐고, 남는 것은 서버가
    #    종료 이벤트마다 자동 전진시킨다(premium_generation.advance_generation_queue).
    submitted: list[str] = []
    if state.missing and pet_image_url:
        submitted = await _submit_missing(
            user_id=user_id, pet_id=pet_id, missing=state.missing,
            pet_image_url=pet_image_url, api_base=api_base,
        )

    # ⑤ 방금 과금했는데 작업이 하나도 안 생겼다면 되돌린다.
    #    (키프레임 준비 실패·프로바이더 거절 등 — 예전에는 이 경로에서 크레딧이 샜다:
    #     작업 행이 없는 세션은 영원히 processing 이라 종료 환불이 돌지 않는다.)
    if charged and not submitted and not state.active:
        await refund_credits(user_id, charged, strict=False)
        if purchase_id:
            await _release_purchase(user_id, pet_id, kind, purchase_id)
        raise PurchaseError(
            "GENERATION_SUBMIT_FAILED",
            "생성을 제출하지 못해 크레딧을 환불했습니다.",
            status=502,
        )

    fresh = await asset_state(user_id, pet_id, actions)
    return PurchaseResult(
        kind=kind,
        status="ready" if not fresh.missing and not fresh.active else "processing",
        credits_charged=charged,
        credits_remaining=remaining,
        ready=fresh.ready,
        generating=sorted(set(fresh.active) | set(submitted)),
        submitted=submitted,
        already_owned=existing is not None,
    )


async def _submit_missing(
    *,
    user_id: str,
    pet_id: str,
    missing: list[str],
    pet_image_url: str,
    api_base: str,
) -> list[str]:
    """큐가 허락하는 만큼 제출한다. 나머지는 서버 자동 전진에 맡긴다."""
    submitted: list[str] = []
    for action in sorted(missing, key=generation_queue.generation_rank):
        ready_actions = [
            (m.action_id or "").upper()
            for m in await motions_svc.list_motions_for_pet(user_id, pet_id)
        ]
        active_actions = await motions_svc.list_active_action_ids_for_pet(user_id, pet_id)
        if not generation_queue.decide(
            action_id=action, ready_actions=ready_actions, active_actions=active_actions
        ).allowed:
            continue  # 상한 — 자동 전진이 나중에 집어 간다
        try:
            r = await premium_generation.submit_premium_action(
                user_id=user_id, pet_id=pet_id, action_id=action,
                pet_image_url=pet_image_url, api_base=api_base,
            )
        except premium_generation.PremiumSubmitError as e:
            logger.warning("프리미엄 제출 실패 — %s (stage=%s): %s", action, e.stage, e)
            continue
        submitted.append(r.action_id)
    return submitted


# ── 종료 시 환불 판정 ─────────────────────────────────────────────────────────


async def reconcile_after_terminal(user_id: str, pet_id: str, action_id: str) -> bool:
    """
    프리미엄 작업이 종료됐다 → 이 구매를 환불해야 하는가.

    정책: **하나도 승격되지 않았을 때만** 환불한다.
      번들은 부분 성공에도 가치가 있다 — 스케줄러는 READY 인 것만 골라 쓰므로
      4종 중 1종만 나와도 자발적 아이들 모션이 동작한다. 레거시 4코인 세트의
      all-or-nothing 환불(1~3/4 도 전액 환불)과 다른 이유가 이것이다: 그쪽은
      /device/sync 가 4종을 모두 요구해 부분 집합의 가치가 실제로 0이다.

    호출은 웹훅의 종료 경로에서 온다. 여러 번 불려도 안전하다 —
    _mark_purchase_refunded 가 영향 행 수로 판정하므로 환불은 한 번뿐이다.
    """
    action = (action_id or "").upper()
    kinds = [KIND_IDLE_BUNDLE] if action in IDLE_EVENTS else [action_kind(action)]

    refunded_any = False
    for kind in kinds:
        purchase_row = await find_active_purchase(user_id, pet_id, kind)
        if not purchase_row:
            continue
        actions = target_actions(kind)
        state = await asset_state(user_id, pet_id, actions)
        if state.active:
            continue  # 아직 진행 중인 게 있다 — 판정은 마지막 종료에서
        if state.ready:
            continue  # 하나라도 나왔다 — 환불하지 않는다
        # 전부 종료됐는데 승격이 0건이다.
        if not await _mark_purchase_refunded(purchase_row):
            continue
        amount = int(purchase_row.get("credits_charged") or 0)
        if amount > 0:
            await refund_credits(user_id, amount, strict=False)
        logger.warning(
            "프리미엄 환불 — kind=%s user=%s pet=%s credits=%s (승격 0건)",
            kind, user_id, pet_id, amount,
        )
        refunded_any = True
    return refunded_any
