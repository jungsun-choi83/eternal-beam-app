-- Eternal Beam: 전시 대기열 (스태프 접수 번호 + 전시 표시 상태)
--
-- 처리 상태(status: QUEUED/RUNNING/READY/NEEDS_REVIEW/FAILED)와 표시 상태는 별도 컬럼이다.
--   display_status: WAITING → UP_NEXT → NOW_SHOWING → COMPLETE
-- 표시 대기열에는 READY(핸드오프가 켜져 있으면 + HANDOFF_CONFIRMED) 실행만 들어간다.
-- queue_number 가 있는 행 = 스태프 접수 건. 운영 API 로 만든 실행은 NULL (대기열 밖).
-- 서비스: backend/services/exhibition_queue_service.py

ALTER TABLE public.exhibition_prep_runs
  ADD COLUMN IF NOT EXISTS queue_number INTEGER,
  ADD COLUMN IF NOT EXISTS pet_name TEXT,
  ADD COLUMN IF NOT EXISTS display_status TEXT,
  ADD COLUMN IF NOT EXISTS display_changed_at TIMESTAMPTZ;

ALTER TABLE public.exhibition_prep_runs
  DROP CONSTRAINT IF EXISTS exhibition_prep_runs_display_status_check,
  ADD CONSTRAINT exhibition_prep_runs_display_status_check
    CHECK (display_status IS NULL OR display_status IN ('WAITING', 'UP_NEXT', 'NOW_SHOWING', 'COMPLETE')),
  DROP CONSTRAINT IF EXISTS exhibition_prep_runs_queue_fields_check,
  ADD CONSTRAINT exhibition_prep_runs_queue_fields_check
    CHECK ((queue_number IS NULL) = (display_status IS NULL)
           AND (queue_number IS NULL OR exhibition_id IS NOT NULL)),
  DROP CONSTRAINT IF EXISTS exhibition_prep_runs_pet_name_check,
  ADD CONSTRAINT exhibition_prep_runs_pet_name_check
    CHECK (pet_name IS NULL OR char_length(pet_name) <= 40);

-- 전시(exhibition_id)마다 번호는 하나씩, UP_NEXT · NOW_SHOWING 은 각각 최대 한 건.
CREATE UNIQUE INDEX IF NOT EXISTS uq_exhibition_prep_runs_queue_number
  ON public.exhibition_prep_runs(exhibition_id, queue_number) WHERE queue_number IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_exhibition_prep_runs_up_next
  ON public.exhibition_prep_runs(exhibition_id) WHERE display_status = 'UP_NEXT';
CREATE UNIQUE INDEX IF NOT EXISTS uq_exhibition_prep_runs_now_showing
  ON public.exhibition_prep_runs(exhibition_id) WHERE display_status = 'NOW_SHOWING';
CREATE INDEX IF NOT EXISTS idx_exhibition_prep_runs_display_queue
  ON public.exhibition_prep_runs(exhibition_id, display_status, queue_number) WHERE queue_number IS NOT NULL;

-- 전시별 순차 번호 카운터 (원자적 upsert).
CREATE TABLE IF NOT EXISTS public.exhibition_queue_counters (
  exhibition_id TEXT PRIMARY KEY,
  last_number INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE public.exhibition_queue_counters ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.exhibition_next_queue_number(p_exhibition_id TEXT)
RETURNS INTEGER
LANGUAGE sql
AS $$
  INSERT INTO public.exhibition_queue_counters AS c (exhibition_id, last_number)
  VALUES (p_exhibition_id, 1)
  ON CONFLICT (exhibition_id)
    DO UPDATE SET last_number = c.last_number + 1, updated_at = NOW()
  RETURNING last_number;
$$;

REVOKE ALL ON FUNCTION public.exhibition_next_queue_number(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.exhibition_next_queue_number(TEXT) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.exhibition_next_queue_number(TEXT) TO service_role;
