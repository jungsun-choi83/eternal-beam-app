# 인앱 결제(IAP) — 영수증 검증 및 크레딧 충전

## 상품

| product_id | 가격 (KRW) | 충전 |
|------------|------------|------|
| `credit_pack_4` | 4,900 | +4 credits |

## 엔드포인트

### `GET /api/v1/payment/products`

상품 카탈로그 조회.

### `POST /api/v1/payment/verify-and-charge`

**Body (JSON)**

```json
{
  "user_id": "demo-user",
  "receipt_data": "<Apple base64 receipt 또는 Google purchaseToken>",
  "store_type": "apple",
  "product_id": "credit_pack_4"
}
```

| 필드 | 필수 | 설명 |
|------|------|------|
| `user_id` | O | 지갑 키 (`user_wallets.user_id`) |
| `receipt_data` | O | 스토어 영수증 토큰 |
| `store_type` | O | `"apple"` \| `"google"` |
| `product_id` | X | 기본 `credit_pack_4` |

---

### 성공 응답 (200) — 최초 충전

```json
{
  "success": true,
  "user_id": "demo-user",
  "product_id": "credit_pack_4",
  "amount_krw": 4900,
  "credits_added": 4,
  "credits_remaining": 16,
  "payment_id": 42,
  "transaction_id": "1000000123456789",
  "store_type": "apple",
  "status": "success",
  "idempotent_replay": false,
  "message": "크레딧이 충전되었습니다."
}
```

### 성공 응답 (200) — 중복 영수증 (재전송)

```json
{
  "success": true,
  "user_id": "demo-user",
  "product_id": "credit_pack_4",
  "amount_krw": 4900,
  "credits_added": 0,
  "credits_remaining": 16,
  "payment_id": 42,
  "transaction_id": "1000000123456789",
  "store_type": "apple",
  "status": "success",
  "idempotent_replay": true,
  "message": "이미 처리된 영수증입니다. 잔액은 변경되지 않았습니다."
}
```

### 실패 (400) — 검증 실패

```json
{
  "detail": "Apple verifyReceipt status=21002"
}
```

---

## 처리 흐름 (서버)

```
① store API로 receipt_data 검증 (또는 PAYMENT_MOCK=1)
② payment_history 에 동일 영수증/transaction_id 있는지 확인
③ PostgreSQL RPC process_iap_charge (단일 트랜잭션)
   - payment_history INSERT (status=success)
   - user_wallets.current_credits += 4
```

중복 방지:

- `payment_history.receipt_fingerprint` UNIQUE (SHA-256)
- `(store_type, transaction_id)` UNIQUE (success 건만)
- `user_id` 단위 asyncio Lock

---

## Supabase SQL

`docs/supabase_hybrid_business.sql` 실행 후:

```bash
# SQL Editor
docs/supabase_payment_iap.sql
```

---

## 환경 변수 (Render)

| 변수 | 용도 |
|------|------|
| `PAYMENT_MOCK=1` | 개발: 스토어 API 없이 검증 통과 |
| `APPLE_SHARED_SECRET` | App Store Connect 공유 비밀 |
| `APPLE_USE_SANDBOX=1` | 샌드박스 검증 URL |
| `GOOGLE_PACKAGE_NAME` | Android 패키지명 |
| `GOOGLE_VERIFY_URL` | (선택) Play 검증 프록시 URL |

---

## cURL 예시 (Mock)

```bash
curl -X POST https://eternal-beam-video-api.onrender.com/api/v1/payment/verify-and-charge \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "demo-user",
    "receipt_data": "mock-receipt-token-abc123xyz",
    "store_type": "apple",
    "product_id": "credit_pack_4"
  }'
```

`PAYMENT_MOCK=1` 일 때 `receipt_data` 8자 이상이면 성공.

---

## 프론트 연동 (참고)

1. 스토어에서 결제 완료 → `receipt` / `purchaseToken` 수신  
2. `POST /api/v1/payment/verify-and-charge` 호출  
3. `credits_remaining` 으로 UI 잔액 갱신  
4. `idempotent_replay: true` 면 이미 충전됨 — 추가 결제 UI 없이 OK  

지갑 조회: `GET /api/v1/pet/wallet/{user_id}`
