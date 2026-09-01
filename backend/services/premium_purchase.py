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
from . import credit_ledger
from . import credit_reservation
from . import generation_credits
from . import product_catalog
from . import generated_motions_service as motions_svc
from . import generation_queue
from . import premium_entitlement
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

# ── 가격은 여기 없다 (Phase 3) ────────────────────────────────────────────────
# 예전에는 두 환경변수가 **카테고리 전체**의 값을 정했다. 아이들 이벤트 넷이 반드시
# 같은 값이어야 했고, 값을 바꾸려면 재배포해야 했다.
#
# 이제 가격은 digital_products 의 **상품 행**이 정한다 → credits_for_kind().


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


def existing_id(row: Optional[dict[str, Any]]) -> str:
    """기존 구매 행의 id — 재시도가 같은 예약 키를 쓰게 한다."""
    return str((row or {}).get("purchase_id") or "")


def _product_key(kind: str) -> str:
    """
    구매 종류 → 카탈로그/원장의 product_key.

        'IDLE_BUNDLE'         → 'idle:BUNDLE'
        'ACTION:BLINKING'     → 'idle:BLINKING'      ← 아이들 이벤트다
        'ACTION:COME_CLOSER'  → 'action:COME_CLOSER'

    ⚠️ **접두사를 kind 에서 그대로 베끼면 안 된다.** 두 이름공간이 겹치지 않는다:

        kind 의 'ACTION:' 은 "한 건짜리 구매"라는 뜻이다 (번들의 반대말).
        product_key 의 'action:' 은 "액션 상품"이라는 뜻이다 (아이들의 반대말).

    Behavior Library 는 아이들 모션도 **한 건씩** 산다(resolve_kind 참고). 그래서
    BLINKING 은 kind 로는 ACTION:BLINKING 이지만 상품으로는 idle:BLINKING 이다.
    kind 접두사를 그대로 쓰면 카탈로그에 없는 'action:BLINKING' 을 찾게 되고,
    가격 조회가 "판매하지 않는 상품"으로 실패한다.

    그래서 분류는 **레지스트리**가 한다 — 문자열이 아니라.
    """
    if kind == KIND_IDLE_BUNDLE:
        return product_catalog.KEY_IDLE_BUNDLE
    if kind.startswith(ACTION_KIND_PREFIX):
        action = kind[len(ACTION_KIND_PREFIX) :].strip().upper()
        if action in IDLE_EVENTS:
            return product_catalog.idle_key(action)
        return product_catalog.action_key(action)
    return kind


async def credits_for_kind(kind: str) -> int:
    """
    이 구매의 크레딧 가격. **카탈로그가 정한다** (Phase 3).

    예전에는 카테고리 환경변수 두 개(IDLE_BUNDLE_CREDITS / ACTION_EVENT_CREDITS)가
    각각 **카테고리 전체**의 값을 정했다. 그래서 BLINKING 과 TAIL_WAGGING 에 다른
    값을 매길 방법이 없었고, 값을 바꾸려면 재배포가 필요했다.

    이제 상품마다 행이 있고, 가격을 바꾸는 것은 UPDATE 한 줄이다.

    Raises:
        PurchaseError: 알 수 없는 구매 종류, 또는 카탈로그에 없는 상품(판매 불가)
    """
    key = _product_key(kind)
    if kind != KIND_IDLE_BUNDLE and not kind.startswith(ACTION_KIND_PREFIX):
        raise PurchaseError("UNKNOWN_KIND", f"알 수 없는 구매 종류: {kind}")

    try:
        price = await product_catalog.credit_price(key)
    except product_catalog.CatalogUnavailableError as e:
        # 가격을 모르면 **과금하지 않는다.** 0 으로 떨어뜨리면 장애 중에 공짜로
        # 유료 생성이 돌고, 예전 기본값으로 떨어뜨리면 카탈로그가 권위라는 말이
        # 거짓이 된다.
        raise PurchaseError("CATALOG_UNAVAILABLE", e.message, status=503) from e

    if price is None:
        # 행이 없다 = 판매 불가. **무료가 아니다.**
        raise PurchaseError(
            "PRODUCT_NOT_SOLD",
            f"현재 판매하지 않는 상품입니다: {key}",
            status=409,
        )
    return price


def target_actions(kind: str) -> tuple[str, ...]:
    """
    이 요청이 대상으로 삼는 액션 집합.

    번들(KIND_IDLE_BUNDLE)은 **레지스트리에서** 가져온다(IDLE_EVENTS). 하드코딩된
    목록이나 개수 4를 쓰지 않으므로, 5번째 아이들 모션이 추가되면 자동으로 들어온다.
    BREATHING 은 IDLE_EVENTS 에 없다 — 무료 기본 모션이라 대상 밖이다.

    ACTION:<ID> 는 **정확히 한 건**이다. Phase 4 에서 대상이 PET_ACTIONS 에서
    PREMIUM_ACTIONS(= PET_ACTIONS + IDLE_EVENTS)로 넓어졌다: Behavior Library 가
    아이들 모션을 **하나씩** 생성하기 때문이다. 예전에는 아이들 모션을 만들려면
    번들뿐이었고, 그러면 BLINKING 하나를 눌러도 4종이 전부 제출돼 "선택한 것만
    생성한다"는 규칙이 깨진다.

    번들 경로는 그대로 남는다 — 지우면 레거시 크레딧 계약(rollback)이 깨진다.
    """
    if kind == KIND_IDLE_BUNDLE:
        return tuple(IDLE_EVENTS)
    if kind.startswith(ACTION_KIND_PREFIX):
        action = kind[len(ACTION_KIND_PREFIX) :].strip().upper()
        if action not in PREMIUM_ACTIONS:
            raise PurchaseError(
                "ACTION_NOT_SUPPORTED",
                f"{action} 는 생성 가능한 프리미엄 행동이 아닙니다.",
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
    # 편의: 행동 id 를 그대로 준 경우 ACTION: 을 붙여 준다.
    # PREMIUM_ACTIONS 전체가 대상이다 — Behavior Library 는 아이들 모션도 한 건씩
    # 요청한다. 레거시 4종(IDLE/TOUCH/VOICE/NFC)은 PREMIUM_ACTIONS 에 없으므로
    # 여기로 들어올 수 없다.
    if k in PREMIUM_ACTIONS:
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


async def _unmark_purchase_refunded(purchase: dict[str, Any]) -> bool:
    """
    환불 표시를 **되돌린다.** 표시는 찍었는데 지갑 환불이 확정되지 않은 경우 전용.

    ── 왜 필요한가 ─────────────────────────────────────────────────────────
    환불은 두 걸음이다: ① 원장에 '환불됨' 도장 → ② 지갑에 크레딧 반환.
    도장을 먼저 찍는 것은 옳다(동시 웹훅에서 이중 환불을 막는 유일한 방법이다).
    그런데 ② 가 실패했는데 도장이 남으면 상태가 이렇게 된다:

        원장: "이 구매는 환불됐다"   지갑: 크레딧 없음

    그러면 재시도조차 일어나지 않는다 — 다음 웹훅은 활성 구매를 못 찾고
    조용히 지나간다. 고객은 크레딧을 잃고, 기록은 잃지 않았다고 말한다.
    도장을 되돌려 놓으면 다음 종료 이벤트가 같은 판정을 다시 내려 환불을
    재시도한다.

    반환값은 되돌리기 성공 여부다. 실패하면 호출부가 **크게 로그를 남긴다** —
    수동 조치가 필요한 유일한 상태이기 때문이다.
    """
    if _use_db() and _supabase():
        try:
            r = (
                _supabase()
                .table(_table())
                .update({"refunded_at": None})
                .eq("purchase_id", purchase["purchase_id"])
                .execute()
            )
            return bool(getattr(r, "data", None))
        except Exception:
            logger.exception("환불 표시 되돌리기 실패 (purchase_id=%s)", purchase["purchase_id"])
            return False
    key = f"{purchase['user_id']}|{purchase['pet_id']}|{purchase['kind']}"
    row = _MOCK_PURCHASES.get(key)
    if not row or row.get("purchase_id") != purchase["purchase_id"]:
        return False
    row["refunded_at"] = None
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
    구매/생성 요청 + 누락 자산 생성 착수.

    순서가 계약이다:

        0) 펫 소유권          → 남의 펫이면 403 (인가보다 먼저 — 남의 펫 존재를
                                구독 상태로 추측하게 두지 않는다)
        0') 구독 인가         → 구독 모드에서 권한이 없으면 402, **생성 없음**
        1) 대상 전부 READY    → 재생성 없음 (구독이어도 무제한 재생성이 아니다)
        2) 일부 진행 중       → 중복 제출 없음
        3) 그 외              → 누락분만 제출

    과금은 **구독 모드에서 완전히 빠진다**. PREMIUM_REQUIRES_SUBSCRIPTION=0 일
    때만 예전 크레딧 경로(원장 선점 → 차감 → 실패 시 환불)가 돈다.
    """
    actions = target_actions(kind)
    credits = await credits_for_kind(kind)

    await assert_pet_owned(user_id, pet_id)

    # 구독 인가. 소유권 **뒤에** 둔다 — 남의 펫 요청은 구독 유무와 무관하게
    # 언제나 PET_NOT_OWNED 로 끝나야 한다(존재 여부가 새어 나가지 않게).
    try:
        entitlement = await premium_entitlement.get_entitlement(user_id)
    except premium_entitlement.EntitlementUnavailableError as e:
        raise PurchaseError("SUBSCRIPTION_CHECK_UNAVAILABLE", e.message, status=503) from e

    if entitlement.blocks_generation:
        # 이미 READY 인 자산은 그대로 남아 있고 계속 재생된다 — 여기서 막는 것은
        # **새 생성뿐**이다. BREATHING 은 PREMIUM_ACTIONS 밖이라 애초에 무관하다.
        raise PurchaseError(
            "SUBSCRIPTION_REQUIRED",
            "프리미엄 모션 생성에는 활성 구독이 필요합니다.",
            status=402,
        )

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
    #
    # 구독 모드에서는 이 블록 전체를 건너뛴다. 크레딧이 오가지 않으므로 구매 원장에
    # 선점할 것도, 차감할 것도, 되돌릴 것도 없다. premium_purchases 테이블은
    # **크레딧 시대의 기록 그대로 남겨 둔다** — 0원 행을 섞어 쓰면 같은 컬럼이 두
    # 가지를 뜻하게 되고, 나중에 이 원장을 다른 용도로 쓸 때 구분할 수 없다.
    #
    # "이미 있는 것을 또 만들지 않는다"는 보장은 원장이 아니라 위의 asset_state()
    # READY/GENERATING 검사가 이미 하고 있다 — 구독이 무제한 재생성이 아닌 이유다.
    existing = None
    charged = 0
    remaining: Optional[int] = None
    purchase_id: Optional[str] = None
    #: 이 구매를 뒷받침하는 예약. 세션에 매달려 확정/해제 판정에 쓰인다.
    reservation_ledger_id: Optional[str] = None

    if not entitlement.enforced:
        existing = await find_active_purchase(user_id, pet_id, kind)

    if not entitlement.enforced and existing is None:
        # ③ 원장 선점 — 여기서 동시 요청이 하나로 좁혀진다.
        purchase_id = await _claim_purchase(user_id, pet_id, kind, credits)
        if purchase_id is None:
            # 경합에서 졌다 = 다른 요청이 이미 구매했다 → 과금 없음.
            existing = await find_active_purchase(user_id, pet_id, kind)
        else:
            try:
                # ── 차감이 아니라 **예약**이다 (Phase 7) ──────────────────
                # 잔액은 지금 빠지지만, 원장 행은 RESERVED 로 남는다. 생성이
                # 검증을 통과하면 확정되고, 실패하면 해제되어 되돌아온다.
                #
                # 사유는 **무엇을 샀는가**로 정한다. 번들은 아이들 묶음이고
                # ACTION:<ID> 는 액션 한 건이다.
                # 멱등 키는 구매 원장의 선점 행 id — 그 행이 이 지출의 근거이고,
                # 부분 unique 인덱스가 이미 "한 구매에 하나"를 보장한다.
                reservation = await credit_reservation.reserve(
                    user_id=user_id,
                    credits=credits,
                    idempotency_key=credit_ledger.purchase_key(purchase_id),
                    product_key=_product_key(kind),
                    reason=(
                        credit_ledger.REASON_IDLE_GENERATION
                        if kind == KIND_IDLE_BUNDLE
                        else credit_ledger.REASON_ACTION_GENERATION
                    ),
                    ref_type="premium_purchases",
                    ref_id=purchase_id,
                )
            except credit_reservation.InsufficientCreditsError:
                await _release_purchase(user_id, pet_id, kind, purchase_id)
                raise PurchaseError(
                    "INSUFFICIENT_CREDITS", "크레딧이 부족합니다.", status=402
                )
            except credit_reservation.ReservationError as e:
                # 예약을 DB 로 확정하지 못했다 → 과금도 생성도 하지 않는다.
                await _release_purchase(user_id, pet_id, kind, purchase_id)
                raise PurchaseError(
                    "WALLET_UNAVAILABLE", e.message, status=503
                ) from e
            charged = credits
            reservation_ledger_id = reservation.ledger_id
            remaining = reservation.balance_after

    # ④ 누락분 제출. 큐 상한은 generation_queue 가 쥐고, 남는 것은 서버가
    #    종료 이벤트마다 자동 전진시킨다(premium_generation.advance_generation_queue).
    submitted: list[str] = []
    if state.missing and pet_image_url:
        submitted = await _submit_missing(
            user_id=user_id, pet_id=pet_id, missing=state.missing,
            pet_image_url=pet_image_url, api_base=api_base,
            # ACTION:<ID> = 사용자가 고른 한 건. 번들은 예전 그대로 우선순위를 따른다.
            explicit_pick=kind.startswith(ACTION_KIND_PREFIX),
            # kind 단위로 잡아 둔 예약을 세션에 매단다 — 종료 경로가 이 값으로
            # 확정/해제를 판정한다.
            reservation_ledger_id=reservation_ledger_id,
            credits_reserved=charged,
        )

    # ⑤ 방금 과금했는데 작업이 하나도 안 생겼다면 되돌린다.
    #    (키프레임 준비 실패·프로바이더 거절 등 — 예전에는 이 경로에서 크레딧이 샜다:
    #     작업 행이 없는 세션은 영원히 processing 이라 종료 환불이 돌지 않는다.)
    if charged and not submitted and not state.active:
        try:
            await refund_credits(
                user_id,
                charged,
                idempotency_key=credit_ledger.refund_key(purchase_id or ""),
                product_key=_product_key(kind),
                ref_type="premium_purchases",
                ref_id=purchase_id,
            )
        except WalletUnavailableError as e:
            # 환불을 DB 로 확정하지 못했다. **원장 행을 남긴다** — 지우면 고객은
            # 이미 차감된 채로 다시 구매하게 되고(이중 과금), 그건 되돌리기가
            # 훨씬 어렵다. 행이 남아 있으면 다음 요청이 find_active_purchase 로
            # 이 구매를 찾아 charged=0 으로 재시도한다: 고객은 낸 값에 해당하는
            # 자산을 결국 받는다.
            logger.error(
                "프리미엄 제출 실패 후 환불 미확정 — 원장 유지 (user=%s pet=%s kind=%s credits=%s): %s",
                user_id, pet_id, kind, charged, e.message,
            )
            raise PurchaseError(
                "REFUND_UNCONFIRMED",
                "생성을 제출하지 못했습니다. 크레딧은 그대로 보관되며 "
                "잠시 후 다시 시도하면 추가 과금 없이 이어집니다.",
                status=503,
            ) from e
        if purchase_id:
            await _release_purchase(user_id, pet_id, kind, purchase_id)
        raise PurchaseError(
            "GENERATION_SUBMIT_FAILED",
            "생성을 제출하지 못해 크레딧을 환불했습니다.",
            status=502,
        )

    # ⑤' 구독 모드: 되돌릴 과금은 없지만, 아무것도 제출되지 않았는데 "생성 중"으로
    #     보고하면 안 된다. 그러면 프론트가 영원히 끝나지 않는 폴링에 들어가고
    #     사용자는 만들어지지 않는 모션을 기다린다.
    if entitlement.enforced and not submitted and not state.active:
        raise PurchaseError(
            "GENERATION_SUBMIT_FAILED",
            "생성을 제출하지 못했습니다. 잠시 후 다시 시도해 주세요.",
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
    explicit_pick: bool = False,
    reservation_ledger_id: str | None = None,
    credits_reserved: int = 0,
) -> list[str]:
    """
    큐가 허락하는 만큼 제출한다. 나머지는 서버 자동 전진에 맡긴다.

    explicit_pick=True 는 **사용자가 이 행동 하나를 직접 골랐다**는 뜻이다
    (Behavior Library 의 [생성]). 그때는 생성 우선순위를 강요하지 않는다 —
    누른 것과 다른 행동이 만들어지거나, 앞선 우선순위가 준비될 때까지 거절되면
    안 되기 때문이다. 동시 실행 상한은 그대로 적용된다.
    """
    submitted: list[str] = []
    for action in sorted(missing, key=generation_queue.generation_rank):
        ready_actions = [
            (m.action_id or "").upper()
            for m in await motions_svc.list_motions_for_pet(user_id, pet_id)
        ]
        active_actions = await motions_svc.list_active_action_ids_for_pet(user_id, pet_id)
        if not generation_queue.decide(
            action_id=action,
            ready_actions=ready_actions,
            active_actions=active_actions,
            respect_priority=not explicit_pick,
        ).allowed:
            continue  # 상한 — 자동 전진이 나중에 집어 간다
        try:
            # 예약은 **kind 단위로 한 번** 잡혀 있다(purchase() 참고). 여기서는
            # 그 예약을 세션에 실어 보낼 뿐이다 — 액션마다 다시 잡으면 번들
            # (N개 아이들 = 1 크레딧)의 가격이 N배가 된다.
            r = await premium_generation.submit_premium_action(
                user_id=user_id, pet_id=pet_id, action_id=action,
                pet_image_url=pet_image_url, api_base=api_base,
                reservation_ledger_id=reservation_ledger_id,
                credits_reserved=credits_reserved,
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
    # 아이들 모션은 **두 경로**로 만들어질 수 있다: 레거시 번들 구매(크레딧 모드)와
    # Behavior Library 의 단건 요청. 어느 쪽으로 과금됐는지 모르므로 둘 다 확인한다.
    # (구독 모드에서는 원장 행 자체가 없어 둘 다 조용히 넘어간다.)
    kinds = (
        [KIND_IDLE_BUNDLE, action_kind(action)]
        if action in IDLE_EVENTS
        else [action_kind(action)]
    )

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
        #
        # 순서가 중요하다: 도장을 **먼저** 찍는다. 동시 웹훅 둘이 같은 판정에
        # 도달해도 도장은 하나만 통과하므로 환불도 한 번뿐이다.
        if not await _mark_purchase_refunded(purchase_row):
            continue
        amount = int(purchase_row.get("credits_charged") or 0)
        if amount > 0:
            try:
                # 멱등 키가 구매 id 기반이라, 웹훅이 여러 번 배달돼도 환불은
                # 한 번만 원장에 남는다 — refunded_at 도장과 이중으로 막는다.
                await refund_credits(
                    user_id,
                    amount,
                    idempotency_key=credit_ledger.refund_key(
                        str(purchase_row.get("purchase_id") or "")
                    ),
                    product_key=_product_key(kind),
                    ref_type="premium_purchases",
                    ref_id=str(purchase_row.get("purchase_id") or ""),
                )
            except WalletUnavailableError as e:
                # ⚠️ 여기가 고객이 크레딧을 잃던 자리다 (Phase 1 감사).
                #
                # 예전에는 비-strict 환불이라 인메모리에만 반영되고 성공을 반환했다:
                # 도장은 찍혔고, 지갑은 프로세스 메모리에서만 늘었고, Render 가
                # 인스턴스를 재활용하면 그 증분은 사라졌다. 원장은 "환불됨"이라
                # 말하므로 다음 웹훅도 이 구매를 찾지 못하고, 아무도 발견하지 못했다.
                #
                # 이제 도장을 되돌려 다음 종료 이벤트가 같은 판정을 다시 내리게 한다.
                restored = await _unmark_purchase_refunded(purchase_row)
                logger.error(
                    "프리미엄 환불 미확정 — kind=%s user=%s pet=%s credits=%s "
                    "환불표시_되돌림=%s: %s",
                    kind, user_id, pet_id, amount, restored, e.message,
                )
                if not restored:
                    # 되돌리기까지 실패했다 = 자동 복구 경로가 없다. 사람이 봐야 한다.
                    logger.critical(
                        "수동 조치 필요 — 환불 표시는 남고 크레딧은 반환되지 않았다 "
                        "(purchase_id=%s user=%s credits=%s)",
                        purchase_row.get("purchase_id"), user_id, amount,
                    )
                # 이 kind 는 환불되지 않았다. 다른 kind 판정은 계속 진행한다.
                continue
        logger.warning(
            "프리미엄 환불 — kind=%s user=%s pet=%s credits=%s (승격 0건)",
            kind, user_id, pet_id, amount,
        )
        refunded_any = True
    return refunded_any
