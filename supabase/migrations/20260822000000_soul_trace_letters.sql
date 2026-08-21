-- Soul Trace 편지 **참조**(link/import) — Eternal Beam 은 편지를 만들지 않는다.
--
-- ── 이 테이블의 성격 ────────────────────────────────────────────────────────
-- Soul Trace 가 편지를 **생성**하고, Eternal Beam 은 그것을 **가리킨다.**
-- 물리 제품(편지/메모리 박스)에 인쇄될 본문이 필요하므로 스냅샷을 함께 보관하지만,
-- 그 본문을 여기서 만들어 내는 경로는 **없다** — 항상 밖에서 들어온다.
--
-- 왜 스냅샷을 두는가: 인쇄는 되돌릴 수 없다. 주문 시점의 본문이 남아 있어야
-- "고객이 받은 종이"와 "지금 화면의 편지"가 달라졌을 때 무엇이 인쇄됐는지 알 수
-- 있다. Soul Trace 가 나중에 편지를 수정해도 이미 인쇄된 주문은 흔들리지 않는다.
--
-- source_letter_id 가 Soul Trace 쪽 식별자다. 같은 편지를 두 번 import 해도
-- 같은 행으로 수렴한다(unique) — **중복 편지를 만들지 않는다**는 요구사항이
-- 스키마 수준에서 성립한다.
--
-- ⚠️ 현재 Soul Trace 는 백엔드가 없다(프론트 화면 하나뿐이며 하드코딩 데모다).
--    그래서 이 테이블은 지금 **통합 지점**으로서 존재한다: Soul Trace 가 실제
--    편지 API 를 갖게 되면 import 경로만 그쪽을 바라보면 되고, 주문 스키마는
--    다시 마이그레이션할 필요가 없다.

create table if not exists public.soul_trace_letters (
  letter_id text primary key,
  user_id text not null,
  -- canonical Eternal Beam petId. **펫은 새로 만들지 않는다** — 이미 있는 것을 가리킨다.
  -- 아직 펫이 없는 단계(Soul Trace 만 한 사용자)를 허용하려고 nullable 이다.
  pet_id text,
  -- Soul Trace 쪽 원본 식별자. 같은 편지를 두 번 들여와도 한 행으로 수렴한다.
  source_letter_id text,
  source text not null default 'soul_trace',
  -- 인쇄용 스냅샷. **여기서 만들어지지 않는다.**
  child_name text,
  letter_kicker text,
  letter_body text,
  letter_excerpt text,
  imported_at timestamptz not null default now()
);

-- 같은 Soul Trace 편지를 두 번 들여오지 않는다.
create unique index if not exists soul_trace_letters_source_idx
  on public.soul_trace_letters (user_id, source_letter_id)
  where source_letter_id is not null;

create index if not exists soul_trace_letters_user_idx
  on public.soul_trace_letters (user_id, pet_id);

comment on table public.soul_trace_letters is
  'Soul Trace 편지 참조/스냅샷. Eternal Beam 은 편지를 생성하지 않는다 — 가리킬 뿐이다';
comment on column public.soul_trace_letters.letter_body is
  '인쇄용 본문 스냅샷. 항상 외부(Soul Trace)에서 들어온다';
comment on column public.soul_trace_letters.source_letter_id is
  'Soul Trace 쪽 식별자. unique 라 같은 편지가 중복 import 되지 않는다';
