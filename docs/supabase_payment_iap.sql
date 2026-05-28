-- Eternal Beam: 인앱 결제(IAP) 영수증 · 결제 이력
-- supabase_hybrid_business.sql 실행 후 이 파일을 Supabase SQL Editor에서 실행

-- 결제 이력 (중복 영수증 방지)
create table if not exists public.payment_history (
  id bigserial primary key,
  user_id text not null,
  product_id text not null,
  store_type text not null check (store_type in ('apple', 'google', 'mock')),
  receipt_fingerprint text not null,
  transaction_id text,
  amount_krw int not null check (amount_krw >= 0),
  credits_added int not null check (credits_added > 0),
  status text not null check (status in ('pending', 'success', 'failed')),
  error_message text,
  raw_receipt_meta jsonb,
  created_at timestamptz not null default now()
);

-- 동일 영수증 재전송 → 1회만 충전
create unique index if not exists uq_payment_receipt_fingerprint
  on public.payment_history (receipt_fingerprint);

-- 스토어 트랜잭션 ID 중복 방지 (검증 성공 건만)
create unique index if not exists uq_payment_store_transaction
  on public.payment_history (store_type, transaction_id)
  where transaction_id is not null and status = 'success';

create index if not exists idx_payment_history_user
  on public.payment_history (user_id, created_at desc);

-- 지갑 크레딧 충전 (원자적)
create or replace function public.add_wallet_credits(p_user_id text, p_amount int)
returns int
language plpgsql
as $$
declare
  new_bal int;
begin
  if p_amount is null or p_amount <= 0 then
    raise exception 'invalid_amount';
  end if;

  insert into public.user_wallets (user_id, current_credits, updated_at)
  values (p_user_id, 0, now())
  on conflict (user_id) do nothing;

  update public.user_wallets
  set current_credits = current_credits + p_amount,
      updated_at = now()
  where user_id = p_user_id
  returning current_credits into new_bal;

  return new_bal;
end;
$$;

-- IAP 성공 처리: 결제 이력 insert + 지갑 충전 (단일 트랜잭션)
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

  new_bal := public.add_wallet_credits(p_user_id, p_credits_added);

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
