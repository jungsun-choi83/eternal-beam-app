"""
프리미엄 생성 인가(authorization) — **월 구독 기반**.

Phase 2 의 핵심 변경: 프리미엄 모션의 생성 권한을 **크레딧 잔액이 아니라 구독
상태**가 정한다.

    BLINKING · EAR_TWITCHING · HEAD_TILTING · TAIL_WAGGING · COME_CLOSER
    (= scenarios.pet_scenarios.PREMIUM_ACTIONS)

예전에는 이 판정이 지갑 잔액이었다. 그래서 두 방향으로 다 틀렸다:
구독이 만료된 사용자도 크레딧만 있으면 생성할 수 있었고, 구독 중인 사용자도
잔액이 0이면 생성할 수 없었다. 이 모듈이 그 판정을 구독으로 옮긴다.

경계 — 이 모듈이 **하지 않는** 것 세 가지:

  1) **재생 권한을 정하지 않는다.** 이미 승격된 canonical 자산은 구독이 만료돼도
     지워지지 않고 계속 재생된다. 재생 접근권의 권위는 예전 그대로
     generated_motions 다. 여기는 "**새로** 만들어도 되는가"에만 답한다.

  2) **BREATHING 을 건드리지 않는다.** 무료 기본 모션이라 PREMIUM_ACTIONS 밖이고,
     따라서 이 판정 자체가 적용되지 않는다. 구독이 없어도 계속 돈다.

  3) **레거시 4코인(IDLE/TOUCH/VOICE/NFC)을 건드리지 않는다.** 그쪽은 계속 지갑에서
     크레딧을 차감한다 — 구독의 월 크레딧 지급이 바로 그 재원이므로, 지급을 없애면
     레거시 기기 팩의 자금줄이 끊긴다. 그래서 지급은 그대로 둔다.

또 하나 중요한 것: **구독은 무제한 재생성이 아니다.** 이 모듈은 "권한이 있는가"만
답하고, "이미 있는 것을 또 만들지 않는다"는 판정은 예전 그대로
premium_purchase.asset_state() 의 READY/GENERATING 검사가 맡는다. 두 판정은 서로
독립이며 둘 다 통과해야 제출이 일어난다.

PREMIUM_REQUIRES_SUBSCRIPTION=0 이면 예전 크레딧 과금 경로로 통째로 되돌아간다 —
롤백 스위치이자, 크레딧 계약 테스트(test_premium_purchase.py)가 계속 도는 근거다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from . import subscription_store_service as sub_store

logger = logging.getLogger(__name__)


def subscription_required() -> bool:
    """
    프리미엄 생성에 구독을 요구하는가. 기본 **켜짐**.

    끄면(=0) 이 모듈은 판정을 포기하고, premium_purchase 는 예전처럼 크레딧을
    차감한다. 프로덕션 사고 시 코드 배포 없이 되돌리기 위한 스위치다.
    """
    return os.getenv("PREMIUM_REQUIRES_SUBSCRIPTION", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


class EntitlementUnavailableError(Exception):
    """
    구독 상태를 **신뢰성 있게 읽지 못했다.**

    조회 실패를 "권한 있음"으로 해석하지 않는다 — 소유권 검사(assert_pet_owned)와
    같은 fail-closed 규칙이다. Supabase 장애 중에 프로바이더 비용이 새는 것보다
    503 으로 거절하는 편이 낫다.
    """

    def __init__(self, message: str = "구독 상태를 확인할 수 없습니다."):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class EntitlementState:
    """구독 인가 판정 결과. 라우터가 그대로 응답에 실을 수 있는 모양."""

    #: 프리미엄 **생성**이 허용되는가 (active, 또는 해지 유예 기간 내)
    entitled: bool
    #: "active" | "canceled" | "expired" | None(구독 이력 없음)
    status: Optional[str]
    #: 구독 게이트가 켜져 있는가. False 면 entitled 는 참고값일 뿐 강제되지 않는다.
    enforced: bool

    @property
    def blocks_generation(self) -> bool:
        """이 상태에서 새 프리미엄 생성을 막아야 하는가."""
        return self.enforced and not self.entitled


async def get_entitlement(user_id: str) -> EntitlementState:
    """
    이 사용자의 프리미엄 생성 인가 상태. **읽기 전용 — 아무것도 바꾸지 않는다.**

    구독 이력이 아예 없으면 status=None, entitled=False 다(무료 사용자).
    그래도 BREATHING 과 이미 READY 인 자산은 영향받지 않는다 — §경계 참고.
    """
    enforced = subscription_required()
    uid = (user_id or "").strip()
    if not uid:
        return EntitlementState(entitled=False, status=None, enforced=enforced)

    try:
        sub = await sub_store.get_subscription(uid)
    except Exception as e:  # noqa: BLE001 — 조회 실패는 통과가 아니다
        logger.exception("구독 상태 조회 실패 (user=%s)", uid)
        raise EntitlementUnavailableError() from e

    return EntitlementState(
        entitled=sub_store.is_entitled(sub),
        status=sub.status if sub else None,
        enforced=enforced,
    )
