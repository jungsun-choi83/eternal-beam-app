-- 후보(candidate) → 검증 → 정규(canonical) 승격 + 세션 종료 상태 + 멱등 환불.
--
-- 순수 추가(additive)다. 기존 행은:
--   attempt=1, candidate_url/promoted_at/validation = null  (실제로 그러했다)
--   refunded_at/finalized_at = null                          (환불·종료 이력 없음)
-- status 에는 CHECK 를 걸지 않는다 — 배포 순서가 어긋나도 쓰기가 거부되지 않게.

alter table if exists public.motion_generation_jobs
  add column if not exists candidate_url text,
  add column if not exists attempt int not null default 1,
  add column if not exists validation jsonb,
  add column if not exists promoted_at timestamptz;

comment on column public.motion_generation_jobs.candidate_url is
  '검증 전 후보 MP4 위치 ({user}/{pet}/candidates/{PLACE}_{ACTION}_{attempt}_{job}.mp4)';
comment on column public.motion_generation_jobs.attempt is
  '이 액션의 시도 번호 (1 = 최초, 2 = 재시도). MAX_ACTION_ATTEMPTS 로 제한';
comment on column public.motion_generation_jobs.validation is
  '진단 검증 결과(드리프트 지표 등). 현재는 비차단(non-blocking)';
comment on column public.motion_generation_jobs.promoted_at is
  '정규 경로로 승격된 시각. null 이면 아직 canonical 이 아니다';

alter table if exists public.credit_generation_sessions
  add column if not exists refunded_at timestamptz,
  add column if not exists finalized_at timestamptz;

comment on column public.credit_generation_sessions.refunded_at is
  '환불 시각. 설정돼 있으면 중복 환불 금지 (웹훅 재전송 방어)';
comment on column public.credit_generation_sessions.finalized_at is
  '세션이 종료 상태(completed/partial/failed)로 확정된 시각';

-- 세션별 작업 조회 — 상태 재계산이 매 웹훅마다 돈다.
create index if not exists motion_generation_jobs_session_idx
  on public.motion_generation_jobs (session_id);
