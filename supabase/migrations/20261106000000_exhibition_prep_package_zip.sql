-- Eternal Beam: exhibition_prep_runs 핸드오프 ZIP 패키지 컬럼
--
-- READY 이고 queue_number 가 있는 실행은 package_id("visitor_0028")와 그 ZIP
-- (exhibition/{id}/visitor_0028.zip, subject_rgba/breathing_weight/locked_mask/
-- manifest 4개, preview_overlay 제외) 을 하나 더 만든다. 핸드오프는 이 ZIP 의
-- 서명 URL(packed_url) 하나만 외부 시스템에 보낸다.
-- 생성: backend/services/exhibition_prep_service.py (process_run)
-- 전송: backend/services/exhibition_handoff_client.py (build_payload)

ALTER TABLE public.exhibition_prep_runs
  ADD COLUMN IF NOT EXISTS package_id TEXT,
  ADD COLUMN IF NOT EXISTS package_zip_path TEXT;
