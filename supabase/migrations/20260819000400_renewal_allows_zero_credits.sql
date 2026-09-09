-- 갱신 RPC: **0 크레딧 플랜을 허용한다** (웹 멤버십).
--
-- 증상: Toss 웹 멤버십(web_membership, credits_per_month=0)이 갱신될 때
--       Supabase 가 P0001 invalid_amount 로 실패한다.
--
-- 원인: add_wallet_credits 의 정의가 저장소에 **두 벌** 있고, 배포된 것은 엄격한
--       쪽이다.
--         supabase/migrations/20260721000200_hybrid_business_wallet.sql
--             → greatest(p_amount, 0)  (0 을 조용히 통과시킴)
--         docs/supabase_payment_iap.sql
--             → p_amount <= 0 이면 raise 'invalid_amount'   ← 나중에 적용되어 이김
--       process_subscription_renewal 은 크레딧 수와 무관하게 이 함수를 **무조건**
--       호출하므로, 0 크레딧 플랜이 들어오는 순간 갱신 전체가 실패한다.
--
-- 고치는 위치: add_wallet_credits 가 아니라 **호출부**다.
--   add_wallet_credits 의 0 거부는 IAP 충전 경로(process_iap_charge)에서는 여전히
--   올바른 가드다 — 0원짜리 크레딧 충전은 언제나 버그다. 그 가드를 풀면 이 버그를
--   잡아 주던 그물이 사라진다. 그래서 "0 크레딧이 정상인" 갱신 경로에서만
--   호출을 건너뛴다. Python 쪽 목업 경로(process_renewal_mock)가 이미 같은 규칙을
--   쓰고 있어, 두 구현이 이제 일치한다.
--
-- 계약:
--   p_credits > 0   → 예전 그대로 지갑에 충전한다 (레거시 12크레딧 플랜 불변)
--   p_credits = 0   → **지갑을 건드리지 않는다.** 행을 만들지도 않는다.
--                     credits_remaining 은 기존 잔액(없으면 0)을 그대로 보고한다.
--   p_credits < 0   → 여전히 invalid_amount 로 실패한다 (음수는 언제나 버그다)
--   p_credits null  → 여전히 invalid_amount
--
-- 그 외 동작(이벤트 기록, 구독 upsert, 중복 지문 처리)은 한 글자도 바뀌지 않는다.

create or replace function public.process_subscription_renewal(
  p_user_id text,
  p_plan_id text,
  p_store_type text,
  p_event_type text,
  p_event_fingerprint text,
  p_transaction_id text,
  p_original_transaction_id text,
  p_credits int,
  p_amount_krw int,
  p_next_billing timestamptz,
  p_raw_payload jsonb default null
)
returns jsonb
language plpgsql
as $$
declare
  event_id bigint;
  new_bal int;
begin
  -- 음수·null 은 호출부 버그다. 지갑에 닿기 전에 끊는다.
  if p_credits is null or p_credits < 0 then
    raise exception 'invalid_amount';
  end if;

  insert into public.subscription_webhook_events (
    user_id, plan_id, store_type, event_type, event_fingerprint,
    transaction_id, credits_granted, amount_krw, raw_payload
  ) values (
    p_user_id, p_plan_id, p_store_type, p_event_type, p_event_fingerprint,
    p_transaction_id, p_credits, p_amount_krw, p_raw_payload
  )
  returning id into event_id;

  insert into public.user_subscriptions (
    user_id, plan_id, status, next_billing_date, store_type,
    original_transaction_id, latest_transaction_id, updated_at
  ) values (
    p_user_id, p_plan_id, 'active', p_next_billing, p_store_type,
    p_original_transaction_id, p_transaction_id, now()
  )
  on conflict (user_id) do update set
    plan_id = excluded.plan_id,
    status = 'active',
    next_billing_date = excluded.next_billing_date,
    store_type = excluded.store_type,
    original_transaction_id = coalesce(excluded.original_transaction_id, user_subscriptions.original_transaction_id),
    latest_transaction_id = excluded.latest_transaction_id,
    updated_at = now();

  if p_credits > 0 then
    -- 레거시 유료 크레딧 플랜(standard_subscription 등) — 예전 경로 그대로.
    new_bal := public.add_wallet_credits(p_user_id, p_credits);
  else
    -- 0 크레딧 플랜(웹 멤버십) — 지갑을 **읽기만** 한다. 없으면 0.
    -- 여기서 행을 만들지 않는 것이 중요하다: 웹 멤버십 가입자에게 존재하지도 않던
    -- 지갑이 생기면, 쓸 곳 없는 잔액 0짜리 계정이 늘어난다.
    select current_credits into new_bal
      from public.user_wallets
     where user_id = p_user_id;
    new_bal := coalesce(new_bal, 0);
  end if;

  return jsonb_build_object(
    'event_id', event_id,
    'credits_remaining', new_bal,
    'status', 'active',
    'next_billing_date', p_next_billing
  );
exception
  when unique_violation then
    raise exception 'duplicate_subscription_event';
end;
$$;

comment on function public.process_subscription_renewal is
  '구독 갱신. p_credits=0 은 정상(웹 멤버십)이며 지갑을 건드리지 않는다. 음수는 invalid_amount';
