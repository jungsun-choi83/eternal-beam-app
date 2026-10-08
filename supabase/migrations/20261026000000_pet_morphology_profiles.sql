-- 펫 형태 프로필 (Phase 3.5) — 개체 구조(몸비율/체형) 전용, 신원과 분리.
--
-- 원칙:
-- * 원본 레퍼런스에서 파생된 append-only 버전드 프로필이다.
-- * 시각 신원(코트/무늬/색)과 분리된다.
-- * 품종은 선택 메타데이터로만 보관되며 생성 템플릿에 쓰지 않는다.

create table if not exists public.pet_morphology_profiles (
  id uuid primary key default gen_random_uuid(),
  pet_id text not null,
  content_id text,
  user_id text not null,
  version int not null,
  status text not null check (status in ('complete', 'partial')),
  source_reference_ids jsonb not null default '[]'::jsonb,
  -- 융합된 형태 프로필 본문.
  profile jsonb not null default '{}'::jsonb,
  -- 레퍼런스별 관측값/근거 스냅샷.
  reference_observations jsonb not null default '{}'::jsonb,
  completeness jsonb not null default '{}'::jsonb,
  analyzer_versions jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create unique index if not exists pet_morphology_profiles_version_uidx
  on public.pet_morphology_profiles (pet_id, version);

create index if not exists pet_morphology_profiles_pet_idx
  on public.pet_morphology_profiles (pet_id, created_at desc);

create index if not exists pet_morphology_profiles_user_idx
  on public.pet_morphology_profiles (user_id, pet_id);

comment on table public.pet_morphology_profiles is
  '레퍼런스에서 파생된 버전드 형태(구조) 프로필. 신원(코트/무늬/색)과 분리';
