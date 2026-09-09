"""
Apple App Store Server Notifications / Google Play RTDN → 통합 이벤트.

SUBSCRIPTION_MOCK=1 또는 단순 JSON 바디 시 목업 파싱.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any, Literal, Optional

from ..data.subscription_plans import resolve_plan_id_from_product

StoreType = Literal["apple", "google", "mock", "toss"]
RenewalTypes = frozenset({"INITIAL_BUY", "RENEWAL", "SUBSCRIBED", "DID_RENEW"})
ExpireTypes = frozenset(
  {"EXPIRATION", "EXPIRED", "REVOKE", "REFUND", "DID_FAIL_TO_RENEW", "GRACE_PERIOD_EXPIRED"}
)
CancelTypes = frozenset({"CANCEL", "DID_CHANGE_RENEWAL_STATUS", "DID_CHANGE_RENEWAL_PREF"})


def _mock_enabled() -> bool:
  return os.getenv("SUBSCRIPTION_MOCK", "0").strip().lower() in ("1", "true", "yes")


@dataclass
class ParsedSubscriptionWebhook:
  store_type: StoreType
  event_type: str
  user_id: str
  plan_id: str
  transaction_id: str
  original_transaction_id: str
  event_fingerprint: str
  raw: dict[str, Any]


def _fingerprint(store: str, event: str, tx: str, user: str) -> str:
  payload = f"{store}:{event}:{tx}:{user}"
  return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_event_type(raw: str) -> str:
  u = (raw or "").strip().upper().replace("-", "_").replace(" ", "_")
  aliases = {
    "SUBSCRIBED": "INITIAL_BUY",
    "DID_RENEW": "RENEWAL",
    "DID_RECOVER": "RENEWAL",
    "RENEW": "RENEWAL",
    "EXPIRED": "EXPIRATION",
    "REVOKED": "EXPIRATION",
  }
  return aliases.get(u, u)


def parse_subscription_webhook(body: dict[str, Any]) -> ParsedSubscriptionWebhook:
  """
  통합 파서.

  목업/테스트 JSON 예:
  {
    "store_type": "apple",
    "notification_type": "INITIAL_BUY",
    "user_id": "demo-user",
    "plan_id": "standard_subscription",
    "transaction_id": "sub_tx_001"
  }
  """
  raw = body.get("raw") if isinstance(body.get("raw"), dict) else body

  # --- 명시적 필드 (앱·테스트) ---
  user_id = (body.get("user_id") or raw.get("user_id") or "").strip()
  notif = body.get("notification_type") or raw.get("notification_type")
  store = (body.get("store_type") or raw.get("store_type") or "mock").strip().lower()

  plan_id = body.get("plan_id") or raw.get("plan_id")
  product_id = body.get("product_id") or raw.get("product_id")
  if product_id and not plan_id:
    plan_id = resolve_plan_id_from_product(str(product_id))
  plan_id = (plan_id or "standard_subscription").strip()

  tx = (
    body.get("transaction_id")
    or raw.get("transaction_id")
    or raw.get("purchaseToken")
    or ""
  )
  tx = str(tx).strip()
  orig = (
    body.get("original_transaction_id")
    or raw.get("original_transaction_id")
    or raw.get("originalTransactionId")
    or tx
  )
  orig = str(orig).strip()

  # --- Apple ASN v2 (간략) ---
  if not notif and isinstance(raw, dict):
    if raw.get("notificationType"):
      notif = raw["notificationType"]
      store = "apple"
    elif raw.get("signedPayload"):
      if _mock_enabled():
        notif = "INITIAL_BUY"
        store = "apple"
      else:
        raise ValueError(
          "Apple signedPayload 검증은 프로덕션에서 구현 필요 (SUBSCRIPTION_MOCK=1 로 테스트)"
        )

  # --- Google RTDN (간략) ---
  if not notif and isinstance(raw, dict):
    sub_notif = raw.get("subscriptionNotification") or {}
    if isinstance(sub_notif, dict) and sub_notif.get("notificationType") is not None:
      gmap = {
        1: "RENEWAL",
        2: "RENEWAL",
        3: "CANCEL",
        4: "INITIAL_BUY",
        12: "EXPIRATION",
        13: "EXPIRATION",
      }
      notif = gmap.get(int(sub_notif["notificationType"]), "OTHER")
      store = "google"
      tx = str(sub_notif.get("purchaseToken") or tx)
      if not user_id:
        user_id = str(raw.get("user_id") or sub_notif.get("obfuscatedExternalAccountId") or "").strip()

  event_type = _normalize_event_type(str(notif or "OTHER"))

  if not user_id:
    if _mock_enabled() and tx:
      user_id = f"sub_user_{hashlib.sha256(orig.encode()).hexdigest()[:12]}"
    else:
      raise ValueError("user_id is required in webhook body")

  # 정규 Eternal Beam 신원으로 맞춘다. 스토어가 준 이메일의 대소문자 하나 때문에
  # 저장(A)과 프리미엄 인가 조회(B)가 갈라지면 결제한 사용자가 "구독 없음"이 된다.
  # resolve_identity 가 eb_user_id 를 소문자 이메일로 만드는 것과 같은 규칙이다.
  from .identity_service import canonical_user_id

  user_id = canonical_user_id(user_id) or user_id

  if not tx:
    tx = f"gen_{hashlib.sha256(f'{user_id}:{event_type}'.encode()).hexdigest()[:20]}"

  st: StoreType = store if store in ("apple", "google", "mock", "toss") else "mock"  # type: ignore[assignment]
  fp = _fingerprint(st, event_type, tx, user_id)

  return ParsedSubscriptionWebhook(
    store_type=st,
    event_type=event_type,
    user_id=user_id,
    plan_id=plan_id,
    transaction_id=tx,
    original_transaction_id=orig,
    event_fingerprint=fp,
    raw=body if isinstance(body, dict) else {},
  )


def is_renewal_event(event_type: str) -> bool:
  return _normalize_event_type(event_type) in RenewalTypes


def is_expiration_event(event_type: str) -> bool:
  return _normalize_event_type(event_type) in ExpireTypes


def is_cancel_event(event_type: str) -> bool:
  return _normalize_event_type(event_type) in CancelTypes
