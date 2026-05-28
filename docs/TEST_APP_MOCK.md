# 테스트 앱 — 목업(IAP · 누끼) 설정

소비자용 스토어 결제 없이 **로컬/스테이징에서 전체 플로우**를 검증할 때 사용합니다.

## 1. 백엔드 (FastAPI)

```powershell
cd backend
copy env.local.example env.local
cd ..
npm run video-api
```

`backend/env.local` 핵심 값:

| 변수 | 값 | 설명 |
|------|-----|------|
| `HYBRID_USE_SUPABASE` | `0` | Supabase 없이 메모리 지갑 |
| `PAYMENT_MOCK` | `1` | 영수증 8자 이상이면 IAP 통과 |
| `STARTER_CREDITS` | `4` | 신규 사용자 시작 크레딧 |

헬스 확인: http://localhost:8000/api/health

## 2. 프론트 (Vite)

```powershell
copy .env.test.example .env.local
npm run dev
```

| 변수 | 값 | 설명 |
|------|-----|------|
| `VITE_MOCK_CUTOUT` | `1` | 서버 누끼 없이 사진만 리사이즈 (빠름) |
| `VITE_IAP_MOCK` | `1` | 테마 화면에서 목업 크레딧 충전 버튼 |
| `VITE_ENABLE_CREDITS` | `1` | 크레딧·Luma 경로 사용 |

`VITE_VIDEO_API_URL`을 비우면 dev에서 `/api` → Vite 프록시 → `:8000`.

## 3. IAP 목업 테스트 (`credit_pack_4`)

1. 앱 → 사진 업로드 → AI 처리 → **배경 선택**
2. 크레딧이 4개 미만이면 **「크레딧 충전 (테스트 · 4,900원)」** 버튼 표시
3. 탭하면 `POST /api/v1/payment/verify-and-charge` 호출 (+4 크레딧)

### cURL (백엔드만 검증)

```bash
curl -X POST http://localhost:8000/api/v1/payment/verify-and-charge \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"demo-user\",\"receipt_data\":\"mock_receipt_token_abc12345\",\"store_type\":\"apple\",\"product_id\":\"credit_pack_4\"}"
```

성공 예:

```json
{
  "success": true,
  "product_id": "credit_pack_4",
  "amount_krw": 4900,
  "credits_added": 4,
  "credits_remaining": 8,
  "status": "success",
  "idempotent_replay": false,
  "message": "크레딧이 충전되었습니다."
}
```

## 4. 누끼 「연결 느림/끊김」 줄이기

| 방법 | 설정 |
|------|------|
| 테스트 앱 (권장) | `VITE_MOCK_CUTOUT=1` |
| 실제 서버 누끼 | `npm run video-api` 실행, Render 콜드스타트 시 1~2분 대기 |
| 서버 재시도 | 앱이 자동으로 `/api/health` 워밍업 + 누끼 2회 시도 |

## 5. Supabase 사용 시 (선택)

1. `docs/supabase_hybrid_business.sql`
2. `docs/supabase_payment_iap.sql`
3. `HYBRID_USE_SUPABASE=1`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`

## 6. 구독 목업 (Standard · 월 9,900원 · +12 크레딧)

`SUBSCRIPTION_MOCK=1` (backend/env.local) 후:

```bash
curl -X POST http://localhost:8000/api/v1/subscription/webhook \
  -H "Content-Type: application/json" \
  -d "{\"notification_type\":\"INITIAL_BUY\",\"user_id\":\"demo-user\",\"transaction_id\":\"sub_test_001\"}"
```

상세: `docs/SUBSCRIPTION.md`

## 7. 관련 파일

| 역할 | 경로 |
|------|------|
| IAP 라우터 | `backend/routers/payment_v1.py` |
| 충전 로직 | `backend/services/iap_charge_service.py` |
| 상품 정의 | `backend/data/iap_products.py` |
| SQL | `docs/supabase_payment_iap.sql` |
| API 문서 | `docs/IAP_PAYMENT.md` |
| 프론트 목업 | `src/lib/iap-mock.ts` |
| 누끼 목업 | `src/lib/mock-cutout.ts` |

## 7. 프로덕션 전환

- `PAYMENT_MOCK=0`, `APPLE_SHARED_SECRET`, `GOOGLE_PACKAGE_NAME` 설정
- `VITE_IAP_MOCK=0`, 실제 스토어 SDK에서 `receipt_data` 전달
- `VITE_MOCK_CUTOUT=0` (실제 rembg 서버 누끼)
