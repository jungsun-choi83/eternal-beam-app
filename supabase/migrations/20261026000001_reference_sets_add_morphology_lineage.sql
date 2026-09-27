-- Phase 3 morphology lineage pinning on reference sets.

alter table public.pet_reference_sets
  add column if not exists morphology_profile_id uuid references public.pet_morphology_profiles(id),
  add column if not exists morphology_profile_version int;

comment on column public.pet_reference_sets.morphology_profile_id is
  '이 세트가 근거로 사용한 형태 프로필 id (append-only lineage pin)';

comment on column public.pet_reference_sets.morphology_profile_version is
  '이 세트가 근거로 사용한 형태 프로필 version';
