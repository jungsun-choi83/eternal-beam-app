-- Phase 7H+ reliability fix — duplicate-paid-run protection.
--
-- claim_next_pet_generation_run() lets multiple workers each claim a
-- *different* row concurrently (that is the point — FOR UPDATE SKIP LOCKED).
-- But nothing stopped two *different* rows from existing for the same
-- pet's work in the first place: two concurrent start_generation_run calls
-- with two different idempotency_keys (e.g. a client retry racing the
-- original request, or a double-tap) each inserted their own row, and two
-- workers then each drove that pet's canonical/keyframe/motion build
-- through its own paid provider submission.
--
-- This partial unique index makes that impossible at the source of truth:
-- at most one non-terminal run may exist per (user_id, pet_id, motion_id,
-- request_kind), independent of idempotency_key. A concurrent request for
-- the same logical work must join the existing active run instead.
-- pet_generation_run_service._insert_or_get() checks this scope before
-- inserting as the fast path; this index is the cross-process guarantee —
-- a losing insert is caught and re-read as a join, never surfaced as a
-- generic persistence failure.
--
-- Terminal statuses (PUBLISHED, FAILED, CANCELLED) are deliberately excluded
-- from the predicate so a finished/dead run never blocks legitimate future
-- generation for the same pet/motion.

create unique index if not exists pet_generation_runs_active_scope_idx
  on public.pet_generation_runs (user_id, pet_id, motion_id, request_kind)
  where status in ('QUEUED', 'RUNNING', 'WAITING_PROVIDER', 'RECOVERY_REQUIRED');
