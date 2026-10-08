-- Archive -> Eternal Beam intake mapping.
--
-- One row represents one Archive application transferred into Eternal Beam.
-- Customer email is kept separate from Eternal Beam's internal owner_id.

create table if not exists public.archive_intakes (
    archive_application_id text primary key
        check (archive_application_id ~ '^[A-Za-z0-9_-]{1,128}$'),

    -- Eternal Beam internal identifiers.
    owner_id text not null unique
        check (owner_id = 'archive_' || archive_application_id),
    content_id text not null unique
        check (content_id = 'archive_' || archive_application_id),
    pet_id text not null unique
        check (pet_id = 'pet_archive_' || archive_application_id),

    -- Applicant / pet information coming from Archive.
    customer_email text not null
        check (length(customer_email) between 3 and 254),
    pet_name text not null
        check (length(btrim(pet_name)) between 1 and 200),
    pet_type text
        check (pet_type is null or length(btrim(pet_type)) between 1 and 200),
    breed text
        check (breed is null or length(btrim(breed)) between 1 and 200),

    -- Number of currently transferred original reference photos.
    reference_count integer not null default 0
        check (reference_count >= 0 and reference_count <= 3),

    -- Integration / generation lifecycle.
    status text not null default 'RECEIVED'
        check (
            status in (
                'RECEIVED',
                'REFERENCES_READY',
                'GENERATION_QUEUED',
                'GENERATING',
                'COMPLETED',
                'FAILED'
            )
        ),

    generation_run_id text,
    result_url text,
    last_error text,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists archive_intakes_status_idx
    on public.archive_intakes (status);

create index if not exists archive_intakes_customer_email_idx
    on public.archive_intakes (customer_email);

-- This table contains customer information and must never be directly
-- accessible from the browser.
alter table public.archive_intakes enable row level security;

-- RLS has deliberately no anon/authenticated policies. Revoke table grants as
-- defense in depth and grant only the backend service role used by this intake.
revoke all on table public.archive_intakes from public, anon, authenticated;
grant select, insert, update, delete on table public.archive_intakes to service_role;

comment on table public.archive_intakes is
    'Server-only Archive application intake metadata. Contains customer PII; no browser policies.';
