-- 펫 신원 프로필 (Visual + Structural Identity, Phase 2) — 레퍼런스에서 **파생된** 메타데이터.
--
-- ── 무엇인가 ────────────────────────────────────────────────────────────────
-- pet_reference_images 의 원본 증거를 분석해 만든 버전드 신원 프로필이다.
-- 이후 파이프라인(정본 펫 이미지 생성, 액션 키프레임, 생성 QA)이 "이 펫은
-- 어떻게 생겼고 어떤 구조인가"를 조회하는 곳.
--
-- ── 원칙 ────────────────────────────────────────────────────────────────────
-- 1) 원본 사진이 정본이다. 프로필은 파생 메타데이터일 뿐이며 원본을 대체하거나
--    덮어쓸 수 없다 — 이 테이블은 원본 행/객체를 절대 건드리지 않는다.
-- 2) 프로필은 불변(append-only)이다. 재분석은 새 version 행을 만든다.
--    silent overwrite 로 "그때 생성에 쓴 신원"을 잃지 않기 위해서다.
-- 3) 증거가 없는 항목은 'unknown' 으로 기록된다. 분석기가 추측을 채우지 않는다.
--
-- visual_identity / structural_identity 를 jsonb 로 두는 이유: 분석기 스키마가
-- 아직 진화 중이다(motion_generation_jobs.validation 과 같은 선택). 필드별
-- 신뢰도는 payload 안에 함께 기록된다. analyzer_versions 가 어떤 분석기가
-- 만든 값인지 영구히 남긴다.

create table if not exists public.pet_identity_profiles (
  id uuid primary key default gen_random_uuid(),
  pet_id text not null,
  -- 프로필의 주 content_id (원본 레퍼런스들의 content). 조회 편의용.
  content_id text,
  -- 빌드 시점의 신원. pet_reference_images.user_id 와 같은 주의 사항.
  user_id text not null,
  -- (pet_id 안에서) 1부터 증가. 최신 = max(version).
  version int not null,
  -- complete = 분석 가능한 원본이 하나 이상 실제 분석됨
  -- partial  = 프로필은 만들었지만 일부/전부가 unknown (예: 누끼 없음)
  status text not null check (status in ('complete', 'partial')),
  -- 이 프로필이 근거로 삼은 pet_reference_images.id 목록 (원본만).
  source_reference_ids jsonb not null default '[]'::jsonb,
  -- 레퍼런스별 적격성 평가 (reference id → 평가 결과). Phase 3 의 신뢰
  -- 레퍼런스 선택 근거.
  reference_eligibility jsonb not null default '{}'::jsonb,
  visual_identity jsonb not null default '{}'::jsonb,
  structural_identity jsonb not null default '{}'::jsonb,
  -- 카테고리별 known/unknown 집계 — "이 프로필이 얼마나 채워졌는가".
  completeness jsonb not null default '{}'::jsonb,
  -- 어떤 분석기/모델 버전이 이 값을 만들었는가 (재현성·드리프트 추적).
  analyzer_versions jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- 버전은 펫 안에서 유일 — 경쟁 빌드가 같은 버전을 갖지 못한다.
create unique index if not exists pet_identity_profiles_version_uidx
  on public.pet_identity_profiles (pet_id, version);

create index if not exists pet_identity_profiles_pet_idx
  on public.pet_identity_profiles (pet_id, created_at desc);
create index if not exists pet_identity_profiles_user_idx
  on public.pet_identity_profiles (user_id, pet_id);

comment on table public.pet_identity_profiles is
  '레퍼런스에서 파생된 버전드 펫 신원 프로필 (append-only). 원본 증거를 대체하지 않는다';
comment on column public.pet_identity_profiles.reference_eligibility is
  '레퍼런스별 신원 작업 적격성 — Phase 3 의 신뢰 레퍼런스 선택 근거';
comment on column public.pet_identity_profiles.analyzer_versions is
  '값을 만든 분석기/모델 버전. unknown 필드는 "그 버전이 몰랐다"는 기록이다';
