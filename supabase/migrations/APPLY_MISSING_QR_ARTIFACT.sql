-- ════════════════════════════════════════════════════════════════════════════
-- 프로덕션에 **적용되지 않은** 두 마이그레이션을 한 번에 따라잡는다.
--
--   대상 프로젝트: kdlukiujgclczwqmwvmk  (Eternal Beam)
--   ⚠️ Soul Trace(pjoyuvqykggcuvbsnxio) 가 아니다.
--
-- Supabase 대시보드 → SQL Editor 에 통째로 붙여 넣고 실행한다.
-- 전부 additive 이고 idempotent 다 — 기존 데이터를 지우거나 바꾸지 않는다.
--
-- 원본 마이그레이션(내용 동일):
--   20260824000000_shaker_qr_artifacts.sql
--   20260824000100_production_qr_from_artifact.sql
--
-- ── 왜 필요한가 (실측) ──────────────────────────────────────────────────────
-- 결제된 주문 eb_order_d78a1b1e6c034ad38a1a 이 생산 준비에서 멈췄다:
--
--   production_package.prepare()
--     → get_package()                     ← 여기서 실패
--       → select(..., qr_source, ...)     ← 42703 column does not exist
--       → PACKAGE_STORE_UNAVAILABLE
--
-- prepare() 는 멱등성 확인을 위해 **가장 먼저** get_package() 를 부른다. 그래서
-- insert 는 시도조차 되지 않았고, production_packages 에 행이 남지 않았다.
-- ════════════════════════════════════════════════════════════════════════════

begin;

-- ── 1. QR 산출물 보관 (20260824000000) ──────────────────────────────────────
-- 원문 토큰은 저장하지 않으므로 URL 을 복원할 수 없다. 그래서 발급 순간의
-- QR 을 **바이트 그대로** 보관한다 — 재인쇄가 이미 배송된 종이와 같아야 한다.
create table if not exists public.shaker_qr_artifacts (
  -- shaker_shares.share_id 와 1:1. 비밀이 아닌 식별자다.
  share_id text primary key,
  -- 어느 공유의 것인지 확인용. **원문 토큰이 아니다.**
  token_hash text not null,
  pet_id text not null,
  -- 인쇄용 벡터. QR 의 정본이다.
  qr_svg text not null,
  -- 화면 미리보기·붙여넣기용(선택). base64.
  qr_png_base64 text,
  -- 생성 당시 QR 이 가리킨 호스트. base URL 이 바뀌었는지 운영이 알 수 있어야 한다
  -- (이미 인쇄된 QR 은 옛 호스트를 가리킨 채로 남는다).
  target_host text,
  -- CUSTOMER | OPS | LETTER | MEMORY_BOX — 인쇄물용인지 구분한다.
  purpose text,
  created_at timestamptz not null default now()
);

comment on table public.shaker_qr_artifacts is
  '발급 시점의 QR 원본. 토큰을 복원하지 않고 같은 QR 을 다시 뽑기 위한 보관본';

-- ── 2. 산출물로도 생산 준비 (20260824000100) ────────────────────────────────
-- 산출물로 준비되면 URL 이 없다. NOT NULL 이 남아 있으면 그 경로가 통째로 막힌다.
alter table public.production_packages
  alter column qr_share_url drop not null;

alter table public.production_packages
  add column if not exists qr_source text;

comment on column public.production_packages.qr_share_url is
  'QR 대상 URL. 산출물로 준비된 경우 null 이다 (토큰은 복원되지 않는다)';
comment on column public.production_packages.qr_source is
  'url = 운영이 URL 을 넘김 / artifact = 보관된 QR 산출물을 재사용';

commit;

-- ── 3. PostgREST 스키마 캐시 갱신 ───────────────────────────────────────────
-- 이걸 빼면 컬럼은 생겼는데 API 는 한동안 "column does not exist" 를 계속 준다.
notify pgrst, 'reload schema';

-- ── 적용 직후 확인 ──────────────────────────────────────────────────────────
select column_name, is_nullable
  from information_schema.columns
 where table_schema = 'public'
   and table_name = 'production_packages'
   and column_name in ('qr_share_url', 'qr_source')
 order by column_name;

select count(*) as shaker_qr_artifacts_rows from public.shaker_qr_artifacts;
