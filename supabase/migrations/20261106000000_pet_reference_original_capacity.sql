-- Enforce the shared pet-reference invariant at the database boundary.
--
-- Application checks remain useful for early errors and mock mode, but they
-- cannot serialize separate API workers. A transaction-scoped advisory lock
-- makes the accepted-original count and the INSERT/reactivation one critical
-- section per pet.

create or replace function public.enforce_pet_reference_original_capacity()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_active_count integer;
begin
  -- Exact values from pet_reference_images and pet_reference_service:
  -- role='original', acceptance_state='accepted', maximum=3.
  if new.role <> 'original' or new.acceptance_state <> 'accepted' then
    return new;
  end if;

  -- An already-active original that remains active for the same pet consumes
  -- no additional slot. This also keeps harmless metadata updates lock-free.
  if tg_op = 'UPDATE'
     and old.role = 'original'
     and old.acceptance_state = 'accepted'
     and old.pet_id = new.pet_id then
    return new;
  end if;

  perform pg_advisory_xact_lock(hashtextextended(new.pet_id, 0));

  -- Let the existing (pet_id, content_hash) unique index handle an INSERT
  -- retry. The backend then reloads and returns the existing reference rather
  -- than turning a duplicate at capacity into a limit error.
  if tg_op = 'INSERT'
     and new.content_hash is not null
     and exists (
       select 1
       from public.pet_reference_images pri
       where pri.pet_id = new.pet_id
         and pri.role = 'original'
         and pri.content_hash = new.content_hash
     ) then
    return new;
  end if;

  if tg_op = 'UPDATE' then
    select count(*)
      into v_active_count
      from public.pet_reference_images pri
     where pri.pet_id = new.pet_id
       and pri.role = 'original'
       and pri.acceptance_state = 'accepted'
       and pri.id <> old.id;
  else
    select count(*)
      into v_active_count
      from public.pet_reference_images pri
     where pri.pet_id = new.pet_id
       and pri.role = 'original'
       and pri.acceptance_state = 'accepted';
  end if;

  if v_active_count >= 3 then
    raise exception using
      errcode = 'P0001',
      message = 'PET_REFERENCE_ORIGINAL_LIMIT',
      detail = 'A pet can have at most 3 active original references.';
  end if;

  return new;
end;
$$;

drop trigger if exists pet_reference_images_original_capacity_insert_trg
  on public.pet_reference_images;
drop trigger if exists pet_reference_images_original_capacity_update_trg
  on public.pet_reference_images;

create trigger pet_reference_images_original_capacity_insert_trg
before insert
on public.pet_reference_images
for each row
execute function public.enforce_pet_reference_original_capacity();

create trigger pet_reference_images_original_capacity_update_trg
before update of pet_id, role, acceptance_state
on public.pet_reference_images
for each row
execute function public.enforce_pet_reference_original_capacity();

comment on function public.enforce_pet_reference_original_capacity() is
  'Serializes each pet active-original mutation and enforces at most 3 accepted originals.';
