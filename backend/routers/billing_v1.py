"""
/api/v1/billing — 웹 정기결제 (Toss 가 1번 제공자).

신원 규칙은 구독 라우터와 같다: **모든 경로가 토큰에서 확정된 정규 신원만 쓴다.**
바디의 user_id 를 받지 않는다 — 받으면 남의 구독을 결제·해지할 수 있다.

시크릿 규칙: TOSS_SECRET_KEY 와 billingKey 는 **어떤 응답에도 실리지 않는다.**
프론트로 나가는 것은 공개 클라이언트 키와 주문 정보뿐이다.

자격은 이 라우터가 직접 바꾸지 않는다 — billing_service 가 정규화된 이벤트를
만들고, 기존 자격 코어가 반영한다.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from ..auth import AuthedUser, require_user
from ..services import billing_service, billing_store, toss_billing

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/billing", tags=["billing-v1"])


def _http(e) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


class CheckoutResponse(BaseModel):
    provider: str
    #: Toss 결제창을 띄우는 **공개** 키. 시크릿이 아니다.
    client_key: str
    customer_key: str
    order_id: str
    order_name: str
    amount: int
    plan_id: str
    success_path: str = toss_billing.BILLING_SUCCESS_PATH
    fail_path: str = toss_billing.BILLING_FAIL_PATH


class ConfirmRequest(BaseModel):
    #: Toss 가 리다이렉트로 돌려주는 카드 등록 인증 키
    auth_key: str
    customer_key: str
    order_id: str
    plan_id: str = billing_service.DEFAULT_PLAN_ID


@router.get("/config")
async def billing_config():
    """
    결제 사용 가능 여부와 공개 키. 로그인 전 화면이 "결제 가능한가"를 물을 수 있다.

    **시크릿을 싣지 않는다.** 설정 여부만 알려 준다.
    """
    ck = toss_billing.client_key()
    return {
        "provider": billing_service.PROVIDER_TOSS,
        "configured": bool(ck) or toss_billing.mock_enabled(),
        "client_key": ck,
        "test_mode": toss_billing.mock_enabled() or (bool(ck) and toss_billing.is_test_key(ck)),
    }


@router.post("/checkout", response_model=CheckoutResponse)
async def start_checkout(user: AuthedUser = Depends(require_user)):
    """결제창에 필요한 값 발급. **아직 청구하지 않는다.**"""
    try:
        s = await billing_service.start_checkout(user_id=user.user_id)
    except toss_billing.TossError as e:
        raise _http(e) from e
    except billing_store.BillingStoreError as e:
        raise _http(e) from e
    return CheckoutResponse(
        provider=s.provider, client_key=s.client_key, customer_key=s.customer_key,
        order_id=s.order_id, order_name=s.order_name, amount=s.amount, plan_id=s.plan_id,
    )


@router.post("/confirm")
async def confirm_checkout(body: ConfirmRequest, user: AuthedUser = Depends(require_user)):
    """
    카드 등록 → 첫 청구 → 자격 ACTIVE.

    같은 order_id 로 다시 들어오면 재청구하지 않는다(새로고침 방어).
    """
    try:
        return await billing_service.confirm_checkout(
            user_id=user.user_id, auth_key=body.auth_key,
            customer_key=body.customer_key, order_id=body.order_id, plan_id=body.plan_id,
        )
    except (billing_service.BillingError, toss_billing.TossError,
            billing_store.BillingStoreError) as e:
        raise _http(e) from e


@router.post("/cancel")
async def cancel_subscription(user: AuthedUser = Depends(require_user)):
    """해지 예약 — 이미 낸 기간이 끝날 때까지 계속 이용할 수 있다."""
    try:
        return await billing_service.cancel(user_id=user.user_id)
    except (billing_service.BillingError, billing_store.BillingStoreError) as e:
        raise _http(e) from e


@router.post("/resume")
async def resume_subscription(user: AuthedUser = Depends(require_user)):
    """해지 예약 취소 (기간이 남아 있을 때). 재결제 없음."""
    try:
        return await billing_service.resume(user_id=user.user_id)
    except (billing_service.BillingError, billing_store.BillingStoreError) as e:
        raise _http(e) from e


@router.get("/status")
async def billing_status(user: AuthedUser = Depends(require_user)):
    """
    본인 청구 상태. 다른 기기에서 로그인하면 이 조회만으로 복원된다
    (웹 결제에는 스토어식 '구매 복원'이 없다 — 신원이 곧 구독이다).
    """
    try:
        return await billing_service.status(user_id=user.user_id)
    except billing_store.BillingStoreError as e:
        raise _http(e) from e


@router.post("/renew-due")
async def renew_due(
    limit: int = 100,
    x_billing_cron_secret: str | None = Header(default=None),
):
    """
    기간이 끝난 구독 갱신 (크론/배치 전용).

    사용자 토큰이 아니라 **공유 시크릿**으로 인가한다 — 부르는 주체가 사람이
    아니라 스케줄러다. 미설정이면 503 으로 닫는다: 무인증으로 열리면 아무나
    전체 사용자에게 청구를 트리거할 수 있다.
    """
    import hmac

    expected = (os.getenv("BILLING_CRON_SECRET") or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "CRON_NOT_CONFIGURED",
                "message": "BILLING_CRON_SECRET 이 설정되지 않았습니다.",
            },
        )
    provided = (x_billing_cron_secret or "").strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=401,
            detail={"code": "CRON_FORBIDDEN", "message": "크론 시크릿이 올바르지 않습니다."},
        )

    results = await billing_service.renew_due(limit=limit)
    return {"processed": len(results), "results": results}
