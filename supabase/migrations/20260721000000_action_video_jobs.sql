-- Eternal Beam: action_video_jobs (LivePortrait 액션 20종 배치 생성 잡 큐)
-- Supabase SQL Editor에서 실행하세요.
--
-- 큐/워커 패턴: FastAPI가 이 테이블에 행을 넣고(enqueue), 사용자의 로컬 RTX 4090
-- 머신에서 도는 backend/workers/live_portrait_worker.py 가 status='queued' 행을
-- polling으로 집어가 처리(claim)한다. Redis/Celery 등 추가 인프라 없이 기존
-- Supabase(Postgres)만으로 충분한 스케일(견당 20개 영상, 동시 워커 1~소수).

CREATE EXTENSION IF NOT EXISTS pgcrypto; -- gen_random_uuid()

CREATE TABLE IF NOT EXISTS public.action_video_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id TEXT NOT NULL,
  content_id TEXT NOT NULL,
  dog_image_url TEXT NOT NULL,
  -- queued | running | done | failed
  status TEXT NOT NULL DEFAULT 'queued',
  -- {"total": 20, "completed": 3, "current_action": "run", "updated_at": "..."}
  progress_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  -- 완료 후 20개 결과 매니페스트([{action, driving_video, output_url, duration_sec, resolution, success, error}, ...])
  results_json JSONB,
  error TEXT,
  -- 워커 클레임(선점) 추적 — 여러 워커 인스턴스 동시 polling 시 중복 처리 방지 및
  -- 크래시된 워커가 물고 있던 job을 일정 시간 후 재클레임하기 위함(스테일 체크).
  claimed_by TEXT,
  claimed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_action_video_jobs_status_created
  ON public.action_video_jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_action_video_jobs_user_content
  ON public.action_video_jobs(user_id, content_id);

ALTER TABLE public.action_video_jobs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all for action_video_jobs" ON public.action_video_jobs;
CREATE POLICY "Allow all for action_video_jobs" ON public.action_video_jobs FOR ALL USING (true) WITH CHECK (true);

-- 워커가 매 잡마다 updated_at을 직접 갱신하지만, 안전망으로 트리거도 둔다.
CREATE OR REPLACE FUNCTION public.set_action_video_jobs_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_action_video_jobs_updated_at ON public.action_video_jobs;
CREATE TRIGGER trg_action_video_jobs_updated_at
  BEFORE UPDATE ON public.action_video_jobs
  FOR EACH ROW EXECUTE FUNCTION public.set_action_video_jobs_updated_at();
