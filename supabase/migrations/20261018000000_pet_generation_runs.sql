-- Phase 7C — durable server-side orchestration for the new Phase 1–7A pipeline.
--
-- This is coordination state only. Phase-specific version/candidate tables remain
-- authoritative for their outputs, and pet_motion_publications remains the
-- publication ledger.

create table if not exists public.pet_generation_runs (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  pet_id text not null,
  content_id text not null,
  motion_id text not null check (motion_id = 'BREATHING'),
  request_kind text not null check (request_kind = 'FREE_HOME'),
  idempotency_key text not null,

  status text not null default 'QUEUED' check (
    status in ('QUEUED', 'RUNNING', 'PUBLISHED', 'FAILED', 'CANCELLED')
  ),
  current_stage text not null default 'QUEUED' check (
    current_stage in (
      'QUEUED', 'IDENTITY', 'REFERENCE_SET', 'CANONICAL', 'KEYFRAMES',
      'MOTION_SPEC', 'MOTION_GENERATION', 'QA', 'PUBLICATION', 'PUBLISHED'
    )
  ),

  identity_profile_id uuid references public.pet_identity_profiles(id),
  identity_profile_version int,
  reference_set_id uuid references public.pet_reference_sets(id),
  reference_set_version int,
  canonical_version_id uuid references public.pet_canonical_versions(id),
  canonical_version int,
  keyframes jsonb not null default '{}'::jsonb,
  motion_spec_version text,
  motion_version_id uuid references public.pet_motion_versions(id),
  motion_version int,
  selected_candidate_id uuid references public.pet_motion_candidates(id),
  publication_id uuid references public.pet_motion_publications(id),

  -- canonical/keyframe/motion provider boundaries are currently blocking. An
  -- IN_FLIGHT marker survives a process crash and prevents blind resubmission.
  provider_state jsonb not null default '{}'::jsonb,
  last_error jsonb,
  retry_count int not null default 0 check (retry_count >= 0),

  execution_token uuid,
  lease_expires_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz,

  unique (user_id, pet_id, motion_id, request_kind, idempotency_key)
);

create index if not exists pet_generation_runs_pet_idx
  on public.pet_generation_runs (user_id, pet_id, created_at desc);
create index if not exists pet_generation_runs_status_idx
  on public.pet_generation_runs (status, lease_expires_at);

comment on table public.pet_generation_runs is
  'Phase 7C durable orchestration state. Phase output tables remain source of truth.';
comment on column public.pet_generation_runs.provider_state is
  'Per-stage blocking-provider receipt; ambiguous IN_FLIGHT calls are never blindly retried.';

-- Atomically claim a queued/failed/stale run. A second web process receives
-- claimed=false and must only return the current representation.
create or replace function public.claim_pet_generation_run(
  p_run_id uuid,
  p_user_id text,
  p_lease_seconds int default 21600
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_run public.pet_generation_runs%rowtype;
  v_was_retry boolean;
begin
  select * into v_run
    from public.pet_generation_runs
   where id = p_run_id
   for update;

  if not found then
    raise exception 'GENERATION_RUN_NOT_FOUND';
  end if;
  if v_run.user_id <> p_user_id then
    raise exception 'PET_NOT_OWNED';
  end if;
  if v_run.status in ('PUBLISHED', 'CANCELLED') then
    return jsonb_build_object('claimed', false, 'run', to_jsonb(v_run));
  end if;
  if v_run.status = 'RUNNING'
     and v_run.lease_expires_at is not null
     and v_run.lease_expires_at > now() then
    return jsonb_build_object('claimed', false, 'run', to_jsonb(v_run));
  end if;

  v_was_retry := v_run.status in ('RUNNING', 'FAILED');
  update public.pet_generation_runs
     set status = 'RUNNING',
         retry_count = retry_count + case when v_was_retry then 1 else 0 end,
         execution_token = gen_random_uuid(),
         lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
         last_error = null,
         updated_at = now()
   where id = p_run_id
   returning * into v_run;

  return jsonb_build_object('claimed', true, 'run', to_jsonb(v_run));
end;
$$;

revoke all on function public.claim_pet_generation_run(uuid, text, int)
  from public, anon, authenticated;
grant execute on function public.claim_pet_generation_run(uuid, text, int)
  to service_role;
