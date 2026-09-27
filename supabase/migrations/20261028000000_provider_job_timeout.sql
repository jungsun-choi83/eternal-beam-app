-- Phase 7D reliability fix #1 — durable provider-job deadline/timeout.
--
-- A submitted provider job must never wait forever: it now carries a
-- persisted deadline (set once, at submission, so a worker restart cannot
-- reset it) and bounded consecutive poll/collect error counters. Either one
-- tripping moves the job to the new terminal TIMED_OUT state, which is
-- never re-polled and never auto-resubmitted (same as FAILED).

alter table public.pet_generation_provider_jobs
  drop constraint if exists pet_generation_provider_jobs_submission_status_check;
alter table public.pet_generation_provider_jobs
  add constraint pet_generation_provider_jobs_submission_status_check check (
    submission_status in (
      'PREPARED', 'SUBMITTING', 'SUBMITTED', 'SUCCEEDED',
      'COLLECTED', 'FAILED', 'AMBIGUOUS', 'TIMED_OUT'
    )
  );

alter table public.pet_generation_provider_jobs
  add column if not exists deadline_at timestamptz;
alter table public.pet_generation_provider_jobs
  add column if not exists consecutive_poll_errors int not null default 0;
alter table public.pet_generation_provider_jobs
  add column if not exists consecutive_collect_errors int not null default 0;

comment on column public.pet_generation_provider_jobs.deadline_at is
  'Set once at submission (submitted_at + PROVIDER_JOB_TIMEOUT_SECONDS). Past this, the job is forced TIMED_OUT instead of polled again.';
comment on column public.pet_generation_provider_jobs.consecutive_poll_errors is
  'Resets to 0 on every poll that returns a provider status. At PROVIDER_JOB_MAX_CONSECUTIVE_ERRORS the job is forced TIMED_OUT.';
comment on column public.pet_generation_provider_jobs.consecutive_collect_errors is
  'Resets to 0 once collect() succeeds. At PROVIDER_JOB_MAX_CONSECUTIVE_ERRORS the job is forced TIMED_OUT.';
