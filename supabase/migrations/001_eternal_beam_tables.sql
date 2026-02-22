-- Eternal Beam: Supabase 테이블 및 함수
-- Supabase 대시보드 → SQL Editor에서 이 파일 내용 붙여넣고 Run 하세요.

-- 1) 콘텐츠 테이블
CREATE TABLE IF NOT EXISTS public.contents (
  content_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  main_photo_url TEXT,
  hologram_video_id TEXT,
  video_url TEXT,
  background_image_url TEXT,
  background_theme_id TEXT,
  background_theme_name TEXT,
  composed_preview_url TEXT,
  mixed_audio_url TEXT,
  payment_id TEXT,
  payment_status TEXT DEFAULT 'completed',
  amount INTEGER DEFAULT 0,
  nfc_slot_number INTEGER,
  nfc_written BOOLEAN DEFAULT FALSE,
  nfc_written_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2) 슬롯별 콘텐츠 매핑 (기기: "2번 슬롯" → content_id → 영상 URL)
CREATE TABLE IF NOT EXISTS public.slot_content_mapping (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  slot_number INTEGER NOT NULL,
  content_id TEXT NOT NULL REFERENCES public.contents(content_id) ON DELETE CASCADE,
  device_id TEXT,
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(slot_number)
);

-- 3) 기기 슬롯 이벤트 로그 (선택)
CREATE TABLE IF NOT EXISTS public.device_slot_events (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  slot_number INTEGER NOT NULL,
  content_id TEXT,
  device_id TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4) RLS 및 정책
ALTER TABLE public.contents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.slot_content_mapping ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.device_slot_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow all for contents" ON public.contents;
CREATE POLICY "Allow all for contents" ON public.contents FOR ALL USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow all for slot_mapping" ON public.slot_content_mapping;
CREATE POLICY "Allow all for slot_mapping" ON public.slot_content_mapping FOR ALL USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow all for device_events" ON public.device_slot_events;
CREATE POLICY "Allow all for device_events" ON public.device_slot_events FOR ALL USING (true) WITH CHECK (true);

-- 5) 슬롯 번호로 재생 정보 조회 (기기/앱에서 RPC 호출)
CREATE OR REPLACE FUNCTION public.get_playback_by_slot(p_slot_number INTEGER)
RETURNS TABLE (
  content_id TEXT,
  video_url TEXT,
  mixed_audio_url TEXT,
  composed_preview_url TEXT,
  background_theme_name TEXT
) AS $$
  SELECT c.content_id, c.video_url, c.mixed_audio_url, c.composed_preview_url, c.background_theme_name
  FROM public.slot_content_mapping m
  JOIN public.contents c ON c.content_id = m.content_id
  WHERE m.slot_number = p_slot_number
  LIMIT 1;
$$ LANGUAGE sql SECURITY DEFINER;
