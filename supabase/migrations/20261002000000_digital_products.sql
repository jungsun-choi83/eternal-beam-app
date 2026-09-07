-- 디지털 상품 카탈로그 (Phase 3) — **가격의 유일한 권위.**
--
-- ── 원칙: 가격은 카테고리가 아니라 상품이 정한다 ────────────────────────────
--
--     theme:aurora          THEME   5
--     theme:sunset          THEME   4
--     idle:BLINKING         IDLE    3
--     action:COME_CLOSER    ACTION  2
--     theme:custom_photo_bg AI_BG   8
--
-- Aurora 5 · Sunset 4 · Limited 8 이 동시에 성립해야 한다. 그래서 가격은 **행**에
-- 있고, product_type 은 분류일 뿐 값에 관여하지 않는다.
--
-- ── 무엇을 대체하는가 ───────────────────────────────────────────────────────
-- 지금까지 가격은 세 곳에 흩어져 있었고, 어느 것도 상품 단위가 아니었다:
--
--   THEME_PRICE_<KEY>_KRW        Render 환경변수. 테마마다 하나씩 env 를 늘려야 했다
--   IDLE_BUNDLE_CREDITS          **카테고리 전체**가 한 값 (아이들 전부 1크레딧)
--   ACTION_EVENT_CREDITS         **카테고리 전체**가 한 값 (액션 전부 1크레딧)
--   src/components/memorial/themes.ts  프론트에 박힌 "$2.99"
--
-- 마지막 것이 특히 나빴다: 가격이 **브라우저 번들** 안에 있어서, 가격을 바꾸려면
-- 프론트를 다시 배포해야 했고 서버 값과 어긋나면 "눌러도 거절당하는 버튼"이 생겼다.
--
-- 이제 가격을 바꾸는 것은 UPDATE 한 줄이다. 배포도, 재시작도, 환경변수도 없다.
--
-- ── 없는 상품은 **무료가 아니라 판매 불가**다 ───────────────────────────────
-- theme_catalog.price_krw() 가 None 과 0 을 구분했던 이유를 그대로 가져온다:
-- 가격 미설정을 0 으로 떨어뜨리면 **설정 누락이 곧 전량 무료 배포**가 된다.
-- 무료 상품은 credit_price = 0 인 행을 **명시적으로** 갖는다. 행이 없으면 팔 수 없다.

create table if not exists public.digital_products (
  -- '<타입 접두사>:<도메인 식별자>'
  --
  -- 도메인 식별자는 **이미 존재하는 키를 그대로 쓴다**:
  --   theme:<themeKey>     user_theme_entitlements.theme_key 와 같은 값
  --   idle:<IDLE_EVENT>    scenarios.pet_scenarios.IDLE_EVENTS
  --   action:<ACTION_ID>   scenarios.pet_scenarios.PET_ACTIONS
  --
  -- 새 식별자를 발명하지 않는 이유: 소유권 조회가 전부 기존 키로 되어 있고,
  -- 여기서 이름을 바꾸면 카탈로그와 소유권이 조인되지 않는다.
  product_key   text primary key,

  -- 분류일 뿐이다. **가격에 관여하지 않는다.** 화면 묶기·필터링에 쓴다.
  product_type  text not null,

  -- 0 = 명시적으로 무료. 음수는 없다.
  credit_price  int not null check (credit_price >= 0),

  display_name  text,

  -- 판매 중단은 **삭제가 아니라 표시**다. 행을 지우면 그것을 산 사람의 원장
  -- (credit_ledger.product_key)이 가리킬 곳을 잃는다.
  active        boolean not null default true,

  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

alter table public.digital_products drop constraint if exists digital_products_type_check;
alter table public.digital_products add constraint digital_products_type_check
  check (product_type in ('THEME', 'IDLE', 'ACTION', 'AI_BG'));

-- 접두사와 타입이 어긋나면 조회가 조용히 빗나간다 (idle: 로 시작하는데 타입이
-- THEME 이면 아이들 목록에서 사라진다). 규약을 제약으로 고정한다.
--
-- AI_BG 는 예외다: custom_photo_bg 는 themes.ts 의 테마 키이자(그래서 소유권이
-- user_theme_entitlements 에 있다) 실제로는 생성형 배경이다. 키는 소유권을 따라
-- 'theme:' 를 쓰고, 타입은 성격을 따라 AI_BG 를 쓴다.
alter table public.digital_products drop constraint if exists digital_products_key_prefix_check;
alter table public.digital_products add constraint digital_products_key_prefix_check check (
  (product_type = 'THEME'  and product_key like 'theme:%')
  or (product_type = 'IDLE'   and product_key like 'idle:%')
  or (product_type = 'ACTION' and product_key like 'action:%')
  or (product_type = 'AI_BG'  and product_key like 'theme:%')
);

create index if not exists digital_products_type_idx
  on public.digital_products (product_type)
  where active;

comment on table public.digital_products is
  '디지털 상품 카탈로그. 크레딧 가격의 유일한 권위 — 가격은 카테고리가 아니라 상품이 정한다';
comment on column public.digital_products.credit_price is
  '0 = 명시적 무료. 행이 없으면 무료가 아니라 **판매 불가**다';
comment on column public.digital_products.active is
  '판매 중단 표시. 행을 지우지 않는 이유는 credit_ledger.product_key 가 가리키기 때문';

-- ── 조회 ────────────────────────────────────────────────────────────────────
-- 가격을 못 찾으면 null 이다. 호출부는 그것을 "0" 이 아니라 "팔 수 없음"으로 읽는다.
create or replace function public.product_credit_price(p_product_key text)
returns int
language sql
stable
as $$
  select credit_price
    from public.digital_products
   where product_key = p_product_key
     and active;
$$;

comment on function public.product_credit_price is
  '상품의 크레딧 가격. 없거나 비활성이면 null = 판매 불가(무료 아님)';

-- ── 시드 ────────────────────────────────────────────────────────────────────
--
-- ⚠️ **여기 있는 숫자는 현재 유효 가격을 그대로 옮긴 것이다.** 이 마이그레이션은
--    구조를 바꾸지 지금 팔리는 값을 바꾸지 않는다. 실제 가격표는 PM 이 정하고,
--    supabase/migrations/… 가 아니라 UPDATE 로 적용한다
--    (docs/PRICING.md 에 적용용 SQL 이 있다).
--
--    현재 유효 가격의 출처:
--      IDLE_BUNDLE_CREDITS  기본 1  → idle:BUNDLE
--      ACTION_EVENT_CREDITS 기본 1  → 개별 아이들·액션 각 1
--      테마                        → 지금은 KRW(Toss) 전용이라 **크레딧 가격이 없었다.**
--                                    크레딧 전환 전까지 판매 불가(행 없음)로 둔다.
--
--    가격을 발명하지 않는 이유는 theme_catalog.py 가 이미 적어 둔 그대로다:
--    "PM 이 정하지 않은 값을 코드가 정하면 그 숫자가 그대로 매출이 된다."

insert into public.digital_products (product_key, product_type, credit_price, display_name)
values
  -- ── 무료 테마 (명시적으로 0) ──────────────────────────────────────────────
  -- 행을 두는 이유: "무료"와 "가격 미설정"을 구분하기 위해서다. 행이 없으면
  -- 판매 불가이므로, 무료 테마가 카탈로그에서 빠지면 쓸 수 없게 된다.
  ('theme:fresh_forest',   'THEME',  0, 'Fresh Forest'),
  ('theme:beach',          'THEME',  0, 'Beach'),
  ('theme:snow_forest',    'THEME',  0, 'Snow Forest'),
  ('theme:celestial',      'THEME',  0, 'Celestial'),
  ('theme:golden_meadow',  'THEME',  0, 'Golden Meadow'),
  ('theme:starlight',      'THEME',  0, 'Starlight'),

  -- ── 아이들 ────────────────────────────────────────────────────────────────
  -- BREATHING 은 **언제나 무료**다. 이 저장소 전체가 그 계약 위에 서 있다
  -- (routers/generate.py, shaker_v1.py, physical_product.py 주석 참고).
  -- 유료 목록에 실수로 들어가지 않도록 0 으로 **명시**한다.
  ('idle:BREATHING',       'IDLE',   0, 'Breathing'),

  ('idle:BLINKING',        'IDLE',   1, 'Blinking'),
  ('idle:EAR_TWITCHING',   'IDLE',   1, 'Ear Twitching'),
  ('idle:HEAD_TILTING',    'IDLE',   1, 'Head Tilting'),
  ('idle:TAIL_WAGGING',    'IDLE',   1, 'Tail Wagging'),

  -- 번들도 **하나의 상품**이다. 카테고리가 아니라 상품이 가격을 갖는다는 원칙이
  -- 여기서도 그대로다 — 묶음 할인은 정상적인 상업 개념이고, 구성원 가격의 합일
  -- 필요가 없다. 개별 판매가 도입되면 active=false 로 내리면 된다(코드 변경 없음).
  ('idle:BUNDLE',          'IDLE',   1, 'Idle Motion Bundle'),

  -- ── 액션 ──────────────────────────────────────────────────────────────────
  ('action:COME_CLOSER',   'ACTION', 1, 'Come Closer')
on conflict (product_key) do nothing;

-- ⚠️ 유료 테마(aurora / sunset / ocean_deep)와 AI 배경(custom_photo_bg)은
--    **일부러 넣지 않았다.** 지금 그것들은 KRW(Toss) 로 팔리고 크레딧 가격이
--    존재한 적이 없다. 여기서 숫자를 만들어 넣으면 그 숫자가 곧 매출이 된다.
--
--    크레딧으로 전환할 때 docs/PRICING.md 의 SQL 로 추가한다. 그전까지
--    product_credit_price() 는 null 을 돌려주고, 크레딧 결제는 "판매 불가"로
--    닫힌다 — KRW 경로는 지금까지처럼 그대로 동작한다.
