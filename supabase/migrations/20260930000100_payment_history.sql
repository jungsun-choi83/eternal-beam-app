-- IAP 결제 이력 — **docs/ 에서 마이그레이션으로 승격** (Phase 1 — 재무 안전).
--
-- ── 왜 이 파일이 필요한가 ───────────────────────────────────────────────────
-- `payment_history` 는 IAP 크레딧 충전의 **유일한 재플레이 방어**다:
--
--     iap_charge_service.verify_and_charge
--       → payment_history_service.find_success_by_fingerprint(fp)
--       → 이미 성공한 영수증이면 credits_added=0 으로 반환 (재충전 없음)
--
-- 그런데 이 표의 정의는 `docs/supabase_payment_iap.sql` 에만 있었고
-- `supabase/migrations/` 에는 없었다. 즉 **사람이 SQL Editor 에 붙여넣었는지에
-- 따라** 재플레이 방어가 있기도 하고 없기도 했다. 표가 없으면:
--
--   * find_success_by_fingerprint 가 조회 실패 → 재플레이 감지 불가
--   * 같은 영수증을 다시 보내면 크레딧이 **다시** 충전된다
--   * 신규 환경(스테이징·재구축)은 이 사실을 조용히 물려받는다
--
-- 20260721000200 이 user_wallets 를 같은 이유로 docs 에서 끌어올렸다. 그때
-- payment_history 만 남겨진 것이 이번에 발견된 구멍이다.
--
-- ⚠️ **동작을 바꾸지 않는다.** 스키마·인덱스·RPC 모두 docs 판과 동일하다.
--    이미 수동으로 적용한 DB 에서는 전부 no-op 이다(create if not exists /
--    create or replace). 적용하지 않았던 DB 에서는 없던 방어가 생긴다.

-- ── 결제 이력 ───────────────────────────────────────────────────────────────
create table if not exists public.payment_history (
  id bigserial primary key,
  user_id text not null,
  product_id text not null,
  -- 'mock' 이 허용되는 것은 의도적이다. PAYMENT_MOCK=1 로 만들어진 행을
  -- 실 매출과 구분할 수 있어야 하고, 그 구분이 곧 마이그레이션 전 검증의 근거다
  -- (scripts/audit_financial_records.py 참고).
  --
  -- ⚠️ 'paypal' 은 **의도적으로 없다.** PayPal 은 legacy/dev-only 로 분류됐고
  --    (docs/PAYPAL_LEGACY.md) 그 데이터는 크레딧 시스템으로 들어오지 않는다.
  --    레거시 기록은 public.purchased_slots 에 그대로 남는다.
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

-- 같은 영수증은 한 번만 충전된다. **애플리케이션 락이 아니라 DB 가 막는다** —
-- iap_charge_service 의 asyncio.Lock 은 한 프로세스 안에서만 유효하고,
-- Render 가 인스턴스를 늘리는 순간 그 방어는 사라진다.
create unique index if not exists uq_payment_receipt_fingerprint
  on public.payment_history (receipt_fingerprint);

-- 스토어 트랜잭션 ID 중복 방지 (검증 성공 건만).
-- 부분 인덱스인 이유: 실패 건은 같은 tx 로 여러 번 남을 수 있어야 한다
-- (재시도 이력 자체가 진단 정보다).
create unique index if not exists uq_payment_store_transaction
  on public.payment_history (store_type, transaction_id)
  where transaction_id is not null and status = 'success';

create index if not exists idx_payment_history_user
  on public.payment_history (user_id, created_at desc);

comment on table public.payment_history is
  'IAP 결제 이력. receipt_fingerprint unique 가 재충전 방어의 전부 — 표가 없으면 방어도 없다';
comment on column public.payment_history.store_type is
  'apple | google | mock. mock 은 PAYMENT_MOCK=1 로 만들어진 비실매출 행';

-- ── IAP 성공 처리: 이력 insert + 지갑 충전을 **한 트랜잭션으로** ────────────
--
-- 나눠서 하면 두 가지 부분 실패가 생긴다:
--   이력만 성공 → 고객은 돈을 냈는데 크레딧이 없다
--   충전만 성공 → 같은 영수증으로 무한 충전이 가능하다
-- 함수 본문 하나가 곧 하나의 트랜잭션이라 둘 다 불가능하다.
--
-- add_wallet_credits 의 권위 정의는 20260930000000 에 있다.
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
  -- 재플레이. 이력도 충전도 롤백되므로 잔액은 그대로다.
  when unique_violation then
    raise exception 'duplicate_receipt';
end;
$$;

comment on function public.process_iap_charge is
  'IAP 충전: payment_history insert + 지갑 증분을 한 트랜잭션으로. 재플레이는 duplicate_receipt';
