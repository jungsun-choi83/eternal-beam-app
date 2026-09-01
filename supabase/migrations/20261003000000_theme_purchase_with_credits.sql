-- 테마를 Beam Credit 으로 산다 (Phase 4) — **첫 실제 크레딧 커머스**.
--
--     예전:  Aurora → ₩4,900 → Toss → entitlement
--     지금:  Aurora → 5 Beam Credits → entitlement → 영구 소유
--
-- ── 원자성이 이 파일의 전부다 ───────────────────────────────────────────────
-- 한 번의 구매는 **세 가지**를 바꾼다:
--
--     1. 지갑 잔액        -5
--     2. 원장 한 줄        theme_purchase, delta -5, product_key 'theme:aurora'
--     3. 소유권 한 줄      user_theme_entitlements (영구)
--
-- 셋이 **전부 일어나거나 하나도 일어나지 않아야** 한다. 부분 성공은 둘 다 나쁘다:
--
--     차감만 성공 → 고객은 크레딧을 잃고 테마는 못 쓴다. 원장에는 샀다고 적혀 있다.
--     소유권만 성공 → 공짜로 테마를 준 것이고, 원장은 그 사실을 설명하지 못한다.
--
-- Python 계층은 PostgREST 라 다중 문장 트랜잭션이 없다. 그래서 셋을 **하나의
-- 함수 본문** 안에서 한다 — process_subscription_renewal / wallet_apply 가 이미
-- 쓰고 있는 방식이며, 이 저장소에서 원자성을 얻는 유일한 방법이다.
--
-- ── 새 소유권 테이블을 만들지 않는다 ────────────────────────────────────────
-- user_theme_entitlements 를 그대로 쓴다. 이미 (user_id, theme_key) PK 에
-- order_id 부분 unique, expires_at(null=영구)까지 갖춘 표이고, 카탈로그가 읽는
-- **유일한 소유권 권위**다. 결제 수단이 KRW 에서 크레딧으로 바뀌는 것은
-- provider/amount/currency 값이 바뀌는 일이지 새 표가 필요한 일이 아니다.
--
-- 표를 하나 더 만들면 "어느 쪽이 진짜 소유권인가"가 생기고, 그 질문은 PayPal 의
-- purchased_slots 가 이미 한 번 만들어 낸 문제다(docs/PAYPAL_LEGACY.md).

create or replace function public.purchase_theme_with_credits(
  p_user_id text,
  p_theme_key text,
  p_idempotency_key text
)
returns jsonb
language plpgsql
as $$
declare
  v_product_key text;
  v_price int;
  v_active boolean;
  v_existing public.user_theme_entitlements%rowtype;
  v_wallet jsonb;
  v_balance int;
begin
  if p_user_id is null or btrim(p_user_id) = '' then
    raise exception 'invalid_user';
  end if;
  if p_theme_key is null or btrim(p_theme_key) = '' then
    raise exception 'invalid_theme';
  end if;
  if p_idempotency_key is null or btrim(p_idempotency_key) = '' then
    raise exception 'idempotency_key_required';
  end if;

  v_product_key := 'theme:' || lower(btrim(p_theme_key));

  -- ① 가격. **카탈로그가 유일한 권위다** (Phase 3).
  select credit_price, active into v_price, v_active
    from public.digital_products
   where product_key = v_product_key;

  if not found or not v_active then
    -- 행이 없다 = 판매 불가. **무료가 아니다.**
    raise exception 'product_not_sold';
  end if;

  if v_price <= 0 then
    -- 무료 테마를 "구매"할 이유가 없다. 0 크레딧 차감은 wallet_apply 가
    -- invalid_amount 로 거절하므로, 여기서 더 정확한 이름으로 끊는다.
    raise exception 'theme_is_free';
  end if;

  -- ② 이미 갖고 있는가. **있으면 과금하지 않는다.**
  --
  -- 여기서 일찍 반환하는 것이 중요하다: 아래 upsert 는 기존 행을 덮어쓰므로,
  -- 이 검사가 없으면 Toss 로 산 소유권(provider='toss', amount=4900, currency='KRW')이
  -- 크레딧 기록으로 조용히 바뀐다. 결제 이력이 사라지는 것과 같다.
  select * into v_existing
    from public.user_theme_entitlements
   where user_id = p_user_id and theme_key = lower(btrim(p_theme_key));

  if found
     and v_existing.status = 'owned'
     and (v_existing.expires_at is null or v_existing.expires_at > now()) then
    select current_credits into v_balance
      from public.user_wallets where user_id = p_user_id;
    return jsonb_build_object(
      'theme_key', lower(btrim(p_theme_key)),
      'charged', 0,
      'already_owned', true,
      'credits_remaining', coalesce(v_balance, 0),
      'order_id', v_existing.order_id
    );
  end if;

  -- ③ 차감 + 원장. wallet_apply 가 둘을 함께 하고, 재플레이와 잔액 부족을 판정한다.
  --
  --   재플레이(같은 키) → 차감하지 않고 기존 행을 돌려준다
  --   잔액 부족         → insufficient_credits 로 여기서 전체가 롤백된다
  --
  -- unit_price 에 v_price 를 **스냅샷**한다. 나중에 카탈로그 가격이 바뀌어도
  -- 이 거래가 얼마였는지는 고정된다 — 환불액 계산의 근거다.
  v_wallet := public.wallet_apply(
    p_user_id         => p_user_id,
    p_delta           => -v_price,
    p_reason          => 'theme_purchase',
    p_idempotency_key => p_idempotency_key,
    p_product_key     => v_product_key,
    p_unit_price      => v_price,
    p_ref_type        => 'user_theme_entitlements',
    p_ref_id          => lower(btrim(p_theme_key))
  );

  -- ④ 소유권. **expires_at = null = 영구.**
  --
  -- on conflict 로 덮어쓰는 경우는 만료·환불·폐기된 행을 다시 사는 것뿐이다
  -- (살아 있는 소유권은 ② 에서 이미 반환했다).
  insert into public.user_theme_entitlements (
    user_id, theme_key, status, provider, order_id,
    payment_key, amount, currency, purchased_at, expires_at
  ) values (
    p_user_id, lower(btrim(p_theme_key)), 'owned', 'credits', p_idempotency_key,
    null, v_price, 'CREDIT', now(), null
  )
  on conflict (user_id, theme_key) do update set
    status = 'owned',
    provider = 'credits',
    order_id = excluded.order_id,
    payment_key = null,
    amount = excluded.amount,
    currency = 'CREDIT',
    purchased_at = now(),
    expires_at = null;

  return jsonb_build_object(
    'theme_key', lower(btrim(p_theme_key)),
    'charged', v_price,
    'already_owned', false,
    'credits_remaining', (v_wallet ->> 'balance_after')::int,
    'order_id', p_idempotency_key,
    'ledger_id', v_wallet ->> 'ledger_id'
  );
end;
$$;

comment on function public.purchase_theme_with_credits is
  '테마 크레딧 구매: 차감 + 원장 + 소유권을 한 트랜잭션으로. 부분 성공이 불가능하다';

-- ── 테마 크레딧 가격 ────────────────────────────────────────────────────────
--
-- Phase 3 은 테마의 크레딧 가격을 **일부러 비워 뒀다** — 그때는 테마가 KRW 로만
-- 팔렸고, 존재하지 않는 가격을 코드가 발명하면 그 숫자가 곧 매출이 되기 때문이다.
--
-- 이제 값이 정해졌다:
--     Aurora  5     (지시받은 값)
--     Sunset  4     (지시받은 값)
--
-- ⚠️ ocean_deep 과 custom_photo_bg 는 지시에 없었다. Aurora 와 같은 티어(5)로
--    두되, 이것은 **추정치**다. docs/PRICING.md 의 UPDATE 한 줄로 언제든 바꿀 수
--    있고 배포가 필요 없다. custom_photo_bg 는 AI 배경이라 타입이 AI_BG 다.
insert into public.digital_products (product_key, product_type, credit_price, display_name)
values
  ('theme:aurora',          'THEME', 5, 'Aurora'),
  ('theme:sunset',          'THEME', 4, 'Sunset'),
  ('theme:ocean_deep',      'THEME', 5, 'Ocean Deep'),
  ('theme:custom_photo_bg', 'AI_BG', 8, 'My Photo, Animated')
on conflict (product_key) do update
   set product_type = excluded.product_type,
       credit_price = excluded.credit_price,
       display_name = excluded.display_name,
       updated_at = now();
