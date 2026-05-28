-- Eternal Beam: 정기 구독 (Standard Subscription)
-- supabase_hybrid_business.sql + supabase_payment_iap.sql 실행 후 적용

-- 구독 상태 (유저당 1건 — 스탠다드 플랜)
create table if not exists public.user_subscriptions (
  user_id text primary key,
  plan_id text not null,
  status text not null check (status in ('active', 'canceled', 'expired')),
  next_billing_date timestamptz,
  store_type text check (store_type in ('apple', 'google', 'mock')),
  original_transaction_id text,
  latest_transaction_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_user_subscriptions_status
  on public.user_subscriptions (status, next_billing_date);

-- 웹훅 이벤트 멱등 (동일 갱신 2회 → 크레딧 1회만)
create table if not exists public.subscription_webhook_events (
  id bigserial primary key,
  user_id text not null,
  plan_id text not null,
  store_type text not null,
  event_type text not null,
  event_fingerprint text not null,
  transaction_id text,
  credits_granted int not null default 0,
  amount_krw int not null default 0,
  raw_payload jsonb,
  created_at timestamptz not null default now()
);

create unique index if not exists uq_subscription_event_fingerprint
  on public.subscription_webhook_events (event_fingerprint);

create index if not exists idx_subscription_events_user
  on public.subscription_webhook_events (user_id, created_at desc);

-- 구독 갱신: 이벤트 로그 + 구독 active + 지갑 +12 (단일 트랜잭션)
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

  new_bal := public.add_wallet_credits(p_user_id, p_credits);

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

-- 구독 만료/해지 (크레딧 지급 없음)
create or replace function public.process_subscription_status_change(
  p_user_id text,
  p_plan_id text,
  p_status text,
  p_event_type text,
  p_event_fingerprint text,
  p_store_type text,
  p_transaction_id text default null,
  p_raw_payload jsonb default null
)
returns jsonb
language plpgsql
as $$
declare
  event_id bigint;
begin
  if p_status not in ('canceled', 'expired') then
    raise exception 'invalid_status';
  end if;

  insert into public.subscription_webhook_events (
    user_id, plan_id, store_type, event_type, event_fingerprint,
    transaction_id, credits_granted, amount_krw, raw_payload
  ) values (
    p_user_id, p_plan_id, p_store_type, p_event_type, p_event_fingerprint,
    p_transaction_id, 0, 0, p_raw_payload
  )
  returning id into event_id;

  insert into public.user_subscriptions (
    user_id, plan_id, status, updated_at
  ) values (
    p_user_id, p_plan_id, p_status, now()
  )
  on conflict (user_id) do update set
    status = excluded.status,
    updated_at = now();

  return jsonb_build_object('event_id', event_id, 'status', p_status);
exception
  when unique_violation then
    raise exception 'duplicate_subscription_event';
end;
$$;
