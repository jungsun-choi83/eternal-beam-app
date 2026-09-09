"""
정규화된 구독 이벤트 — **모든 결제 제공자가 통과하는 단 하나의 문**.

    Toss (지금)  ┐
    Apple (나중) ├─→ NormalizedSubscriptionEvent ─→ 기존 자격 코어
    Google(나중) ┘                                  (user_subscriptions)

이 파일의 존재 이유는 하나다: **자격 코어가 제공자를 몰라야 한다.**

제공자마다 자격을 직접 건드리면 제공자를 늘릴 때마다 자격 판정·갱신·해지 로직이
분기되고, 결국 "Toss 는 되는데 Apple 은 안 되는" 상태가 만들어진다. 그래서 제공자는
**자격을 직접 쓰지 않는다** — 정규화된 이벤트를 만들어 여기로 넘기고, 자격 변경은
기존 handle_subscription_webhook 이 예전 그대로 처리한다(멱등성·사용자 락·RPC/목업
폴백·해지 유예까지 전부 이미 검증된 경로다).

새 제공자를 붙이는 방법:
  1) 제공자 SDK/API 로 결제를 확인한다 (제공자 모듈 안에서)
  2) NormalizedSubscriptionEvent 를 만든다
  3) apply_subscription_event() 를 부른다
자격 코어는 손대지 않는다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

#: 결제 제공자 식별자. 새 제공자는 여기 추가한다.
BillingProvider = Literal["toss", "apple", "google"]

#: 자격 코어가 이해하는 이벤트 종류.
#: subscription_webhook_parser 의 RenewalTypes/ExpireTypes/CancelTypes 와 같은 어휘다.
EventType = Literal[
    "INITIAL_BUY",        # 첫 결제 성공 → ACTIVE
    "RENEWAL",            # 갱신 결제 성공 → ACTIVE, 기간 연장
    "CANCEL",             # 해지 예약 → 기간 끝까지 유지
    "EXPIRATION",         # 기간 종료 → EXPIRED
    "DID_FAIL_TO_RENEW",  # 갱신 실패 → **연장하지 않는다**
]


@dataclass(frozen=True)
class NormalizedSubscriptionEvent:
    """
    제공자 중립 구독 이벤트.

    provider 는 **기록용**이다 — 자격 판정에 쓰이지 않는다. 자격은 event_type 과
    user_id 만 본다.
    """

    provider: BillingProvider
    event_type: EventType
    #: 정규 Eternal Beam 신원 (토큰에서 확정된 값). 제공자 고객 id 가 아니다.
    user_id: str
    plan_id: str
    #: 제공자 고유 거래 식별자. 멱등성 지문(fingerprint)의 재료가 된다.
    transaction_id: str
    #: 이 이벤트로 확보된 이용 종료 시각 (성공 결제에만 있다)
    period_end: Optional[datetime] = None
    raw: Optional[dict[str, Any]] = None


def _payload(event: NormalizedSubscriptionEvent) -> dict[str, Any]:
    """
    자격 코어(handle_subscription_webhook)가 받는 바디로 변환.

    store_type 에 제공자를 그대로 싣는다 — 파서가 명시적 notification_type/user_id 를
    우선하므로 제공자별 파싱 분기가 필요 없다. 멱등성 지문이
    (store, event, tx, user) 로 만들어지므로 제공자가 달라도 충돌하지 않는다.
    """
    return {
        "store_type": event.provider,
        "notification_type": event.event_type,
        "user_id": event.user_id,
        "plan_id": event.plan_id,
        "transaction_id": event.transaction_id,
        "raw": event.raw or {},
    }


async def apply_subscription_event(event: NormalizedSubscriptionEvent):
    """
    정규화된 이벤트 → 자격 반영. **제공자가 자격을 만지는 유일한 통로다.**

    반환값은 기존 SubscriptionWebhookResponse 그대로다(entitled, status, next_billing).

    멱등성은 자격 코어가 쥔다: 같은 (provider, event_type, transaction_id, user_id)
    는 지문이 같아 두 번째 호출이 idempotent_replay 로 처리된다. 그래서 제공자 쪽에서
    재시도가 나도 자격이 두 번 연장되지 않는다.

    ⚠️ DID_FAIL_TO_RENEW 는 자격 코어에서 **만료 계열**로 분류된다
    (subscription_webhook_parser.ExpireTypes). 즉 실패한 갱신은 기간을 늘리지 않고
    상태를 만료로 내린다 — "실패했는데 연장되는" 사고가 구조적으로 불가능하다.
    """
    from .subscription_webhook_service import handle_subscription_webhook

    logger.info(
        "구독 이벤트 — provider=%s type=%s user=%s tx=%s",
        event.provider, event.event_type, event.user_id, event.transaction_id,
    )
    return await handle_subscription_webhook(_payload(event))
