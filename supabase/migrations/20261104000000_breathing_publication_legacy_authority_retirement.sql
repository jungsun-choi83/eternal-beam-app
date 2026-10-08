-- Retire legacy QA authority for BREATHING publication.
--
-- Stacks on 20261103000000. Adds an optional p_qa_authority argument:
--   'business' -> only the candidate's business-v1 receipt decides. This closes
--                 the hole where a legacy PASS published without the receipt
--                 being checked. A missing receipt is refused, except the
--                 grandfathered direct-publish case below.
--   null       -> the mode stamped on the generation run at creation
--                 (provider_state._business_qa_cutover.breathing_qa_authority),
--                 else 'legacy' (behavior of 20261103000000, unchanged).
-- Existing six-argument callers keep working through the default.
-- Apply this BEFORE setting BREATHING_QA_AUTHORITY=business.
-- Rollback: drop this function and re-run 20261103000000.

drop function if exists public.publish_phase6_breathing(text, text, uuid, uuid, text, text);

create or replace function public.publish_phase6_breathing(
  p_user_id text,
  p_pet_id text,
  p_motion_version_id uuid,
  p_selected_candidate_id uuid,
  p_bucket text,
  p_object_path text,
  p_qa_authority text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_version public.pet_motion_versions%rowtype;
  v_candidate public.pet_motion_candidates%rowtype;
  v_existing public.pet_motion_publications%rowtype;
  v_pet public.pets%rowtype;
  v_current_version int;
  v_receipt jsonb;
  v_enrolled boolean := false;
  v_run_mode text;
  v_mode text;
  v_delivers boolean := false;
  v_grandfathered boolean := false;
begin
  if nullif(trim(p_user_id), '') is null
     or nullif(trim(p_pet_id), '') is null
     or nullif(trim(p_bucket), '') is null
     or nullif(trim(p_object_path), '') is null then
    raise exception 'PHASE7A_INVALID';
  end if;

  select * into v_version
    from public.pet_motion_versions
   where id = p_motion_version_id
   for update;
  if not found then
    raise exception 'MOTION_VERSION_NOT_FOUND';
  end if;
  if v_version.user_id <> p_user_id or v_version.pet_id <> p_pet_id then
    raise exception 'PET_NOT_OWNED';
  end if;
  if v_version.motion_id <> 'BREATHING' then
    raise exception 'BREATHING_REQUIRED';
  end if;
  if v_version.status <> 'complete' then
    raise exception 'MOTION_NOT_PUBLISHABLE:%', v_version.status;
  end if;
  if v_version.selected_candidate_id is null
     or v_version.selected_candidate_id <> p_selected_candidate_id then
    raise exception 'SELECTED_CANDIDATE_MISSING';
  end if;

  select * into v_candidate
    from public.pet_motion_candidates
   where id = p_selected_candidate_id
   for share;
  if not found
     or v_candidate.motion_version_id <> v_version.id
     or v_candidate.user_id <> p_user_id
     or v_candidate.pet_id <> p_pet_id
     or v_candidate.motion_id <> 'BREATHING'
     or v_candidate.selected is not true then
    raise exception 'SELECTED_CANDIDATE_MISSING';
  end if;
  -- QA authority for this publication. The caller's explicit mode wins; else
  -- the mode stamped on the generation run at creation; else legacy.
  select r.provider_state -> '_business_qa_cutover' ->> 'breathing_qa_authority'
    into v_run_mode
    from public.pet_generation_runs r
   where r.motion_version_id = v_version.id
     and r.user_id = p_user_id
   order by r.created_at desc
   limit 1;
  v_mode := coalesce(nullif(trim(coalesce(p_qa_authority, '')), ''), v_run_mode, 'legacy');

  if v_mode = 'business' then
    -- Legacy authority retired: the business-v1 receipt alone decides. A legacy
    -- PASS whose receipt blocks is refused; a legacy FAIL whose receipt delivers
    -- is published. The legacy decision column is telemetry only.
    v_receipt := coalesce(v_candidate.qa_result -> 'business_qa', '{}'::jsonb);
    v_delivers :=
      v_receipt ->> 'version' = 'business-v1'
      and v_receipt ->> 'delivery_action' in ('DELIVER', 'DELIVER_WITH_ADVISORY')
      and coalesce(v_receipt ->> 'integrity_status', 'FAIL') <> 'FAIL';
    -- Direct (no business-stamped run) publication of an already-approved
    -- historical video: legacy PASS, no receipt, created before the cutover.
    v_grandfathered :=
      v_run_mode is distinct from 'business'
      and v_candidate.decision = 'PASS'
      and (v_candidate.qa_result -> 'business_qa') is null
      and v_candidate.created_at < timestamptz '2026-10-02T15:52:00+00:00';
    if not (v_delivers or v_grandfathered) then
      raise exception 'CANDIDATE_NOT_PASS:%', v_candidate.decision;
    end if;
  else
    -- Legacy mode: unchanged from 20261103000000.
    if v_candidate.decision <> 'PASS' then
      -- Business QA cutover: the legacy decision column stays as telemetry. A
      -- non-PASS candidate is publishable only when its run is enrolled and its
      -- business-v1 receipt authorizes delivery without an integrity failure.
      v_receipt := coalesce(v_candidate.qa_result -> 'business_qa', '{}'::jsonb);
      select exists (
        select 1
          from public.pet_generation_runs r
         where r.motion_version_id = v_version.id
           and r.user_id = p_user_id
           and r.provider_state -> '_business_qa_cutover' ->> 'enrolled' = 'true'
      ) into v_enrolled;
      if not (
        v_enrolled
        and v_receipt ->> 'version' = 'business-v1'
        and v_receipt ->> 'delivery_action' in ('DELIVER', 'DELIVER_WITH_ADVISORY')
        and coalesce(v_receipt ->> 'integrity_status', 'FAIL') <> 'FAIL'
      ) then
        raise exception 'CANDIDATE_NOT_PASS:%', v_candidate.decision;
      end if;
    end if;
  end if;

  select * into v_existing
    from public.pet_motion_publications
   where motion_version_id = p_motion_version_id;
  if found then
    if v_existing.selected_candidate_id <> p_selected_candidate_id
       or v_existing.user_id <> p_user_id
       or v_existing.pet_id <> p_pet_id
       or v_existing.bucket <> p_bucket
       or v_existing.object_path <> p_object_path then
      raise exception 'PUBLICATION_CONFLICT';
    end if;
    return jsonb_build_object(
      'publication_id', v_existing.id,
      'published_at', v_existing.published_at,
      'deduplicated', true
    );
  end if;

  select * into v_pet from public.pets where pet_id = p_pet_id for update;
  if found and v_pet.user_id <> p_user_id then
    raise exception 'PET_NOT_OWNED';
  end if;

  if found and v_pet.breathing_motion_version_id is not null then
    select motion_version into v_current_version
      from public.pet_motion_publications
     where motion_version_id = v_pet.breathing_motion_version_id;
    if v_current_version is not null and v_current_version > v_version.version then
      raise exception 'STALE_MOTION_VERSION';
    end if;
  end if;

  insert into public.pet_motion_publications (
    motion_version_id, selected_candidate_id, user_id, pet_id, motion_id,
    motion_version, bucket, object_path, background_baked
  ) values (
    v_version.id, v_candidate.id, p_user_id, p_pet_id, 'BREATHING',
    v_version.version, p_bucket, p_object_path, false
  ) returning * into v_existing;

  insert into public.pets (
    pet_id, user_id, content_id, breathing_bucket, breathing_object_path,
    breathing_motion_version_id, source, background_baked, created_at, updated_at
  ) values (
    p_pet_id, p_user_id,
    case when p_pet_id like 'pet_%' then substring(p_pet_id from 5) else null end,
    p_bucket, p_object_path, v_version.id, 'app', false, now(), now()
  )
  on conflict (pet_id) do update set
    breathing_bucket = excluded.breathing_bucket,
    breathing_object_path = excluded.breathing_object_path,
    breathing_motion_version_id = excluded.breathing_motion_version_id,
    background_baked = false,
    updated_at = now()
  where public.pets.user_id = excluded.user_id;

  if not found then
    raise exception 'PET_NOT_OWNED';
  end if;

  return jsonb_build_object(
    'publication_id', v_existing.id,
    'published_at', v_existing.published_at,
    'deduplicated', false
  );
end;
$$;

-- 브라우저는 이 함수를 직접 부르지 않는다. 검증된 JWT를 받는 FastAPI가 service-role
-- 연결로만 호출한다.
revoke all on function public.publish_phase6_breathing(text, text, uuid, uuid, text, text, text)
  from public, anon, authenticated;
grant execute on function public.publish_phase6_breathing(text, text, uuid, uuid, text, text, text)
  to service_role;
