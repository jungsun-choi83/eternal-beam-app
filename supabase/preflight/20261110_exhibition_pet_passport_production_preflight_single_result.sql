-- Eternal Beam Exhibition / Pet Passport single-result production preflight
--
-- READ-ONLY. This is one SQL statement so Supabase SQL Editor shows one
-- visible result table instead of discarding earlier result sets. It performs
-- catalog reads and aggregate reads only. It does not call allocators,
-- registration/photo RPCs, or storage APIs, and contains no write statements.
--
-- Run in the Supabase SQL Editor after selecting project
-- kdlukiujgclczwqmwvmk. A missing object or permission is deliberately
-- reported as MISSING_OR_NOT_VISIBLE, NOT_RUN, or UNVERIFIED; never as PASS.

WITH
expected_objects(object_name) AS (
  VALUES
    ('exhibition_prep_runs'::text),
    ('exhibition_queue_counters'::text),
    ('exhibition_queue_source_config'::text),
    ('exhibition_pet_passport_registrations'::text)
),
expected_tables(table_name) AS (
  SELECT object_name FROM expected_objects
),
expected_indexes(table_name, index_name) AS (
  VALUES
    ('exhibition_prep_runs', 'idx_exhibition_prep_runs_status_created'),
    ('exhibition_prep_runs', 'idx_exhibition_prep_runs_exhibition'),
    ('exhibition_prep_runs', 'idx_exhibition_prep_runs_handoff'),
    ('exhibition_prep_runs', 'uq_exhibition_prep_runs_queue_number'),
    ('exhibition_prep_runs', 'uq_exhibition_prep_runs_up_next'),
    ('exhibition_prep_runs', 'uq_exhibition_prep_runs_now_showing'),
    ('exhibition_prep_runs', 'idx_exhibition_prep_runs_display_queue'),
    ('exhibition_pet_passport_registrations', 'idx_exhibition_pet_passport_photo_status')
),
expected_triggers(table_name, trigger_name) AS (
  VALUES
    ('exhibition_prep_runs', 'trg_exhibition_prep_runs_updated_at'),
    ('exhibition_prep_runs', 'trg_exhibition_prep_runs_queue_number_available'),
    ('exhibition_pet_passport_registrations', 'trg_exhibition_pet_passport_updated_at'),
    ('exhibition_pet_passport_registrations', 'trg_exhibition_pet_passport_queue_number_available')
),
expected_functions(function_name, function_signature) AS (
  VALUES
    ('set_exhibition_prep_runs_updated_at', 'public.set_exhibition_prep_runs_updated_at()'),
    ('set_exhibition_pet_passport_updated_at', 'public.set_exhibition_pet_passport_updated_at()'),
    ('exhibition_next_queue_number', 'public.exhibition_next_queue_number(text)'),
    ('exhibition_queue_lock_key', 'public.exhibition_queue_lock_key(text)'),
    ('exhibition_queue_assert_number_available', 'public.exhibition_queue_assert_number_available()'),
    ('exhibition_allocate_queue_number', 'public.exhibition_allocate_queue_number(text, text)'),
    ('exhibition_register_pet_passport', 'public.exhibition_register_pet_passport(text, text, text, boolean, text, text, text)'),
    ('exhibition_get_pet_passport', 'public.exhibition_get_pet_passport(text)'),
    ('exhibition_attach_pet_passport_photo', 'public.exhibition_attach_pet_passport_photo(text, integer, uuid)'),
    ('exhibition_get_pet_passport_photo_ref', 'public.exhibition_get_pet_passport_photo_ref(text)')
),
actual_indexes AS (
  SELECT
    tn.relname AS table_name,
    ixn.relname AS index_name,
    format(
      'unique=%s; primary=%s; valid=%s; predicate=%s; definition=%s',
      i.indisunique,
      i.indisprimary,
      i.indisvalid,
      COALESCE(pg_get_expr(i.indpred, i.indrelid), '<none>'),
      pg_get_indexdef(i.indexrelid)
    ) AS detail
  FROM pg_index i
  JOIN pg_class ixn ON ixn.oid = i.indexrelid
  JOIN pg_class tn ON tn.oid = i.indrelid
  JOIN pg_namespace n ON n.oid = tn.relnamespace
  WHERE n.nspname = 'public'
    AND tn.relname IN (SELECT table_name FROM expected_tables)
),
actual_triggers AS (
  SELECT
    c.relname AS table_name,
    t.tgname AS trigger_name,
    format('enabled=%s; %s', t.tgenabled, pg_get_triggerdef(t.oid, true)) AS detail
  FROM pg_trigger t
  JOIN pg_class c ON c.oid = t.tgrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE NOT t.tgisinternal
    AND n.nspname = 'public'
    AND c.relname IN (SELECT table_name FROM expected_tables)
),
actual_functions AS (
  SELECT
    p.proname AS function_name,
    pg_get_function_identity_arguments(p.oid) AS identity_arguments,
    p.oid,
    format(
      'security_definer=%s; volatility=%s; config=%s; definition=%s',
      p.prosecdef,
      p.provolatile,
      COALESCE(array_to_string(p.proconfig, ', '), '<none>'),
      pg_get_functiondef(p.oid)
    ) AS detail
  FROM pg_proc p
  JOIN pg_namespace n ON n.oid = p.pronamespace
  WHERE n.nspname = 'public'
),
table_grants AS (
  SELECT table_name, grantee, privilege_type
  FROM information_schema.table_privileges
  WHERE table_schema = 'public'
    AND table_name IN (SELECT table_name FROM expected_tables)
),
routine_grants AS (
  SELECT routine_name, grantee, privilege_type
  FROM information_schema.routine_privileges
  WHERE routine_schema = 'public'
),
policy_counts AS (
  SELECT
    e.table_name,
    COUNT(p.policyname)::bigint AS policy_count
  FROM expected_tables e
  LEFT JOIN pg_policies p
    ON p.schemaname = 'public'
   AND p.tablename = e.table_name
  GROUP BY e.table_name
),
actual_policies AS (
  SELECT
    schemaname,
    tablename,
    policyname,
    format('roles=%s; cmd=%s; using=%s; with_check=%s', roles, cmd, COALESCE(qual, '<none>'), COALESCE(with_check, '<none>')) AS detail
  FROM pg_policies
  WHERE (schemaname = 'public' AND tablename IN (SELECT table_name FROM expected_tables))
     OR (schemaname = 'storage' AND tablename IN ('buckets', 'objects'))
),
run_max AS (
  SELECT exhibition_id, MAX(queue_number) AS max_run_queue_number
  FROM public.exhibition_prep_runs
  WHERE exhibition_id IS NOT NULL AND queue_number IS NOT NULL
  GROUP BY exhibition_id
),
counter_ids AS (
  SELECT exhibition_id FROM public.exhibition_queue_counters
  UNION
  SELECT exhibition_id FROM public.exhibition_prep_runs
  WHERE exhibition_id IS NOT NULL
),
counter_checks AS (
  SELECT
    i.exhibition_id,
    c.last_number,
    COALESCE(r.max_run_queue_number, 0) AS max_run_queue_number,
    CASE
      WHEN c.exhibition_id IS NULL THEN 'FAIL'
      WHEN c.last_number < COALESCE(r.max_run_queue_number, 0) THEN 'FAIL'
      ELSE 'PASS'
    END AS status,
    CASE
      WHEN c.exhibition_id IS NULL THEN 'counter row missing for exhibition'
      ELSE format('last_number=%s; max_run_queue_number=%s', c.last_number, COALESCE(r.max_run_queue_number, 0))
    END AS detail
  FROM counter_ids i
  LEFT JOIN public.exhibition_queue_counters c ON c.exhibition_id = i.exhibition_id
  LEFT JOIN run_max r ON r.exhibition_id = i.exhibition_id
),
integrity_counts AS (
  SELECT
    (SELECT COUNT(*) FROM (
      SELECT exhibition_id, queue_number
      FROM public.exhibition_prep_runs
      WHERE exhibition_id IS NOT NULL AND queue_number IS NOT NULL
      GROUP BY exhibition_id, queue_number
      HAVING COUNT(*) > 1
    ) duplicates) AS duplicate_queue_groups,
    (SELECT COUNT(*)
     FROM public.exhibition_prep_runs
     WHERE (queue_number IS NULL) <> (display_status IS NULL)
        OR (queue_number IS NOT NULL AND exhibition_id IS NULL)
        OR (queue_number IS NOT NULL AND queue_number <= 0)) AS invalid_queue_rows,
    (SELECT COUNT(*) FROM (
      SELECT exhibition_id, display_status
      FROM public.exhibition_prep_runs
      WHERE display_status IN ('UP_NEXT', 'NOW_SHOWING')
      GROUP BY exhibition_id, display_status
      HAVING COUNT(*) > 1
    ) display_duplicates) AS display_duplicate_groups
),
storage_bucket AS (
  SELECT
    COUNT(*)::bigint AS bucket_rows,
    BOOL_OR(public) AS is_public,
    STRING_AGG(
      format('name=%s; public=%s; file_size_limit=%s; allowed_mime_types=%s', name, public, file_size_limit, allowed_mime_types),
      ' | '
    ) AS detail
  FROM storage.buckets
  WHERE id = 'user-assets'
),
storage_inventory AS (
  SELECT
    COUNT(*)::bigint AS object_count,
    MIN(created_at) AS oldest_object,
    MAX(updated_at) AS newest_object
  FROM storage.objects
  WHERE bucket_id = 'user-assets'
    AND name LIKE 'exhibition/%'
)
SELECT category, check_name, object_name, status, detail
FROM (
  SELECT
    'identity'::text AS category,
    'database_target'::text AS check_name,
    current_database()::text AS object_name,
    'INFO'::text AS status,
    format('executing_role=%s; schema=%s; inspected_at=%s', current_user, current_schema(), clock_timestamp()) AS detail

  UNION ALL

  SELECT
    'schema_object',
    'expected_object',
    e.object_name,
    CASE WHEN to_regclass('public.' || e.object_name) IS NULL THEN 'MISSING_OR_NOT_VISIBLE' ELSE 'FOUND' END,
    CASE WHEN to_regclass('public.' || e.object_name) IS NULL
      THEN 'object is absent or not visible to this role'
      ELSE format('regclass=%s', to_regclass('public.' || e.object_name))
    END
  FROM expected_objects e

  UNION ALL

  SELECT
    'rls',
    'rls_enabled',
    e.table_name,
    CASE
      WHEN c.oid IS NULL THEN 'MISSING_OR_NOT_VISIBLE'
      WHEN c.relrowsecurity THEN 'PASS'
      ELSE 'FAIL'
    END,
    CASE WHEN c.oid IS NULL THEN 'table is absent or not visible'
      ELSE format('rls_enabled=%s; force_rls=%s', c.relrowsecurity, c.relforcerowsecurity)
    END
  FROM expected_tables e
  LEFT JOIN pg_class c ON c.relname = e.table_name
  LEFT JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
  WHERE c.oid IS NULL OR n.oid IS NOT NULL

  UNION ALL

  SELECT 'index', 'actual_index', table_name || '.' || index_name, 'FOUND', detail
  FROM actual_indexes

  UNION ALL

  SELECT
    'index',
    'expected_index_name',
    e.table_name || '.' || e.index_name,
    CASE WHEN a.index_name IS NULL THEN 'MISSING_OR_NOT_VISIBLE' ELSE 'FOUND' END,
    COALESCE(a.detail, 'expected index name is absent; inspect actual_index rows for an equivalent definition')
  FROM expected_indexes e
  LEFT JOIN actual_indexes a
    ON a.table_name = e.table_name AND a.index_name = e.index_name

  UNION ALL

  SELECT 'trigger', 'actual_trigger', table_name || '.' || trigger_name, 'FOUND', detail
  FROM actual_triggers

  UNION ALL

  SELECT
    'trigger',
    'expected_trigger_name',
    e.table_name || '.' || e.trigger_name,
    CASE WHEN a.trigger_name IS NULL THEN 'MISSING_OR_NOT_VISIBLE' ELSE 'FOUND' END,
    COALESCE(a.detail, 'expected trigger name is absent')
  FROM expected_triggers e
  LEFT JOIN actual_triggers a
    ON a.table_name = e.table_name AND a.trigger_name = e.trigger_name

  UNION ALL

  SELECT
    'function',
    'actual_function',
    function_name || '(' || identity_arguments || ')',
    'FOUND',
    detail
  FROM actual_functions
  WHERE function_name IN (SELECT function_name FROM expected_functions)

  UNION ALL

  SELECT
    'function',
    'expected_function_signature',
    e.function_signature,
    CASE WHEN to_regprocedure(e.function_signature) IS NULL THEN 'MISSING_OR_NOT_VISIBLE' ELSE 'FOUND' END,
    COALESCE(a.detail, 'expected function signature is absent')
  FROM expected_functions e
  LEFT JOIN actual_functions a
    ON a.oid = to_regprocedure(e.function_signature)

  UNION ALL

  SELECT
    'grant',
    'actual_table_grant',
    table_name || ':' || grantee || ':' || privilege_type,
    'FOUND',
    'actual information_schema.table_privileges row'
  FROM table_grants

  UNION ALL

  SELECT
    'grant',
    'service_role_table_grant',
    e.table_name,
    CASE
      WHEN to_regclass('public.' || e.table_name) IS NULL THEN 'MISSING_OR_NOT_VISIBLE'
      WHEN EXISTS (
        SELECT 1 FROM table_grants g
        WHERE g.table_name = e.table_name AND g.grantee = 'service_role'
      ) THEN 'FOUND'
      ELSE 'FAIL'
    END,
    'service_role must have an explicit table grant for service-role backend access'
  FROM expected_tables e

  UNION ALL

  SELECT
    'grant',
    'public_or_visitor_table_grant',
    e.table_name,
    CASE
      WHEN to_regclass('public.' || e.table_name) IS NULL THEN 'MISSING_OR_NOT_VISIBLE'
      WHEN EXISTS (
        SELECT 1 FROM table_grants g
        WHERE g.table_name = e.table_name
          AND g.grantee IN ('PUBLIC', 'anon', 'authenticated')
      ) THEN 'FAIL'
      ELSE 'PASS'
    END,
    'PUBLIC, anon, and authenticated should have no direct table privileges'
  FROM expected_tables e

  UNION ALL

  SELECT
    'grant',
    'actual_function_grant',
    routine_name || ':' || grantee || ':' || privilege_type,
    'FOUND',
    'actual information_schema.routine_privileges row'
  FROM routine_grants
  WHERE routine_name IN (SELECT function_name FROM expected_functions)

  UNION ALL

  SELECT
    'grant',
    'service_role_function_execute',
    e.function_signature,
    CASE
      WHEN to_regprocedure(e.function_signature) IS NULL THEN 'MISSING_OR_NOT_VISIBLE'
      WHEN has_function_privilege('service_role', a.oid, 'EXECUTE') THEN 'FOUND'
      ELSE 'FAIL'
    END,
    'service_role must be able to execute the backend RPC/function'
  FROM expected_functions e
  LEFT JOIN actual_functions a
    ON a.oid = to_regprocedure(e.function_signature)

  UNION ALL

  SELECT
    'grant',
    'visitor_function_execute',
    e.function_signature,
    CASE
      WHEN to_regprocedure(e.function_signature) IS NULL THEN 'MISSING_OR_NOT_VISIBLE'
      WHEN has_function_privilege('anon', a.oid, 'EXECUTE')
        OR has_function_privilege('authenticated', a.oid, 'EXECUTE') THEN 'FAIL'
      ELSE 'PASS'
    END,
    'anon and authenticated should not execute private Passport functions directly'
  FROM expected_functions e
  LEFT JOIN actual_functions a
    ON a.oid = to_regprocedure(e.function_signature)

  UNION ALL

  SELECT
    'rls',
    'policy_inventory',
    p.table_name,
    CASE
      WHEN to_regclass('public.' || p.table_name) IS NULL THEN 'MISSING_OR_NOT_VISIBLE'
      WHEN p.policy_count = 0 THEN 'INFO'
      ELSE 'FOUND'
    END,
    format('policy_count=%s', p.policy_count)
  FROM policy_counts p

  UNION ALL

  SELECT
    'rls',
    'actual_policy',
    schemaname || '.' || tablename || '.' || policyname,
    'FOUND',
    detail
  FROM actual_policies

  UNION ALL

  SELECT
    'dependency',
    'pgcrypto_extension',
    'pgcrypto',
    CASE WHEN e.installed_version IS NULL THEN 'FAIL' ELSE 'PASS' END,
    format('default_version=%s; installed_version=%s', e.default_version, e.installed_version)
  FROM pg_available_extensions e
  WHERE e.name = 'pgcrypto'

  UNION ALL

  SELECT
    'dependency',
    'required_builtin_function',
    p.oid::regprocedure::text,
    'FOUND',
    'required PostgreSQL function is available'
  FROM pg_proc p
  JOIN pg_namespace n ON n.oid = p.pronamespace
  WHERE (n.nspname, p.proname) IN (
    ('pg_catalog', 'hashtextextended'),
    ('pg_catalog', 'pg_advisory_xact_lock'),
    ('pg_catalog', 'gen_random_uuid')
  )

  UNION ALL

  SELECT 'queue_integrity', 'counter_vs_max_run', c.exhibition_id, c.status, c.detail
  FROM counter_checks c

  UNION ALL

  SELECT
    'queue_integrity',
    'duplicate_run_queue_groups',
    'exhibition_prep_runs',
    CASE WHEN i.duplicate_queue_groups = 0 THEN 'PASS' ELSE 'FAIL' END,
    format('duplicate_queue_groups=%s', i.duplicate_queue_groups)
  FROM integrity_counts i

  UNION ALL

  SELECT
    'queue_integrity',
    'invalid_run_queue_rows',
    'exhibition_prep_runs',
    CASE WHEN i.invalid_queue_rows = 0 THEN 'PASS' ELSE 'FAIL' END,
    format('invalid_queue_rows=%s', i.invalid_queue_rows)
  FROM integrity_counts i

  UNION ALL

  SELECT
    'queue_integrity',
    'duplicate_display_states',
    'exhibition_prep_runs',
    CASE WHEN i.display_duplicate_groups = 0 THEN 'PASS' ELSE 'FAIL' END,
    format('duplicate_display_groups=%s', i.display_duplicate_groups)
  FROM integrity_counts i

  UNION ALL

  SELECT
    'queue_integrity',
    'cross_table_passport_collisions',
    'exhibition_pet_passport_registrations',
    'NOT_RUN',
    'Passport table is absent or not visible; no cross-table query was attempted in this pre-migration-safe statement'

  UNION ALL

  SELECT
    'storage',
    'user_assets_bucket',
    'user-assets',
    CASE
      WHEN b.bucket_rows = 0 THEN 'MISSING_OR_NOT_VISIBLE'
      WHEN b.is_public THEN 'FAIL'
      ELSE 'PASS'
    END,
    COALESCE(b.detail, 'bucket row absent or not visible')
  FROM storage_bucket b

  UNION ALL

  SELECT
    'storage',
    'exhibition_object_inventory',
    'user-assets/exhibition/',
    CASE WHEN s.object_count = 0 THEN 'EMPTY_OR_NOT_VISIBLE' ELSE 'INFO' END,
    format('object_count=%s; oldest=%s; newest=%s', s.object_count, s.oldest_object, s.newest_object)
  FROM storage_inventory s

  UNION ALL

  SELECT
    'storage',
    'storage_rls_state',
    c.relname,
    CASE WHEN c.relrowsecurity THEN 'PASS' ELSE 'FAIL' END,
    format('rls_enabled=%s; force_rls=%s', c.relrowsecurity, c.relforcerowsecurity)
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'storage'
    AND c.relname IN ('buckets', 'objects')

  UNION ALL

  SELECT
    'rollout',
    'queue_source_config',
    'exhibition_queue_source_config',
    'NOT_RUN',
    'Table is absent or not visible; final PASSPORT mode is not verified by this pre-migration-safe statement'
) checks
ORDER BY category, check_name, object_name;
