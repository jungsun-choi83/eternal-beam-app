-- 크레딧 원장 (Phase 2 — 회계 기반).
--
--     user_wallets   지금 잔액이 **얼마인가**
--     credit_ledger  그 잔액이 **왜 그런가**
--
-- ── 왜 필요한가 ─────────────────────────────────────────────────────────────
-- 지금까지 잔액은 정수 하나였다. "내 크레딧이 왜 7 이죠?" 라는 질문에 답할 방법이
-- 저장소 어디에도 없었다. 충전·차감·환불이 각각 다른 표(payment_history,
-- premium_purchases, subscription_webhook_events, credit_generation_sessions)에
-- 흩어져 있고, 그중 어느 것도 지갑 잔액과 대조되지 않는다.
--
-- 그 결과 Phase 1 이 고친 종류의 결함 — 원장에는 '환불됨' 도장이 있는데 지갑은
-- 늘지 않은 상태 — 이 **탐지되지 않았다.** 대조할 것이 없었기 때문이다.
--
-- 이 표가 생기면 불변식이 하나 생긴다:
--
--     sum(credit_ledger.delta) == user_wallets.current_credits
--
-- 이 등식이 깨지면 무언가 잘못된 것이고, credit_ledger_drift() 가 그것을 찾는다.
--
-- ── 원자성 ──────────────────────────────────────────────────────────────────
-- 지갑 변경과 원장 기록이 **따로 일어나면 안 된다.** 따로 하면 정확히 이 표가
-- 잡으려던 불일치를 이 표가 만들어 낸다. Python 계층은 PostgREST 를 쓰므로 다중
-- 문장 트랜잭션이 없다 — 그래서 wallet_apply() 안에서 둘을 함께 한다
-- (process_subscription_renewal 이 이미 쓰고 있는 방식과 같다).

-- ── 표 ──────────────────────────────────────────────────────────────────────
create table if not exists public.credit_ledger (
  -- gen_random_uuid() 는 PG13+ 내장이며 Supabase 에서 바로 쓸 수 있다.
  ledger_id       uuid primary key default gen_random_uuid(),
  user_id         text not null,

  -- **부호가 있는 값.** +충전 / -차감. 합이 곧 잔액이라는 성질이 여기서 나온다.
  delta           int not null,

  -- 이 움직임 **직후**의 잔액. 없어도 sum() 으로 계산할 수 있지만, 있으면
  -- 어느 시점부터 어긋났는지 이진 탐색으로 찾을 수 있다 — 사고 조사에서
  -- "언제부터" 는 "얼마나" 만큼 중요하다.
  balance_after   int not null check (balance_after >= 0),

  reason          text not null,

  -- 무엇에 썼는가 (digital_products.product_key 와 같은 문자열 규약).
  -- 'theme:aurora' | 'idle:SLEEPING' | 'action:PAW_WAVE' | 'credit_pack_4' ...
  product_key     text,

  -- **지불 시점의 가격 스냅샷.** 카탈로그 가격이 바뀌어도 과거 거래의 의미는
  -- 바뀌지 않아야 한다. 이 값이 없으면 나중 가격으로 환불액을 계산하게 된다.
  unit_price      int,

  -- RESERVED  예약됨 (차감은 일어났고 결과는 아직 없다)
  -- COMMITTED 확정
  -- RELEASED  예약 취소 — 되돌린 금액은 reservation_release 행으로 따로 기록된다
  state           text not null default 'COMMITTED',

  -- **재플레이 방어의 축.** 이 표의 unique 인덱스 하나가 이중 충전·이중 차감을
  -- 막는다. 애플리케이션 락은 프로세스 안에서만 유효하고, Render 가 인스턴스를
  -- 늘리는 순간 사라진다.
  idempotency_key text not null,

  -- 이 움직임을 설명하는 바깥 세계의 레코드.
  -- 'payment_history' | 'premium_purchases' | 'physical_orders' | 'scene_generation_jobs' ...
  ref_type        text,
  ref_id          text,

  created_at      timestamptz not null default now(),
  -- RESERVED 인 동안 null. 확정/해제되는 순간 채워진다.
  settled_at      timestamptz
);

-- ── 사유 ────────────────────────────────────────────────────────────────────
-- 제약으로 두는 이유: 오타 난 사유("idle_generatoin")는 조용히 저장된 뒤 집계에서
-- 사라진다. 그러면 원장이 있는데도 설명하지 못하는 금액이 생긴다.
alter table public.credit_ledger drop constraint if exists credit_ledger_reason_check;
alter table public.credit_ledger add constraint credit_ledger_reason_check check (
  reason in (
    -- 들어오는 것
    'credit_pack_topup',        -- Toss/IAP 로 크레딧 팩 구매
    'starter_bonus',            -- 가입 시 기본 지급 (STARTER_CREDITS)
    'soultrace_bonus',          -- Soul Trace 핸드오프 프로모션
    'membership_grant',         -- 정기결제 성공 → 월 지급
    'physical_product_bonus',   -- LETTER / MEMORY BOX 구매 보너스
    'refund',                   -- 제공하지 못한 것에 대한 반환
    'reservation_release',      -- 예약 해제 — 애초에 제공된 적이 없다
    'legacy_migration',         -- 원장 도입 시점의 개시 잔액

    -- 나가는 것
    'theme_purchase',
    'idle_generation',
    'action_generation',
    'ai_background_generation',

    -- 양방향
    'admin_adjustment'
  )
);

alter table public.credit_ledger drop constraint if exists credit_ledger_state_check;
alter table public.credit_ledger add constraint credit_ledger_state_check
  check (state in ('RESERVED', 'COMMITTED', 'RELEASED'));

-- ── 사유와 부호는 함께 움직인다 ─────────────────────────────────────────────
-- 차감을 충전으로 기록하는 버그는 원장을 **틀린 채로 그럴듯하게** 만든다.
-- 합계는 맞는데 설명이 거꾸로인 원장은 없는 것보다 나쁘다.
alter table public.credit_ledger drop constraint if exists credit_ledger_direction_check;
alter table public.credit_ledger add constraint credit_ledger_direction_check check (
  (reason in (
    'credit_pack_topup', 'starter_bonus', 'soultrace_bonus', 'membership_grant',
    'physical_product_bonus', 'refund', 'reservation_release'
  ) and delta > 0)
  -- 개시 잔액은 0 일 수 있다 (잔액 0 인 기존 지갑도 원장에 자리를 갖는다).
  or (reason = 'legacy_migration' and delta >= 0)
  or (reason in (
    'theme_purchase', 'idle_generation', 'action_generation', 'ai_background_generation'
  ) and delta < 0)
  -- 운영 조정만 양방향. 0 은 어느 쪽이든 기록할 이유가 없다.
  or (reason = 'admin_adjustment' and delta <> 0)
);

-- settled_at 은 RESERVED 인 동안에만 비어 있다. 둘이 어긋나면 "확정됐는데 시각이
-- 없는" 행이 생기고, 그런 행은 정산 집계에서 조용히 빠진다.
alter table public.credit_ledger drop constraint if exists credit_ledger_settled_check;
alter table public.credit_ledger add constraint credit_ledger_settled_check
  check ((state = 'RESERVED') = (settled_at is null));

-- ── 인덱스 ──────────────────────────────────────────────────────────────────
-- 이 하나가 이중 기록 방어의 전부다.
create unique index if not exists credit_ledger_idem_uidx
  on public.credit_ledger (idempotency_key);

-- 사용자 내역 조회 (고객 문의·화면).
create index if not exists credit_ledger_user_idx
  on public.credit_ledger (user_id, created_at desc);

-- 미결 예약 회수. 부분 인덱스라 확정된 행 수백만 개를 훑지 않는다.
create index if not exists credit_ledger_open_idx
  on public.credit_ledger (state, created_at)
  where state = 'RESERVED';

-- 바깥 레코드로부터의 역추적 (주문 → 크레딧 움직임).
create index if not exists credit_ledger_ref_idx
  on public.credit_ledger (ref_type, ref_id)
  where ref_type is not null;

comment on table public.credit_ledger is
  '크레딧 움직임 원장. sum(delta) = user_wallets.current_credits 가 불변식';
comment on column public.credit_ledger.idempotency_key is
  '재플레이 방어의 축. unique 인덱스가 이중 충전·이중 차감을 DB 수준에서 막는다';
comment on column public.credit_ledger.unit_price is
  '지불 시점의 가격 스냅샷. 카탈로그 가격이 바뀌어도 과거 거래의 의미는 고정된다';
comment on column public.credit_ledger.balance_after is
  '이 움직임 직후의 잔액. 언제부터 어긋났는지 찾기 위한 값';

-- ── 핵심 연산: 지갑 변경 + 원장 기록을 **한 번에** ──────────────────────────
--
-- 반환: { ledger_id, balance_after, replayed }
--
-- 오류:
--   invalid_user / invalid_amount / idempotency_key_required   호출부 버그
--   insufficient_credits                                       잔액 부족
--
-- ⚠️ 두 쓰기를 나누지 말 것. 나누는 순간 이 표가 잡으려던 불일치를 이 표가 만든다.
create or replace function public.wallet_apply(
  p_user_id text,
  p_delta int,
  p_reason text,
  p_idempotency_key text,
  p_product_key text default null,
  p_unit_price int default null,
  p_state text default 'COMMITTED',
  p_ref_type text default null,
  p_ref_id text default null
)
returns jsonb
language plpgsql
as $$
declare
  new_bal int;
  lid uuid;
  prior public.credit_ledger%rowtype;
  st text := coalesce(nullif(btrim(p_state), ''), 'COMMITTED');
begin
  if p_user_id is null or btrim(p_user_id) = '' then
    raise exception 'invalid_user';
  end if;
  -- 0 은 "움직임 없음"이다. 기록할 이유도, 지갑을 건드릴 이유도 없다.
  if p_delta is null or p_delta = 0 then
    raise exception 'invalid_amount';
  end if;
  if p_idempotency_key is null or btrim(p_idempotency_key) = '' then
    raise exception 'idempotency_key_required';
  end if;

  -- ① 재플레이 — 이미 기록된 키면 **아무것도 적용하지 않는다.**
  select * into prior
    from public.credit_ledger
   where idempotency_key = p_idempotency_key;
  if found then
    return jsonb_build_object(
      'ledger_id', prior.ledger_id,
      'balance_after', prior.balance_after,
      'replayed', true
    );
  end if;

  -- ② 지갑 행 보장. 잔액은 건드리지 않는다.
  insert into public.user_wallets (user_id, current_credits, updated_at)
  values (p_user_id, 0, now())
  on conflict (user_id) do nothing;

  -- ③ 잔액 적용. 차감은 조건부 UPDATE 한 문장이라 초과 인출이 불가능하다.
  if p_delta > 0 then
    update public.user_wallets
       set current_credits = current_credits + p_delta,
           updated_at = now()
     where user_id = p_user_id
    returning current_credits into new_bal;
  else
    update public.user_wallets
       set current_credits = current_credits + p_delta,
           updated_at = now()
     where user_id = p_user_id
       and current_credits >= (-p_delta)
    returning current_credits into new_bal;
    if not found then
      raise exception 'insufficient_credits';
    end if;
  end if;

  -- ④ 원장 기록. ③ 과 같은 트랜잭션이므로 둘 중 하나만 남을 수 없다.
  insert into public.credit_ledger (
    user_id, delta, balance_after, reason, product_key, unit_price,
    state, idempotency_key, ref_type, ref_id, settled_at
  ) values (
    p_user_id, p_delta, new_bal, p_reason, p_product_key, p_unit_price,
    st, p_idempotency_key, p_ref_type, p_ref_id,
    case when st = 'RESERVED' then null else now() end
  )
  returning ledger_id into lid;

  return jsonb_build_object('ledger_id', lid, 'balance_after', new_bal, 'replayed', false);

exception
  when unique_violation then
    -- 동시 요청이 같은 키로 먼저 커밋했다. **지갑 변경도 함께 롤백된다**
    -- (plpgsql 의 예외 블록은 서브트랜잭션이다) — 이중 적용이 불가능한 이유다.
    select * into prior
      from public.credit_ledger
     where idempotency_key = p_idempotency_key;
    if not found then
      -- 우리가 낸 unique 위반이 idempotency_key 때문이 아니었다.
      -- 조용히 성공으로 처리하지 않는다.
      raise;
    end if;
    return jsonb_build_object(
      'ledger_id', prior.ledger_id,
      'balance_after', prior.balance_after,
      'replayed', true
    );
end;
$$;

comment on function public.wallet_apply is
  '지갑 변경 + 원장 기록을 한 트랜잭션으로. idempotency_key 로 재플레이를 흡수한다';

-- ── 지갑 보장 + 가입 보너스 ────────────────────────────────────────────────
--
-- 예전에는 Python 이 user_wallets 에 STARTER_CREDITS 로 직접 insert 했다. 그러면
-- 잔액 4 짜리 지갑의 원장 합계가 0 이 되어, 첫 사용자부터 불변식이 깨진다.
--
-- 보너스는 사용자당 **한 번뿐**이다 (키가 'starter:<uid>'). 지갑을 지웠다 만들어도
-- 다시 지급되지 않는다 — 예전에 localStorage 를 지우면 STARTER_CREDITS 를 무한히
-- 받을 수 있었던 문제와 같은 부류를 DB 수준에서 막는다.
create or replace function public.wallet_ensure(p_user_id text, p_starter int default 0)
returns int
language plpgsql
as $$
declare
  bal int;
begin
  if p_user_id is null or btrim(p_user_id) = '' then
    raise exception 'invalid_user';
  end if;

  insert into public.user_wallets (user_id, current_credits, updated_at)
  values (p_user_id, 0, now())
  on conflict (user_id) do nothing;

  if coalesce(p_starter, 0) > 0 then
    perform public.wallet_apply(
      p_user_id          => p_user_id,
      p_delta            => p_starter,
      p_reason           => 'starter_bonus',
      p_idempotency_key  => 'starter:' || p_user_id,
      p_ref_type         => 'user_wallets',
      p_ref_id           => p_user_id
    );
  end if;

  select current_credits into bal from public.user_wallets where user_id = p_user_id;
  return coalesce(bal, 0);
end;
$$;

comment on function public.wallet_ensure is
  '지갑 행 보장 + 가입 보너스 1회 지급(원장 기록 포함). 보너스는 사용자당 한 번뿐';

-- ── 대조 ────────────────────────────────────────────────────────────────────
--
-- 불변식이 깨진 지갑을 찾는다. 비어 있어야 정상이다.
--
-- 운영에서 이걸 주기적으로 돌리는 것이 원장을 갖는 실질적 이유다 — 표만 만들고
-- 아무도 대조하지 않으면 그냥 로그일 뿐이다.
create or replace function public.credit_ledger_drift()
returns table (user_id text, wallet_balance int, ledger_sum bigint, difference bigint)
language sql
stable
as $$
  select
    w.user_id,
    w.current_credits as wallet_balance,
    coalesce(sum(l.delta), 0) as ledger_sum,
    w.current_credits - coalesce(sum(l.delta), 0) as difference
  from public.user_wallets w
  left join public.credit_ledger l on l.user_id = w.user_id
  group by w.user_id, w.current_credits
  having w.current_credits <> coalesce(sum(l.delta), 0);
$$;

comment on function public.credit_ledger_drift is
  '지갑 잔액과 원장 합계가 어긋난 사용자. 비어 있어야 정상이다';
