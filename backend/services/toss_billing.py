"""
Toss Payments 자동결제(빌링) 클라이언트 — **제공자 어댑터**.

이 모듈은 Toss API 만 안다. 자격(user_subscriptions)을 절대 건드리지 않는다 —
결과를 정규화된 이벤트로 만들어 billing_events 로 넘기는 것은 호출부의 일이다.

흐름 (Toss 자동결제 표준):

    1) 프론트  requestBillingAuth(customerKey)  → 사용자가 카드 등록
    2) 리다이렉트 successUrl?authKey=..&customerKey=..
    3) 백엔드  POST /v1/billing/authorizations/issue  → billingKey 발급
    4) 백엔드  POST /v1/billing/{billingKey}          → 실제 청구
    5) 갱신    같은 4번을 주기마다 반복

⚠️ **시크릿은 백엔드 전용이다.** TOSS_SECRET_KEY 는 절대 응답에 실리지 않고
프론트로 나가지 않는다. billingKey 도 마찬가지다 — 그것 자체가 결제 수단이다.

TOSS_MOCK=1 이면 네트워크를 타지 않는다. 테스트와 로컬 개발용이며, 실제 청구가
일어나지 않는다. 테스트 키(test_sk_...)를 쓰더라도 실 결제는 발생하지 않지만,
네트워크 의존 없이 계약을 검증하려면 목업이 필요하다.
"""

from __future__ import annotations

import base64
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

TOSS_API_BASE = "https://api.tosspayments.com"

#: 결제 수단 등록 후 Toss 가 되돌려보내는 경로 (프론트 라우트)
BILLING_SUCCESS_PATH = "/billing/success"
BILLING_FAIL_PATH = "/billing/fail"


class TossError(Exception):
    """Toss 호출 실패. code 로 실패 사유를 구분한다."""

    def __init__(self, code: str, message: str, *, status: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def mock_enabled() -> bool:
    return os.getenv("TOSS_MOCK", "0").strip().lower() in ("1", "true", "yes")


def _secret_key() -> str:
    return (os.getenv("TOSS_SECRET_KEY") or "").strip()


def client_key() -> str:
    """
    프론트에 내려도 되는 **공개** 키.

    Toss 클라이언트 키는 공개용이다(결제창을 띄우는 데만 쓰인다). 시크릿 키와
    혼동하지 않도록 이름과 반환 지점을 분리해 둔다.
    """
    return (os.getenv("TOSS_CLIENT_KEY") or "").strip()


def is_test_key(key: str) -> bool:
    """test_ 접두사 = 샌드박스. 실 키가 섞여 들어오면 알아채야 한다."""
    return key.startswith("test_")


def assert_configured() -> None:
    """
    설정 누락은 **열리는 게 아니라 닫힌다**.

    시크릿이 없는데 결제를 시도하면 Toss 가 401 을 주고, 우리는 그것을 "결제 실패"
    로 오해해 사용자에게 카드 문제라고 말하게 된다. 먼저 여기서 끊는다.
    """
    if mock_enabled():
        return
    if not _secret_key():
        raise TossError(
            "TOSS_NOT_CONFIGURED",
            "TOSS_SECRET_KEY 가 설정되지 않았습니다.",
            status=503,
        )
    if not client_key():
        raise TossError(
            "TOSS_NOT_CONFIGURED",
            "TOSS_CLIENT_KEY 가 설정되지 않았습니다.",
            status=503,
        )


def _auth_header() -> str:
    # Toss 는 시크릿 키를 basic auth 의 username 으로 쓴다 (비밀번호는 빈 문자열).
    raw = f"{_secret_key()}:".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def new_customer_key(user_id: str) -> str:
    """
    Toss 고객 키. **사용자 식별자를 그대로 쓰지 않는다.**

    customerKey 는 결제창 URL 에 실려 브라우저에 노출된다. 이메일(=우리 신원)을
    그대로 쓰면 결제 링크에서 사용자 이메일이 새어 나간다. 무작위 값을 만들고
    우리 DB 에서만 신원과 연결한다.
    """
    return f"eb_{uuid.uuid4().hex}"


def new_order_id(kind: str) -> str:
    """주문 번호 = 멱등성의 축. 결제 1건마다 새로 만든다."""
    return f"eb_{kind.lower()}_{uuid.uuid4().hex[:20]}"


@dataclass(frozen=True)
class BillingKeyResult:
    billing_key: str
    customer_key: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class ChargeResult:
    ok: bool
    payment_key: Optional[str]
    order_id: str
    amount: int
    raw: dict[str, Any]
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None


async def _post(path: str, body: dict[str, Any], *, idempotency_key: str | None = None) -> dict:
    import httpx

    headers = {
        "Authorization": _auth_header(),
        "Content-Type": "application/json",
    }
    # Toss 는 Idempotency-Key 헤더를 지원한다 — 네트워크 재시도가 이중 청구가
    # 되지 않게 한다. 우리 order_id 를 그대로 쓴다.
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    async with httpx.AsyncClient(timeout=20.0) as client:
        res = await client.post(f"{TOSS_API_BASE}{path}", json=body, headers=headers)
    try:
        data = res.json()
    except Exception:
        data = {"raw_text": res.text[:500]}
    if res.status_code >= 400:
        code = str(data.get("code") or f"HTTP_{res.status_code}")
        msg = str(data.get("message") or "Toss 요청이 실패했습니다.")
        raise TossError(code, msg, status=502)
    return data


async def issue_billing_key(*, auth_key: str, customer_key: str) -> BillingKeyResult:
    """
    카드 등록 인증(authKey) → 청구에 쓸 billingKey.

    이 호출은 **돈을 움직이지 않는다.** 결제 수단을 저장할 뿐이다.
    """
    assert_configured()
    if mock_enabled():
        return BillingKeyResult(
            billing_key=f"mock_bk_{uuid.uuid4().hex[:16]}",
            customer_key=customer_key,
            raw={"mock": True},
        )

    data = await _post(
        "/v1/billing/authorizations/issue",
        {"authKey": auth_key, "customerKey": customer_key},
    )
    bk = str(data.get("billingKey") or "").strip()
    if not bk:
        raise TossError("NO_BILLING_KEY", "Toss 응답에 billingKey 가 없습니다.")
    return BillingKeyResult(billing_key=bk, customer_key=customer_key, raw=data)


async def charge(
    *,
    billing_key: str,
    customer_key: str,
    amount: int,
    order_id: str,
    order_name: str,
) -> ChargeResult:
    """
    실제 청구. **실패를 예외로 올리지 않는다** — 실패도 결과의 한 형태다.

    갱신 배치가 한 건 실패로 통째로 멈추면 안 되고, 실패는 "연장하지 않음"이라는
    정상적인 상태 전이이기 때문이다. 호출부는 ok 를 보고 이벤트 종류를 정한다:
        ok=True  → RENEWAL / INITIAL_BUY
        ok=False → DID_FAIL_TO_RENEW  (자격 코어에서 만료 계열로 처리된다)
    """
    assert_configured()
    if mock_enabled():
        # 목업에서도 실패를 시험할 수 있어야 한다 — 금액 0 을 실패 신호로 쓴다.
        if amount <= 0:
            return ChargeResult(
                ok=False, payment_key=None, order_id=order_id, amount=amount,
                raw={"mock": True}, failure_code="MOCK_FAILURE",
                failure_message="목업 결제 실패 (amount<=0)",
            )
        return ChargeResult(
            ok=True, payment_key=f"mock_pk_{uuid.uuid4().hex[:16]}",
            order_id=order_id, amount=amount, raw={"mock": True},
        )

    try:
        data = await _post(
            f"/v1/billing/{billing_key}",
            {
                "customerKey": customer_key,
                "amount": amount,
                "orderId": order_id,
                "orderName": order_name,
            },
            idempotency_key=order_id,
        )
    except TossError as e:
        logger.warning("Toss 청구 실패 — order=%s code=%s: %s", order_id, e.code, e.message)
        return ChargeResult(
            ok=False, payment_key=None, order_id=order_id, amount=amount,
            raw={"code": e.code}, failure_code=e.code, failure_message=e.message,
        )

    status = str(data.get("status") or "").upper()
    if status != "DONE":
        return ChargeResult(
            ok=False, payment_key=data.get("paymentKey"), order_id=order_id,
            amount=amount, raw=data, failure_code=status or "NOT_DONE",
            failure_message=f"결제가 완료 상태가 아닙니다 (status={status}).",
        )
    return ChargeResult(
        ok=True, payment_key=str(data.get("paymentKey") or ""), order_id=order_id,
        amount=int(data.get("totalAmount") or amount), raw=data,
    )


# ── 일회성 결제 (구독과 별개 경로) ────────────────────────────────────────────
#
# 정기결제(billingKey)와 **다른 API 다.** 정기결제는 저장된 카드로 서버가 청구하고,
# 일회성 결제는 사용자가 결제창에서 직접 승인한 뒤 서버가 그것을 **확인**한다.
#
#     결제창 승인 → 리다이렉트(paymentKey, orderId, amount) → 서버가 confirm
#
# 테마 구매처럼 "카드를 저장할 이유가 없는" 단건 결제에 쓴다. 카드 등록을
# 요구하지 않으므로 구독한 적 없는 사용자도 결제할 수 있다.


@dataclass(frozen=True)
class ConfirmResult:
    ok: bool
    payment_key: Optional[str]
    order_id: str
    #: **Toss 가 확인해 준 실제 승인 금액.** 클라이언트가 보낸 값이 아니다.
    amount: int
    raw: dict[str, Any]
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None


async def confirm_payment(
    *, payment_key: str, order_id: str, amount: int
) -> ConfirmResult:
    """
    결제창 승인 → **서버 검증**. 이 호출이 성공해야 돈이 실제로 움직인다.

    ⚠️ amount 는 **서버가 보관한 주문 금액**을 넘겨야 한다. 리다이렉트 쿼리의
    금액을 그대로 넘기면 사용자가 URL 을 고쳐 1원짜리 결제로 유료 상품을 살 수
    있다. Toss 도 주문 금액과 다르면 거절하지만, 우리가 먼저 거르는 것이 맞다 —
    방어를 결제사에 위임하지 않는다.

    charge() 와 같은 규약이다: **실패를 예외로 올리지 않는다.**
    """
    assert_configured()
    if mock_enabled():
        if amount <= 0:
            return ConfirmResult(
                ok=False, payment_key=payment_key, order_id=order_id, amount=amount,
                raw={"mock": True}, failure_code="MOCK_FAILURE",
                failure_message="목업 결제 실패 (amount<=0)",
            )
        return ConfirmResult(
            ok=True, payment_key=payment_key or f"mock_pk_{uuid.uuid4().hex[:16]}",
            order_id=order_id, amount=amount, raw={"mock": True},
        )

    try:
        data = await _post(
            "/v1/payments/confirm",
            {"paymentKey": payment_key, "orderId": order_id, "amount": amount},
            idempotency_key=order_id,
        )
    except TossError as e:
        logger.warning("Toss 결제 확인 실패 — order=%s code=%s: %s", order_id, e.code, e.message)
        return ConfirmResult(
            ok=False, payment_key=payment_key, order_id=order_id, amount=amount,
            raw={"code": e.code}, failure_code=e.code, failure_message=e.message,
        )

    status = str(data.get("status") or "").upper()
    if status != "DONE":
        return ConfirmResult(
            ok=False, payment_key=str(data.get("paymentKey") or payment_key),
            order_id=order_id, amount=amount, raw=data,
            failure_code=status or "NOT_DONE",
            failure_message=f"결제가 완료 상태가 아닙니다 (status={status}).",
        )

    # 승인 금액은 **Toss 응답**을 정본으로 쓴다.
    return ConfirmResult(
        ok=True, payment_key=str(data.get("paymentKey") or payment_key),
        order_id=str(data.get("orderId") or order_id),
        amount=int(data.get("totalAmount") or amount), raw=data,
    )


async def _get(path: str) -> dict:
    import httpx

    headers = {"Authorization": _auth_header()}
    async with httpx.AsyncClient(timeout=20.0) as client:
        res = await client.get(f"{TOSS_API_BASE}{path}", headers=headers)
    try:
        data = res.json()
    except Exception:
        data = {"raw_text": res.text[:500]}
    if res.status_code >= 400:
        code = str(data.get("code") or f"HTTP_{res.status_code}")
        msg = str(data.get("message") or "Toss 조회가 실패했습니다.")
        raise TossError(code, msg, status=502)
    return data


async def lookup_payment_by_order(order_id: str) -> Optional[ConfirmResult]:
    """
    주문번호로 결제 상태 조회 — **재조정(reconciliation)의 근거.**

    왜 필요한가: 결제창에서 승인이 끝난 직후 사용자가 브라우저를 닫으면
    successUrl 로 돌아오지 못한다. 그러면 Toss 에는 승인된 결제가 있는데 우리
    주문은 영원히 pending 이다 — **돈은 받았고 물건은 만들지 않는 상태**다.
    이 조회가 그 간극을 메운다.

    반환:
        ConfirmResult(ok=True)   승인 완료 — 호출부가 주문을 PAID 로 만든다
        ConfirmResult(ok=False)  아직/취소/실패
        None                     결제 자체가 존재하지 않는다 (결제창을 안 열었다)

    ⚠️ 목업에서는 **항상 None** 이다. 목업이 "승인됨"을 지어내면 재조정 테스트가
       실제로는 없는 결제를 확정하게 되고, 그 습관이 프로덕션 버그가 된다.
       재조정 경로 테스트는 이 함수를 명시적으로 갈아 끼워서 한다.
    """
    assert_configured()
    if mock_enabled():
        return None

    try:
        data = await _get(f"/v1/payments/orders/{order_id}")
    except TossError as e:
        # 없는 주문은 404 로 온다 — 장애가 아니라 "결제한 적 없음"이다.
        if "NOT_FOUND" in (e.code or "").upper():
            return None
        logger.warning("Toss 주문 조회 실패 — order=%s code=%s", order_id, e.code)
        return None

    status = str(data.get("status") or "").upper()
    payment_key = str(data.get("paymentKey") or "") or None
    amount = int(data.get("totalAmount") or 0)
    if status == "DONE":
        return ConfirmResult(
            ok=True, payment_key=payment_key,
            order_id=str(data.get("orderId") or order_id), amount=amount, raw=data,
        )
    return ConfirmResult(
        ok=False, payment_key=payment_key, order_id=order_id, amount=amount,
        raw=data, failure_code=status or "NOT_DONE",
        failure_message=f"결제가 완료 상태가 아닙니다 (status={status}).",
    )
