-- RETURN_HOME companions belong to a source COME_CLOSER run, not just a pet.
-- Keep the existing idempotency-key uniqueness for retries of the same source;
-- permit distinct source runs to queue their own companions concurrently.
begin;

drop index if exists public.pet_generation_runs_active_scope_idx;
create unique index pet_generation_runs_active_scope_idx
  on public.pet_generation_runs (user_id, pet_id, motion_id, request_kind)
  where status in ('QUEUED', 'RUNNING', 'WAITING_PROVIDER', 'RECOVERY_REQUIRED')
    and not (motion_id = 'RETURN_HOME' and request_kind = 'FREE_HOME');

commit;
