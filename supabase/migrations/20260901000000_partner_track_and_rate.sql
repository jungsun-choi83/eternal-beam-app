-- Phase 16 — 파트너 귀속에 코드·갈래·정산비율을 더한다.
--
--   대상 프로젝트: kdlukiujgclczwqmwvmk  (Eternal Beam)
--   ⚠️ partners / partner_codes 정본은 Soul Trace 쪽이다. 여기 있는 값은 전부
--      **그 시점의 스냅샷**이며 조인 대상이 아니다.
--
-- Phase 15 는 partner_id/type/name 을 옮겨 왔다. 정산을 계산하려면 세 가지가 더
-- 필요하다: 어느 코드로 들어왔는가(partner_code), 어느 갈래였는가(partner_track),
-- 그리고 **그때 약속된 비율은 얼마였는가**(partner_share_rate).
--
-- ── 왜 비율까지 주문에 복제하는가 ───────────────────────────────────────────
-- 비율은 계약이고, 계약은 바뀐다. 파트너 테이블의 현재 비율로 과거 주문을
-- 계산하면 3월에 10% 로 팔린 주문이 4월에 15% 로 재계산된다. 이미 정산이 끝난
-- 달의 숫자가 조용히 움직이는 것이고, 그건 장부가 아니다.
-- 주문 시점의 비율을 그 자리에 얼려 둔다.

-- ── 가져온 편지 ─────────────────────────────────────────────────────────────
alter table public.soul_trace_letters
  add column if not exists partner_code text,
  add column if not exists partner_track text,
  add column if not exists partner_share_rate numeric(6, 4);

alter table public.soul_trace_letters
  drop constraint if exists soul_trace_letters_partner_track_check;
alter table public.soul_trace_letters
  add constraint soul_trace_letters_partner_track_check
  check (partner_track is null or partner_track in ('living', 'memorial'));

-- ── 주문 스냅샷 ─────────────────────────────────────────────────────────────
alter table public.physical_orders
  add column if not exists partner_code text,
  add column if not exists partner_track text,
  add column if not exists partner_share_rate numeric(6, 4);

alter table public.physical_orders
  drop constraint if exists physical_orders_partner_track_check;
alter table public.physical_orders
  add constraint physical_orders_partner_track_check
  check (partner_track is null or partner_track in ('living', 'memorial'));

-- 정산은 "파트너 × 기간"으로 뽑는다. 코드별 성과는 그 다음 문제라 인덱스는
-- 코드에만 얇게 건다 — 이미 partner_id 인덱스가 Phase 15 에 있다.
create index if not exists physical_orders_partner_code_idx
  on public.physical_orders (partner_code)
  where partner_code is not null;

comment on column public.physical_orders.partner_share_rate is
  '주문 시점 정산 비율 스냅샷. 파트너의 현재 비율이 바뀌어도 과거 주문은 움직이지 않는다';
comment on column public.physical_orders.partner_track is
  '주문 시점 QR 갈래(living|memorial). Soul Trace LetterMode 와 같은 값';
comment on column public.physical_orders.partner_code is
  '주문 시점 유입 코드 스냅샷. 코드가 회수돼도 그때의 사실은 남는다';
