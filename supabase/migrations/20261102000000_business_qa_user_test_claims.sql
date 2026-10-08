-- Phase 12: limit the production worker to the explicit Business QA user-test
-- pet cohort without changing run, publication, or ownership records.
-- NULL preserves the pre-Phase-12 queue behavior; an empty array claims none.

drop function if exists public.claim_next_pet_generation_run(text, int, int);
drop function if exists public.claim_next_pet_generation_run(text, int, int, text[]);

create or replace function public.claim_next_pet_generation_run(
  p_worker_id text,
  p_lease_seconds int default 300,
  p_max_lease_recoveries int default 2,
  p_pet_allowlist text[] default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_run public.pet_generation_runs%rowtype;
  v_cutover constant timestamptz := '2026-09-27T13:00:00+00'::timestamptz;
  v_max constant int := greatest(coalesce(p_max_lease_recoveries, 2), 0);
begin
  -- Keep the existing stale-lease self-heal, scoped to the same cohort this
  -- worker is authorized to claim.
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

  update public.pet_generation_runs
     set status = 'RUNNING',
         worker_id = p_worker_id,
         execution_token = gen_random_uuid(),
         lease_expires_at = now() + make_interval(secs => greatest(p_lease_seconds, 60)),
         lease_recoveries = lease_recoveries
           + case when v_run.status = 'RUNNING' then 1 else 0 end,
         next_attempt_at = null,
         updated_at = now()
   where id = v_run.id
   returning * into v_run;

  return jsonb_build_object('claimed', true, 'run', to_jsonb(v_run));
end;
$$;

revoke all on function public.claim_next_pet_generation_run(text, int, int, text[])
  from public, anon, authenticated;
grant execute on function public.claim_next_pet_generation_run(text, int, int, text[])
  to service_role;

-- Read-only deployment marker used by the worker preflight. Calling the claim
-- RPC itself would mutate queue state, so readiness must never probe it.
create or replace function public.business_qa_user_test_claim_contract()
returns text
language sql
stable
security definer
set search_path = public
as $$
  select 'business-user-test-v1'::text;
$$;

revoke all on function public.business_qa_user_test_claim_contract()
  from public, anon, authenticated;
grant execute on function public.business_qa_user_test_claim_contract()
  to service_role;
