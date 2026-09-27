-- 펫 레퍼런스 이미지 (Durable Pet Identity Intake, Phase 1) — 신원 파이프라인의 증거층.
--
-- ── 왜 필요한가 ─────────────────────────────────────────────────────────────
-- 지금까지 "원본 사진"은 브라우저 상태(data: URL)에만 있었다. 누끼(cutout)는
-- 스토리지에 남지만 **원본은 어디에도 남지 않는다** — 새로고침 한 번이면 이후
-- 신원 파이프라인(멀티뷰 레퍼런스 → 정본 펫 이미지 → 액션 키프레임)이 의존할
-- 출발점이 사라진다. 이 테이블은 펫당 여러 장의 레퍼런스를 **추가만** 하는
-- 방식으로 기록한다. 행을 갱신해 역사를 지우지 않는다.
--
-- ── 원본 vs 파생 ────────────────────────────────────────────────────────────
-- role = 'original'  사용자가 준 원본 증거. 절대 덮어쓰지 않는다.
-- role = 'derived'   크롭·누끼·마스크·생성 이미지 등 파이프라인 산물.
--                    파생물은 원본을 대체하는 정본이 될 수 없다.
-- 원본의 저장 경로에는 콘텐츠 해시가 들어가므로 같은 바이트는 같은 객체로
-- 수렴하고, 다른 바이트가 기존 원본 객체를 덮어쓸 방법이 없다.
--
-- pets 테이블과 같은 이유로 서명 URL 을 저장하지 않는다 — 버킷 + 객체 경로만
-- (20260825000000_pets_registry.sql 참고).
--
-- pets 에 FK 를 걸지 않는다: 레퍼런스는 생성 완료 **전**(등록 전)에 먼저 생긴다.
-- pet_id 는 프론트 규약("pet_" + content_id, src/lib/pet-identity.ts)으로 파생된다.

create table if not exists public.pet_reference_images (
  id uuid primary key default gen_random_uuid(),
  pet_id text not null,
  content_id text not null,
  -- 업로드 시점의 신원. pets.user_id 와 같은 주의가 필요하다: 스토리지 경로
  -- 접두사(localStorage 신원)와 인증된 canonical id 가 다를 수 있다.
  -- 조회 라우터는 pets 레지스트리 소유자를 우선해 소유권을 다시 확인한다.
  user_id text not null,
  -- 'original' = 사용자 제공 원본 증거 / 'derived' = 파이프라인 산물
  role text not null check (role in ('original', 'derived')),
  -- 어느 경로로 들어왔는가: 'app' 고객 앱 / 'ops' 운영 / 'pipeline' 서버 파이프라인
  source text not null default 'app' check (source in ('app', 'ops', 'pipeline')),
  -- 파생물의 종류 (원본이면 null): 'cutout_vitmatte', 'cutout_rembg', 'cutout_client' …
  derived_kind text,
  -- 파생물이 나온 원본 레퍼런스. 알 수 없으면 null — 추측해 연결하지 않는다.
  parent_reference_id uuid,
  -- 만료되지 않는 정본 위치.
  bucket text not null,
  object_path text not null,
  original_filename text,
  mime_type text,
  width int,
  height int,
  bytes_size bigint,
  -- 바이트의 sha256 hex — 재시도 멱등의 기준이자 저장 경로의 일부.
  content_hash text,
  -- 뷰 라벨. 멀티뷰 업로더는 이후 단계 — 지금은 UNKNOWN 만 기록된다.
  view_label text not null default 'UNKNOWN' check (
    view_label in ('FRONT', 'LEFT', 'RIGHT', 'BACK', 'TOP', 'FULL_BODY', 'FACE_CLOSEUP', 'UNKNOWN')
  ),
  -- 아래 라벨들은 이후 비전 패스가 채운다. **모르는 값을 추측해 넣지 않는다** —
  -- 지금 파이프라인은 이 정보를 갖고 있지 않으므로 전부 'unknown'/null 로 남는다.
  pose_label text,
  face_visibility text not null default 'unknown' check (
    face_visibility in ('visible', 'partial', 'hidden', 'unknown')
  ),
  body_visibility text not null default 'unknown' check (
    body_visibility in ('full', 'partial', 'head_only', 'unknown')
  ),
  tail_visibility text not null default 'unknown' check (
    tail_visibility in ('visible', 'partial', 'hidden', 'unknown')
  ),
  occlusion text not null default 'unknown' check (
    occlusion in ('none', 'partial', 'heavy', 'unknown')
  ),
  -- 검출기 산출물 (예: YOLO {animal_class, confidence, bbox}). 스키마가 아직
  -- 움직이므로 jsonb — motion_generation_jobs.validation 과 같은 선택.
  detection jsonb,
  -- 사람 오염 여부. null = 평가되지 않음 (모르는 것을 false 로 적지 않는다).
  person_detected boolean,
  -- SAM2/ViTMatte cutout_quality 메타 등 세그멘테이션 진단.
  diagnostics jsonb,
  acceptance_state text not null default 'accepted' check (
    acceptance_state in ('accepted', 'rejected', 'pending')
  ),
  rejection_code text,
  -- (pet_id, role) 안에서 1부터 증가. 현재 단일 사진 온보딩의 원본이 version 1 이다.
  version int not null default 1,
  created_at timestamptz not null default now()
);

-- 같은 펫에 같은 원본 바이트 → 한 행 (재시도·중복 업로드 멱등).
create unique index if not exists pet_reference_images_original_hash_uidx
  on public.pet_reference_images (pet_id, content_hash)
  where role = 'original' and content_hash is not null;

-- 같은 파생 객체는 한 번만 기록된다 (누끼 재저장이 행을 불리지 않도록).
create unique index if not exists pet_reference_images_derived_object_uidx
  on public.pet_reference_images (pet_id, object_path)
  where role = 'derived';

-- 버전은 (pet, role) 안에서 유일 — 경쟁 삽입이 같은 버전을 갖지 못한다.
create unique index if not exists pet_reference_images_version_uidx
  on public.pet_reference_images (pet_id, role, version);

create index if not exists pet_reference_images_pet_idx
  on public.pet_reference_images (pet_id, created_at desc);
create index if not exists pet_reference_images_user_idx
  on public.pet_reference_images (user_id, pet_id);

comment on table public.pet_reference_images is
  '펫별 레퍼런스 이미지 대장 (append-only). original = 사용자 원본 증거, derived = 파이프라인 산물. 파생물은 원본을 대체하지 못한다';
comment on column public.pet_reference_images.content_hash is
  '바이트 sha256. 멱등 재시도의 기준이며 원본 저장 경로에 포함된다 — 다른 바이트가 같은 객체를 덮어쓸 수 없다';
comment on column public.pet_reference_images.user_id is
  '업로드 시점 신원. pets.user_id 처럼 canonical 인증 신원과 다를 수 있다 — 조회는 레지스트리 소유자를 우선한다';
comment on column public.pet_reference_images.view_label is
  '멀티뷰 계획의 뷰 슬롯. 현재 파이프라인은 UNKNOWN 만 기록한다 — 추측 라벨 금지';
