-- System B 액션 생성을 프로바이더 전환 가능하게 만들기 위한 추가 컬럼.
--
-- 순수 추가(additive)다:
--   * 기존 행은 DEFAULT 'luma' 로 읽히므로 백필이 필요 없다.
--   * luma_generation_id 는 그대로 외부 ID 자리로 쓴다 — fal 의 request_id 도
--     같은 컬럼에 들어가고, resolve 경로(조회 인덱스)가 바뀌지 않는다.
--     컬럼명이 다소 부정확해지지만 이름 변경은 별도 작업으로 미룬다.
--
-- 코드가 이 컬럼을 쓰기 **전에** 배포되어야 한다.

alter table if exists public.motion_generation_jobs
  add column if not exists provider text not null default 'luma',
  add column if not exists provider_model text;

comment on column public.motion_generation_jobs.provider is
  'luma | wan_turbo | wan_a14b — 이 작업을 제출한 프로바이더';
comment on column public.motion_generation_jobs.provider_model is
  '실제 모델 식별자 (예: ray-2, fal-ai/wan/v2.2-a14b/image-to-video/turbo)';
comment on column public.motion_generation_jobs.luma_generation_id is
  '프로바이더 외부 작업 ID (luma generation id 또는 fal request_id)';

-- 웹훅은 이 컬럼으로 작업을 되찾는다 — 조회 성능 보장.
create index if not exists motion_generation_jobs_external_id_idx
  on public.motion_generation_jobs (luma_generation_id);
