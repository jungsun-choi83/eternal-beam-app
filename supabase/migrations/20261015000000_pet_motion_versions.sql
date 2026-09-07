-- 모션 비디오 (Reference-locked Video Generation, Phase 6) — 테마 독립 펫 모션.
--
-- ── 무엇인가 ────────────────────────────────────────────────────────────────
-- Phase 5.1 계약(resolve_video_generation_spec) + 승인된 키프레임에서 생성한
-- 펫 모션 영상의 버전드 기록이다. **펫 모션 자산**이지 최종 테마 장면이 아니다 —
-- 테마는 이후 합성 레이어다.
--
-- ── 왜 generated_motions 를 확장하지 않는가 ─────────────────────────────────
-- generated_motions / owned_generated_assets 는 **프로덕션 계약**이다:
-- action_id CHECK, /device/sync 의 4모션 규약, 크레딧 원장이 그 위에 서 있다.
-- Phase 6 자산을 거기 쓰면 프로덕션이 바뀐다. 이 테이블들은 Phase 4/5 와 같은
-- 병행 아키텍처이며, Pet Action Library 로의 승격은 이후 단계의 명시적 작업이다.
--
-- ── 원칙 (Phase 4/5 와 동일) ────────────────────────────────────────────────
-- * 버전 불변, 후보는 QA 이전에 저장, ERROR(프로바이더) ≠ FAIL(QA).
-- * FAIL 후보는 절대 승격되지 않는다 (fail-open 금지).
-- * 근거 사슬: 모션 영상 → 모션 스펙 버전 → 키프레임 버전 → 정본 → 세트 → 원본.

create table if not exists public.pet_motion_versions (
  id uuid primary key default gen_random_uuid(),
  pet_id text not null,
  user_id text not null,
  motion_id text not null,
  motion_class text not null,
  motion_spec_version text,
  -- 어떤 키프레임(버전)에서 생성됐는가 — 근거 사슬의 다음 고리.
  start_keyframe_id uuid,
  start_keyframe_version int,
  target_keyframe_id uuid,
  target_keyframe_version int,
  canonical_version_id uuid,
  version int not null,
  status text not null default 'building' check (
    status in ('building', 'complete', 'review', 'failed')
  ),
  selected_candidate_id uuid,
  selection_reason text,
  video_strategy text,
  -- 명시적 출력 사양 (종횡비/해상도/길이/오디오 off) — 프로바이더 기본값에
  -- 기대지 않는다. Wan 16:9→9:16 사고의 재발 방지.
  output_spec jsonb not null default '{}'::jsonb,
  prompt text,
  prompt_version text,
  qa_summary jsonb not null default '{}'::jsonb,
  analyzer_versions jsonb not null default '{}'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  completed_at timestamptz
);

create unique index if not exists pet_motion_versions_version_uidx
  on public.pet_motion_versions (pet_id, motion_id, version);
create index if not exists pet_motion_versions_pet_idx
  on public.pet_motion_versions (pet_id, motion_id, created_at desc);
create index if not exists pet_motion_versions_user_idx
  on public.pet_motion_versions (user_id, pet_id);

create table if not exists public.pet_motion_candidates (
  id uuid primary key default gen_random_uuid(),
  motion_version_id uuid not null,
  pet_id text not null,
  user_id text not null,
  motion_id text not null,
  provider text not null,
  model text,
  attempt int not null default 1,
  provider_job_id text,
  start_keyframe_id uuid,
  target_keyframe_id uuid,
  motion_reference_id text,
  raw_bucket text,
  raw_video_path text,
  -- 파생(매팅된 펫 전용) 영상 — v1 은 만들지 않는다 (nullable 로 준비만).
  derived_video_path text,
  prompt_version text,
  -- 정확히 어떤 레퍼런스가 들어갔는가 (identity-locked input 기록).
  input_references jsonb not null default '[]'::jsonb,
  generation_metadata jsonb not null default '{}'::jsonb,
  qa_result jsonb not null default '{}'::jsonb,
  decision text not null default 'ERROR' check (
    decision in ('PASS', 'REVIEW', 'FAIL', 'ERROR')
  ),
  selected boolean not null default false,
  error text,
  created_at timestamptz not null default now()
);

create index if not exists pet_motion_candidates_version_idx
  on public.pet_motion_candidates (motion_version_id, attempt);
create index if not exists pet_motion_candidates_pet_idx
  on public.pet_motion_candidates (pet_id, motion_id, created_at desc);

comment on table public.pet_motion_versions is
  '테마 독립 펫 모션 영상 버전 (append-only). Pet Action Library 승격은 이후 단계의 명시적 작업이다';
comment on table public.pet_motion_candidates is
  '모션 영상 후보 — QA 이전에 저장. FAIL 은 절대 승격되지 않는다 (fail-open 금지)';
