# Pet Passport production preflight

The read-only SQL preflight is:

[`supabase/preflight/20261110_exhibition_pet_passport_production_preflight.sql`](../supabase/preflight/20261110_exhibition_pet_passport_production_preflight.sql)

For Supabase SQL Editor, use the single-result version so all diagnostics are
returned in one visible table:

[`supabase/preflight/20261110_exhibition_pet_passport_production_preflight_single_result.sql`](../supabase/preflight/20261110_exhibition_pet_passport_production_preflight_single_result.sql)

## Manual execution

1. Open the Supabase dashboard and select project `kdlukiujgclczwqmwvmk`.
2. Open SQL Editor and create a new query.
3. Paste the SQL file exactly as stored in the branch.
4. Confirm the project selector and role before pressing Run.
5. Save every result set and `NOTICE` message with the inspection timestamp.

The script is read-only. It does not call queue allocators, registration RPCs, storage APIs, or write statements. It handles the expected absence of the Passport tables and queue-source configuration with `NOTICE` messages.

## Render verification checklist

- Record the deployed API service name, commit/revision, build timestamp, and health response.
- Confirm the API revision contains the Passport registration, photo-association, and photo-access routes.
- Confirm the Exhibition worker is suspended before migration and record its deployed revision.
- Identify every production queue writer, including older API instances, scheduled jobs, scripts, and operator tools.
- Confirm no old writer can allocate numbers after `PASSPORT` mode is enabled.
- Schedule a maintenance window that prevents concurrent queue allocation and photo submissions during migration and verification.
- Restart the worker only after schema verification and compatible API deployment.

## Backup minimum

Database backup:

- Managed database backup or snapshot before migration.
- Export the relevant table definitions, function definitions, grants, RLS state, indexes, constraints, counters, and queue maxima.
- Record the `expo-test-01` counter and maximum assigned queue number immediately before migration.

Private storage backup:

- Separately inventory all `user-assets` objects under the Exhibition prefix.
- Preserve the object paths and confirm the source objects for all existing Exhibition runs are recoverable.
- A database backup alone does not restore Supabase Storage objects.

## GO / NO-GO

GO only if:

- The exact existing index and trigger definitions match the migration assumptions or have an approved compatibility correction.
- Required columns, allocator function, `pgcrypto`, advisory locking, grants, and service-role execution are verified.
- Anonymous/authenticated access remains denied and the private bucket remains service-role-only.
- No queue collisions or counter drift are reported.
- The deployed API and worker revisions are compatible with all three migrations.
- Database and private-storage backups are complete and restoration responsibility is clear.

NO-GO if any result is missing, if an old queue writer remains active, if the worker cannot be suspended, if storage recovery is unverified, or if the build/API revision does not contain the Step 1–5A implementation.
