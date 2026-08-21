-- canonical 펫 레지스트리 (Phase 13.2) — **모든** Eternal Beam 펫.
--
-- ── 왜 필요한가 ─────────────────────────────────────────────────────────────
-- Phase 10 의 운영 검색은 generated_motions 를 펫 목록으로 썼다. 그 전제가
-- 틀렸다: generated_motions 는 **프리미엄 모션이 승격됐을 때만** 채워진다.
-- 무료 BREATHING 만 있는 펫은 거기 한 줄도 없고, 그래서 운영 콘솔에서 아예
-- 보이지 않았다 — QR 을 붙일 수도, 공유를 만들 수도 없었다.
--
-- 그런데 QR 제품의 대상이 정확히 그 사람들이다(기기가 없는 무료 사용자).
-- 즉 제품의 주 고객이 생산 파이프라인에 들어올 수 없는 상태였다.
--
-- 이 테이블이 그 구멍을 메운다: **BREATHING 하나만 있어도 펫은 여기 등록된다.**
-- 멤버십도 프리미엄 모션도 필요 없다.
--
-- ── 무엇을 저장하지 않는가 ──────────────────────────────────────────────────
-- 서명 URL 을 저장하지 않는다. 만료되는 값을 정본으로 두면 며칠 뒤 죽는다
-- (Phase 10·11 에서 겪었다). 만료되지 않는 **버킷 + 객체 경로**만 둔다.
--
-- generated_motions 를 흉내 낸 가짜 행을 만들지 않는다 — 그러면 자산 상태
-- (asset_state)가 오염되어 없는 프리미엄 행동이 READY 로 보인다.

create table if not exists public.pets (
  -- canonical petId. 프론트 규약: "pet_" + content_id (src/lib/pet-identity.ts).
  pet_id text primary key,
  -- **인증된** Eternal Beam user_id. 스토리지 경로의 접두사와는 다를 수 있다
  -- (예전 업로드가 localStorage 신원이나 'anonymous' 로 저장됐기 때문).
  user_id text not null,
  content_id text,
  -- BREATHING 위치. 만료되지 않는 정본.
  breathing_bucket text,
  breathing_object_path text,
  -- 'app' = 고객 앱이 파이프라인 완료 후 등록 / 'ops' = 운영 수동 등록·백필
  source text not null default 'app',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- 운영이 고객으로 찾는 경로.
create index if not exists pets_user_idx on public.pets (user_id);
create index if not exists pets_content_idx on public.pets (content_id);

comment on table public.pets is
  '모든 펫의 canonical 레지스트리. BREATHING 만 있어도 등록된다 — 멤버십/프리미엄 불필요';
comment on column public.pets.user_id is
  '인증된 canonical user_id. 스토리지 경로 접두사와 다를 수 있다(예전 업로드 신원)';
comment on column public.pets.breathing_object_path is
  '만료되지 않는 객체 경로. 서명 URL 을 정본으로 두지 않는다';
