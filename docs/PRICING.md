# 가격표 — `digital_products`

**가격의 권위는 DB 한 곳이다.** 코드에도, 환경변수에도, 프론트 번들에도 없다.
가격을 바꾸는 것은 `UPDATE` 한 줄이며 배포·재시작·환경변수 변경이 필요 없다.

---

## 1. 원칙

> **가격은 카테고리가 아니라 상품이 정한다.**

```
theme:aurora    THEME   5
theme:sunset    THEME   4
theme:limited   THEME   8      ← 같은 카테고리, 다른 값. 정상이다.
```

`product_type` 은 분류(화면 묶기·필터링)일 뿐 값에 관여하지 않는다.

---

## 2. 무엇이 사라졌나

| 예전 | 문제 |
|---|---|
| `THEME_PRICE_<KEY>_KRW` (Render 환경변수) | 테마마다 env 를 하나씩 늘려야 했다 |
| `IDLE_BUNDLE_CREDITS` | **카테고리 전체**가 한 값 |
| `ACTION_EVENT_CREDITS` | **카테고리 전체**가 한 값 — 아이들 넷이 반드시 같은 가격 |
| `themes.ts` 의 `price: "$2.99"` | **브라우저 번들 안의 가격.** 바꾸려면 프론트 재배포, 서버와 어긋나면 "눌러도 거절당하는 버튼" |

전부 제거됐다. `backend/tests/test_product_catalog.py` 가 되살아나지 못하게 막는다.

---

## 3. 지금 시드된 값

마이그레이션은 **현재 유효 가격을 그대로 옮겼을 뿐** 값을 바꾸지 않았다.

| product_key | type | credit_price | 출처 |
|---|---|---|---|
| `theme:fresh_forest` 외 5개 | THEME | 0 | 무료 테마 (명시적 0) |
| `idle:BREATHING` | IDLE | 0 | **언제나 무료** — 저장소 전체의 계약 |
| `idle:BLINKING` / `EAR_TWITCHING` / `HEAD_TILTING` / `TAIL_WAGGING` | IDLE | 1 | 옛 `ACTION_EVENT_CREDITS` 기본값 |
| `idle:BUNDLE` | IDLE | 1 | 옛 `IDLE_BUNDLE_CREDITS` 기본값 |
| `action:COME_CLOSER` | ACTION | 1 | 옛 `ACTION_EVENT_CREDITS` 기본값 |

### 일부러 넣지 않은 것

`theme:aurora` · `theme:sunset` · `theme:ocean_deep` · `theme:custom_photo_bg`

이들은 지금 **KRW(Toss)** 로 팔리고 크레딧 가격이 존재한 적이 없다. 여기서 숫자를
만들어 넣으면 그 숫자가 곧 매출이 된다 — `theme_catalog.py` 가 적어 둔 그대로:
*"PM 이 정하지 않은 값을 코드가 정하면 그 숫자가 그대로 매출이 된다."*

행이 없으면 `product_credit_price()` 는 `null` 을 돌려주고 크레딧 결제는
**판매 불가**로 닫힌다(무료가 아니다). KRW 경로는 지금까지처럼 그대로 동작한다.

---

## 4. 가격 적용하기

배포 없이, SQL Editor 에서 실행한다.

### 값 바꾸기

```sql
update public.digital_products
   set credit_price = 3, updated_at = now()
 where product_key = 'idle:BLINKING';
```

### 상품 추가하기 (테마를 크레딧으로 전환할 때)

```sql
insert into public.digital_products (product_key, product_type, credit_price, display_name)
values
  ('theme:aurora',          'THEME', 5, 'Aurora'),
  ('theme:sunset',          'THEME', 4, 'Sunset'),
  ('theme:ocean_deep',      'THEME', 5, 'Ocean Deep'),
  ('theme:custom_photo_bg', 'AI_BG', 8, 'My Photo, Animated')
on conflict (product_key) do update
   set credit_price = excluded.credit_price,
       product_type = excluded.product_type,
       updated_at = now();
```

> ⚠️ `custom_photo_bg` 는 타입이 `AI_BG` 지만 키는 `theme:` 로 시작한다.
> 그 키가 `user_theme_entitlements.theme_key` 와 같아야 소유권이 조인되기 때문이다.
> 이름을 바꾸면 기존 소유권 행이 고아가 된다.

### 판매 중단

```sql
update public.digital_products set active = false where product_key = 'idle:BUNDLE';
```

**행을 지우지 않는다.** `credit_ledger.product_key` 가 과거 거래에서 이 값을
가리키고 있다.

### 확인

```sql
select product_key, product_type, credit_price, active
  from public.digital_products
 order by product_type, product_key;
```

---

## 5. 가격을 모를 때의 동작 (fail closed)

| 상황 | 결과 |
|---|---|
| 행이 없다 | `PRODUCT_NOT_SOLD` (409) — **무료가 아니다** |
| `active = false` | 같음 |
| `credit_price = 0` | 명시적 무료 — 과금 없이 통과 |
| 카탈로그 조회 실패 | `CATALOG_UNAVAILABLE` (503) — 가격을 추측하지 않는다 |

세 번째와 첫 번째를 구분하는 것이 핵심이다. 미설정을 0 으로 떨어뜨리면
**설정 누락이 곧 전량 무료 배포**가 된다.

발견 경로(`GET /assets`)는 예외다: 카탈로그가 죽어도 `prices` 만 비고 재생·발견은
계속된다. 재생은 가격과 무관하고, 실제 과금은 `POST /purchase` 가 자기 자리에서
다시 fail-closed 로 판정한다.

---

## 6. 관련 파일

| 파일 | 역할 |
|---|---|
| `supabase/migrations/20261002000000_digital_products.sql` | 표 · 제약 · 시드 |
| `backend/services/product_catalog.py` | 조회 · 키 규약 · fail-closed 규칙 |
| `backend/services/premium_purchase.py` `credits_for_kind()` | 구매 종류 → 가격 |
| `backend/tests/test_product_catalog.py` | 계약 고정 |
| `docs/PRICING.md` | 이 문서 |
