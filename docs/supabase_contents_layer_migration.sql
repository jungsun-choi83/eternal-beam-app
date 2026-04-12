-- Content_ID 레이어 시스템 컬럼 추가
-- 기존 contents 테이블에 subject_id, scale, position_x, position_y 추가
-- (background_theme_id는 이미 있음 → background_id로 사용)

-- 컬럼이 없으면 추가 (PostgreSQL)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='contents' AND column_name='subject_id') THEN
    ALTER TABLE public.contents ADD COLUMN subject_id TEXT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='contents' AND column_name='scale') THEN
    ALTER TABLE public.contents ADD COLUMN scale REAL DEFAULT 1.0;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='contents' AND column_name='position_x') THEN
    ALTER TABLE public.contents ADD COLUMN position_x INTEGER DEFAULT 0;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='contents' AND column_name='position_y') THEN
    ALTER TABLE public.contents ADD COLUMN position_y INTEGER DEFAULT 0;
  END IF;
END $$;
