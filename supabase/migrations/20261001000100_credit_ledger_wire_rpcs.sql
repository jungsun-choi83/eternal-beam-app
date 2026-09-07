-- 기존 지갑 RPC 를 원장에 연결한다 (Phase 2).
--
-- ── 왜 별도 파일인가 ────────────────────────────────────────────────────────
-- 20261001000000 은 **새 구조**를 만들었다(표 + wallet_apply). 이 파일은 **기존
-- 경로**를 그리로 흘려보낸다. 나눠 두면 사고가 났을 때 "구조가 문제인가, 배선이
-- 문제인가"를 갈라낼 수 있고, 이 파일만 되돌릴 수도 있다.
--
-- ── 무엇이 바뀌는가 ────────────────────────────────────────────────────────
-- 지갑을 움직이는 SQL 경로는 넷뿐이고, 전부 여기서 원장을 남기게 된다:
--
--     add_wallet_credits            Python add_credits / refund_credits
--     deduct_wallet_credits         Python deduct_credits(strict=True)
--     process_iap_charge            IAP 크레딧 팩
--     process_subscription_renewal  멤버십 월 지급
--
-- 하나라도 빠지면 원장은 첫날부터 불완전해지고, 그러면 대조(credit_ledger_drift)가
-- 의미를 잃는다 — "어긋났다"가 버그인지 미기록인지 구분되지 않기 때문이다.
--
-- ── 호환성 ──────────────────────────────────────────────────────────────────
-- add/deduct 는 **인자를 추가**한다. 기존 2-인자 호출이 그대로 동작하도록 새 인자에
-- 전부 기본값을 준다. `create or replace` 로는 시그니처를 바꿀 수 없으므로 먼저
-- drop 한다 — plpgsql 본문의 함수 참조는 실행 시점에 해소되므로, 이 함수를 부르는
-- 다른 함수들은 drop 의 영향을 받지 않는다.
--
-- ⚠️ 사유(reason)를 넘기지 않는 호출은 'admin_adjustment' 로 기록된다. 그것은
--    "분류되지 않은 움직임"이라는 뜻이며, 원장에 그렇게 보이는 것이 **누락보다
--    낫다.** Python 계층은 실제 사유를 넘기므로 정상 경로에서는 나타나지 않는다.

-- ── 충전 ────────────────────────────────────────────────────────────────────
drop function if exists public.add_wallet_credits(text, int);

create function public.add_wallet_credits(
  p_user_id text,
  p_amount int,
  p_reason text default 'admin_adjustment',
  p_idempotency_key text default null,
  p_product_key text default null,
  p_unit_price int default null,
  p_ref_type text default null,
  p_ref_id text default null
)
returns int
language plpgsql
as $$
declare
  res jsonb;
begin
  -- 0·음수·null 은 호출부 버그다 (20260930000000 이 정한 계약 그대로).
  if p_amount is null or p_amount <= 0 then
    raise exception 'invalid_amount';
  end if;

  res := public.wallet_apply(
    p_user_id         => p_user_id,
    p_delta           => p_amount,
    p_reason          => p_reason,
    -- 키가 없으면 재플레이 방어가 없다 — 예전과 **같은** 수준이다(원래 이 함수에는
    -- 멱등성이 없었다). 다만 기록은 남으므로, 이중 충전이 일어나면 원장에서 두 줄로
    -- 보인다. 예전에는 잔액만 늘고 흔적이 없었다.
    p_idempotency_key => coalesce(p_idempotency_key, 'auto:' || gen_random_uuid()::text),
    p_product_key     => p_product_key,
    p_unit_price      => p_unit_price,
    p_ref_type        => p_ref_type,
    p_ref_id          => p_ref_id
  );

  return (res ->> 'balance_after')::int;
end;
$$;

comment on function public.add_wallet_credits is
  '지갑 증분 + 원장 기록. p_amount <= 0 은 invalid_amount. 사유 미지정은 admin_adjustment';

-- ── 차감 ────────────────────────────────────────────────────────────────────
drop function if exists public.deduct_wallet_credits(text, int);

create function public.deduct_wallet_credits(
  p_user_id text,
  p_amount int,
  p_reason text default 'admin_adjustment',
  p_idempotency_key text default null,
  p_product_key text default null,
  p_unit_price int default null,
  p_ref_type text default null,
  p_ref_id text default null
)
returns int
language plpgsql
as $$
declare
  res jsonb;
begin
  if p_amount is null or p_amount <= 0 then
    raise exception 'invalid_amount';
  end if;

  -- 부호를 여기서 뒤집는다. 호출부는 예전처럼 **양수**를 넘긴다 — 계약을 바꾸면
  -- 어딘가 한 곳이 안 바뀌고, 그 한 곳은 차감 대신 충전을 한다.
  res := public.wallet_apply(
    p_user_id         => p_user_id,
    p_delta           => -p_amount,
    p_reason          => p_reason,
    p_idempotency_key => coalesce(p_idempotency_key, 'auto:' || gen_random_uuid()::text),
    p_product_key     => p_product_key,
    p_unit_price      => p_unit_price,
    p_ref_type        => p_ref_type,
    p_ref_id          => p_ref_id
  );

  return (res ->> 'balance_after')::int;
end;
$$;

comment on function public.deduct_wallet_credits is
  '지갑 조건부 차감 + 원장 기록. 부족하면 insufficient_credits. 양수 금액을 받는다';

-- ── IAP 크레딧 팩 ───────────────────────────────────────────────────────────
--
-- 20260930000100 판과 동작이 같고, 충전이 원장을 남기는 것만 다르다.
-- 멱등 키는 영수증 지문이다 — payment_history 의 unique 인덱스와 **같은 축**이라
-- 두 방어가 어긋나지 않는다.
create or replace function public.process_iap_charge(
  p_user_id text,
  p_product_id text,
  p_store_type text,
  p_receipt_fingerprint text,
  p_transaction_id text,
  p_amount_krw int,
  p_credits_added int,
  p_raw_meta jsonb default null
)
returns jsonb
language plpgsql
as $$
declare
  pay_id bigint;
  new_bal int;
begin
  insert into public.payment_history (
    user_id, product_id, store_type, receipt_fingerprint, transaction_id,
    amount_krw, credits_added, status, raw_receipt_meta
  ) values (
    p_user_id, p_product_id, p_store_type, p_receipt_fingerprint, p_transaction_id,
    p_amount_krw, p_credits_added, 'success', p_raw_meta
  )
  returning id into pay_id;

  new_bal := public.add_wallet_credits(
    p_user_id         => p_user_id,
    p_amount          => p_credits_added,
    p_reason          => 'credit_pack_topup',
    p_idempotency_key => 'iap:' || p_receipt_fingerprint,
    p_product_key     => p_product_id,
    -- 이 크레딧에 실제로 지불된 KRW. 나중에 환불액을 계산할 때 필요하다.
    p_unit_price      => p_amount_krw,
    p_ref_type        => 'payment_history',
    p_ref_id          => pay_id::text
  );

  return jsonb_build_object(
    'payment_id', pay_id,
    'credits_remaining', new_bal,
    'status', 'success'
  );
exception
  when unique_violation then
    raise exception 'duplicate_receipt';
end;
$$;

-- ── 멤버십 갱신 ─────────────────────────────────────────────────────────────
--
-- 20260819000400 판의 계약을 **한 글자도 바꾸지 않는다**:
--   p_credits > 0   → 지갑 충전 (+ 이제 원장 기록)
--   p_credits = 0   → 지갑을 건드리지 않는다 (웹 멤버십). 행도 만들지 않는다.
--   p_credits < 0   → invalid_amount
--   중복 지문       → duplicate_subscription_event
--
-- 멱등 키는 이벤트 지문이다 — subscription_webhook_events 의 unique 인덱스와 같은
-- 축이라, 갱신이 두 번 배달돼도 크레딧은 한 번만 지급된다.
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
    new_bal := public.add_wallet_credits(
      p_user_id         => p_user_id,
      p_amount          => p_credits,
      p_reason          => 'membership_grant',
      p_idempotency_key => 'membership:' || p_event_fingerprint,
      p_product_key     => p_plan_id,
      p_unit_price      => p_amount_krw,
      p_ref_type        => 'subscription_webhook_events',
      p_ref_id          => event_id::text
    );
  else
    -- 0 크레딧 플랜(웹 멤버십) — 지갑을 **읽기만** 한다. 행을 만들지 않는 것이
    -- 중요하다: 쓸 곳 없는 잔액 0짜리 계정이 늘어나지 않게.
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
  '구독 갱신. p_credits=0 은 정상(웹 멤버십). 충전은 membership_grant 로 원장에 남는다';
