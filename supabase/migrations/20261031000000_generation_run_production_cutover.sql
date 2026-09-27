-- Phase 7D+ production cutover — stop claim_next_pet_generation_run() from
-- ever resurrecting a pre-production QUEUED / WAITING_PROVIDER / stale-RUNNING
-- row that has not been touched since before the production launch boundary.
--
-- Root cause being fixed (see the Phase 7 worker-claim audit): the claim
-- query has never had any identity or recency filter — any eligible row from
-- any pet/user, however old, was claimable. Manually scoping the worker with
-- PHASE6_LIVE_MODE=allowlist + PHASE6_LIVE_ALLOWLIST only gated the final
-- MOTION_GENERATION provider call (video_motion_providers.live_generation_
-- allowed()); it never constrained which row the worker claimed, and it had
-- no effect at all on the CANONICAL_IMAGE/KEYFRAME_IMAGE stages, which are
-- gated by the separate CANONICAL_GENERATION_MOCK/KEYFRAME_GENERATION_MOCK
-- flags. So an old/unrelated row could still be claimed and run real, paid
-- canonical + keyframe generation before ever reaching the allowlist check.
--
-- Fix: add a floor on updated_at (not created_at) to the existing WHERE
-- clause. Why updated_at and not created_at:
--   - created_at is immutable, and retry_generation_run() (pet_generation_
--     run_service.py) intentionally preserves it when a user legitimately
--     retries an old FAILED/CANCELLED/RECOVERY_REQUIRED run. Gating on
--     created_at would permanently strand any pre-cutover run a real user
--     retries after this migration ships.
--   - updated_at is bumped by every write that represents real activity:
--     start_generation_run() and retry_generation_run() on insert/retry, and
--     every _progress()/heartbeat_pet_generation_run() call the worker makes
--     while actually advancing a run. A row nobody has touched since before
--     the cutover — an old manual/dev QUEUED insert, a crashed pre-cutover
--     RUNNING lease, an abandoned pre-cutover WAITING_PROVIDER poll — keeps
--     an old updated_at and is permanently excluded, while any genuinely
--     new or legitimately retried run clears the floor immediately.
--
-- The function signature is unchanged (still claim_next_pet_generation_run
-- (text, int)) — the cutover is a fixed constant in the query, not a new
-- parameter, so no caller (backend/services/pet_generation_run_service.py
-- _claim_next()) needs to change and there is no risk of Postgres treating
-- an added parameter as a second, ambiguous overload of this function.
--
-- Bump v_cutover in a follow-up migration only if a future dev/test queue
-- needs to be purged again the same way — it is intentionally a static
-- constant, not something callers control per-request.

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
  v_cutover constant timestamptz := '2026-09-27T13:00:00+00'::timestamptz;
begin
  select * into v_run
    from public.pet_generation_runs
   where (
     status = 'QUEUED'
     or (status = 'WAITING_PROVIDER' and coalesce(next_attempt_at, now()) <= now())
     or (status = 'RUNNING' and lease_expires_at <= now())
   )
   and updated_at >= v_cutover
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

revoke all on function public.claim_next_pet_generation_run(text, int)
  from public, anon, authenticated;
grant execute on function public.claim_next_pet_generation_run(text, int)
  to service_role;
