-- Eternal Beam Exhibition / Pet Passport production preflight
--
-- READ-ONLY: this script contains catalog queries, aggregate reads, and one
-- read-only DO block for optional Passport tables. It does not call queue
-- allocators, registration RPCs, storage APIs, or any INSERT/UPDATE/DELETE.
--
-- Run manually in the Supabase SQL Editor for the confirmed production
-- project only after checking the project selector. Save every result set and
-- the execution timestamp with the release record.

-- 1. Identity and target objects
SELECT
  current_database() AS database_name,
  current_user AS executing_role,
  current_schema() AS current_schema,
  clock_timestamp() AS inspected_at;

SELECT
  n.nspname AS schema_name,
  c.relname AS object_name,
  CASE c.relkind
    WHEN 'r' THEN 'table'
    WHEN 'p' THEN 'partitioned_table'
    WHEN 'v' THEN 'view'
    WHEN 'm' THEN 'materialized_view'
    ELSE c.relkind::text
  END AS object_kind,
  c.relrowsecurity AS rls_enabled,
  c.relforcerowsecurity AS rls_forced,
  to_regclass(format('%I.%I', n.nspname, c.relname)) AS regclass
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN (
    'exhibition_prep_runs',
    'exhibition_queue_counters',
    'exhibition_queue_source_config',
    'exhibition_pet_passport_registrations'
  )
ORDER BY c.relname;

-- 2. Exact indexes and predicates. Compare these definitions with the
-- existing-display-queue migration before applying the Passport migrations.
SELECT
  tn.relname AS table_name,
  ixn.relname AS index_name,
  i.indisprimary AS is_primary,
  i.indisunique AS is_unique,
  i.indisvalid AS is_valid,
  pg_get_expr(i.indpred, i.indrelid) AS partial_predicate,
  pg_get_indexdef(i.indexrelid) AS exact_definition
FROM pg_index i
JOIN pg_class ixn ON ixn.oid = i.indexrelid
JOIN pg_class tn ON tn.oid = i.indrelid
JOIN pg_namespace n ON n.oid = tn.relnamespace
WHERE n.nspname = 'public'
  AND tn.relname IN (
    'exhibition_prep_runs',
    'exhibition_queue_counters',
    'exhibition_queue_source_config',
    'exhibition_pet_passport_registrations'
  )
ORDER BY tn.relname, ixn.relname;

-- 3. Relevant columns, constraints, and validation state
SELECT
  n.nspname AS schema_name,
  c.relname AS table_name,
  a.attnum AS ordinal_position,
  a.attname AS column_name,
  pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
  NOT a.attnotnull AS is_nullable,
  pg_get_expr(ad.adbin, ad.adrelid) AS default_expression
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
WHERE n.nspname = 'public'
  AND c.relname IN (
    'exhibition_prep_runs',
    'exhibition_queue_counters',
    'exhibition_queue_source_config',
    'exhibition_pet_passport_registrations'
  )
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY c.relname, a.attnum;

SELECT
  n.nspname AS schema_name,
  c.relname AS table_name,
  con.conname AS constraint_name,
  CASE con.contype
    WHEN 'p' THEN 'PRIMARY KEY'
    WHEN 'u' THEN 'UNIQUE'
    WHEN 'f' THEN 'FOREIGN KEY'
    WHEN 'c' THEN 'CHECK'
    WHEN 'x' THEN 'EXCLUSION'
    ELSE con.contype::text
  END AS constraint_type,
  con.convalidated AS is_validated,
  pg_get_constraintdef(con.oid, true) AS exact_definition
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN (
    'exhibition_prep_runs',
    'exhibition_queue_counters',
    'exhibition_queue_source_config',
    'exhibition_pet_passport_registrations'
  )
ORDER BY c.relname, con.conname;

-- 4. Exact trigger definitions
SELECT
  n.nspname AS schema_name,
  c.relname AS table_name,
  t.tgname AS trigger_name,
  t.tgenabled AS enabled_state,
  pg_get_triggerdef(t.oid, true) AS exact_definition
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE NOT t.tgisinternal
  AND n.nspname = 'public'
  AND c.relname IN (
    'exhibition_prep_runs',
    'exhibition_queue_counters',
    'exhibition_queue_source_config',
    'exhibition_pet_passport_registrations'
  )
ORDER BY c.relname, t.tgname;

-- 5. Existing and expected function definitions. This is metadata only; no
-- function is invoked by this script.
SELECT
  n.nspname AS schema_name,
  p.proname AS function_name,
  pg_get_function_identity_arguments(p.oid) AS identity_arguments,
  p.prosecdef AS security_definer,
  p.provolatile AS volatility,
  p.proconfig AS configuration,
  pg_get_functiondef(p.oid) AS exact_definition
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.proname IN (
    'set_exhibition_prep_runs_updated_at',
    'set_exhibition_pet_passport_updated_at',
    'exhibition_next_queue_number',
    'exhibition_queue_lock_key',
    'exhibition_queue_assert_number_available',
    'exhibition_allocate_queue_number',
    'exhibition_register_pet_passport',
    'exhibition_get_pet_passport',
    'exhibition_attach_pet_passport_photo',
    'exhibition_get_pet_passport_photo_ref'
  )
ORDER BY p.proname, pg_get_function_identity_arguments(p.oid);

-- 6. Table grants, function grants, RLS policies, and role visibility
SELECT
  table_schema,
  table_name,
  grantee,
  privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
  AND table_name IN (
    'exhibition_prep_runs',
    'exhibition_queue_counters',
    'exhibition_queue_source_config',
    'exhibition_pet_passport_registrations'
  )
ORDER BY table_name, grantee, privilege_type;

SELECT
  routine_schema,
  routine_name,
  specific_name,
  grantee,
  privilege_type
FROM information_schema.routine_privileges
WHERE routine_schema = 'public'
  AND routine_name IN (
    'exhibition_next_queue_number',
    'exhibition_queue_lock_key',
    'exhibition_queue_assert_number_available',
    'exhibition_allocate_queue_number',
    'exhibition_register_pet_passport',
    'exhibition_get_pet_passport',
    'exhibition_attach_pet_passport_photo',
    'exhibition_get_pet_passport_photo_ref'
  )
ORDER BY routine_name, specific_name, grantee, privilege_type;

SELECT
  schemaname,
  tablename,
  policyname,
  roles,
  cmd,
  qual,
  with_check
FROM pg_policies
WHERE schemaname = 'public'
  AND tablename IN (
    'exhibition_prep_runs',
    'exhibition_queue_counters',
    'exhibition_queue_source_config',
    'exhibition_pet_passport_registrations'
  )
ORDER BY tablename, policyname;

-- 7. Required extension and built-in function availability
SELECT
  name,
  default_version,
  installed_version
FROM pg_available_extensions
WHERE name = 'pgcrypto';

SELECT
  n.nspname AS schema_name,
  p.oid::regprocedure AS matching_procedure
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE (n.nspname, p.proname) IN (
    ('pg_catalog', 'hashtextextended'),
    ('pg_catalog', 'pg_advisory_xact_lock'),
    ('pg_catalog', 'gen_random_uuid')
  )
ORDER BY p.proname, p.oid::regprocedure::text;

-- 8. Private storage metadata and aggregate Exhibition object inventory.
-- Object names are intentionally not returned by this preflight.
SELECT id, name, public, file_size_limit, allowed_mime_types
FROM storage.buckets
WHERE id = 'user-assets';

SELECT
  bucket_id,
  COUNT(*) AS object_count,
  MIN(created_at) AS oldest_object,
  MAX(updated_at) AS newest_object
FROM storage.objects
WHERE bucket_id = 'user-assets'
  AND name LIKE 'exhibition/%'
GROUP BY bucket_id;

SELECT
  schemaname,
  tablename,
  policyname,
  roles,
  cmd,
  qual,
  with_check
FROM pg_policies
WHERE schemaname = 'storage'
  AND tablename IN ('buckets', 'objects')
ORDER BY tablename, policyname;

-- 9. Existing queue counters and maximum assigned Exhibition numbers
WITH run_max AS (
  SELECT exhibition_id, MAX(queue_number) AS max_run_queue_number
  FROM public.exhibition_prep_runs
  WHERE exhibition_id IS NOT NULL AND queue_number IS NOT NULL
  GROUP BY exhibition_id
)
SELECT
  c.exhibition_id,
  c.last_number,
  COALESCE(r.max_run_queue_number, 0) AS max_run_queue_number,
  CASE
    WHEN c.last_number < COALESCE(r.max_run_queue_number, 0) THEN 'COUNTER_BEHIND_RUNS'
    ELSE 'OK'
  END AS counter_check
FROM public.exhibition_queue_counters c
LEFT JOIN run_max r ON r.exhibition_id = c.exhibition_id
ORDER BY c.exhibition_id;

-- 10. Existing Exhibition queue consistency checks
SELECT exhibition_id, queue_number, COUNT(*) AS duplicate_count
FROM public.exhibition_prep_runs
WHERE exhibition_id IS NOT NULL AND queue_number IS NOT NULL
GROUP BY exhibition_id, queue_number
HAVING COUNT(*) > 1
ORDER BY exhibition_id, queue_number;

SELECT id, exhibition_id, queue_number, display_status, status, error_code
FROM public.exhibition_prep_runs
WHERE (queue_number IS NULL) <> (display_status IS NULL)
   OR (queue_number IS NOT NULL AND exhibition_id IS NULL)
   OR (queue_number IS NOT NULL AND queue_number <= 0)
ORDER BY exhibition_id, queue_number, id;

SELECT exhibition_id, display_status, COUNT(*) AS row_count
FROM public.exhibition_prep_runs
WHERE display_status IN ('UP_NEXT', 'NOW_SHOWING')
GROUP BY exhibition_id, display_status
HAVING COUNT(*) > 1
ORDER BY exhibition_id, display_status;

-- 11. Optional Passport checks. This block is safe before the Passport table
-- exists and reports findings through SQL Editor NOTICE messages. It never
-- selects email, token hashes, or visitor rows.
DO $$
DECLARE
  v_count BIGINT;
BEGIN
  IF to_regclass('public.exhibition_pet_passport_registrations') IS NULL THEN
    RAISE NOTICE 'PASSPORT_TABLE: ABSENT';
    RETURN;
  END IF;

  SELECT COUNT(*) INTO v_count
  FROM public.exhibition_pet_passport_registrations;
  RAISE NOTICE 'PASSPORT_REGISTRATION_COUNT: %', v_count;

  SELECT COUNT(*) INTO v_count
  FROM public.exhibition_pet_passport_registrations
  WHERE (photo_status = 'PHOTO_RECEIVED' AND (photo_run_id IS NULL OR photo_received_at IS NULL))
     OR (photo_status = 'WAITING_FOR_PHOTO' AND (photo_run_id IS NOT NULL OR photo_received_at IS NOT NULL));
  RAISE NOTICE 'PASSPORT_STATUS_INCONSISTENCIES: %', v_count;

  SELECT COUNT(*) INTO v_count
  FROM (
    SELECT exhibition_id, queue_number
    FROM public.exhibition_pet_passport_registrations
    GROUP BY exhibition_id, queue_number
    HAVING COUNT(*) > 1
  ) duplicates;
  RAISE NOTICE 'PASSPORT_INTERNAL_QUEUE_COLLISIONS: %', v_count;

  SELECT COUNT(*) INTO v_count
  FROM public.exhibition_pet_passport_registrations p
  JOIN public.exhibition_prep_runs r
    ON r.exhibition_id = p.exhibition_id
   AND r.queue_number = p.queue_number;
  RAISE NOTICE 'CROSS_TABLE_QUEUE_COLLISIONS: %', v_count;

  SELECT COUNT(*) INTO v_count
  FROM public.exhibition_pet_passport_registrations p
  LEFT JOIN public.exhibition_prep_runs r ON r.id = p.photo_run_id
  WHERE p.photo_status = 'PHOTO_RECEIVED'
    AND (r.id IS NULL OR r.exhibition_id IS DISTINCT FROM p.exhibition_id);
  RAISE NOTICE 'PASSPORT_BROKEN_PHOTO_REFERENCES: %', v_count;
END;
$$;

-- 12. If exhibition_queue_source_config exists, inspect rollout modes only.
-- This reports configuration; it does not change it. It is conditional because
-- the table is expected to be absent before the Step 1 migration.
DO $$
DECLARE
  v_row RECORD;
BEGIN
  IF to_regclass('public.exhibition_queue_source_config') IS NULL THEN
    RAISE NOTICE 'QUEUE_SOURCE_CONFIG_TABLE: ABSENT';
    RETURN;
  END IF;

  FOR v_row IN EXECUTE
    'SELECT exhibition_id, mode, updated_at
       FROM public.exhibition_queue_source_config
      ORDER BY exhibition_id'
  LOOP
    RAISE NOTICE 'QUEUE_SOURCE_CONFIG: %', row_to_json(v_row);
  END LOOP;
END;
$$;
