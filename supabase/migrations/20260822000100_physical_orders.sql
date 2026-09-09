-- 물리 제품 주문 (Phase 12) — LETTER / MEMORY BOX.
--
-- ── 네 번째 축이다 ──────────────────────────────────────────────────────────
--   user_subscriptions       "이번 달 회원인가"      → 만료된다
--   user_theme_entitlements  "이 테마를 샀는가"      → 남는다
--   wallets / credits        레거시 4코인            → 별개
--   physical_orders          "실물을 주문했는가"     → 여기
--
-- 넷 다 독립이다. 실물을 사도 회원이 되지 않고, 크레딧도 테마도 생기지 않는다.
-- **BREATHING 은 어느 쪽과도 무관하게 언제나 무료다** — ₩14,900 은 종이와 배송에
-- 대한 값이지 디지털 경험에 대한 값이 아니다.
--
-- ── 하나의 canonical petId ──────────────────────────────────────────────────
-- 주문은 이미 존재하는 pet_id 를 **가리킨다**. 주문용 펫을 새로 만들지 않는다.
-- QR 도 마찬가지다: shaker_share_id 로 **기존 공유를 재사용**하고, 없으면 null 로
-- 두어 Phase 13 에서 운영이 붙인다 — 주문마다 새 펫 경험을 찍어 내지 않는다.
--
-- ── 상태가 셋인 이유 ────────────────────────────────────────────────────────
-- 결제·생산·배송은 서로 다른 속도로 움직이고 담당도 다르다. 한 컬럼에 섞으면
-- "결제됐지만 아직 인쇄 전"과 "인쇄됐지만 미발송"을 구분할 수 없다.
--   payment_status     pending | paid | failed        (Toss)
--   production_status  pending | ready | printed      (Phase 13, 운영)
--   shipping_status    pending | shipped | delivered  (운영)

create table if not exists public.physical_orders (
  -- Toss orderId 를 그대로 쓴다. 멱등성의 축.
  order_id text primary key,
  user_id text not null,
  -- canonical petId. 새로 만들지 않는다.
  pet_id text not null,
  -- Soul Trace 편지 참조. LETTER/MEMORY_BOX 는 이 편지를 인쇄한다.
  soul_trace_letter_id text,
  -- 'LETTER' | 'MEMORY_BOX'  (NFC 는 나중이다)
  product_type text not null,
  amount integer not null,
  currency text not null default 'KRW',

  -- 결제
  payment_status text not null default 'pending',
  provider text not null default 'toss',
  payment_key text,
  failure_code text,

  -- 수령인 · 배송지
  recipient_name text,
  recipient_phone text,
  postal_code text,
  address_line1 text,
  address_line2 text,

  -- 이행 (Phase 13 이 채운다)
  production_status text not null default 'pending',
  shipping_status text not null default 'pending',
  tracking_number text,
  -- 기존 Shaker 공유 재사용. null 이면 아직 붙이지 않은 것이다.
  shaker_share_id text,

  created_at timestamptz not null default now(),
  paid_at timestamptz
);

-- 운영이 고객/펫/주문번호로 찾는다 (Phase 12 요구사항).
create index if not exists physical_orders_user_idx on public.physical_orders (user_id);
create index if not exists physical_orders_pet_idx on public.physical_orders (pet_id);
create index if not exists physical_orders_paid_idx
  on public.physical_orders (payment_status, created_at desc);

comment on table public.physical_orders is
  '물리 제품 주문. 구독·테마·크레딧과 완전히 별개이며 BREATHING 무료와도 무관하다';
comment on column public.physical_orders.pet_id is
  'canonical petId 를 가리킨다. 주문용 펫을 새로 만들지 않는다';
comment on column public.physical_orders.shaker_share_id is
  '기존 Shaker 공유 재사용. 주문마다 새 펫 경험을 만들지 않는다 (Phase 13 이 붙인다)';
comment on column public.physical_orders.production_status is
  'Phase 13 이행 파이프라인이 채운다. 결제/배송과 다른 속도로 움직여 컬럼을 나눴다';
