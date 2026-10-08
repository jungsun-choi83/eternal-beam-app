-- Phase 12 customer acceptance/complaint telemetry. This table has no trigger
-- or authority path into generation, QA, publication, or ownership.

create table if not exists public.pet_business_qa_user_test_feedback (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.pet_generation_runs(id) on delete cascade,
  user_id text not null,
  pet_id text not null,
  motion_id text not null,
  terminal_state text not null check (
    terminal_state in ('DELIVERED_GENERATED', 'DELIVERED_FALLBACK')
  ),
  accepted boolean not null,
  complaints jsonb not null default '[]'::jsonb,
  comment text,
  cutover_version text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (run_id, user_id)
);

create index if not exists pet_business_qa_user_test_feedback_created_idx
  on public.pet_business_qa_user_test_feedback (created_at desc);
create index if not exists pet_business_qa_user_test_feedback_pet_idx
  on public.pet_business_qa_user_test_feedback (pet_id, motion_id);

comment on table public.pet_business_qa_user_test_feedback is
  'Phase 12 product feedback only. Never read as QA, retry, delivery, publication, or ownership authority.';

