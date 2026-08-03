-- Eternal Beam: background_video_jobs ("내 사진으로 나만의 배경 만들기" 잡 큐)
-- Supabase SQL Editor에서 실행하세요.
--
-- action_video_jobs(LivePortrait 액션 20종 큐, 20260721000000_action_video_jobs.sql)와
-- 동일한 큐/워커 관례(Supabase 테이블 polling, Redis/Celery 없음)를 따르는 자매
-- 테이블 — 산출물 모양이 달라(1개 배경 영상 vs 20개 액션 영상 배열) 컬럼을
-- 억지로 공유하지 않고 별도 테이블로 분리했다(자세한 이유는
-- backend/services/background_video_jobs.py 상단 docstring 참고).
--
-- 처리 흐름: FastAPI가 이 테이블에 행을 넣고(enqueue) → 사용자의 로컬 RTX 4090
-- 머신에서 도는 backend/workers/background_video_worker.py 가 polling으로 집어가
-- (SAM2 인페인팅 → Luma 배경 애니메이션 → seamless loop → fps/duration 동기화 →
-- Supabase Storage 업로드) 처리한다.

CREATE EXTENSION IF NOT EXISTS pgcrypto; -- gen_random_uuid()

CREATE TABLE IF NOT EXISTS public.background_video_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id TEXT NOT NULL,
  content_id TEXT NOT NULL,
  -- 강아지가 포함된 사용자의 "원본" 사진(누끼 전) — 배경 영역을 복원하려면
  -- 누끼(강아지만 남은 PNG)가 아니라 원본 전체 사진이 필요하다.
  source_image_url TEXT NOT NULL,
  -- queued | running | done | failed
  status TEXT NOT NULL DEFAULT 'queued',
  -- {"stage": "inpainting" | "luma_generation" | "seamless_loop" | "syncing_fps" |
  --   "uploading", "detail": "...", "updated_at": "..."}
  progress_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  -- 완료 후 최종 배경 영상 URL(fps/duration 동기화 + seamless loop까지 끝난 결과).
  result_video_url TEXT,
  -- {"inpaint_meta": {...}, "luma_prompt": "...", "target_fps": 24, "target_duration_sec": 4.0, ...}
  result_meta_json JSONB,
  error TEXT,
  -- 호출자가 이미 알고 있는 실제 강아지 영상의 fps/길이가 있으면 여기 지정 —
  -- 없으면 워커가 background_video_sync.py의 기본값(24fps/4.0초)을 쓴다.
  target_fps DOUBLE PRECISION,
  target_duration_sec DOUBLE PRECISION,
  -- 워커 클레임(선점) 추적 — action_video_jobs와 동일한 목적.
  claimed_by TEXT,
  claimed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_background_video_jobs_status_created
  ON public.background_video_jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_background_video_jobs_user_content
  ON public.background_video_jobs(user_id, content_id);

ALTER TABLE public.background_video_jobs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all for background_video_jobs" ON public.background_video_jobs;
CREATE POLICY "Allow all for background_video_jobs" ON public.background_video_jobs FOR ALL USING (true) WITH CHECK (true);

CREATE OR REPLACE FUNCTION public.set_background_video_jobs_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_background_video_jobs_updated_at ON public.background_video_jobs;
CREATE TRIGGER trg_background_video_jobs_updated_at
  BEFORE UPDATE ON public.background_video_jobs
  FOR EACH ROW EXECUTE FUNCTION public.set_background_video_jobs_updated_at();
