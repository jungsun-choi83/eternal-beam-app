-- Eternal Beam: 사용자별 저장 용량 및 생성 횟수 제한
-- Supabase SQL Editor에서 실행하거나: supabase db push

-- 1) 사용자별 할당량 (Firebase UID 기준)
CREATE TABLE IF NOT EXISTS public.user_quotas (
  user_id TEXT PRIMARY KEY,
  plan_type TEXT NOT NULL DEFAULT 'basic' CHECK (plan_type IN ('basic', 'premium', 'lifetime')),
  storage_usage_bytes BIGINT DEFAULT 0,
  generation_count_this_month INT DEFAULT 0,
  quota_reset_at TIMESTAMPTZ DEFAULT (date_trunc('month', now()) + interval '1 month'),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 2) 사용자 업로드 미디어 (용량 계산용)
CREATE TABLE IF NOT EXISTS public.user_media (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id TEXT NOT NULL,
  file_path TEXT NOT NULL,
  file_size_bytes BIGINT NOT NULL,
  media_type TEXT NOT NULL CHECK (media_type IN ('video', 'audio', 'image')),
  content_id TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_media_user_id ON public.user_media(user_id);

-- 3) RLS
ALTER TABLE public.user_quotas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_media ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow all for user_quotas" ON public.user_quotas;
DROP POLICY IF EXISTS "Allow all for user_media" ON public.user_media;

CREATE POLICY "Allow all for user_quotas" ON public.user_quotas
  FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Allow all for user_media" ON public.user_media
  FOR ALL USING (true) WITH CHECK (true);

-- 4) 플랜 제한 상수 조회 함수
CREATE OR REPLACE FUNCTION public.get_plan_limits(p_plan_type TEXT)
RETURNS TABLE (
  max_generations INT,
  max_storage_bytes BIGINT,
  max_height INT
) AS $$
  SELECT
    CASE p_plan_type
      WHEN 'basic' THEN 10
      WHEN 'premium' THEN 30
      WHEN 'lifetime' THEN 30
      ELSE 10
    END,
    CASE p_plan_type
      WHEN 'basic' THEN 5::bigint * 1024 * 1024 * 1024      -- 5GB
      WHEN 'premium' THEN 20::bigint * 1024 * 1024 * 1024  -- 20GB
      WHEN 'lifetime' THEN 50::bigint * 1024 * 1024 * 1024 -- 50GB
      ELSE 5::bigint * 1024 * 1024 * 1024
    END,
    CASE p_plan_type
      WHEN 'basic' THEN 720
      WHEN 'premium' THEN 1080
      WHEN 'lifetime' THEN 1080
      ELSE 720
    END;
$$ LANGUAGE sql IMMUTABLE;

-- 5) 사용자 할당량 조회 (없으면 생성)
CREATE OR REPLACE FUNCTION public.ensure_user_quota(p_user_id TEXT, p_plan_type TEXT DEFAULT 'basic')
RETURNS public.user_quotas AS $$
  INSERT INTO public.user_quotas (user_id, plan_type, storage_usage_bytes, generation_count_this_month, quota_reset_at, updated_at)
  VALUES (p_user_id, p_plan_type, 0, 0, date_trunc('month', now()) + interval '1 month', now())
  ON CONFLICT (user_id) DO UPDATE SET
    plan_type = COALESCE(EXCLUDED.plan_type, user_quotas.plan_type),
    updated_at = now()
  RETURNING *;
$$ LANGUAGE sql;

-- 6) 할당량 증가 (합성 완료 시)
CREATE OR REPLACE FUNCTION public.increment_user_quota(
  p_user_id TEXT,
  p_added_storage_bytes BIGINT DEFAULT 0,
  p_increment_generation BOOLEAN DEFAULT true
)
RETURNS void AS $$
  INSERT INTO public.user_quotas (user_id, plan_type, storage_usage_bytes, generation_count_this_month, quota_reset_at, updated_at)
  VALUES (p_user_id, 'basic', p_added_storage_bytes, CASE WHEN p_increment_generation THEN 1 ELSE 0 END, date_trunc('month', now()) + interval '1 month', now())
  ON CONFLICT (user_id) DO UPDATE SET
    storage_usage_bytes = user_quotas.storage_usage_bytes + p_added_storage_bytes,
    generation_count_this_month = user_quotas.generation_count_this_month + CASE WHEN p_increment_generation THEN 1 ELSE 0 END,
    updated_at = now();
$$ LANGUAGE sql;
