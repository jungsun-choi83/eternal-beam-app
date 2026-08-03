-- Eternal Beam: auto_rigging_jobs (SAM2+포즈추정+Spine2D 자동 리깅 잡 큐)
-- Supabase SQL Editor에서 실행하세요.
--
-- action_video_jobs(LivePortrait 액션 20종 큐, 20260721000000_action_video_jobs.sql)
-- 및 background_video_jobs(20260721000100_background_video_jobs.sql)와 동일한
-- 큐/워커 관례(Supabase 테이블 polling, Redis/Celery 없음)를 따르는 자매 테이블.
--
-- 이 큐는 "Action(달려오기/짖기/배깔기) 리깅" 파이프라인용 — LivePortrait(영상
-- 생성) 큐와는 완전히 별개다: 산출물이 mp4가 아니라 Spine2D 스켈레톤 에셋
-- (skeleton.json + skeleton.atlas + skeleton.png)이다. 사용자의 최신 결정에 따라
-- LivePortrait는 보조/비교 대상으로 격하되었고, 이 SAM2+포즈추정+Spine2D
-- 리깅 파이프라인이 Action 카테고리의 1차 경로가 된다(자세한 배경은
-- docs/Spine2D_리깅_파이프라인_진행상황.md 참고).
--
-- 처리 흐름: FastAPI가 이 테이블에 행을 넣고(enqueue) → 사용자의 로컬 RTX 4090
-- 머신에서 도는 backend/workers/auto_rigging_worker.py 가 polling으로 집어가
-- (SAM2 세그멘테이션 → 포즈 추정 → 본 생성+이미지 워핑 → 액션 애니메이션 재타겟
-- → Spine 에셋 3파일 업로드) 처리한다.

CREATE EXTENSION IF NOT EXISTS pgcrypto; -- gen_random_uuid()

CREATE TABLE IF NOT EXISTS public.auto_rigging_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id TEXT NOT NULL,
  content_id TEXT NOT NULL,
  -- 반려동물 사진(누끼 여부 무관 — 알파채널이 없으면 워커가 SAM2/GrabCut으로 자체 세그멘테이션).
  pet_image_url TEXT NOT NULL,
  -- 이번 잡에서 만들 액션들. 현재 구현된 값은 "lie_down"(배깔기) 뿐 —
  -- "run"(달려오기)/"bark"(짖기)는 아직 미구현(문서의 다음 단계 참고). 빈 배열이면
  -- 워커가 구현된 액션 전체(현재는 lie_down만)를 기본으로 생성.
  requested_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- "heuristic" | "deeplabcut_superanimal" | "auto" — backend/services/pose_estimation_service.py 참고.
  pose_backend TEXT NOT NULL DEFAULT 'heuristic',
  -- queued | running | done | failed
  status TEXT NOT NULL DEFAULT 'queued',
  -- {"stage": "segmentation" | "pose_estimation" | "rigging" | "animation" | "uploading",
  --   "detail": "...", "updated_at": "..."}
  progress_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  -- 완료 후 {"skeleton_json_url":..., "atlas_url":..., "atlas_page_url":...,
  --          "debug_keypoints_url":..., "pose_backend_used":..., "warnings": [...]}
  result_json JSONB,
  error TEXT,
  -- 워커 클레임(선점) 추적 — action_video_jobs와 동일한 목적.
  claimed_by TEXT,
  claimed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auto_rigging_jobs_status_created
  ON public.auto_rigging_jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_auto_rigging_jobs_user_content
  ON public.auto_rigging_jobs(user_id, content_id);

ALTER TABLE public.auto_rigging_jobs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all for auto_rigging_jobs" ON public.auto_rigging_jobs;
CREATE POLICY "Allow all for auto_rigging_jobs" ON public.auto_rigging_jobs FOR ALL USING (true) WITH CHECK (true);

CREATE OR REPLACE FUNCTION public.set_auto_rigging_jobs_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_auto_rigging_jobs_updated_at ON public.auto_rigging_jobs;
CREATE TRIGGER trg_auto_rigging_jobs_updated_at
  BEFORE UPDATE ON public.auto_rigging_jobs
  FOR EACH ROW EXECUTE FUNCTION public.set_auto_rigging_jobs_updated_at();
