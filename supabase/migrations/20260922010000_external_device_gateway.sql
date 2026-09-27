-- New Eternal Beam external Pi/APK transport.
-- Device credentials remain deployment secrets; these tables hold delivery
-- state only.  The payload is JSON so the contract can evolve additively.

create table if not exists public.device_connection_state (
  device_id text primary key,
  online boolean not null default false,
  connected_at timestamptz,
  last_seen timestamptz,
  last_ack text,
  updated_at timestamptz not null default now()
);

create table if not exists public.device_commands (
  command_id uuid primary key,
  device_id text not null,
  event text not null check (event in ('theme_play', 'pet_asset')),
  payload jsonb not null,
  status text not null default 'pending' check (status in ('pending', 'acked')),
  created_at timestamptz not null default now(),
  acked_at timestamptz
);

create index if not exists device_commands_pending_idx
  on public.device_commands (device_id, status, created_at);
