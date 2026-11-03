-- Eternal Beam: exhibition_prep_runs (전시용 리깅 입력 패키지 실행)
--
-- 일반 펫 생성(pet_generation_runs)과 완전히 분리된 실행 테이블.
--   QUEUED → RUNNING(stage: CUTOUT → MAPS → PACKAGE) → READY | NEEDS_REVIEW | FAILED
-- 산출물은 storage 의 exhibition/{id}/ 아래에만 쓴다 (user_assets / pet_references 행 없음).
-- 처리: backend/workers/exhibition_prep_worker.py

CREATE EXTENSION IF NOT EXISTS pgcrypto; -- gen_random_uuid()

CREATE TABLE IF NOT EXISTS public.exhibition_prep_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  exhibition_id TEXT,
  created_by TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'QUEUED'
    CHECK (status IN ('QUEUED', 'RUNNING', 'READY', 'NEEDS_REVIEW', 'FAILED')),
  stage TEXT CHECK (stage IS NULL OR stage IN ('CUTOUT', 'MAPS', 'PACKAGE')),
  -- exhibition/{id}/source.png (EXIF 회전 반영, 긴 변 상한 적용된 PNG)
  source_path TEXT NOT NULL,
  -- {"source_sha256", "source_wh", "processing_wh", "processing_scale"}
  source_info JSONB NOT NULL DEFAULT '{}'::jsonb,
  -- 운영자 머리 위치 힌트 {"xy_in_source": [x, y]} (선택)
  head_hint JSONB,
  review_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- {"subject_rgba.png": "exhibition/<id>/subject_rgba.png", ...}
  outputs_json JSONB,
  manifest_json JSONB,
  error_code TEXT,
  error_message TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  claimed_by TEXT,
  claimed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_exhibition_prep_runs_status_created
  ON public.exhibition_prep_runs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_exhibition_prep_runs_exhibition
  ON public.exhibition_prep_runs(exhibition_id, created_at);

-- 서비스 롤 전용: 정책 없이 RLS 만 켠다 (anon/authenticated 접근 불가).
ALTER TABLE public.exhibition_prep_runs ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.set_exhibition_prep_runs_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_exhibition_prep_runs_updated_at ON public.exhibition_prep_runs;
CREATE TRIGGER trg_exhibition_prep_runs_updated_at
  BEFORE UPDATE ON public.exhibition_prep_runs
  FOR EACH ROW EXECUTE FUNCTION public.set_exhibition_prep_runs_updated_at();
