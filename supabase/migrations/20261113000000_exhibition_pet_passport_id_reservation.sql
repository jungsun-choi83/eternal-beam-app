-- Issue the exhibition Pet ID before the visitor supplies a pet name or email.
-- Existing full registration remains strict; this is a separate, token-scoped
-- reservation contract for the approved homepage CTA.

ALTER TABLE public.exhibition_pet_passport_registrations
  ALTER COLUMN pet_name DROP NOT NULL,
  ALTER COLUMN email DROP NOT NULL,
  ALTER COLUMN privacy_consent DROP NOT NULL,
  ALTER COLUMN privacy_consent_at DROP NOT NULL;

ALTER TABLE public.exhibition_pet_passport_registrations
  DROP CONSTRAINT IF EXISTS exhibition_pet_passport_registrations_pet_name_check,
  DROP CONSTRAINT IF EXISTS exhibition_pet_passport_registrations_email_check,
  DROP CONSTRAINT IF EXISTS exhibition_pet_passport_registrations_privacy_consent_check;

ALTER TABLE public.exhibition_pet_passport_registrations
  ADD CONSTRAINT exhibition_pet_passport_registrations_pet_name_check
    CHECK (pet_name IS NULL OR char_length(btrim(pet_name)) BETWEEN 1 AND 40),
  ADD CONSTRAINT exhibition_pet_passport_registrations_email_check
    CHECK (email IS NULL OR char_length(email) BETWEEN 3 AND 320),
  ADD CONSTRAINT exhibition_pet_passport_registrations_privacy_consent_check
    CHECK (
      (privacy_consent IS TRUE AND privacy_consent_at IS NOT NULL)
      OR (privacy_consent IS NULL AND privacy_consent_at IS NULL)
    );

COMMENT ON COLUMN public.exhibition_pet_passport_registrations.pet_name IS
  'Pet name may remain null between ID reservation and the later Passport name step.';
COMMENT ON COLUMN public.exhibition_pet_passport_registrations.email IS
  'Visitor email is optional until a later approved Passport communication step.';
COMMENT ON COLUMN public.exhibition_pet_passport_registrations.privacy_consent IS
  'Null for an ID-only reservation that has collected no visitor PII; true for full registration.';

CREATE OR REPLACE FUNCTION public.exhibition_create_pet_passport_id(
  p_exhibition_id TEXT,
  p_token_hash TEXT
)
RETURNS TABLE (
  id UUID,
  exhibition_id TEXT,
  queue_number INTEGER,
  pet_name TEXT,
  photo_status TEXT,
  photo_received_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ,
  replayed BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_existing public.exhibition_pet_passport_registrations%ROWTYPE;
  v_queue_number INTEGER;
BEGIN
  IF p_exhibition_id IS NULL OR p_exhibition_id !~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$' THEN
    RAISE EXCEPTION 'INVALID_EXHIBITION_ID';
  END IF;
  IF p_token_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'INVALID_REGISTRATION_TOKEN';
  END IF;

  -- The opaque token is the complete idempotency and recovery credential.
  -- Serialize retries before allocating a queue number.
  PERFORM pg_advisory_xact_lock(public.exhibition_queue_lock_key(p_exhibition_id));

  SELECT *
  INTO v_existing
  FROM public.exhibition_pet_passport_registrations registration
  WHERE registration.token_hash = p_token_hash
  FOR UPDATE;

  IF FOUND THEN
    IF v_existing.exhibition_id <> p_exhibition_id THEN
      RAISE EXCEPTION 'REGISTRATION_TOKEN_ALREADY_USED';
    END IF;
    RETURN QUERY SELECT v_existing.id, v_existing.exhibition_id,
      v_existing.queue_number, v_existing.pet_name, v_existing.photo_status,
      v_existing.photo_received_at, v_existing.created_at, TRUE;
    RETURN;
  END IF;

  v_queue_number := public.exhibition_allocate_queue_number(p_exhibition_id, 'PASSPORT');

  INSERT INTO public.exhibition_pet_passport_registrations (
    exhibition_id, queue_number, pet_name, email, privacy_consent,
    privacy_consent_at, privacy_policy_version, token_hash, request_fingerprint
  ) VALUES (
    p_exhibition_id, v_queue_number, NULL, NULL, NULL,
    NULL, 'v1', p_token_hash, p_token_hash
  )
  RETURNING
    exhibition_pet_passport_registrations.id,
    exhibition_pet_passport_registrations.exhibition_id,
    exhibition_pet_passport_registrations.queue_number,
    exhibition_pet_passport_registrations.pet_name,
    exhibition_pet_passport_registrations.photo_status,
    exhibition_pet_passport_registrations.photo_received_at,
    exhibition_pet_passport_registrations.created_at
  INTO id, exhibition_id, queue_number, pet_name, photo_status,
    photo_received_at, created_at;

  replayed := FALSE;
  RETURN NEXT;
END;
$$;

REVOKE ALL ON FUNCTION public.exhibition_create_pet_passport_id(TEXT, TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.exhibition_create_pet_passport_id(TEXT, TEXT)
  TO service_role;
