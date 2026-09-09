-- Phase 15 — 파트너 귀속 (동물병원 / 장례식장).
--
--   대상 프로젝트: kdlukiujgclczwqmwvmk  (Eternal Beam)
--   ⚠️ partners / partner_codes 정본은 **Soul Trace 쪽**에 있다
--      (pjoyuvqykggcuvbsnxio). 두 프로젝트는 DB 를 공유하지 않으므로 여기서는
--      조인할 수 없다 — 그래서 유형·이름을 **스냅샷**으로 함께 들고 온다.
--
-- ── 왜 이름까지 복제하는가 ──────────────────────────────────────────────────
-- 정규화만 보면 partner_id 하나면 충분하다. 그런데 운영 화면이 파트너 이름을
-- 보여 주려면 Soul Trace 에 매번 물어야 하고, 그 호출이 실패하면 운영 콘솔이
-- 멈춘다. 게다가 인쇄·정산은 **주문 시점의 사실**을 남겨야 한다 — 병원이
-- 이름을 바꾸거나 계약이 끝나도 그때의 귀속은 그대로여야 한다.
--
-- ── 전부 nullable 이다 ──────────────────────────────────────────────────────
-- 직접 유입 고객이 다수이고 그들의 흐름은 조금도 달라지지 않아야 한다.
-- NULL = 직접 유입. 기존 편지·주문은 손대지 않으며 그대로 유효하다.

-- ── 가져온 편지에 귀속 ──────────────────────────────────────────────────────
alter table public.soul_trace_letters
  add column if not exists partner_id text,
  add column if not exists partner_type text,
  add column if not exists partner_name text;

create index if not exists soul_trace_letters_partner_idx
  on public.soul_trace_letters (partner_id)
  where partner_id is not null;

-- ── 주문에 스냅샷 ───────────────────────────────────────────────────────────
-- 편지에서 복사한다. 주문은 정산 단위이므로 **주문 시점의 값**이 남아야 한다 —
-- 나중에 편지 쪽 귀속이 바뀌어도 이미 결제된 주문은 흔들리지 않는다.
alter table public.physical_orders
  add column if not exists partner_id text,
  add column if not exists partner_type text,
  add column if not exists partner_name text;

-- 운영이 파트너별·유형별로 주문을 찾는 경로.
create index if not exists physical_orders_partner_idx
  on public.physical_orders (partner_id)
  where partner_id is not null;

create index if not exists physical_orders_partner_type_idx
  on public.physical_orders (partner_type)
  where partner_type is not null;

comment on column public.soul_trace_letters.partner_id is
  'Soul Trace 가 코드로 확정한 귀속. NULL = 직접 유입. 브라우저 값이 아니다';
comment on column public.physical_orders.partner_id is
  '주문 시점 귀속 스냅샷. 편지에서 복사되며 이후 변경에 흔들리지 않는다';
comment on column public.physical_orders.partner_name is
  '주문 시점 파트너 이름. 두 프로젝트가 DB 를 공유하지 않아 조인 대신 복제한다';
