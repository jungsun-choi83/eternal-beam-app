-- Eternal Beam Exhibition: remove unnecessary direct public/visitor privileges.
--
-- This migration changes grants only. It does not change rows, queue counters,
-- queue allocation, RLS state, triggers, or storage. The Exhibition API and
-- worker use the service-role client, so service_role remains explicitly able
-- to perform the existing table and trigger operations.

REVOKE ALL ON TABLE public.exhibition_prep_runs
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.exhibition_queue_counters
  FROM PUBLIC, anon, authenticated;

-- Preserve the existing service-role behavior, including administrative
-- cleanup capabilities that may be used outside the application code.
GRANT ALL ON TABLE public.exhibition_prep_runs TO service_role;
GRANT ALL ON TABLE public.exhibition_queue_counters TO service_role;

-- This is a trigger-only helper. Direct execution by browser roles is not
-- needed; service_role is retained for service-role writes that fire it.
REVOKE ALL ON FUNCTION public.set_exhibition_prep_runs_updated_at()
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.set_exhibition_prep_runs_updated_at()
  TO service_role;

-- The Passport trigger helper is created by the Step 1 Passport migration and
-- is hardened here in the same grant-only pass.
REVOKE ALL ON FUNCTION public.set_exhibition_pet_passport_updated_at()
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.set_exhibition_pet_passport_updated_at()
  TO service_role;
