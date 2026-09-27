-- Beam Credit 팩 — **KRW 가 크레딧으로 들어오는 유일한 문** (Phase 5).
--
--     Toss → Beam Credits → Theme → 영구 소유
--
-- ── Toss 의 역할이 바뀌었다 ─────────────────────────────────────────────────
-- Phase 4 까지 Toss 는 테마를 직접 팔았다(₩4,900 → Aurora). 이제 Toss 는 **크레딧
-- 팩만** 판다. 테마는 크레딧으로 산다. 실제 돈이 오가는 지점이 하나로 줄어드는
-- 것이 요점이다 — 결제 검증·환불·세금·정산이 한 경로에만 있으면 된다.
--
-- ── 가격은 프론트에 없다 ────────────────────────────────────────────────────
-- 팩 구성과 가격은 이 표가 정한다. 화면은 GET /api/v1/credits/packs 로 받아
-- 그대로 그린다. digital_products 와 같은 원칙이고 같은 이유다: 가격이 브라우저
-- 번들에 있으면 바꾸는 데 배포가 필요하고, 서버와 어긋나면 눌러도 거절당하는
-- 버튼이 생긴다.

create table if not exists public.credit_packs (
  -- 'pack_5' 처럼 안정적인 키. 원장의 product_key 로도 쓰인다.
  pack_key      text primary key,
  -- 이 팩이 지급하는 크레딧. 0 은 팩이 아니다.
  credits       int not null check (credits > 0),
  -- 실제 청구 금액(KRW). **결제 확인의 기준값**이며 리다이렉트가 들고 온 값이 아니다.
  price_krw     int not null check (price_krw > 0),
  display_name  text,
  -- 판매 중단은 삭제가 아니라 표시다. 지우면 그 팩으로 결제한 주문이 가리킬 곳을 잃는다.
  active        boolean not null default true,
  -- 화면 정렬 순서. 가격순 정렬을 프론트가 추측하지 않게 서버가 정한다.
  sort_order    int not null default 0,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index if not exists credit_packs_active_idx
  on public.credit_packs (sort_order) where active;

comment on table public.credit_packs is
  'Beam Credit 팩 카탈로그. 가격의 유일한 권위 — 프론트는 하드코딩하지 않는다';

-- ── 주문 ────────────────────────────────────────────────────────────────────
--
-- theme_purchase_orders 와 **같은 이유로** 서버가 주문을 보관한다: 일회성 결제는
-- 사용자가 결제창을 마친 뒤 우리 successUrl 로 돌아오고, 그 파라미터는 **주소창에
-- 있다.** amount 를 그대로 믿고 confirm 하면 URL 을 고쳐 1원짜리 승인으로 30
-- 크레딧을 받을 수 있다. 그래서 체크아웃 시점에 (주문 → 사용자 → 팩 → 금액 →
-- 크레딧)을 적어 두고, 확인할 때는 **저장된 값**으로 Toss 에 묻는다.
create table if not exists public.credit_pack_orders (
  -- Toss orderId. 멱등성의 축.
  order_id      text primary key,
  user_id       text not null,
  pack_key      text not null,
  -- 체크아웃 시점에 서버가 확정한 값. 확인은 이 값들로 한다.
  amount        int not null,
  credits       int not null,
  currency      text not null default 'KRW',
  -- 'pending' | 'paid' | 'failed'
  status        text not null default 'pending',
  provider      text not null default 'toss',
  payment_key   text,
  failure_code  text,
  created_at    timestamptz not null default now(),
  confirmed_at  timestamptz
);

create index if not exists credit_pack_orders_user_idx
  on public.credit_pack_orders (user_id, status, created_at desc);

comment on table public.credit_pack_orders is
  '크레딧 팩 주문. 금액·크레딧을 서버가 보관해 리다이렉트 파라미터 위조를 막는다';
comment on column public.credit_pack_orders.credits is
  '지급할 크레딧. 체크아웃 시점에 고정된다 — 팩 가격이 나중에 바뀌어도 이 주문은 그대로다';

-- ── 확인: 주문 확정 + 지갑 충전 + 원장을 **한 트랜잭션으로** ────────────────
--
-- 나누면 두 가지 부분 실패가 생긴다:
--     주문만 paid   → 고객은 돈을 냈는데 크레딧이 없다. 주문은 성공이라고 말한다.
--     충전만 성공   → 같은 주문으로 다시 확인해 무한 충전이 가능하다.
--
-- ⚠️ Toss 승인(네트워크)은 이 함수 **바깥**에서 먼저 끝나 있어야 한다. 여기서는
--    이미 승인된 결제를 장부에 반영하는 일만 한다 — 트랜잭션 안에서 외부 호출을
--    기다리면 잠금을 붙든 채 네트워크를 기다리게 된다.
create or replace function public.confirm_credit_pack_order(
  p_order_id text,
  p_user_id text,
  p_payment_key text
)
returns jsonb
language plpgsql
as $$
declare
  o public.credit_pack_orders%rowtype;
  w jsonb;
  bal int;
begin
  if p_order_id is null or btrim(p_order_id) = '' then
    raise exception 'order_required';
  end if;

  -- 같은 주문의 동시 확인을 직렬화한다. 이것이 없으면 두 요청이 각각 pending 을
  -- 읽고 둘 다 충전한다.
  select * into o from public.credit_pack_orders
   where order_id = p_order_id for update;

  if not found then
    raise exception 'order_not_found';
  end if;

  -- 남의 주문을 확인할 수 없다. order_id 는 리다이렉트 URL 에 노출되므로,
  -- 이 검사가 없으면 남의 결제로 자기 지갑을 채울 수 있다.
  if o.user_id is distinct from p_user_id then
    raise exception 'order_not_found';
  end if;

  -- 재확인(새로고침·뒤로가기·네트워크 재시도)은 오류가 아니라 멱등 성공이다.
  if o.status = 'paid' then
    select current_credits into bal from public.user_wallets where user_id = p_user_id;
    return jsonb_build_object(
      'order_id', o.order_id, 'pack_key', o.pack_key,
      'credits_added', 0, 'credits_remaining', coalesce(bal, 0),
      'amount', o.amount, 'replayed', true
    );
  end if;

  if o.status <> 'pending' then
    raise exception 'order_not_pending';
  end if;

  update public.credit_pack_orders
     set status = 'paid', payment_key = p_payment_key, confirmed_at = now()
   where order_id = p_order_id;

  -- 멱등 키가 주문 id 다 — 원장 쪽에서도 같은 축으로 막힌다.
  -- unit_price 에 **KRW 금액**을 스냅샷한다: 이 크레딧이 얼마짜리였는지가
  -- 나중에 환불액을 계산하는 근거다.
  w := public.wallet_apply(
    p_user_id         => p_user_id,
    p_delta           => o.credits,
    p_reason          => 'credit_pack_topup',
    p_idempotency_key => 'pack:' || o.order_id,
    p_product_key     => o.pack_key,
    p_unit_price      => o.amount,
    p_ref_type        => 'credit_pack_orders',
    p_ref_id          => o.order_id
  );

  return jsonb_build_object(
    'order_id', o.order_id, 'pack_key', o.pack_key,
    'credits_added', o.credits,
    'credits_remaining', (w ->> 'balance_after')::int,
    'amount', o.amount, 'replayed', false
  );
end;
$$;

comment on function public.confirm_credit_pack_order is
  '크레딧 팩 확인: 주문 paid + 지갑 충전 + 원장을 한 트랜잭션으로. 재확인은 멱등';

-- ── 시드 ────────────────────────────────────────────────────────────────────
--
-- ⚠️ 지시받은 예시 가격이다. **상업적 가격은 나중에 바뀔 수 있고**, 바꾸는 것은
--    UPDATE 한 줄이다(docs/PRICING.md). 프론트 배포가 필요 없다는 것이 이 표의
--    존재 이유다.
insert into public.credit_packs (pack_key, credits, price_krw, display_name, sort_order)
values
  ('pack_5',   5,  4900,  '5 Beam Credits',  10),
  ('pack_12',  12, 9900,  '12 Beam Credits', 20),
  ('pack_30',  30, 19900, '30 Beam Credits', 30)
on conflict (pack_key) do update
   set credits = excluded.credits,
       price_krw = excluded.price_krw,
       display_name = excluded.display_name,
       sort_order = excluded.sort_order,
       updated_at = now();
