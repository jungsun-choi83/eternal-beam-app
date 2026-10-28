-- Eternal Beam: Pet Passport registration and queue-number cutover guard.
--
-- Registrations are intentionally separate from exhibition_prep_runs. A Passport
-- waits for the later exhibition photo; it does not wait for the hologram worker.
-- All access is through the backend service role. The raw bearer token is never
-- stored, and the email column is never returned by a public RPC.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS public.exhibition_queue_source_config (
  exhibition_id TEXT PRIMARY KEY,
  mode TEXT NOT NULL DEFAULT 'STAFF'
    CHECK (mode IN ('STAFF', 'DUAL', 'PASSPORT')),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.exhibition_queue_source_config IS
  'Service-role-only rollout switch: STAFF preserves legacy allocation, DUAL allows both shared sources, PASSPORT blocks new staff allocations.';

ALTER TABLE public.exhibition_queue_source_config ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.exhibition_queue_source_config FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.exhibition_queue_source_config TO service_role;

CREATE TABLE IF NOT EXISTS public.exhibition_pet_passport_registrations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  exhibition_id TEXT NOT NULL,
  queue_number INTEGER NOT NULL CHECK (queue_number > 0),
  pet_name TEXT NOT NULL CHECK (char_length(btrim(pet_name)) BETWEEN 1 AND 40),
  email TEXT NOT NULL CHECK (char_length(email) BETWEEN 3 AND 320),
  privacy_consent BOOLEAN NOT NULL CHECK (privacy_consent IS TRUE),
  privacy_consent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  privacy_policy_version TEXT NOT NULL DEFAULT 'v1'
    CHECK (char_length(btrim(privacy_policy_version)) BETWEEN 1 AND 40),
  -- sha256 of the high-entropy client idempotency/access token.
  token_hash TEXT NOT NULL CHECK (token_hash ~ '^[0-9a-f]{64}$'),
  request_fingerprint TEXT NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
  -- Deliberately independent of exhibition_prep_runs.status. This is only the
  -- Passport's wait for the later exhibition photo.
  photo_status TEXT NOT NULL DEFAULT 'WAITING_FOR_PHOTO'
    CHECK (photo_status IN ('WAITING_FOR_PHOTO', 'PHOTO_RECEIVED')),
  -- A received Passport must retain its source run. Deleting the linked run
  -- is rejected rather than silently orphaning the visitor's photo.
  photo_run_id UUID REFERENCES public.exhibition_prep_runs(id) ON DELETE RESTRICT,
  photo_received_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (exhibition_id, queue_number),
  -- A source run can represent at most one Passport photo association.
  UNIQUE (photo_run_id),
  -- Global uniqueness keeps token-only retrieval unambiguous across exhibitions.
  UNIQUE (token_hash),
  CHECK ((photo_status = 'PHOTO_RECEIVED') = (photo_received_at IS NOT NULL)),
  CHECK ((photo_status = 'PHOTO_RECEIVED') = (photo_run_id IS NOT NULL))
);

COMMENT ON TABLE public.exhibition_pet_passport_registrations IS
  'Private visitor registration. Email and token hashes are service-role-only; public retrieval requires the opaque bearer token.';
COMMENT ON COLUMN public.exhibition_pet_passport_registrations.email IS
  'Visitor email. Never select this column from a public RPC or public API response.';
COMMENT ON COLUMN public.exhibition_pet_passport_registrations.token_hash IS
  'sha256 of the 256-bit-plus opaque Passport access/idempotency token; raw token is never persisted.';

CREATE INDEX IF NOT EXISTS idx_exhibition_pet_passport_photo_status
  ON public.exhibition_pet_passport_registrations(exhibition_id, photo_status, queue_number);

ALTER TABLE public.exhibition_pet_passport_registrations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.exhibition_pet_passport_registrations FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.exhibition_pet_passport_registrations TO service_role;

CREATE OR REPLACE FUNCTION public.set_exhibition_pet_passport_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_exhibition_pet_passport_updated_at
  ON public.exhibition_pet_passport_registrations;
CREATE TRIGGER trg_exhibition_pet_passport_updated_at
  BEFORE UPDATE ON public.exhibition_pet_passport_registrations
  FOR EACH ROW EXECUTE FUNCTION public.set_exhibition_pet_passport_updated_at();

-- The existing staff flow and the Passport flow share this transaction lock.
-- This also protects against a manually inserted legacy queue row racing a
-- Passport registration. hashtextextended collisions only serialize unrelated
-- exhibitions; they cannot create duplicate numbers.
CREATE OR REPLACE FUNCTION public.exhibition_queue_lock_key(p_exhibition_id TEXT)
RETURNS BIGINT
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT hashtextextended(p_exhibition_id, 0);
$$;

CREATE OR REPLACE FUNCTION public.exhibition_queue_assert_number_available()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF NEW.exhibition_id IS NULL OR NEW.queue_number IS NULL THEN
    RETURN NEW;
  END IF;

  PERFORM pg_advisory_xact_lock(public.exhibition_queue_lock_key(NEW.exhibition_id));

  IF TG_TABLE_NAME = 'exhibition_prep_runs' THEN
    IF EXISTS (
      SELECT 1
      FROM public.exhibition_pet_passport_registrations r
      WHERE r.exhibition_id = NEW.exhibition_id
        AND r.queue_number = NEW.queue_number
    ) THEN
      RAISE EXCEPTION 'QUEUE_NUMBER_ALREADY_RESERVED'
        USING ERRCODE = '23505';
    END IF;
  ELSE
    IF EXISTS (
      SELECT 1
      FROM public.exhibition_prep_runs r
      WHERE r.exhibition_id = NEW.exhibition_id
        AND r.queue_number = NEW.queue_number
    ) THEN
      RAISE EXCEPTION 'QUEUE_NUMBER_ALREADY_IN_USE'
        USING ERRCODE = '23505';
    END IF;
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_exhibition_prep_runs_queue_number_available
  ON public.exhibition_prep_runs;
CREATE TRIGGER trg_exhibition_prep_runs_queue_number_available
  BEFORE INSERT OR UPDATE OF exhibition_id, queue_number
  ON public.exhibition_prep_runs
  FOR EACH ROW EXECUTE FUNCTION public.exhibition_queue_assert_number_available();

DROP TRIGGER IF EXISTS trg_exhibition_pet_passport_queue_number_available
  ON public.exhibition_pet_passport_registrations;
CREATE TRIGGER trg_exhibition_pet_passport_queue_number_available
  BEFORE INSERT OR UPDATE OF exhibition_id, queue_number
  ON public.exhibition_pet_passport_registrations
  FOR EACH ROW EXECUTE FUNCTION public.exhibition_queue_assert_number_available();

-- Backfill the existing counter before either source can allocate again. The
-- migration is safe if counters were already populated.
INSERT INTO public.exhibition_queue_counters (exhibition_id, last_number)
SELECT exhibition_id, MAX(queue_number)
FROM public.exhibition_prep_runs
WHERE exhibition_id IS NOT NULL AND queue_number IS NOT NULL
GROUP BY exhibition_id
ON CONFLICT (exhibition_id) DO UPDATE
SET last_number = GREATEST(
      public.exhibition_queue_counters.last_number,
      EXCLUDED.last_number
    ),
    updated_at = NOW();

CREATE OR REPLACE FUNCTION public.exhibition_allocate_queue_number(
  p_exhibition_id TEXT,
  p_source TEXT
)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_mode TEXT;
  v_last INTEGER;
  v_existing_runs INTEGER;
  v_existing_passports INTEGER;
  v_next INTEGER;
BEGIN
  IF p_exhibition_id IS NULL OR p_exhibition_id !~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$' THEN
    RAISE EXCEPTION 'INVALID_EXHIBITION_ID';
  END IF;
  IF p_source NOT IN ('STAFF', 'PASSPORT') THEN
    RAISE EXCEPTION 'INVALID_QUEUE_SOURCE';
  END IF;

  PERFORM pg_advisory_xact_lock(public.exhibition_queue_lock_key(p_exhibition_id));

  SELECT COALESCE(c.mode, 'STAFF')
  INTO v_mode
  FROM (SELECT 1) seed
  LEFT JOIN public.exhibition_queue_source_config c
    ON c.exhibition_id = p_exhibition_id;

  IF p_source = 'STAFF' AND v_mode = 'PASSPORT' THEN
    RAISE EXCEPTION 'STAFF_QUEUE_ALLOCATION_CLOSED';
  END IF;
  IF p_source = 'PASSPORT' AND v_mode = 'STAFF' THEN
    RAISE EXCEPTION 'PASSPORT_REGISTRATION_NOT_ENABLED';
  END IF;

  INSERT INTO public.exhibition_queue_counters (exhibition_id, last_number)
  VALUES (p_exhibition_id, 0)
  ON CONFLICT (exhibition_id) DO NOTHING;

  SELECT c.last_number
  INTO v_last
  FROM public.exhibition_queue_counters c
  WHERE c.exhibition_id = p_exhibition_id
  FOR UPDATE;

  SELECT COALESCE(MAX(queue_number), 0)
  INTO v_existing_runs
  FROM public.exhibition_prep_runs
  WHERE exhibition_id = p_exhibition_id;

  SELECT COALESCE(MAX(queue_number), 0)
  INTO v_existing_passports
  FROM public.exhibition_pet_passport_registrations
  WHERE exhibition_id = p_exhibition_id;

  v_next := GREATEST(v_last, v_existing_runs, v_existing_passports) + 1;

  UPDATE public.exhibition_queue_counters
  SET last_number = v_next, updated_at = NOW()
  WHERE exhibition_id = p_exhibition_id;

  RETURN v_next;
END;
$$;

-- Preserve the existing staff RPC name and callers. Only its source guard and
-- drift-safe max checks are new.
CREATE OR REPLACE FUNCTION public.exhibition_next_queue_number(p_exhibition_id TEXT)
RETURNS INTEGER
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT public.exhibition_allocate_queue_number(p_exhibition_id, 'STAFF');
$$;

REVOKE ALL ON FUNCTION public.exhibition_queue_lock_key(TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.exhibition_queue_assert_number_available() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.exhibition_allocate_queue_number(TEXT, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.exhibition_next_queue_number(TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.exhibition_allocate_queue_number(TEXT, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.exhibition_next_queue_number(TEXT) TO service_role;

CREATE OR REPLACE FUNCTION public.exhibition_register_pet_passport(
  p_exhibition_id TEXT,
  p_pet_name TEXT,
  p_email TEXT,
  p_privacy_consent BOOLEAN,
  p_privacy_policy_version TEXT,
  p_token_hash TEXT,
  p_request_fingerprint TEXT
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
  IF char_length(btrim(COALESCE(p_pet_name, ''))) NOT BETWEEN 1 AND 40 THEN
    RAISE EXCEPTION 'INVALID_PET_NAME';
  END IF;
  IF char_length(btrim(COALESCE(p_email, ''))) NOT BETWEEN 3 AND 320 THEN
    RAISE EXCEPTION 'INVALID_EMAIL';
  END IF;
  IF p_privacy_consent IS NOT TRUE THEN
    RAISE EXCEPTION 'PRIVACY_CONSENT_REQUIRED';
  END IF;
  IF char_length(btrim(COALESCE(p_privacy_policy_version, ''))) NOT BETWEEN 1 AND 40 THEN
    RAISE EXCEPTION 'INVALID_PRIVACY_POLICY_VERSION';
  END IF;
  IF p_token_hash !~ '^[0-9a-f]{64}$' OR p_request_fingerprint !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'INVALID_REGISTRATION_TOKEN';
  END IF;

  -- Serialize retries before looking up the idempotency key. This prevents a
  -- concurrent retry from consuming a second queue number.
  PERFORM pg_advisory_xact_lock(public.exhibition_queue_lock_key(p_exhibition_id));

  SELECT *
  INTO v_existing
  FROM public.exhibition_pet_passport_registrations r
  WHERE r.exhibition_id = p_exhibition_id
    AND r.token_hash = p_token_hash
  FOR UPDATE;

  IF FOUND THEN
    IF v_existing.request_fingerprint <> p_request_fingerprint THEN
      RAISE EXCEPTION 'IDEMPOTENCY_KEY_REUSED';
    END IF;
    RETURN QUERY SELECT v_existing.id, v_existing.exhibition_id,
      v_existing.queue_number, v_existing.pet_name, v_existing.photo_status,
      v_existing.photo_received_at, v_existing.created_at, TRUE;
    RETURN;
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.exhibition_pet_passport_registrations r
    WHERE r.token_hash = p_token_hash
  ) THEN
    RAISE EXCEPTION 'REGISTRATION_TOKEN_ALREADY_USED';
  END IF;

  v_queue_number := public.exhibition_allocate_queue_number(p_exhibition_id, 'PASSPORT');

  INSERT INTO public.exhibition_pet_passport_registrations (
    exhibition_id, queue_number, pet_name, email, privacy_consent,
    privacy_consent_at, privacy_policy_version, token_hash, request_fingerprint
  ) VALUES (
    p_exhibition_id, v_queue_number, btrim(p_pet_name), btrim(lower(p_email)),
    TRUE, NOW(), btrim(p_privacy_policy_version), p_token_hash, p_request_fingerprint
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

CREATE OR REPLACE FUNCTION public.exhibition_get_pet_passport(p_token_hash TEXT)
RETURNS TABLE (
  exhibition_id TEXT,
  queue_number INTEGER,
  pet_name TEXT,
  photo_status TEXT,
  photo_received_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT r.exhibition_id, r.queue_number, r.pet_name, r.photo_status,
         r.photo_received_at, r.created_at
  FROM public.exhibition_pet_passport_registrations r
  WHERE r.token_hash = p_token_hash
    AND p_token_hash ~ '^[0-9a-f]{64}$';
$$;

REVOKE ALL ON FUNCTION public.exhibition_register_pet_passport(TEXT, TEXT, TEXT, BOOLEAN, TEXT, TEXT, TEXT)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.exhibition_get_pet_passport(TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.exhibition_register_pet_passport(TEXT, TEXT, TEXT, BOOLEAN, TEXT, TEXT, TEXT)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.exhibition_get_pet_passport(TEXT) TO service_role;
