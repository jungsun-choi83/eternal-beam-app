-- 테마 일회성 결제 주문 (Phase 11 마무리).
--
-- ── 왜 주문을 **서버가** 보관하는가 ─────────────────────────────────────────
-- 일회성 결제는 사용자가 결제창에서 승인한 뒤 우리 successUrl 로 돌아온다:
--
--     /themes/success?paymentKey=…&orderId=…&amount=…
--
-- 이 값들은 **브라우저 주소창에 있다.** amount 를 그대로 믿고 confirm 하면
-- 사용자가 URL 을 고쳐 1원짜리 승인으로 유료 테마를 살 수 있다. 그래서 체크아웃
-- 시점에 (주문 → 사용자 → 테마 → 금액)을 서버가 적어 두고, 확인할 때는 **저장된
-- 금액**으로 Toss 에 묻는다. 리다이렉트가 들고 온 금액은 대조에만 쓴다.
--
-- ── 멱등성 ──────────────────────────────────────────────────────────────────
-- order_id 가 PK 다. 같은 주문이 두 번 확인돼도(새로고침, 뒤로가기, 네트워크
-- 재시도) 두 번 승인되지 않는다 — status 전이가 pending → paid 한 번뿐이다.
-- 소유권 쪽 멱등성은 user_theme_entitlements.order_id 의 unique 인덱스가 따로 잡는다.
--
-- ── 이 테이블이 담지 않는 것 ────────────────────────────────────────────────
-- 구독도 크레딧도 없다. 테마 주문은 정기결제와 완전히 다른 축이고, 성공하면
-- user_theme_entitlements 한 줄만 만든다.

create table if not exists public.theme_purchase_orders (
  -- Toss orderId. 멱등성의 축.
  order_id text primary key,
  user_id text not null,
  theme_key text not null,
  -- 체크아웃 시점에 서버가 확정한 금액. **결제 확인의 기준값이다.**
  amount integer not null,
  currency text not null default 'KRW',
  -- 'pending' | 'paid' | 'failed'
  status text not null default 'pending',
  provider text not null default 'toss',
  payment_key text,
  failure_code text,
  created_at timestamptz not null default now(),
  confirmed_at timestamptz
);

-- "이 사용자의 진행 중 주문" 조회 — 체크아웃을 다시 눌렀을 때 재사용한다.
create index if not exists theme_purchase_orders_user_idx
  on public.theme_purchase_orders (user_id, theme_key, status);

comment on table public.theme_purchase_orders is
  '테마 일회성 결제 주문. 금액을 서버가 보관해 리다이렉트 파라미터 위조를 막는다';
comment on column public.theme_purchase_orders.amount is
  '체크아웃 시점의 확정 금액. confirm 은 이 값으로 Toss 에 묻는다 (URL 값이 아니라)';
comment on column public.theme_purchase_orders.status is
  'pending → paid | failed. 전이가 한 번뿐이라 이중 승인이 생기지 않는다';
