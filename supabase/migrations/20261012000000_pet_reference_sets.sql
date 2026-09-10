-- 신뢰 레퍼런스 세트 (Multi-view Trusted References, Phase 3) — 버전드 선택 팩.
--
-- ── 무엇인가 ────────────────────────────────────────────────────────────────
-- pet_reference_images 의 원본들과 Phase 2 신원 프로필을 근거로, "이 펫의 가장
-- 좋은 실제 증거"를 역할(PRIMARY_FACE, PRIMARY_FULL_BODY, …)별로 고른 팩이다.
-- Phase 4(정본 펫 이미지 빌더)는 여기서 "어떤 신뢰 가능한 신원 증거가 있는가"를
-- 조회한다.
--
-- ── 원칙 ────────────────────────────────────────────────────────────────────
-- * 세트는 불변(append-only)이다. 사진이 추가되면 새 version 이 생긴다 —
--   역사적 세트를 조용히 바꾸지 않는다.
-- * 모든 선택 항목은 pet_reference_images.id 로 원본 증거까지 추적된다.
--   근거를 모르는 trusted_reference.png 는 존재할 수 없다.
-- * 뷰가 없다고 실패하지 않는다 — coverage 가 MISSING 을 보고할 뿐이다.
--   Phase 3 는 없는 뷰를 절대 만들어 내지 않는다.
-- * 선택에 쓰인 레퍼런스별 분석 전체(reference_analysis)를 세트에 박제한다 —
--   VLM 등 비결정적 분석이 섞여도 "이 세트가 왜 이렇게 선택됐는가"는 영구히
--   재현 가능하다 (결정론 요구).
--
-- 항목을 자식 테이블 대신 items jsonb 로 두는 이유: 항목은 항상 세트와 함께
-- 통째로 읽히고, pet_identity_profiles 의 jsonb 관례와 인메모리 mock 이중
-- 백엔드를 그대로 따르기 위해서다. 각 항목은 reference_id / role / view_label /
-- pose_label / selection_score / component_scores / rank / selection_reason 을 갖는다.
--
-- view_label 은 pet_reference_images.view_label CHECK 의 **상위집합**이다
-- (FRONT_LEFT_3Q / FRONT_RIGHT_3Q 추가). 같은 문자열 체계 하나만 쓴다 —
-- 레퍼런스 행의 enum 은 건드리지 않는다(그 행들은 불변이고 UNKNOWN 만 담고 있다).

create table if not exists public.pet_reference_sets (
  id uuid primary key default gen_random_uuid(),
  pet_id text not null,
  user_id text not null,
  version int not null,
  -- complete = 역할이 하나 이상 선택됨 / partial = 세트는 만들었지만 선택 불가
  status text not null check (status in ('complete', 'partial')),
  -- 근거로 쓴 Phase 2 프로필 (불변 버전 참조).
  identity_profile_id uuid,
  identity_profile_version int,
  -- 이 세트가 고려한 원본 레퍼런스 id 전체.
  source_reference_ids jsonb not null default '[]'::jsonb,
  -- 선택된 항목들. [{reference_id, role, view_label, pose_label,
  --   selection_score, component_scores, rank, selection_reason}]
  items jsonb not null default '[]'::jsonb,
  -- 선택에 쓰인 레퍼런스별 분석 스냅샷 (reference id → 분석 전체).
  reference_analysis jsonb not null default '{}'::jsonb,
  -- 뷰/부위별 커버리지 보고: GOOD | PARTIAL | MISSING.
  coverage jsonb not null default '{}'::jsonb,
  -- 잠정적 등급 (제품 요구 확정 전): LIMITED | MINIMUM | GOOD | EXCELLENT.
  completeness_tier text not null default 'LIMITED' check (
    completeness_tier in ('LIMITED', 'MINIMUM', 'GOOD', 'EXCELLENT')
  ),
  completeness_score numeric,
  analyzer_versions jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create unique index if not exists pet_reference_sets_version_uidx
  on public.pet_reference_sets (pet_id, version);

create index if not exists pet_reference_sets_pet_idx
  on public.pet_reference_sets (pet_id, created_at desc);
create index if not exists pet_reference_sets_user_idx
  on public.pet_reference_sets (user_id, pet_id);

comment on table public.pet_reference_sets is
  '버전드 신뢰 레퍼런스 팩 (append-only). 모든 항목은 pet_reference_images.id 로 원본 증거까지 추적된다';
comment on column public.pet_reference_sets.reference_analysis is
  '선택에 쓰인 레퍼런스별 분석 박제 — 비결정적 분석이 섞여도 선택 근거는 영구 재현 가능';
comment on column public.pet_reference_sets.coverage is
  '뷰/부위별 GOOD/PARTIAL/MISSING. 없는 뷰는 실패가 아니라 MISSING 보고다';
