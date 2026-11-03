-- Durable Archive preparation worker lease and progress metadata.
-- Apply this migration before enabling ARCHIVE_PREPARATION_WORKER_ENABLED.

alter table public.archive_intakes
  add column if not exists preparation_stage text,
  add column if not exists preparation_progress integer not null default 0,
  add column if not exists preparation_attempt integer not null default 0,
  add column if not exists preparation_lease_id text,
  add column if not exists preparation_lease_expires_at timestamptz;

alter table public.archive_intakes
  drop constraint if exists archive_intakes_preparation_progress_check;

alter table public.archive_intakes
  add constraint archive_intakes_preparation_progress_check
  check (preparation_progress between 0 and 100);

create index if not exists archive_intakes_preparation_claim_idx
  on public.archive_intakes (status, preparation_lease_expires_at, updated_at)
  where generation_run_id is null;

create or replace function public.claim_archive_intake_preparation(
  p_archive_application_id text,
  p_worker_id text,
  p_lease_seconds integer default 300
)
returns setof public.archive_intakes
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  perform pg_advisory_xact_lock(
    hashtextextended('archive-intake:' || p_archive_application_id, 0)
  );

  return query
  update public.archive_intakes ai
     set status = 'PREPARING',
         last_error = null,
         preparation_stage = 'CLAIMED',
         preparation_progress = 0,
         preparation_attempt = coalesce(ai.preparation_attempt, 0) + 1,
         preparation_lease_id = gen_random_uuid()::text,
         preparation_lease_expires_at = now() + make_interval(secs => greatest(60, p_lease_seconds)),
         updated_at = now()
   where ai.archive_application_id = p_archive_application_id
     and ai.generation_run_id is null
     and ai.status in ('RECEIVED', 'REFERENCES_READY', 'PREPARING')
     and (
       ai.preparation_lease_id is null
       or ai.preparation_lease_expires_at is null
       or ai.preparation_lease_expires_at < now()
     )
  returning ai.*;
end;
$$;

create or replace function public.claim_archive_intake_preparation(
  p_archive_application_id text
)
returns setof public.archive_intakes
language sql
security definer
set search_path = public, pg_temp
as $$
  select * from public.claim_archive_intake_preparation(
    p_archive_application_id,
    'archive-preparation-legacy',
    300
  );
$$;

create or replace function public.claim_next_archive_intake_preparation(
  p_worker_id text,
  p_lease_seconds integer,
  p_archive_application_id text
)
returns setof public.archive_intakes
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  return query
  with candidate as (
    select ai.archive_application_id
      from public.archive_intakes ai
     where ai.generation_run_id is null
       and ai.status in ('REFERENCES_READY', 'PREPARING')
       and (
         p_archive_application_id is null
         or ai.archive_application_id = p_archive_application_id
       )
       and (
         ai.preparation_lease_id is null
         or ai.preparation_lease_expires_at is null
         or ai.preparation_lease_expires_at < now()
       )
     order by ai.updated_at, ai.created_at
     for update skip locked
     limit 1
  )
  update public.archive_intakes ai
     set status = 'PREPARING',
         last_error = null,
         preparation_stage = 'CLAIMED',
         preparation_progress = 0,
         preparation_attempt = coalesce(ai.preparation_attempt, 0) + 1,
         preparation_lease_id = gen_random_uuid()::text,
         preparation_lease_expires_at = now() + make_interval(secs => greatest(60, p_lease_seconds)),
         updated_at = now()
    from candidate
   where ai.archive_application_id = candidate.archive_application_id
  returning ai.*;
end;
$$;

create or replace function public.claim_next_archive_intake_preparation(
  p_worker_id text,
  p_lease_seconds integer
)
returns setof public.archive_intakes
language sql
security definer
set search_path = public, pg_temp
as $$
  select * from public.claim_next_archive_intake_preparation(
    p_worker_id,
    p_lease_seconds,
    null
  );
$$;

revoke all on function public.claim_archive_intake_preparation(text, text, integer)
  from public, anon, authenticated;
grant execute on function public.claim_archive_intake_preparation(text, text, integer)
  to service_role;

revoke all on function public.claim_archive_intake_preparation(text)
  from public, anon, authenticated;
grant execute on function public.claim_archive_intake_preparation(text)
  to service_role;

revoke all on function public.claim_next_archive_intake_preparation(text, integer)
  from public, anon, authenticated;
grant execute on function public.claim_next_archive_intake_preparation(text, integer)
  to service_role;

revoke all on function public.claim_next_archive_intake_preparation(text, integer, text)
  from public, anon, authenticated;
grant execute on function public.claim_next_archive_intake_preparation(text, integer, text)
  to service_role;

-- Operator recovery changes only the durable preparation queue. It never
-- creates a generation run; the worker remains the sole generation-run caller.
create or replace function public.recover_archive_intake_preparation(
  p_archive_application_id text
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  ai public.archive_intakes%rowtype;
begin
  perform pg_advisory_xact_lock(
    hashtextextended('archive-intake:' || p_archive_application_id, 0)
  );

  select * into ai
    from public.archive_intakes
   where archive_application_id = p_archive_application_id
   for update;

  if not found then
    return jsonb_build_object('outcome', 'NOT_RECOVERABLE', 'intake', null);
  end if;

  if ai.generation_run_id is not null then
    return jsonb_build_object(
      'outcome', 'GENERATION_ALREADY_EXISTS', 'intake', to_jsonb(ai)
    );
  end if;

  if ai.status = 'PREPARING'
     and ai.preparation_lease_id is not null
     and ai.preparation_lease_expires_at > now() then
    return jsonb_build_object(
      'outcome', 'ALREADY_IN_PROGRESS', 'intake', to_jsonb(ai)
    );
  end if;

  if ai.status = 'PREPARING'
     and ai.preparation_stage = 'QUEUED'
     and ai.preparation_lease_id is null then
    return jsonb_build_object(
      'outcome', 'ALREADY_QUEUED', 'intake', to_jsonb(ai)
    );
  end if;

  if ai.status not in ('RECEIVED', 'REFERENCES_READY', 'GENERATION_FAILED', 'PREPARING') then
    return jsonb_build_object('outcome', 'NOT_RECOVERABLE', 'intake', to_jsonb(ai));
  end if;

  update public.archive_intakes
     set status = 'PREPARING',
         last_error = null,
         preparation_stage = 'QUEUED',
         preparation_progress = 0,
         preparation_lease_id = null,
         preparation_lease_expires_at = null,
         updated_at = now()
   where archive_application_id = p_archive_application_id
  returning * into ai;

  return jsonb_build_object('outcome', 'QUEUED', 'intake', to_jsonb(ai));
end;
$$;

revoke all on function public.recover_archive_intake_preparation(text)
  from public, anon, authenticated;
grant execute on function public.recover_archive_intake_preparation(text)
  to service_role;
