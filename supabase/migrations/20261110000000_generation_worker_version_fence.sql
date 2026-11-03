-- Reject generation queue consumers that do not implement the current routing
-- and durable-resume contract. The check happens before a row is selected or
-- mutated, so a rejected worker cannot disturb a lease or provider receipt.

create table if not exists public.pet_generation_run_claim_events (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.pet_generation_runs(id) on delete cascade,
  execution_token uuid not null,
  worker_id text not null,
  worker_hostname text not null,
  worker_pid integer not null check (worker_pid > 0),
  worker_version text not null,
  previous_status text not null,
  claimed_at timestamptz not null default now()
);

create index if not exists idx_pet_generation_run_claim_events_run
  on public.pet_generation_run_claim_events(run_id, claimed_at desc);

alter table public.pet_generation_run_claim_events enable row level security;
revoke all on table public.pet_generation_run_claim_events from public, anon, authenticated;
grant select, insert on table public.pet_generation_run_claim_events to service_role;

-- Remove every historical entry point. An old binary must receive an RPC
-- signature error rather than reaching an unversioned compatibility function.
drop function if exists public.claim_next_pet_generation_run(text, int);
drop function if exists public.claim_next_pet_generation_run(text, int, int);
drop function if exists public.claim_next_pet_generation_run(text, int, int, text[]);
drop function if exists public.claim_next_pet_generation_run(text, text, int, text, int, int, text[]);

create function public.claim_next_pet_generation_run(
  p_worker_id text,
  p_worker_hostname text,
  p_worker_pid integer,
  p_worker_version text,
  p_lease_seconds integer default 300,
  p_max_lease_recoveries integer default 2,
  p_pet_allowlist text[] default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_run public.pet_generation_runs%rowtype;
  v_previous_status text;
  v_cutover constant timestamptz := '2026-09-27T13:00:00+00'::timestamptz;
  v_max constant integer := greatest(coalesce(p_max_lease_recoveries, 2), 0);
  v_expected_worker_version constant text := 'pet-generation-worker-v2';
begin
  if nullif(btrim(p_worker_id), '') is null
     or nullif(btrim(p_worker_hostname), '') is null
     or p_worker_pid is null
     or p_worker_pid <= 0 then
    raise exception using
      errcode = '22023',
      message = 'GENERATION_WORKER_IDENTITY_INVALID';
  end if;

  if p_worker_version is distinct from v_expected_worker_version then
    raise exception using
      errcode = '22023',
      message = 'GENERATION_WORKER_VERSION_MISMATCH',
      detail = format('expected=%s actual=%s', v_expected_worker_version, coalesce(p_worker_version, '<null>'));
  end if;

  -- Preserve the bounded stale-lease recovery behavior of the prior RPC.
  update public.pet_generation_runs
     set status = 'FAILED',
         last_error = jsonb_build_object(
           'stage', current_stage,
           'code', 'WORKER_RECOVERY_EXHAUSTED',
           'message', 'worker lease expired too many times; manual retry required',
           'lease_recoveries', lease_recoveries,
           'at', now()
         ),
         execution_token = null,
         lease_expires_at = null,
         worker_id = null,
         next_attempt_at = null,
         completed_at = now(),
         updated_at = now()
   where status = 'RUNNING'
     and lease_expires_at <= now()
     and lease_recoveries > v_max
     and (p_pet_allowlist is null or pet_id = any(p_pet_allowlist));

  select * into v_run
    from public.pet_generation_runs
   where (
     status = 'QUEUED'
     or (status = 'WAITING_PROVIDER' and coalesce(next_attempt_at, now()) <= now())
     or (
       status = 'RUNNING'
       and lease_expires_at <= now()
       and lease_recoveries <= v_max
     )
   )
   and updated_at >= v_cutover
   and (p_pet_allowlist is null or pet_id = any(p_pet_allowlist))
   order by
     case status
       when 'WAITING_PROVIDER' then 0
       when 'QUEUED' then 1
       else 2
     end,
     updated_at,
     created_at
   for update skip locked
   limit 1;

  if not found then
    return jsonb_build_object('claimed', false);
  end if;

  v_previous_status := v_run.status;

  update public.pet_generation_runs
     set status = 'RUNNING',
         worker_id = p_worker_id,
         execution_token = gen_random_uuid(),
         lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
         lease_recoveries = lease_recoveries
           + case when v_previous_status = 'RUNNING' then 1 else 0 end,
         next_attempt_at = null,
         updated_at = now()
   where id = v_run.id
   returning * into v_run;

  insert into public.pet_generation_run_claim_events (
    run_id,
    execution_token,
    worker_id,
    worker_hostname,
    worker_pid,
    worker_version,
    previous_status
  ) values (
    v_run.id,
    v_run.execution_token,
    p_worker_id,
    p_worker_hostname,
    p_worker_pid,
    p_worker_version,
    v_previous_status
  );

  return jsonb_build_object('claimed', true, 'run', to_jsonb(v_run));
end;
$$;

revoke all on function public.claim_next_pet_generation_run(text, text, int, text, int, int, text[])
  from public, anon, authenticated;
grant execute on function public.claim_next_pet_generation_run(text, text, int, text, int, int, text[])
  to service_role;

