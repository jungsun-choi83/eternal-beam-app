-- 결제 제공자(billing provider) 계층 — **자격(entitlement)과 분리한다**.
--
-- 왜 별도 테이블인가:
--   user_subscriptions      = "이 사용자가 프리미엄을 쓸 수 있는가"  (자격)
--   billing_subscriptions   = "누가 어떻게 돈을 받고 있는가"          (여기)
--   billing_payments        = "어떤 결제가 실제로 일어났는가"        (여기)
--
-- 이 분리가 요구사항의 핵심이다: Toss 가 1번 제공자일 뿐이고, 나중에 Apple/Google 이
-- 붙어도 **자격 코어는 한 줄도 바뀌지 않는다**. 제공자별 필드(billingKey, customerKey,
-- paymentKey)가 user_subscriptions 에 섞이면 제공자를 늘릴 때마다 자격 스키마가
-- 오염되고, 자격 판정(premium_entitlement)이 제공자를 알아야 하게 된다.
--
-- 자격은 오직 **정규화된 이벤트**를 통해서만 바뀐다:
--     Toss/Apple/Google → normalized event → handle_subscription_webhook → user_subscriptions

-- ── 제공자별 구독(청구) 상태 ────────────────────────────────────────────────
create table if not exists public.billing_subscriptions (
  -- 정규 Eternal Beam 신원 (user_subscriptions.user_id 와 같은 값)
  user_id text not null,
  -- 'toss' | 'apple' | 'google'
  provider text not null,
  plan_id text not null default 'standard_subscription',

  -- 제공자 고유 식별자. Toss: customerKey(우리가 생성) / billingKey(Toss 발급).
  -- ⚠️ billing_key 는 **결제 수단 그 자체**다. 절대 프론트로 내보내지 않는다.
  customer_key text,
  billing_key text,

  -- 'active' | 'canceled' | 'expired'  (청구 관점 — 자격과 별개로 관리된다)
  status text not null default 'active',
  -- 해지 예약: 남은 기간까지는 자격 유지, 기간 끝나면 갱신하지 않는다.
  cancel_at_period_end boolean not null default false,
  -- 이번 결제로 확보된 이용 종료 시각. 갱신 판단의 유일한 기준.
  current_period_end timestamptz,

  -- 연속 실패 횟수 — 재시도 정책·강제 만료 판단용.
  failure_count int not null default 0,
  last_error text,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  primary key (user_id, provider)
);

-- 갱신 배치가 "지금 청구해야 할 구독"을 찾는 유일한 조회 패턴.
create index if not exists billing_subscriptions_due_idx
  on public.billing_subscriptions (status, current_period_end);

-- ── 결제 원장 ───────────────────────────────────────────────────────────────
create table if not exists public.billing_payments (
  -- 우리가 생성하는 주문 번호. **멱등성의 축**이다.
  order_id text primary key,
  user_id text not null,
  provider text not null,
  -- 제공자가 돌려준 결제 식별자 (Toss: paymentKey)
  provider_payment_id text,
  -- 'INITIAL' | 'RENEWAL'
  kind text not null,
  amount int not null,
  currency text not null default 'KRW',
  -- 'paid' | 'failed'
  status text not null,
  failure_code text,
  failure_message text,
  -- 이 결제가 확보한 이용 종료 시각 (성공 시에만)
  period_end timestamptz,
  raw jsonb,
  created_at timestamptz not null default now()
);

-- 같은 결제가 두 번 기록되지 않는다 — 제공자 재전송·중복 확인 요청 방어.
create unique index if not exists billing_payments_provider_payment_uniq
  on public.billing_payments (provider, provider_payment_id)
  where provider_payment_id is not null;

create index if not exists billing_payments_user_idx
  on public.billing_payments (user_id, created_at desc);

comment on table public.billing_subscriptions is
  '제공자별 청구 상태. 자격(user_subscriptions)과 분리 — 제공자를 늘려도 자격 스키마는 그대로';
comment on column public.billing_subscriptions.billing_key is
  'Toss 자동결제 키 = 결제 수단. 백엔드 전용, 절대 응답에 싣지 않는다';
comment on column public.billing_subscriptions.current_period_end is
  '결제로 확보된 이용 종료 시각. 갱신은 이 시각이 지난 구독만 대상으로 한다';
comment on table public.billing_payments is
  '결제 원장. order_id 가 멱등성의 축 — 같은 주문은 두 번 청구되지 않는다';
