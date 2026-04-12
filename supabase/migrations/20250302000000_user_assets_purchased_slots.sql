-- Eternal Beam: user_assets, purchased_slots (영상 처리 SSR 연동)
-- Supabase SQL Editor에서 실행하세요.

-- 1) user_assets: 원본 사진, 누끼 사진, 합성 영상 URL
CREATE TABLE IF NOT EXISTS public.user_assets (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id TEXT NOT NULL,
  content_id TEXT NOT NULL,
  asset_type TEXT NOT NULL,
  url TEXT NOT NULL,
  theme_id TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, content_id, asset_type)
);

CREATE INDEX IF NOT EXISTS idx_user_assets_user_content ON public.user_assets(user_id, content_id);

ALTER TABLE public.user_assets ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all for user_assets" ON public.user_assets;
CREATE POLICY "Allow all for user_assets" ON public.user_assets FOR ALL USING (true) WITH CHECK (true);

-- 2) purchased_slots: 유저가 구매한 테마 및 권한
CREATE TABLE IF NOT EXISTS public.purchased_slots (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id TEXT NOT NULL,
  theme_id TEXT NOT NULL,
  payment_id TEXT,
  payment_status BOOLEAN DEFAULT true,
  purchased_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, theme_id)
);

CREATE INDEX IF NOT EXISTS idx_purchased_slots_user ON public.purchased_slots(user_id);

ALTER TABLE public.purchased_slots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all for purchased_slots" ON public.purchased_slots;
CREATE POLICY "Allow all for purchased_slots" ON public.purchased_slots FOR ALL USING (true) WITH CHECK (true);

-- 3) Storage 버킷 'user-assets'는 대시보드에서 수동 생성 후 Public 또는 서비스 역할로 업로드 가능하도록 설정하세요.
