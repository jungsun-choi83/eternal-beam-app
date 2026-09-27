-- 액션 키프레임 (Action Keyframe Builder, Phase 5) — 정본 펫의 포즈별 스틸.
--
-- ── 무엇인가 ────────────────────────────────────────────────────────────────
-- 승인된 정본 펫(Phase 4)에서 "같은 펫, 통제된 다른 포즈"의 스틸 이미지를
-- 만든 기록이다. 키프레임 **역할**(NEUTRAL_IDLE, LIE, SLEEP, LOOK_UP, HAPPY)은
-- 포즈 축이지 액션 축이 아니다 — 액션 id 는 기존 레지스트리
-- (pet_scenarios / luma_idle_templates)의 것을 그대로 쓰고, 여러 액션이 하나의
-- 키프레임을 공유한다 (생성 비용 절약). 네 번째 액션 명명 체계는 없다.
--
-- ── 원칙 (Phase 4 와 동일) ──────────────────────────────────────────────────
-- * 키프레임 버전은 불변. 정본 V2 → 키프레임 V2. 옛 버전을 덮지 않는다.
-- * 후보는 QA 이전에 저장된다. raw 는 생성 증거, cutout 은 파생 보조 자산.
-- * 테마/배경/환경 오브젝트 없음 — 펫 단독, 중립 배경.
-- * 근거 사슬: 키프레임 → 정본 버전 → 레퍼런스 세트 → 원본 증거.

create table if not exists public.pet_action_keyframes (
  id uuid primary key default gen_random_uuid(),
  pet_id text not null,
  user_id text not null,
  -- 이 키프레임을 만든 정본 (불변 참조 — 어느 정본에서 나왔는지 영구 기록).
  canonical_version_id uuid not null,
  canonical_version int,
  -- 포즈 역할 (action_keyframe_spec.KEYFRAME_ROLES 의 키).
  keyframe_role text not null,
  version int not null,
  status text not null default 'building' check (
    status in ('building', 'complete', 'review', 'failed')
  ),
  selected_candidate_id uuid,
  selection_reason text,
  prompt text,
  prompt_version text,
  -- 빌드 시점의 역할 스펙 스냅샷 (supported_action_ids, required_pose,
  -- video_compat …) — 스펙이 진화해도 "그때 무엇을 요구했는지" 남는다.
  spec jsonb not null default '{}'::jsonb,
  qa_summary jsonb not null default '{}'::jsonb,
  analyzer_versions jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  completed_at timestamptz
);

create unique index if not exists pet_action_keyframes_version_uidx
  on public.pet_action_keyframes (pet_id, keyframe_role, version);
create index if not exists pet_action_keyframes_pet_idx
  on public.pet_action_keyframes (pet_id, keyframe_role, created_at desc);
create index if not exists pet_action_keyframes_user_idx
  on public.pet_action_keyframes (user_id, pet_id);

create table if not exists public.pet_action_keyframe_candidates (
  id uuid primary key default gen_random_uuid(),
  keyframe_id uuid not null,
  pet_id text not null,
  user_id text not null,
  keyframe_role text not null,
  provider text not null,
  model text,
  model_version text,
  attempt int not null default 1,
  external_job_id text,
  raw_bucket text,
  raw_object_path text,
  cutout_bucket text,
  cutout_object_path text,
  prompt_version text,
  -- 신원 앵커: 어느 정본 후보에서 나왔는가 + 보조 신뢰 레퍼런스들.
  input_canonical_candidate_id uuid,
  input_reference_ids jsonb not null default '[]'::jsonb,
  generation_metadata jsonb not null default '{}'::jsonb,
  -- identity/pose/structural/usability 컴포넌트가 모두 이 안에 있다.
  qa_result jsonb not null default '{}'::jsonb,
  decision text not null default 'ERROR' check (
    decision in ('PASS', 'REVIEW', 'FAIL', 'ERROR')
  ),
  selected boolean not null default false,
  error text,
  created_at timestamptz not null default now()
);

create index if not exists pet_action_keyframe_candidates_kf_idx
  on public.pet_action_keyframe_candidates (keyframe_id, attempt);
create index if not exists pet_action_keyframe_candidates_pet_idx
  on public.pet_action_keyframe_candidates (pet_id, keyframe_role, created_at desc);

comment on table public.pet_action_keyframes is
  '정본 펫의 포즈별 키프레임 (append-only). 역할은 포즈 축 — 액션 id 는 기존 레지스트리를 재사용한다';
comment on table public.pet_action_keyframe_candidates is
  '키프레임 후보 — QA 이전에 저장. decision ERROR 는 프로바이더 실패로 QA FAIL 과 구분된다';
