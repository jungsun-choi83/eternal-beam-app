# 정기 구독 (Standard Subscription)

## 플랜

| 항목 | 값 |
|------|-----|
| plan_id | `standard_subscription` |
| 표시명 | 스탠다드 구독 플랜 |
| 요금 | 월 9,900 KRW (자동 갱신) |
| 매월 크레딧 | +12 (영상 약 3세트) |

단품 IAP(`credit_pack_4`)와 **별도** — 구독은 월 자동 결제 + 매월 크레딧 지급.

---

## Supabase SQL

`docs/supabase_hybrid_business.sql` → `docs/supabase_payment_iap.sql` 후:

```text
docs/supabase_subscription.sql
```

테이블:

- `user_subscriptions` — user_id, plan_id, status, next_billing_date, …
- `subscription_webhook_events` — 웹훅 멱등 로그

RPC:

- `process_subscription_renewal` — active + next_billing + wallet +12
- `process_subscription_status_change` — canceled / expired

---

## API

### `GET /api/v1/subscription/plans`

플랜 카탈로그.

### `GET /api/v1/subscription/status/{user_id}`

Unity·앱이 **동기화 전** 호출.

```json
{
  "user_id": "demo-user",
  "plan_id": "standard_subscription",
  "status": "active",
  "next_billing_date": "2026-06-26T12:00:00+00:00",
  "entitled": true,
  "credits_remaining": 24,
  "display_name": "스탠다드 구독 플랜",
  "price_krw_monthly": 9900,
  "credits_per_month": 12
}
```

`entitled: false` → Unity에서 `device/sync` 호출 차단 권장.

환경 변수 `SUBSCRIPTION_GATE_DEVICE=1` 이면 서버가 `device/sync` 에서 직접 **403** 반환.

---

### `POST /api/v1/subscription/webhook`

Apple ASN / Google RTDN / **목업 JSON**.

#### 목업 — 최초 구독 (`INITIAL_BUY`)

```bash
curl -X POST http://localhost:8000/api/v1/subscription/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "store_type": "mock",
    "notification_type": "INITIAL_BUY",
    "user_id": "demo-user",
    "plan_id": "standard_subscription",
    "transaction_id": "sub_initial_001"
  }'
```

**응답 (200)**

```json
{
  "success": true,
  "user_id": "demo-user",
  "plan_id": "standard_subscription",
  "event_type": "INITIAL_BUY",
  "subscription_status": "active",
  "credits_added": 12,
  "credits_remaining": 24,
  "next_billing_date": "2026-06-26T...",
  "entitled": true,
  "idempotent_replay": false,
  "message": "구독이 활성화되었고 월 크레딧이 지급되었습니다."
}
```

#### 목업 — 매월 갱신 (`RENEWAL`)

```json
{
  "notification_type": "RENEWAL",
  "user_id": "demo-user",
  "transaction_id": "sub_renew_2026_05"
}
```

동일 `transaction_id` 재전송 → `idempotent_replay: true`, `credits_added: 0`.

#### 만료 (`EXPIRATION`)

```json
{
  "notification_type": "EXPIRATION",
  "user_id": "demo-user",
  "transaction_id": "sub_expire_001"
}
```

→ `subscription_status: "expired"`, `entitled: false`

#### 해지 (`CANCEL`)

→ `subscription_status: "canceled"`  
`next_billing_date` 이전까지는 `entitled: true` (유예).

---

## 처리 흐름

```
스토어 웹훅 (INITIAL_BUY | RENEWAL)
  ① event_fingerprint 중복 검사
  ② process_subscription_renewal (트랜잭션)
     - subscription_webhook_events INSERT
     - user_subscriptions → active, next_billing_date +30일
     - user_wallets += 12

EXPIRATION | DID_FAIL_TO_RENEW
  → user_subscriptions.status = expired

CANCEL
  → status = canceled (기간 만료 전 entitled 유지)
```

---

## Unity 연동

1. NFC / 동기화 전: `GET /api/v1/subscription/status/{user_id}`
2. `entitled == false` → 앱 구독 화면으로 유도, sync 중단
3. (선택) 서버 강제: `SUBSCRIPTION_GATE_DEVICE=1` → `GET /api/v1/device/sync` 403

---

## 환경 변수

| 변수 | 용도 |
|------|------|
| `SUBSCRIPTION_MOCK=1` | signedPayload 없이 JSON 테스트 |
| `SUBSCRIPTION_GATE_DEVICE=1` | device/sync 구독 필수 |
| `HYBRID_USE_SUPABASE=0` | 로컬 메모리 구독·지갑 |

---

## 관련 파일

| 역할 | 경로 |
|------|------|
| SQL | `docs/supabase_subscription.sql` |
| 플랜 | `backend/data/subscription_plans.py` |
| 웹훅 | `backend/routers/subscription_v1.py` |
| 로직 | `backend/services/subscription_webhook_service.py` |
| 파서 | `backend/services/subscription_webhook_parser.py` |
| 저장소 | `backend/services/subscription_store_service.py` |
