-- Phase 11: observation-only legacy QA versus Business QA telemetry.
--
-- One RUN aggregate and one row per observed Canonical/Keyframe/Motion
-- candidate are stored under a deterministic telemetry_key.  This table has
-- no trigger and no foreign-key path back into publication/ownership decisions;
-- telemetry writes therefore cannot authorize generation or delivery.

create table if not exists public.pet_business_qa_shadow_telemetry (
  id uuid primary key default gen_random_uuid(),
  telemetry_key text not null unique,
  telemetry_version text not null,
  run_id uuid not null references public.pet_generation_runs(id) on delete cascade,
  candidate_id text,
  stage text not null,
  stage_role text,
  user_id text not null,
  pet_id text not null,
  motion_id text not null,
  request_kind text not null,

  legacy_decision text,
  business_integrity_status text,
  business_quality_status text,
  delivery_action text,
  retry_action text,

  vlm_tasks_called jsonb not null default '[]'::jsonb,
  vlm_call_count int not null default 0 check (vlm_call_count >= 0),
  vlm_cache_hits int not null default 0 check (vlm_cache_hits >= 0),
  candidate_count int not null default 0 check (candidate_count >= 0),
  fallback_used boolean not null default false,
  retry_used boolean not null default false,

  provider_generation_ms numeric,
  qa_time_ms numeric,
  timings jsonb not null default '{}'::jsonb,
  estimated_generation_cost_usd numeric,
  estimated_vlm_cost_usd numeric,
  comparison_flags jsonb not null default '{}'::jsonb,
  metadata jsonb not null default '{}'::jsonb,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists pet_business_qa_shadow_run_idx
  on public.pet_business_qa_shadow_telemetry (run_id, stage);
create index if not exists pet_business_qa_shadow_comparison_idx
  on public.pet_business_qa_shadow_telemetry (stage, created_at desc);

comment on table public.pet_business_qa_shadow_telemetry is
  'Phase 11 fail-open shadow observations. Never read as QA, retry, delivery, publication, or ownership authority.';
comment on column public.pet_business_qa_shadow_telemetry.telemetry_key is
  'Idempotent run/stage/candidate observation key; repeated worker resumes update the same observation.';
