-- One-time cleanup of generation runs that pre-date migration 20261032
-- (stale-run resurrection fix). Run by hand in the Supabase SQL editor with
-- the service role. Nothing here is executed automatically.
--
-- Why it is needed even after the migration: rows that already existed have
-- lease_recoveries = 0, so a RUNNING row with a long-expired lease would still
-- get its full recovery budget (default 2 + the exhausted hand-out) before
-- the worker gives up on it. Old dev/test QUEUED rows would still be claimed
-- in order. This script closes them out explicitly instead.
--
-- Step 1 — DRY RUN. Look at what would change before touching anything.
select id, user_id, pet_id, motion_id, request_kind, status, current_stage,
       lease_expires_at, next_attempt_at, updated_at, created_at,
       case
         when status = 'RUNNING' and lease_expires_at < now() - interval '1 hour'
           then 'RUNNING → FAILED (lease dead > 1h)'
         when status in ('QUEUED', 'WAITING_PROVIDER')
          and updated_at < now() - interval '24 hours'
           then status || ' → CANCELLED (untouched > 24h)'
       end as planned_change
  from public.pet_generation_runs
 where (status = 'RUNNING' and lease_expires_at < now() - interval '1 hour')
    or (status in ('QUEUED', 'WAITING_PROVIDER')
        and updated_at < now() - interval '24 hours')
 order by updated_at;

-- Step 2 — APPLY (uncomment). Both statements are idempotent and only touch
-- rows nobody is working on: a live worker heartbeats every lease/3 seconds,
-- so a lease that has been expired for an hour has no owner.
--
-- begin;
--
-- update public.pet_generation_runs
--    set status = 'FAILED',
--        last_error = jsonb_build_object(
--          'stage', current_stage,
--          'code', 'WORKER_RECOVERY_EXHAUSTED',
--          'message', 'stale RUNNING run closed by maintenance cleanup; retry manually',
--          'reason', 'maintenance_cleanup_2026_09_29',
--          'at', now()
--        ),
--        execution_token = null,
--        lease_expires_at = null,
--        worker_id = null,
--        next_attempt_at = null,
--        completed_at = now(),
--        updated_at = now()
--  where status = 'RUNNING'
--    and lease_expires_at < now() - interval '1 hour';
--
-- update public.pet_generation_runs
--    set status = 'CANCELLED',
--        last_error = jsonb_build_object(
--          'stage', current_stage,
--          'code', 'RUN_CANCELLED',
--          'message', 'stale queued run closed by maintenance cleanup; retry manually',
--          'reason', 'maintenance_cleanup_2026_09_29',
--          'at', now()
--        ),
--        execution_token = null,
--        lease_expires_at = null,
--        worker_id = null,
--        next_attempt_at = null,
--        completed_at = now(),
--        updated_at = now()
--  where status in ('QUEUED', 'WAITING_PROVIDER')
--    and updated_at < now() - interval '24 hours';
--
-- commit;
--
-- Users who come back to a closed run see the normal error screen with Retry;
-- POST /v1/pet/generation-runs/{id}/retry re-queues that same run with its
-- upstream lineage intact and any SUBMITTED provider job reused by
-- fingerprint, so nothing paid is re-bought by this cleanup.
