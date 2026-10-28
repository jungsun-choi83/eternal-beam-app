-- Save optional Passport email preferences after the ID-only reservation.
-- The raw email remains service-role-only and is never returned by a public
-- Passport retrieval RPC. The opaque token hash is the authorization key.

ALTER TABLE public.exhibition_pet_passport_registrations
  ADD COLUMN IF NOT EXISTS ai_video_consent BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS ai_video_consent_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS marketing_consent BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS marketing_consent_at TIMESTAMPTZ;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'exhibition_pet_passport_ai_video_consent_check'
  ) THEN
    ALTER TABLE public.exhibition_pet_passport_registrations
      ADD CONSTRAINT exhibition_pet_passport_ai_video_consent_check
      CHECK ((ai_video_consent IS TRUE) = (ai_video_consent_at IS NOT NULL));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'exhibition_pet_passport_marketing_consent_check'
  ) THEN
    ALTER TABLE public.exhibition_pet_passport_registrations
      ADD CONSTRAINT exhibition_pet_passport_marketing_consent_check
      CHECK ((marketing_consent IS TRUE) = (marketing_consent_at IS NOT NULL));
  END IF;
END;
$$;

COMMENT ON COLUMN public.exhibition_pet_passport_registrations.ai_video_consent IS
  'Optional consent to receive the future Eternal Beam AI-video email.';
COMMENT ON COLUMN public.exhibition_pet_passport_registrations.ai_video_consent_at IS
  'Timestamp at which the optional AI-video email consent was recorded.';
COMMENT ON COLUMN public.exhibition_pet_passport_registrations.marketing_consent IS
  'Optional consent for separate Eternal Beam marketing communications.';
COMMENT ON COLUMN public.exhibition_pet_passport_registrations.marketing_consent_at IS
  'Timestamp at which the optional marketing consent was recorded.';

CREATE OR REPLACE FUNCTION public.exhibition_save_pet_passport_email(
  p_token_hash TEXT,
  p_email TEXT,
  p_ai_video_consent BOOLEAN DEFAULT FALSE,
  p_marketing_consent BOOLEAN DEFAULT FALSE
)
RETURNS TABLE (
  exhibition_id TEXT,
  queue_number INTEGER,
  pet_name TEXT,
  photo_status TEXT,
  photo_received_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ,
  email_saved BOOLEAN,
  ai_video_consent BOOLEAN,
  marketing_consent BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF p_token_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'INVALID_REGISTRATION_TOKEN';
  END IF;
  IF char_length(btrim(COALESCE(p_email, ''))) NOT BETWEEN 3 AND 320
     OR btrim(p_email) !~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$' THEN
    RAISE EXCEPTION 'INVALID_EMAIL';
  END IF;

  UPDATE public.exhibition_pet_passport_registrations AS registration
  SET email = btrim(lower(p_email)),
      ai_video_consent = COALESCE(p_ai_video_consent, FALSE),
      ai_video_consent_at = CASE
        WHEN COALESCE(p_ai_video_consent, FALSE) THEN NOW()
        ELSE NULL
      END,
      marketing_consent = COALESCE(p_marketing_consent, FALSE),
      marketing_consent_at = CASE
        WHEN COALESCE(p_marketing_consent, FALSE) THEN NOW()
        ELSE NULL
      END
  WHERE registration.token_hash = p_token_hash;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'PASSPORT_NOT_FOUND';
  END IF;

  RETURN QUERY
  SELECT registration.exhibition_id,
         registration.queue_number,
         registration.pet_name,
         registration.photo_status,
         registration.photo_received_at,
         registration.created_at,
         (registration.email IS NOT NULL),
         registration.ai_video_consent,
         registration.marketing_consent
  FROM public.exhibition_pet_passport_registrations AS registration
  WHERE registration.token_hash = p_token_hash;
END;
$$;

REVOKE ALL ON FUNCTION public.exhibition_save_pet_passport_email(TEXT, TEXT, BOOLEAN, BOOLEAN)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.exhibition_save_pet_passport_email(TEXT, TEXT, BOOLEAN, BOOLEAN)
  TO service_role;

-- Keep retrieval token-scoped while exposing only non-sensitive save status.
-- The function's return type is expanded by dropping and recreating it; the
-- existing function signature and all previously returned fields remain.
DROP FUNCTION IF EXISTS public.exhibition_get_pet_passport(TEXT);

CREATE FUNCTION public.exhibition_get_pet_passport(p_token_hash TEXT)
RETURNS TABLE (
  exhibition_id TEXT,
  queue_number INTEGER,
  pet_name TEXT,
  photo_status TEXT,
  photo_received_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ,
  email_saved BOOLEAN,
  ai_video_consent BOOLEAN,
  marketing_consent BOOLEAN
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT registration.exhibition_id,
         registration.queue_number,
         registration.pet_name,
         registration.photo_status,
         registration.photo_received_at,
         registration.created_at,
         (registration.email IS NOT NULL),
         registration.ai_video_consent,
         registration.marketing_consent
  FROM public.exhibition_pet_passport_registrations AS registration
  WHERE registration.token_hash = p_token_hash
    AND p_token_hash ~ '^[0-9a-f]{64}$';
$$;

REVOKE ALL ON FUNCTION public.exhibition_get_pet_passport(TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.exhibition_get_pet_passport(TEXT) TO service_role;
