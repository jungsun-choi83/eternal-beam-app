-- 모션 레퍼런스 라이브러리 (Phase 6.6) — 다종(多種) 모션 레퍼런스 + 매칭 메타데이터.
--
-- ── 무엇인가 ────────────────────────────────────────────────────────────────
-- "이 펫이 누구인가"(정본/키프레임)와 별개로 "호환되는 동물이 **어떻게**
-- 움직이는가"를 담는 레퍼런스 영상 대장이다. 매칭은 구조/생체역학 속성으로만
-- 한다 — 털색·무늬 같은 시각 신원 속성은 여기 없다 (신원 ≠ 모션).
--
-- ── 원칙 ────────────────────────────────────────────────────────────────────
-- * 종(species) 교차 금지: DOG 레퍼런스는 DOG 에게만. 자동 교차 사용 없음.
-- * 품종 축 없음: 형태(크기/다리/몸통) 3축만. CORGI_RUN 같은 품종 명명 금지.
-- * 출처 없는 레퍼런스는 프로덕션에 못 들어간다: license + source_type 필수,
--   commercial_use_allowed 없이는 APPROVED/enabled 불가 (서비스 계층 강제).
-- * 버전 불변: 개선판은 새 version 행. 이미 생성된 영상이 기록한 V1 은
--   영원히 V1 이다 — 자산을 밑에서 갈아끼우지 않는다.
-- * source_type='PET_OWN_MOTION' + pet_id: 미래의 펫 생애 아카이브 개인 모션
--   (최우선 순위) — 지금은 데이터 모델/리졸버 계약만 지원한다.

create table if not exists public.motion_references (
  id uuid primary key default gen_random_uuid(),
  -- 사람이 읽는 키 (예: DOG_RUN_FRONT_SHORT_LEG). 버전과 함께 유일.
  reference_key text not null,
  version int not null default 1,
  species text not null check (species in ('DOG', 'CAT', 'RABBIT', 'OTHER')),
  -- 형태 3축 — UNKNOWN 은 "일반(generic) 레퍼런스"를 뜻한다.
  body_size_class text not null default 'UNKNOWN' check (
    body_size_class in ('SMALL', 'MEDIUM', 'LARGE', 'UNKNOWN')
  ),
  leg_length_class text not null default 'UNKNOWN' check (
    leg_length_class in ('SHORT', 'STANDARD', 'LONG', 'UNKNOWN')
  ),
  body_length_class text not null default 'UNKNOWN' check (
    body_length_class in ('COMPACT', 'STANDARD', 'LONG', 'UNKNOWN')
  ),
  -- 모션 축은 motion_spec.MOTIONS 의 정본 id 를 그대로 쓴다 (병행 명명 금지).
  motion_id text not null,
  motion_class text,
  camera_view text not null default 'UNKNOWN' check (
    camera_view in ('FRONT', 'FRONT_3Q', 'SIDE', 'BACK', 'UNKNOWN')
  ),
  travel_direction text not null default 'UNKNOWN' check (
    travel_direction in ('TOWARD_CAMERA', 'AWAY_FROM_CAMERA', 'LEFT_TO_RIGHT',
                         'RIGHT_TO_LEFT', 'STATIONARY', 'UNKNOWN')
  ),
  speed_class text not null default 'UNKNOWN' check (
    speed_class in ('SLOW', 'NORMAL', 'FAST', 'UNKNOWN')
  ),
  start_pose text,
  end_pose text,
  duration_sec numeric,
  fps numeric,
  resolution text,
  loopable boolean not null default false,
  bucket text,
  object_path text,
  -- 개인 모션(PET_OWN_MOTION)일 때만 채워진다.
  pet_id text,
  -- ── 출처/라이선스 (없으면 프로덕션 진입 불가) ─────────────────────────
  source_type text not null check (
    source_type in ('INTERNAL_RECORDING', 'LICENSED_STOCK', 'COMMISSIONED',
                    'OPEN_DATASET', 'PET_OWN_MOTION')
  ),
  source_description text,
  license text not null,
  license_reference text,
  provider_name text,
  commercial_use_allowed boolean not null default false,
  provenance_notes text,
  -- ── 품질/수명 ─────────────────────────────────────────────────────────
  quality_status text not null default 'DRAFT' check (
    quality_status in ('DRAFT', 'REVIEW', 'APPROVED', 'REJECTED')
  ),
  enabled boolean not null default false,
  created_at timestamptz not null default now()
);

create unique index if not exists motion_references_key_version_uidx
  on public.motion_references (reference_key, version);
create index if not exists motion_references_match_idx
  on public.motion_references (species, motion_id, enabled, quality_status);
create index if not exists motion_references_pet_idx
  on public.motion_references (pet_id)
  where pet_id is not null;

comment on table public.motion_references is
  '다종 모션 레퍼런스 대장. 매칭은 형태/생체역학 축으로만 — 시각 신원 속성 없음. 종 교차 금지, 버전 불변';
comment on column public.motion_references.source_type is
  'PET_OWN_MOTION = 미래의 펫 생애 아카이브 개인 모션 (리졸버 최우선). 추출 파이프라인은 아직 없다';
comment on column public.motion_references.enabled is
  'APPROVED + commercial_use_allowed + license 없이 true 가 될 수 없다 (서비스 계층 강제)';
