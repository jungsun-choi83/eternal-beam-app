-- Fix the ambiguous photo_received_at reference in the Passport photo RPC.
-- This replaces the function body only; it does not modify existing rows or
-- queue state.

CREATE OR REPLACE FUNCTION public.exhibition_attach_pet_passport_photo(
  p_exhibition_id TEXT,
  p_queue_number INTEGER,
  p_run_id UUID
)
RETURNS TABLE (
  run_id UUID,
  exhibition_id TEXT,
  queue_number INTEGER,
  pet_name TEXT,
  created_at TIMESTAMPTZ,
  display_status TEXT,
  status TEXT,
  photo_status TEXT,
  photo_received_at TIMESTAMPTZ
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_registration public.exhibition_pet_passport_registrations%ROWTYPE;
  v_run public.exhibition_prep_runs%ROWTYPE;
  v_photo_received_at TIMESTAMPTZ;
BEGIN
  IF p_exhibition_id IS NULL OR p_exhibition_id !~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$'
     OR p_queue_number IS NULL OR p_queue_number <= 0
     OR p_run_id IS NULL THEN
    RAISE EXCEPTION 'INVALID_PASSPORT_PHOTO_ASSOCIATION';
  END IF;

  PERFORM pg_advisory_xact_lock(public.exhibition_queue_lock_key(p_exhibition_id));

  SELECT *
  INTO v_registration
  FROM public.exhibition_pet_passport_registrations r
  WHERE r.exhibition_id = p_exhibition_id
    AND r.queue_number = p_queue_number
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'PASSPORT_NOT_FOUND';
  END IF;
  IF v_registration.photo_status <> 'WAITING_FOR_PHOTO'
     OR v_registration.photo_run_id IS NOT NULL THEN
    RAISE EXCEPTION 'PASSPORT_PHOTO_ALREADY_ASSOCIATED';
  END IF;

  SELECT *
  INTO v_run
  FROM public.exhibition_prep_runs r
  WHERE r.id = p_run_id
  FOR UPDATE;

  IF NOT FOUND
     OR v_run.exhibition_id IS DISTINCT FROM p_exhibition_id
     OR v_run.status IS DISTINCT FROM 'FAILED'
     OR v_run.error_code IS DISTINCT FROM 'PASSPORT_PHOTO_PENDING_ASSOCIATION'
     OR v_run.queue_number IS NOT NULL
     OR v_run.display_status IS NOT NULL THEN
    RAISE EXCEPTION 'PASSPORT_RUN_NOT_ATTACHABLE';
  END IF;

  -- Set the exact run id first. The run trigger only permits this matching
  -- pair, while the row locks and exhibition advisory lock prevent races.
  UPDATE public.exhibition_pet_passport_registrations AS registration
  SET photo_status = 'PHOTO_RECEIVED',
      photo_run_id = p_run_id,
      photo_received_at = NOW()
  WHERE id = v_registration.id
  RETURNING registration.photo_received_at INTO v_photo_received_at;

  UPDATE public.exhibition_prep_runs
  SET status = 'QUEUED',
      error_code = NULL,
      error_message = NULL,
      queue_number = v_registration.queue_number,
      pet_name = v_registration.pet_name,
      display_status = 'WAITING'
  WHERE id = p_run_id;

  SELECT *
  INTO v_run
  FROM public.exhibition_prep_runs r
  WHERE r.id = p_run_id;

  RETURN QUERY SELECT
    v_run.id,
    v_run.exhibition_id,
    v_run.queue_number,
    v_run.pet_name,
    v_run.created_at,
    v_run.display_status,
    v_run.status,
    'PHOTO_RECEIVED'::TEXT,
    v_photo_received_at;
END;
$$;

REVOKE ALL ON FUNCTION public.exhibition_attach_pet_passport_photo(TEXT, INTEGER, UUID)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.exhibition_attach_pet_passport_photo(TEXT, INTEGER, UUID)
  TO service_role;
