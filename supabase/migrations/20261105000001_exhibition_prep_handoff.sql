-- Eternal Beam: exhibition_prep_runs 핸드오프 컬럼 (외부 전시 시스템에 패키지 전달)
--
-- prep status 는 그대로 둔다 (READY = PACKAGE_READY). 핸드오프는 별도 컬럼:
--   handoff_status: NULL(미전송) → HANDOFF_SENDING → HANDOFF_CONFIRMED | HANDOFF_FAILED
-- 핸드오프 실패/재시도는 저장된 exhibition/{id}/ 패키지를 다시 보낼 뿐이며
-- status 를 QUEUED/RUNNING 으로 되돌리지 않는다 (= CUTOUT/MAPS 재실행 없음).
-- 클라이언트: backend/services/exhibition_handoff_client.py

ALTER TABLE public.exhibition_prep_runs
  ADD COLUMN IF NOT EXISTS handoff_status TEXT
    CHECK (handoff_status IS NULL OR handoff_status IN ('HANDOFF_SENDING', 'HANDOFF_CONFIRMED', 'HANDOFF_FAILED')),
  -- 현재 전송 시도 토큰 (compare-and-set 펜싱용 UUID)
  ADD COLUMN IF NOT EXISTS handoff_attempt_id TEXT,
  ADD COLUMN IF NOT EXISTS handoff_attempts INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS handoff_requested_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS handoff_confirmed_at TIMESTAMPTZ,
  -- 보낸 페이로드 (서명 URL 대신 저장 경로 — 토큰을 DB 에 남기지 않는다)
  ADD COLUMN IF NOT EXISTS handoff_request JSONB,
  -- 외부 시스템 ACK 원문 {"run_id", "accepted": true, ...}
  ADD COLUMN IF NOT EXISTS handoff_ack JSONB,
  ADD COLUMN IF NOT EXISTS handoff_error_code TEXT,
  ADD COLUMN IF NOT EXISTS handoff_error_message TEXT;

CREATE INDEX IF NOT EXISTS idx_exhibition_prep_runs_handoff
  ON public.exhibition_prep_runs(handoff_status, created_at)
  WHERE handoff_status IS NOT NULL;
