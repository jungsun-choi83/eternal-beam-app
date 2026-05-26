-- Eternal Beam: 40-scenario Luma batch (optional — 없으면 서버 MOCK 메모리 사용)
-- Supabase SQL Editor 에서 실행

create table if not exists public.pet_generation_batches (
  batch_id uuid primary key,
  user_id text not null,
  pet_id text not null,
  image_url text not null,
  status text not null default 'queued',
  total int not null default 40,
  completed_count int not null default 0,
  failed_count int not null default 0,
  created_at timestamptz not null default now()
);

create table if not exists public.pet_scenario_videos (
  id bigserial primary key,
  batch_id uuid not null references public.pet_generation_batches(batch_id) on delete cascade,
  user_id text not null,
  pet_id text not null,
  place_key text not null,
  action_key text not null,
  status text not null default 'pending',
  luma_generation_id text,
  storage_path text,
  video_url text,
  error text,
  updated_at timestamptz,
  unique (batch_id, place_key, action_key)
);

create index if not exists idx_pet_scenario_luma_gen
  on public.pet_scenario_videos (luma_generation_id);
