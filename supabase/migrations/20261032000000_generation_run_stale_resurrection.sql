-- Stale generation-run resurrection fix.
--
-- Root cause: claim_next_pet_generation_run() reclaimed *any* RUNNING row
-- whose lease had expired, forever. Every reclaim bumped updated_at, so the
-- production-cutover floor (migration 20261031) never excluded it, and a run
-- whose processing deterministically kills the worker (OOM, crash loop) was
-- re-claimed after every restart. Because the queue was ordered by
-- updated_at ascending, that old crash-looping run was also always claimed
-- *before* a freshly QUEUED run for a new pet — the new pet never started.
--
-- On the other side, nothing ever moved such a run out of RUNNING: the
-- browser's poll timeout told the user to retry, but no backend transition
-- happened, and the "Retry" button re-joined the same still-RUNNING row by
-- idempotency key. There was no cancel API at all.
--
-- This migration:
--   1. Adds lease_recoveries — how many times a RUNNING row has been reclaimed
--      after lease expiry since the last user action (start/retry). Reset to
--      0 by retry_generation_run(); never reset by the worker.
--   2. Caps stale-lease recovery at p_max_lease_recoveries. Beyond the cap the
--      row is handed to the worker exactly once more so that Python owns the
--      FAILED transition (it also reconciles premium credit reservations —
--      pet_generation_run_service._fail()). If that last hand-out itself dies,
--      the self-heal statement at the top of the claim function moves the row
--      to FAILED (code WORKER_RECOVERY_EXHAUSTED) so it can never be claimed
--      again.
--   3. Re-orders the claim queue: WAITING_PROVIDER (a paid job is already
--      in flight) → QUEUED (fresh user intent) → stale-lease RUNNING recovery.
--      A crash-looping recovery can no longer starve a new pet.
--   4. Keeps the claim predicate explicit: PUBLISHED / FAILED / CANCELLED /
--      RECOVERY_REQUIRED are never claimable — only a user action (retry)
--      can put a row back into QUEUED.
--
-- The 2-arg overload is dropped so Postgres never sees two ambiguous
-- signatures; the service always passes all three named parameters.

alter table public.pet_generation_runs
  add column if not exists lease_recoveries int not null default 0
  check (lease_recoveries >= 0);

comment on column public.pet_generation_runs.lease_recoveries is
  'Times a RUNNING row was reclaimed after lease expiry since the last user action. '
  'Reset by retry; once it passes GENERATION_RUN_MAX_LEASE_RECOVERIES the run is FAILED '
  '(WORKER_RECOVERY_EXHAUSTED) instead of being resurrected again.';

drop function if exists public.claim_next_pet_generation_run(text, int);

create or replace function public.claim_next_pet_generation_run(
  p_worker_id text,
  p_lease_seconds int default 300,
  p_max_lease_recoveries int default 2
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
  -- Safety net: a row whose exhausted hand-out (lease_recoveries > cap) also
  -- died must not stay RUNNING forever — it would sit in the active scope,
  -- block a same-key retry from ever leaving RUNNING, and never be claimed.
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
     and lease_recoveries > v_max;

  select * into v_run
    from public.pet_generation_runs
   where (
     status = 'QUEUED'
     or (status = 'WAITING_PROVIDER' and coalesce(next_attempt_at, now()) <= now())
     or (
       status = 'RUNNING'
       and lease_expires_at <= now()
       -- <= (not <) on purpose: the (v_max + 1)-th claim is the exhausted
       -- hand-out that the worker turns into FAILED without doing any work.
       and lease_recoveries <= v_max
     )
   )
   and updated_at >= v_cutover
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

revoke all on function public.claim_next_pet_generation_run(text, int, int)
  from public, anon, authenticated;
grant execute on function public.claim_next_pet_generation_run(text, int, int)
  to service_role;
