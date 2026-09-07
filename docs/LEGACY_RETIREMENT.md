# 레거시 은퇴 현황 (Phase 11)

Beam Credit 경제가 자리를 잡은 뒤, 그것을 대체한 옛 경로들을 정리한 기록이다.

두 가지 원칙이 이 문서 전체를 지배한다:

1. **주문을 만들 수 있는 코드가 남아 있으면 언젠가 다시 호출된다.**
   라우터에서만 떼어 내는 것으로는 부족하다 — 서비스 함수가 남아 있으면
   "임시로 하나만 열자"가 한 줄로 가능하고, 그러면 두 가격이 동시에 살아 있는
   상태로 돌아간다.
2. **과거 구매 증거는 새 아키텍처가 생겼다는 이유로 버리지 않는다.**
   고객이 "예전에 이거 샀는데요" 라고 물을 때 답할 근거가 없으면, 그건 데이터를
   정리한 것이 아니라 고객과의 기록을 잃은 것이다. 환불·분쟁·회계 감사도 마찬가지다.

따라서 **코드는 지우고, 표는 남기되 읽기 전용으로 동결한다.**

---

## 1. PayPal — ✅ 완료

| | |
|---|---|
| 삭제 | `backend/routers/paypal.py` · `backend/services/paypal_service.py` · `backend/models/paypal.py` · `src/lib/paypal-api.ts` · `src/lib/paypal-sdk.ts` · `src/components/memorial/payment-screen.tsx` · PayPal 번역 문구 |
| 남김 | `purchased_slots` 표 (동결·읽기 전용) · `supabase_assets.get_purchased_themes` (조회) |
| 동결 | `20261009000000_freeze_legacy_purchase_tables.sql` — 트리거로 INSERT/UPDATE/DELETE 거부 |

권한 REVOKE 가 아니라 트리거를 쓴 이유: 이 서비스는 service-role 키로 접속하므로
권한으로는 막히지 않는다. 트리거는 접속 주체와 무관하게 걸린다.

**이관하지 않는다.** PayPal 은 개발 중에만 쓰였고 라우터가 마운트된 적이 없어
실 고객 결제가 코드 배치상 불가능했다. 근거와 재검증 방법은
[PAYPAL_LEGACY.md](PAYPAL_LEGACY.md).

고정: `backend/tests/test_paypal_data_excluded.py`

## 2. 테마 KRW 직접 구매 — ✅ 완료 (드레인 창구만 남김)

| | |
|---|---|
| 삭제 | `POST /api/v1/themes/purchase` · `POST /api/v1/themes/checkout` · `theme_purchase.purchase()` · `start_checkout()` · `saved_payment_method()` · `_guard_purchasable()` · 프론트의 `startThemeCheckout` / `purchaseTheme` |
| 남김 | **`POST /api/v1/themes/confirm`** · `theme_purchase.confirm_checkout()` · `theme_purchase_orders` 표 |

### 왜 `/confirm` 만 남기는가

배포하는 순간 Toss 결제창을 띄워 둔 고객이 있을 수 있다. 그 사람이 [승인] 을
누르면 **돈은 나간다.** 받아 줄 곳이 없으면 결제만 되고 테마는 못 받는다.

새 주문을 만드는 경로가 사라졌으므로 미결 주문은 시간이 지나면 0 이 된다.

```sql
-- 드레인이 끝났는지 확인
select count(*) from public.theme_purchase_orders where status = 'pending';
```

0 이 되면 두 가지를 함께 한다:

* `POST /confirm` 과 `theme_purchase.confirm_checkout` 삭제
* `20261009000000` 마이그레이션 안의 `theme_purchase_orders_frozen` 트리거 주석 해제

> ⚠️ 순서를 뒤집지 말 것. 표를 먼저 동결하면 `/confirm` 이 `pending → paid` 를
> 쓰지 못해 **결제만 되고 테마는 못 받는** 상태가 된다.

`theme_order.create()` 는 프로덕션 호출부가 없지만 남아 있다 — 드레인 경로를
시험하려면 "배포 전에 만들어진 미결 주문"을 재현해야 하기 때문이다. 프로덕션이
다시 부르지 못하도록 테스트가 고정한다.

고정: `backend/tests/test_theme_legacy_retired.py` · `src/lib/theme-purchase-flow.test.ts`

## 3. 프론트 `$2.99` 메타데이터 — ✅ 완료

`themes.ts` 의 하드코딩된 `price` 필드가 사라졌다. 가격의 출처는
`digital_products` 카탈로그이고, 화면은 서버가 준 `credit_price` 를 그린다
(Phase 3). PayPal 결제 화면과 함께 그 화면의 번역 문구도 삭제됐다.

## 4. 카테고리 단위 생성 크레딧 가격 — ✅ 완료 (Phase 3에서)

`IDLE_BUNDLE_CREDITS` / `ACTION_EVENT_CREDITS` 환경변수가 사라졌다.
**카테고리가 가격을 정하지 않는다. 개별 상품이 정한다** — `digital_products` 의
`product_key` 마다 값이 있다. 자세한 것은 [PRICING.md](PRICING.md).

## 5. 4크레딧 기기 생성 경로 — 🚫 **보류 (조건 미충족)**

요구된 조건은 "**기기 호환성이 이전되면**" 이었다. 이전되지 않았다.

### 사실

* `GET /v1/device/sync` 는 `ACTION_ORDER` 4종(IDLE/TOUCH/VOICE/NFC)을 훑어
  **하나라도 없으면 `None` 을 돌려준다** → 404
  (`backend/services/generated_motions_service.py`)
* 그 4종 세트를 만드는 것은 `credit_generation_service.generate_with_credit`
  (4코인 팩) 하나뿐이다
* 그 엔드포인트를 호출하는 클라이언트가 **둘** 살아 있다:
  * `device-renderer/` (C++ `HttpDeviceSyncClient`, libcurl)
  * Unity 앱

### 지금 지우면

기기가 **영구히 프로비저닝되지 않는다.** 새 크레딧 경로는 아이들·액션을 낱개로
만들고, 그것으로는 4종 세트가 채워지지 않아 `/device/sync` 가 계속 404 다.

### 이 경로가 스키마에 남긴 흔적 — `legacy_charge`

이 경로는 Phase 7 의 예약 모델로 옮겨지지 못했다. 여전히 `deduct_credits` 로
차감-후-환불을 한다. 그래서 `credit_generation_sessions` 에 예외 플래그가 있다:

```sql
check (credits_charged = 0 or legacy_charge or reservation_ledger_id is not null)
```

두 가지를 동시에 해결한다:

* **기존 행.** `credits_charged` 는 `default 4` 인 기존 컬럼이라 예약 이전의 모든
  세션이 유료로 보인다. 예외 없이 제약을 걸면 마이그레이션이 실패한다
  (`check constraint ... is violated by some row`). 그 과금들은 잘못된 것이
  아니라 당시의 정상 방식이었다.
* **아직 살아 있는 쓰기.** `generate_with_credit` 은 차감을 **먼저** 하고 세션을
  만든다. 제약이 그 insert 를 막으면 예외가 환불 없이 올라가고, 고객은 4크레딧을
  잃고 아무것도 받지 못한다.

> `NOT VALID` 로 우회하지 않았다. `NOT VALID` 제약도 기존 행을 **UPDATE 할 때는**
> 검사하므로, 배포 시점에 `processing` 이던 레거시 세션의 웹훅이 나중에 도착하면
> 그 UPDATE 가 막힌다 — 결제창 문제와 같은 모양이다.

예외가 번지지 않도록 호출부는 하나로 고정돼 있다
(`backend/tests/test_legacy_charge_exemption.py`). 예약을 잊은 **새** 유료 세션은
여전히 거부된다. §5 가 끝나면 인자·컬럼·플래그가 함께 사라진다.

### 은퇴 전에 필요한 것

1. `/device/sync` 가 부분 세트를 다룰 수 있게 되거나(플레이스홀더/폴백),
   `owned_generated_assets` 를 읽도록 바뀌어야 한다
2. `device-renderer` 와 Unity 가 그 새 계약으로 이전돼야 한다
3. 그 다음에야 `generate_with_credit` 과 4코인 팩을 지울 수 있다

그때까지 이 경로는 **작동하는 채로 둔다.** 환불 정책(불완전 세트 = 전액 환불)도
그대로다 — 불완전 세트는 `/device/sync` 에서 404 라 고객에게 가치가 0 이기 때문이다.
