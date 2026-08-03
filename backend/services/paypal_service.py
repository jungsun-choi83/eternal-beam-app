"""
PayPal REST API (Orders v2) — 서버사이드 주문 생성/검증(capture).

흐름:
  1) 프론트에서 결제 버튼 클릭 → POST /api/paypal/create-order (theme_key)
  2) 서버가 여기 create_order()로 PayPal에 주문 생성 → order_id 반환
  3) 프론트 PayPal 버튼이 그 order_id로 사용자 승인(approve) 진행
  4) 승인 후 프론트가 POST /api/paypal/capture-order(order_id) 호출
  5) 서버가 capture_order()로 실제 캡처(승인 확정) → status가 COMPLETED인지 확인 후에만
     purchased_slots에 기록 — 클라이언트가 "결제완료"라고 말한 걸 그대로 믿지 않음.

환경변수:
  PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET  — PayPal Developer Dashboard 앱 자격증명
  PAYPAL_MODE  "sandbox"(기본) | "live"
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Optional

try:
    import requests
except ImportError:
    requests = None

_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}


def _mode() -> str:
    return os.getenv("PAYPAL_MODE", "sandbox").strip().lower()


def _api_base() -> str:
    return (
        "https://api-m.paypal.com"
        if _mode() == "live"
        else "https://api-m.sandbox.paypal.com"
    )


def _credentials() -> tuple[str, str]:
    client_id = (os.getenv("PAYPAL_CLIENT_ID") or "").strip()
    secret = (os.getenv("PAYPAL_CLIENT_SECRET") or "").strip()
    if not client_id or not secret:
        raise RuntimeError(
            "PAYPAL_CLIENT_ID/PAYPAL_CLIENT_SECRET이 설정되지 않았습니다."
        )
    return client_id, secret


def _get_access_token_sync() -> str:
    now = time.time()
    if _token_cache["token"] and now < float(_token_cache["expires_at"]) - 30:
        return _token_cache["token"]

    if not requests:
        raise RuntimeError("requests 패키지가 필요합니다: pip install requests")

    client_id, secret = _credentials()
    resp = requests.post(
        f"{_api_base()}/v1/oauth2/token",
        auth=(client_id, secret),
        data={"grant_type": "client_credentials"},
        headers={"Accept": "application/json", "Accept-Language": "en_US"},
        timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(f"PayPal OAuth 토큰 발급 실패 HTTP {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"PayPal OAuth 응답에 access_token이 없습니다: {data}")
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + float(data.get("expires_in", 3000))
    return token


async def _get_access_token() -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_access_token_sync)


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def create_order(
    amount_usd: str,
    *,
    currency: str = "USD",
    description: str = "",
    reference_id: Optional[str] = None,
) -> dict[str, Any]:
    """PayPal 주문 생성. 반환값에 id(order_id)와 status가 포함됨."""
    if not requests:
        raise RuntimeError("requests 패키지가 필요합니다: pip install requests")

    token = await _get_access_token()
    payload: dict[str, Any] = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {"currency_code": currency, "value": amount_usd},
                "description": description[:127] if description else None,
                "reference_id": reference_id,
            }
        ],
    }
    payload["purchase_units"][0] = {
        k: v for k, v in payload["purchase_units"][0].items() if v is not None
    }

    def _post() -> dict[str, Any]:
        r = requests.post(
            f"{_api_base()}/v2/checkout/orders",
            headers=_headers(token),
            json=payload,
            timeout=20,
        )
        if not r.ok:
            raise RuntimeError(
                f"PayPal 주문 생성 실패 HTTP {r.status_code}: {(r.text or '')[:800]}"
            )
        return r.json()

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _post)


async def capture_order(order_id: str) -> dict[str, Any]:
    """
    승인된 주문을 캡처(실제 대금 확정). 반환 status가 "COMPLETED"여야 결제 성공.
    이미 캡처된 주문을 다시 호출하면 PayPal이 422를 줄 수 있으므로 호출측에서
    idempotency(중복 지급 방지)를 별도로 처리해야 한다.
    """
    if not requests:
        raise RuntimeError("requests 패키지가 필요합니다: pip install requests")

    token = await _get_access_token()

    def _post() -> dict[str, Any]:
        r = requests.post(
            f"{_api_base()}/v2/checkout/orders/{order_id}/capture",
            headers=_headers(token),
            timeout=20,
        )
        if not r.ok:
            raise RuntimeError(
                f"PayPal 결제 확인(capture) 실패 HTTP {r.status_code}: {(r.text or '')[:800]}"
            )
        return r.json()

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _post)


async def get_order(order_id: str) -> dict[str, Any]:
    """주문 상태 조회 (디버깅/재확인용)."""
    if not requests:
        raise RuntimeError("requests 패키지가 필요합니다: pip install requests")

    token = await _get_access_token()

    def _get() -> dict[str, Any]:
        r = requests.get(
            f"{_api_base()}/v2/checkout/orders/{order_id}",
            headers=_headers(token),
            timeout=15,
        )
        if not r.ok:
            raise RuntimeError(f"PayPal 주문 조회 실패 HTTP {r.status_code}: {(r.text or '')[:500]}")
        return r.json()

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get)
