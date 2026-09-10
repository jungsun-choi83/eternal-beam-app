-- Phase 7D — restart-safe provider submissions and worker claims.

alter table public.pet_generation_runs
  drop constraint if exists pet_generation_runs_status_check;
alter table public.pet_generation_runs
  add constraint pet_generation_runs_status_check check (
    status in (
      'QUEUED', 'RUNNING', 'WAITING_PROVIDER', 'RECOVERY_REQUIRED',
      'PUBLISHED', 'FAILED', 'CANCELLED'
    )
  );
alter table public.pet_generation_runs
  add column if not exists worker_id text;
alter table public.pet_generation_runs
  add column if not exists next_attempt_at timestamptz;

create table if not exists public.pet_generation_provider_jobs (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.pet_generation_runs(id),
  user_id text not null,
  pet_id text not null,
  provider_operation text not null check (
    provider_operation in ('CANONICAL_IMAGE', 'KEYFRAME_IMAGE', 'MOTION_VIDEO')
  ),
  phase_version_id uuid not null,
  provider text not null,
  model text not null,
  attempt int not null check (attempt > 0),
  request_fingerprint text not null,
  submission_status text not null default 'PREPARED' check (
    submission_status in (
      'PREPARED', 'SUBMITTING', 'SUBMITTED', 'SUCCEEDED',
      'COLLECTED', 'FAILED', 'AMBIGUOUS'
    )
  ),
  external_job_id text,
  submitted_at timestamptz,
  last_polled_at timestamptz,
  provider_status text,
  provider_error text,
  result_metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (run_id, provider_operation, phase_version_id, provider, attempt)
);

create index if not exists pet_generation_provider_jobs_run_idx
  on public.pet_generation_provider_jobs (run_id, created_at);
create index if not exists pet_generation_provider_jobs_poll_idx
  on public.pet_generation_provider_jobs (submission_status, last_polled_at);

comment on table public.pet_generation_provider_jobs is
  'Phase 7D paid-provider submission receipts. Phase candidate/version tables remain output authority.';
comment on column public.pet_generation_provider_jobs.submission_status is
  'SUBMITTING without an external_job_id is intentionally ambiguous and must never be auto-resubmitted.';

-- Claim one eligible run. FOR UPDATE SKIP LOCKED permits multiple workers while
-- the execution token fences stale workers after lease takeover.
create or replace function public.claim_next_pet_generation_run(
  p_worker_id text,
  p_lease_seconds int default 300
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_run public.pet_generation_runs%rowtype;
begin
  select * into v_run
    from public.pet_generation_runs
   where (
     status = 'QUEUED'
     or (status = 'WAITING_PROVIDER' and coalesce(next_attempt_at, now()) <= now())
     or (status = 'RUNNING' and lease_expires_at <= now())
   )
   order by
     case when status = 'WAITING_PROVIDER' then 0 else 1 end,
     updated_at,
     created_at
   for update skip locked
   limit 1;

  if not found then
    return jsonb_build_object('claimed', false);
  end if;

  update public.pet_generation_runs
     set status = 'RUNNING',
         worker_id = p_worker_id,
         execution_token = gen_random_uuid(),
         lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
         next_attempt_at = null,
         updated_at = now()
   where id = v_run.id
   returning * into v_run;

  return jsonb_build_object('claimed', true, 'run', to_jsonb(v_run));
end;
$$;

create or replace function public.heartbeat_pet_generation_run(
  p_run_id uuid,
  p_execution_token uuid,
  p_lease_seconds int default 300
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_updated int;
begin
  update public.pet_generation_runs
     set lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
         updated_at = now()
   where id = p_run_id
     and execution_token = p_execution_token
     and status = 'RUNNING';
  get diagnostics v_updated = row_count;
  return v_updated = 1;
end;
$$;

revoke all on function public.claim_next_pet_generation_run(text, int)
  from public, anon, authenticated;
grant execute on function public.claim_next_pet_generation_run(text, int)
  to service_role;
revoke all on function public.heartbeat_pet_generation_run(uuid, uuid, int)
  from public, anon, authenticated;
grant execute on function public.heartbeat_pet_generation_run(uuid, uuid, int)
  to service_role;
