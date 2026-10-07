-- Durable Archive intake preparation and lifecycle-safe metadata retries.

alter table public.archive_intakes
  drop constraint if exists archive_intakes_status_check;

alter table public.archive_intakes
  add constraint archive_intakes_status_check
  check (
    status in (
      'RECEIVED',
      'REFERENCES_READY',
      'PREPARING',
      'GENERATION_QUEUED',
      'GENERATION_FAILED',
      'GENERATING',
      'COMPLETED',
      'FAILED'
    )
  );

-- A repeated Archive upload may refresh metadata/reference_count, but must
-- never regress PREPARING, queued, running, completed, or failed lifecycle
-- state, nor clear a generation receipt or future result fields.
create or replace function public.upsert_archive_intake_metadata(
  p_archive_application_id text,
  p_owner_id text,
  p_content_id text,
  p_pet_id text,
  p_customer_email text,
  p_pet_name text,
  p_pet_type text,
  p_breed text,
  p_reference_count integer
)
returns public.archive_intakes
language sql
security definer
set search_path = public, pg_temp
as $$
  insert into public.archive_intakes as existing (
    archive_application_id,
    owner_id,
    content_id,
    pet_id,
    customer_email,
    pet_name,
    pet_type,
    breed,
    reference_count,
    status,
    last_error,
    updated_at
  ) values (
    p_archive_application_id,
    p_owner_id,
    p_content_id,
    p_pet_id,
    lower(btrim(p_customer_email)),
    btrim(p_pet_name),
    nullif(btrim(p_pet_type), ''),
    nullif(btrim(p_breed), ''),
    p_reference_count,
    'REFERENCES_READY',
    null,
    now()
  )
  on conflict (archive_application_id) do update set
    owner_id = excluded.owner_id,
    content_id = excluded.content_id,
    pet_id = excluded.pet_id,
    customer_email = excluded.customer_email,
    pet_name = excluded.pet_name,
    pet_type = excluded.pet_type,
    breed = excluded.breed,
    reference_count = excluded.reference_count,
    status = case
      when existing.status in ('RECEIVED', 'REFERENCES_READY')
        then 'REFERENCES_READY'
      else existing.status
    end,
    last_error = case
      when existing.status in ('RECEIVED', 'REFERENCES_READY') then null
      else existing.last_error
    end,
    updated_at = now()
  returning existing.*;
$$;

-- Only one process may own expensive reference-pair preparation for an application. The
-- advisory lock serializes claim decisions in this transaction; PREPARING is
-- the durable lease after the transaction ends. A crashed claim can be
-- recovered after 15 minutes.
create or replace function public.claim_archive_intake_preparation(
  p_archive_application_id text
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
         updated_at = now()
   where ai.archive_application_id = p_archive_application_id
     and ai.generation_run_id is null
     and (
       ai.status in ('RECEIVED', 'REFERENCES_READY', 'GENERATION_FAILED')
       or (
         ai.status = 'PREPARING'
         and ai.updated_at < now() - interval '15 minutes'
       )
     )
  returning ai.*;
end;
$$;

revoke all on function public.upsert_archive_intake_metadata(
  text, text, text, text, text, text, text, text, integer
) from public, anon, authenticated;
grant execute on function public.upsert_archive_intake_metadata(
  text, text, text, text, text, text, text, text, integer
) to service_role;

revoke all on function public.claim_archive_intake_preparation(text)
  from public, anon, authenticated;
grant execute on function public.claim_archive_intake_preparation(text)
  to service_role;

comment on function public.claim_archive_intake_preparation(text) is
  'Atomically claims one Archive reference-pair preparation attempt with stale recovery.';
