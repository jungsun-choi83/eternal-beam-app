-- 정본 펫 빌더 (Canonical Pet Builder, Phase 4) — 생성된 마스터 펫의 버전드 기록.
--
-- ── 무엇인가 ────────────────────────────────────────────────────────────────
-- Phase 3 신뢰 레퍼런스 세트(실제 증거)로부터 이미지 모델이 만든 "표준화된
-- 마스터 펫"의 이력이다. 액션/키프레임/영상이 아니다 — 이후 단계가 쓸 안정된
-- 정본 펫 이미지 그 자체다.
--
-- ── 원칙 ────────────────────────────────────────────────────────────────────
-- * 정본 버전은 불변이다. 새 레퍼런스 세트 → 새 정본 버전. V1 을 덮지 않는다.
-- * 후보는 QA **이전에** 저장된다 (과금된 생성물이 사라지지 않도록) — 그래서
--   후보는 자식 테이블이다 (motion_generation_jobs 후보 경로와 같은 이유).
-- * 모든 정본은 candidate → reference set → pet_reference_images → 원본 증거로
--   추적된다. 근거 없는 canonical.png 는 존재할 수 없다.
-- * 생성물은 role='generated' 로 대장에 기록되며 **절대** original 이 될 수 없다.

-- ── pet_reference_images.role 에 'generated' 추가 ───────────────────────────
-- Phase 1 의 CHECK 은 original/derived 뿐이었다. 정본 생성물은 제3의 부류다:
-- original(역사적 증거)도 derived(분석 보조)도 아닌 GENERATED(합성 자산).
alter table public.pet_reference_images
  drop constraint if exists pet_reference_images_role_check;
alter table public.pet_reference_images
  add constraint pet_reference_images_role_check
  check (role in ('original', 'derived', 'generated'));

-- 같은 생성 객체는 한 번만 기록된다 (derived 와 같은 규칙).
create unique index if not exists pet_reference_images_generated_object_uidx
  on public.pet_reference_images (pet_id, object_path)
  where role = 'generated';

-- ── 정본 버전 ───────────────────────────────────────────────────────────────
create table if not exists public.pet_canonical_versions (
  id uuid primary key default gen_random_uuid(),
  pet_id text not null,
  user_id text not null,
  version int not null,
  -- building  → 생성 중 (프로바이더 호출 전에 먼저 기록된다 — 과금 영수증)
  -- complete  → PASS 후보가 선택됨
  -- review    → PASS 없음, REVIEW 후보 있음 (사람 검토 대기)
  -- failed    → 쓸 수 있는 후보 없음
  status text not null default 'building' check (
    status in ('building', 'complete', 'review', 'failed')
  ),
  reference_set_id uuid,
  reference_set_version int,
  identity_profile_version int,
  -- 생성에 실제로 넣은 레퍼런스 (pet_reference_images.id, 역할 순).
  input_reference_ids jsonb not null default '[]'::jsonb,
  prompt text,
  prompt_version text,
  output_spec jsonb not null default '{}'::jsonb,
  selected_candidate_id uuid,
  selection_reason text,
  qa_summary jsonb not null default '{}'::jsonb,
  analyzer_versions jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  completed_at timestamptz
);

create unique index if not exists pet_canonical_versions_version_uidx
  on public.pet_canonical_versions (pet_id, version);
create index if not exists pet_canonical_versions_pet_idx
  on public.pet_canonical_versions (pet_id, created_at desc);
create index if not exists pet_canonical_versions_user_idx
  on public.pet_canonical_versions (user_id, pet_id);

-- ── 정본 후보 ───────────────────────────────────────────────────────────────
create table if not exists public.pet_canonical_candidates (
  id uuid primary key default gen_random_uuid(),
  canonical_version_id uuid not null,
  pet_id text not null,
  user_id text not null,
  provider text not null,
  model text,
  model_version text,
  attempt int not null default 1,
  external_job_id text,
  raw_bucket text,
  -- 생성 원본 증거. 절대 파괴하지 않는다 — cutout 은 파생 보조 자산일 뿐이다.
  raw_object_path text,
  cutout_bucket text,
  cutout_object_path text,
  prompt_version text,
  input_reference_ids jsonb not null default '[]'::jsonb,
  generation_metadata jsonb not null default '{}'::jsonb,
  qa_result jsonb not null default '{}'::jsonb,
  -- PASS | REVIEW | FAIL | ERROR (ERROR = 프로바이더/전송 실패 — QA 실패와 구분)
  decision text not null default 'ERROR' check (
    decision in ('PASS', 'REVIEW', 'FAIL', 'ERROR')
  ),
  selected boolean not null default false,
  error text,
  created_at timestamptz not null default now()
);

create index if not exists pet_canonical_candidates_version_idx
  on public.pet_canonical_candidates (canonical_version_id, attempt);
create index if not exists pet_canonical_candidates_pet_idx
  on public.pet_canonical_candidates (pet_id, created_at desc);

-- ── 사람 평가 (Phase 4 검증 하네스, 10~20마리 수동 평가) ────────────────────
create table if not exists public.pet_canonical_evaluations (
  id uuid primary key default gen_random_uuid(),
  canonical_version_id uuid not null,
  candidate_id uuid,
  pet_id text not null,
  user_id text not null,
  -- {face_identity, markings, body_proportions, tail_ears_paws, anatomy,
  --  overall_same_pet} 각 0~10.
  scores jsonb not null default '{}'::jsonb,
  verdict text not null check (verdict in ('PASS', 'REVIEW', 'FAIL')),
  notes text,
  created_at timestamptz not null default now()
);

create index if not exists pet_canonical_evaluations_version_idx
  on public.pet_canonical_evaluations (canonical_version_id);
create index if not exists pet_canonical_evaluations_pet_idx
  on public.pet_canonical_evaluations (pet_id, created_at desc);

comment on table public.pet_canonical_versions is
  '버전드 정본 펫 (append-only). 새 레퍼런스 세트 → 새 버전. 역사적 버전을 덮지 않는다';
comment on table public.pet_canonical_candidates is
  '정본 후보 — QA 이전에 저장된다. raw 는 생성 증거, cutout 은 파생 보조 자산';
comment on column public.pet_canonical_candidates.decision is
  'PASS/REVIEW/FAIL 은 QA 판정, ERROR 는 프로바이더 실패 — 둘을 섞지 않는다';
