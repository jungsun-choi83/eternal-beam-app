-- Eternal Beam Shaker V2 — versioned layered playback assets.
--
-- This table EXTENDS the existing V1 share contract.  It does not replace
-- public.pets.breathing_* or public.shaker_shares.breathing_*.
-- Canonical values are bucket/object paths, never expiring signed URLs.

create table if not exists public.shaker_layered_assets (
  asset_id                 text primary key,
  user_id                  text not null,
  -- Registration is authenticated and may complete after the V1 HTTP response;
  -- the non-blocking V2 worker must not race/fail on that timing. Binding is
  -- enforced by READY manifest lookup against owner + pet + scene.
  pet_id                   text not null,
  content_id               text not null,
  scene_id                 text not null,
  asset_version            text not null,

  status                   text not null default 'PROCESSING',

  pet_bucket               text,
  pet_object_path          text,
  pet_encoding             text,
  alpha_layout             text,

  background_type          text,
  background_bucket        text,
  background_object_path   text,

  -- Production-derived assets use {"mode":"scene-frame"}; future direct
  -- pet-only assets may add normalized placement/crop values without changing
  -- the public share or QR contract.
  placement                jsonb not null default '{"mode":"scene-frame"}'::jsonb,

  -- Optional rendering metadata/assets.  A CSS shadow has no storage path.
  shadow                   jsonb,
  foreground_type          text,
  foreground_bucket        text,
  foreground_object_path   text,

  qa                       jsonb,
  error                    text,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now(),
  ready_at                 timestamptz,

  unique (pet_id, scene_id, asset_version),

  constraint shaker_layered_assets_canonical_pet_check
    check (pet_id = 'pet_' || content_id),

  constraint shaker_layered_assets_status_check
    check (status in ('PROCESSING', 'READY', 'FAILED')),
  constraint shaker_layered_assets_background_type_check
    check (background_type is null or background_type in ('image', 'video')),
  constraint shaker_layered_assets_foreground_type_check
    check (foreground_type is null or foreground_type in ('image', 'video')),
  constraint shaker_layered_assets_encoding_check
    check (pet_encoding is null or pet_encoding in ('packed-vstack-h264')),
  constraint shaker_layered_assets_alpha_layout_check
    check (alpha_layout is null or alpha_layout in ('rgb-top-alpha-bottom')),

  -- READY is an atomic publication state: no partially populated row can be
  -- selected by the public Shaker service.
  constraint shaker_layered_assets_ready_complete_check check (
    status <> 'READY' or (
      coalesce(length(trim(pet_bucket)) > 0, false) and
      coalesce(length(trim(pet_object_path)) > 0, false) and
      coalesce(pet_encoding = 'packed-vstack-h264', false) and
      coalesce(alpha_layout = 'rgb-top-alpha-bottom', false) and
      coalesce(background_type in ('image', 'video'), false) and
      coalesce(length(trim(background_bucket)) > 0, false) and
      coalesce(length(trim(background_object_path)) > 0, false) and
      jsonb_typeof(placement) = 'object' and
      placement->>'mode' in ('scene-frame', 'anchored') and
      (
        foreground_type is null or (
          coalesce(length(trim(foreground_bucket)) > 0, false) and
          coalesce(length(trim(foreground_object_path)) > 0, false)
        )
      ) and
      ready_at is not null and
      coalesce(qa @> '{"passed": true}'::jsonb, false)
    )
  )
);

create index if not exists shaker_layered_assets_ready_scene_idx
  on public.shaker_layered_assets (pet_id, scene_id, created_at desc)
  where status = 'READY';

create index if not exists shaker_layered_assets_owner_idx
  on public.shaker_layered_assets (user_id, pet_id, created_at desc);

-- Two simultaneous V1 completion requests must not start two expensive matte
-- jobs for the same scene. FAILED can retry and READY versions remain history.
create unique index if not exists shaker_layered_assets_one_processing_scene_idx
  on public.shaker_layered_assets (user_id, pet_id, scene_id)
  where status = 'PROCESSING';

alter table public.shaker_layered_assets enable row level security;
revoke all on table public.shaker_layered_assets from anon, authenticated;
grant all on table public.shaker_layered_assets to service_role;

comment on table public.shaker_layered_assets is
  'READY-only versioned manifests for optional layered Shaker playback. V1 BREATHING remains canonical fallback.';
comment on column public.shaker_layered_assets.asset_version is
  'Immutable version token included in every V2 storage object path.';
comment on column public.shaker_layered_assets.status is
  'Public Shaker may expose only complete READY rows; PROCESSING/FAILED always fall back to V1.';

-- New shares can bind to a scene without changing the QR/token format. Existing
-- rows remain NULL and therefore stay on V1 until explicitly reissued.
alter table public.shaker_shares
  add column if not exists scene_id text;
alter table public.shaker_shares
  add column if not exists layered_asset_id text
    references public.shaker_layered_assets(asset_id) on delete set null;

comment on column public.shaker_shares.scene_id is
  'Optional canonical scene snapshot for safe READY V2 lookup. NULL legacy shares remain V1.';
comment on column public.shaker_shares.layered_asset_id is
  'Optional immutable V2 manifest snapshot. QR token/path remains unchanged.';
