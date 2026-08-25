-- Phase 22 — 편지 배경(히어로 이미지) 사본 참조.
--
--   대상 프로젝트: kdlukiujgclczwqmwvmk  (Eternal Beam)
--
-- ── 왜 URL 이 아니라 ref 인가 ───────────────────────────────────────────────
-- Soul Trace 는 히어로 이미지를 저장하지 않는다. hero_image_url 에 들어 있는 것은
-- DALL·E 가 돌려준 **임시 주소**이고 한두 시간이면 죽는다. 인쇄는 결제·생산 이후,
-- 즉 며칠 뒤일 수 있으므로 그 주소를 저장해 두는 설계는 성립하지 않는다.
--
-- 그래서 편지를 가져오는 순간 바이트를 **우리 스토리지로 복사**하고, 여기에는
-- 그 **객체 경로**를 남긴다. 서명 URL 이 아니다 — 서명은 만료되지만 경로는
-- 만료되지 않고, 서명은 인쇄 직전에 그때그때 만들면 된다.
--
-- ── nullable 이다 ──────────────────────────────────────────────────────────
-- 이 컬럼이 생기기 전에 들어온 편지에는 값이 없다. 그 편지들은 지금까지처럼
-- 어두운 스크림 배경으로 인쇄된다(print_render 의 폴백). 과거 주문을 다시 뽑아도
-- 그때와 같은 결과가 나온다.
alter table public.soul_trace_letters
  add column if not exists letter_background_ref text;

comment on column public.soul_trace_letters.letter_background_ref is
  'Eternal Beam 스토리지의 배경 이미지 **객체 경로**. NULL = 배경 없음(스크림 폴백). 서명 URL 이 아니다';
