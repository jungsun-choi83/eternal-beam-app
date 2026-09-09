-- Phase 20 — 장면 × 행동 생성 작업 (유료 프로바이더 이중 제출 방지).
--
--   대상 프로젝트: kdlukiujgclczwqmwvmk  (Eternal Beam)
--   ⚠️ Soul Trace(pjoyuvqykggcuvbsnxio) 가 아니다. 생성·과금은 전부 이쪽이다.
--
-- ── 이 표가 없으면 무슨 일이 일어나는가 ─────────────────────────────────────
-- `/api/generate-pet-video` 는 **동기식**이다: 제출하고 최대 20분 폴링한다.
-- 클라이언트 타임아웃은 25분이고, 그 사이 새로고침·재시도·프록시 502 가 한 번이라도
-- 나면 같은 요청이 다시 들어온다. 이 표가 없으면 그때마다 **새 유료 Luma/WAN
-- 작업**이 제출되고, 첫 작업은 여전히 돌면서 함께 과금된다.
--
-- 그래서 서비스는 이 표를 읽지 못하면 **아무것도 제출하지 않는다**
-- (GENERATION_IDEMPOTENCY_UNAVAILABLE). 표가 없는 것이 곧 생성 정지다 —
-- 조용히 보호 없이 도는 것보다 낫다.
--
-- ── 동일성 단위 ─────────────────────────────────────────────────────────────
-- (user_id, scene_id, behavior).
--   * scene_id 는 (content, 배경 종류, 배경 id, 배치)에서 **결정적으로** 파생된다
--     → 같은 그림을 두 번 승인해도 같은 키다.
--   * behavior 는 BREATHING/BLINKING/… → 한 장면에서 여러 행동을 만드는 것은 정상이고
--     서로를 막지 않아야 한다.
--   * user_id 가 키에 있어야 남의 장면 결과를 물려받지 않는다.

create table if not exists public.scene_generation_jobs (
  -- "user|scene|BEHAVIOR". 서비스가 만드는 문자열이며 조회의 기본 경로다.
  job_key          text primary key,
  user_id          text not null,
  scene_id         text not null,
  behavior         text not null,
  --: 이 작업이 속한 펫 콘텐츠 (진단·정리용). 키의 일부는 아니다.
  content_id       text,

  -- pending   자리만 잡았다 (아직 제출 전)
  -- submitted 프로바이더에 제출했다 — provider_job_id 가 반드시 있다
  -- dreaming  프로바이더가 처리 중
  -- completed video_url 확정
  -- failed    종료. 자리를 비우면 재시도할 수 있다
  status           text not null default 'pending',

  provider         text,
  -- ⚠️ **되찾을 수 있는 유일한 단서.** 이 값이 없으면 이미 돈을 낸 작업을
  --    폴링할 방법이 없고, 남는 선택지는 재제출(= 이중 과금)뿐이다.
  provider_job_id  text,

  video_url        text,
  error            text,

  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

-- ── 동일성 보장 ─────────────────────────────────────────────────────────────
-- job_key 의 PK 만으로도 충분하지만, 키 문자열 조립 규칙이 바뀌어도 논리적
-- 동일성이 유지되도록 (user, scene, behavior) 에도 UNIQUE 를 건다.
-- 동시 요청 둘이 각각 insert 를 시도하면 하나만 이기고, 진 쪽은 23505 를 받아
-- **제출하지 않고 물러난다**(services/scene_generation_jobs.reserve).
create unique index if not exists scene_generation_jobs_identity_uidx
  on public.scene_generation_jobs (user_id, scene_id, behavior);

-- 한 장면의 모든 행동을 한 번에 보는 경로 (운영 진단).
create index if not exists scene_generation_jobs_scene_idx
  on public.scene_generation_jobs (scene_id);

-- 정체된 작업 회수(리컨사일러/죽은 예약 회수)가 쓰는 경로.
create index if not exists scene_generation_jobs_status_updated_idx
  on public.scene_generation_jobs (status, updated_at)
  where status in ('pending', 'submitted', 'dreaming');

-- 제출된 작업은 프로바이더 id 를 반드시 갖는다. 이것이 깨지면 되찾을 수 없는
-- 유료 작업이 생긴다는 뜻이므로, 조용히 넘어가지 않고 DB 가 막는다.
alter table public.scene_generation_jobs
  drop constraint if exists scene_generation_jobs_submitted_has_job_id;
alter table public.scene_generation_jobs
  add constraint scene_generation_jobs_submitted_has_job_id
  check (status <> 'submitted' or provider_job_id is not null);

alter table public.scene_generation_jobs
  drop constraint if exists scene_generation_jobs_status_check;
alter table public.scene_generation_jobs
  add constraint scene_generation_jobs_status_check
  check (status in ('pending', 'submitted', 'dreaming', 'completed', 'failed'));

comment on table public.scene_generation_jobs is
  '정본 장면 × 행동 → 유료 프로바이더 작업 1건. 이 표를 읽지 못하면 생성은 시작되지 않는다';
comment on column public.scene_generation_jobs.provider_job_id is
  '이미 제출된 유료 작업을 되찾는 유일한 단서. 없으면 재제출 = 이중 과금';
comment on column public.scene_generation_jobs.job_key is
  'user|scene|BEHAVIOR. scene_id 가 결정적이라 같은 승인은 같은 키로 수렴한다';
