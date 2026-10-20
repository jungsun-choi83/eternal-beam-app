-- Step 5A: expose only the internal photo reference to the service role.
-- The public retrieval RPC remains unchanged and never returns photo_run_id.

CREATE OR REPLACE FUNCTION public.exhibition_get_pet_passport_photo_ref(p_token_hash TEXT)
RETURNS TABLE (
  exhibition_id TEXT,
  queue_number INTEGER,
  photo_status TEXT,
  photo_run_id UUID
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT r.exhibition_id, r.queue_number, r.photo_status, r.photo_run_id
  FROM public.exhibition_pet_passport_registrations r
  WHERE r.token_hash = p_token_hash
    AND p_token_hash ~ '^[0-9a-f]{64}$'
    AND r.photo_status = 'PHOTO_RECEIVED'
    AND r.photo_run_id IS NOT NULL;
$$;

REVOKE ALL ON FUNCTION public.exhibition_get_pet_passport_photo_ref(TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.exhibition_get_pet_passport_photo_ref(TEXT)
  TO service_role;
