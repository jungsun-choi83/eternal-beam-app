# PayPal — legacy / dev-only (분류 확정)

**분류:** `LEGACY / DEV-ONLY`
**결정:** PayPal 데이터는 크레딧·소유권 마이그레이션에서 **제외한다.**
**코드:** 지금은 **그대로 둔다.** 제거는 별도 단계에서 판단한다.

---

## 1. 결정 요약

PayPal 테마 결제는 개발 중에만 쓰였고 실 고객 구매를 담고 있지 않다.
따라서 `purchased_slots` 의 어떤 행도 `user_theme_entitlements` 로 옮기지 않는다.

이관 코드는 **작성하지 않는다.** 경로가 존재하지 않는 것이 가장 확실한 보장이며,
나중에 누군가 이관을 검토하더라도 그것은 새로 내리는 결정이어야지 기존 스크립트에
플래그를 켜는 일이 되어서는 안 된다.

---

## 2. 근거 (검증 결과)

| # | 확인 | 결과 |
|---|------|------|
| D1 | `backend/main.py` 가 paypal 라우터를 마운트하는가 | **아니오** |
| D2 | 저장소 **전체 이력**에서 `main.py` 에 `paypal` 문자열이 등장한 적이 있는가 | **없음** (`git log -S "paypal" -- backend/main.py` 가 비어 있음) |
| D3 | `purchased_slots` 에 쓰는 코드 | `supabase_assets.record_theme_purchase` **하나뿐**, 호출부는 `routers/paypal.py:106` **하나뿐** |
| D4 | `PAYPAL_MODE` 기본값 | `"sandbox"` (`paypal_service.py:33`) |
| D5 | 이력 어디든 `PAYPAL_MODE=live` 가 있었는가 | **없음** |
| D6 | 배포 설정(`render.yaml`, `vercel.json`, `Dockerfile`, GitHub Actions)에 PayPal 자격증명 | **없음** |
| D7 | `.env.example` / `.env.local` 의 PayPal 값 | 전부 주석 처리된 플레이스홀더 (`your_paypal_client_id_here`) |
| D8 | 프론트 `VITE_PAYPAL_CLIENT_ID` | 미설정 → `isPaypalConfigured()` 가 false → 결제 버튼이 렌더되지 않음 |

**핵심 논거 (D1–D3):**
`purchased_slots` 에 쓰는 유일한 함수는 마운트된 적 없는 라우터에서만 호출된다.
따라서 **배포된 API 는 그 표에 단 한 줄도 쓸 수 없었다.** 실 고객이 PayPal 로
결제하는 것은 코드 배치상 불가능했다.

표에 행이 있다면 그것은 SQL Editor 수동 삽입이거나 개발자가 로컬에서 띄운 서버의
결과이며, 어느 쪽도 실 매출이 아니다.

**추가 방어 (현재 상태):**
라우터에 `dependencies=[Depends(_paypal_disabled)]` 가 걸려 있어, 실수로 마운트해도
모든 경로가 `410 PAYPAL_DISABLED` 로 닫힌다. 즉 `purchased_slots` 는 **동결**돼 있어
새 행이 생길 수 없다.

---

## 3. 재검증 방법

가정을 반증할 증거를 찾는 스크립트다. 반증이 하나라도 나오면 exit code 1.

```bash
# 프로덕션 자격증명으로 실행할 것 (읽기 전용, 이관 기능 없음)
SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \
  python -m backend.scripts.verify_paypal_dev_only
```

확인 항목: `purchased_slots` 행 수와 **PayPal capture id 유무**,
`user_theme_entitlements` 의 `provider='paypal'` 행, 그리고 라우터 마운트 여부.

> `capture id` 가 붙은 행이 나오면 실제 PayPal 승인이 일어났다는 뜻이므로
> **가장 강한 반증**이다. 그때는 이 문서의 분류를 다시 검토해야 한다.

DB 자격증명 없이 실행하면 DB 항목은 `UNKNOWN` 으로 남는다 — 보지 못한 것을
"없다"고 단정하지 않기 위해서다.

---

## 4. 이 분류가 강제하는 규칙

1. **크레딧·소유권 마이그레이션은 `purchased_slots` 를 읽지 않는다.**
   `backend/tests/test_paypal_data_excluded.py` 가 이것을 테스트로 고정한다.
2. **`user_theme_entitlements` 에 `provider='paypal'` 행을 만들지 않는다.**
3. **PayPal 코드는 지우지 않는다** (이번 단계에서는). 410 으로 닫혀 있고
   마운트되지 않아 위험이 없으며, 이력을 남겨 두는 편이 낫다.
4. `purchased_slots` 는 읽기 전용 유물로 취급한다. 새 기능이 참조해서는 안 된다.

---

## 5. 나중에 제거할 때 함께 지울 것

지금은 지우지 않지만, 제거 단계가 오면 범위는 다음과 같다:

- `backend/routers/paypal.py`
- `backend/services/paypal_service.py`
- `backend/services/theme_prices.py` (USD 가격표 — Toss/KRW 로 대체됨)
- `backend/models/paypal.py`
- `supabase_assets.record_theme_purchase` / `get_purchased_themes` / `check_payment_for_theme`
- `routers/assets.py` 의 `GET /purchased-slots`
- 프론트: `src/lib/paypal-sdk.ts`, `src/lib/paypal-api.ts`, `payment-screen.tsx` 의 PayPal 분기
- 표 `public.purchased_slots` (삭제 전 덤프를 남길 것)
- `backend/tests/test_paypal_disabled.py`, `backend/tests/test_paypal_data_excluded.py`

⚠️ 표 삭제는 **가장 마지막**에, 그리고 위 검증 스크립트를 다시 돌려 반증이 없음을
확인한 뒤에 한다.
