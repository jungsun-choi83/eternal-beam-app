-- Eternal Beam: NFC 장소 카드 + 구독 크레딧 (하이브리드 모델)
--
-- 근본 원인 수정: GET /api/v1/pet/wallet/{user_id} 및 POST /api/v1/pet/generate-with-credit
-- 가 500을 반환하던 원인 — `public.user_wallets` 테이블이 실제 Supabase 프로젝트에
-- 생성되어 있지 않았음(PostgREST: "Could not find the table 'public.user_wallets' in
-- the schema cache", code PGRST205). 기존 docs/supabase_hybrid_business.sql 은 SQL
-- Editor에서 수동 실행하는 문서였고 supabase/migrations/ 에는 없어 실제로 적용되지
-- 않은 상태였다. 이 파일을 Supabase에 적용(SQL Editor 붙여넣기 실행 또는
-- `supabase db push`)하면 실제 원인이 해결된다.

create table if not exists public.user_wallets (
  user_id text primary key,
  current_credits int not null default 0 check (current_credits >= 0),
  updated_at timestamptz not null default now()
);

-- 완료된 모션 영상 (Unity device/sync 조회)
create table if not exists public.generated_motions (
  id bigserial primary key,
  user_id text not null,
  pet_id text not null,
  place_id text not null,
  action_id text not null check (action_id in ('IDLE', 'TOUCH', 'VOICE', 'NFC')),
  video_url text not null,
  created_at timestamptz not null default now(),
  unique (user_id, pet_id, place_id, action_id)
);

create index if not exists idx_generated_motions_lookup
  on public.generated_motions (user_id, pet_id, place_id);

-- Luma 진행 중 작업 (웹훅 매핑)
create table if not exists public.motion_generation_jobs (
  id bigserial primary key,
  session_id uuid not null,
  user_id text not null,
  pet_id text not null,
  place_key text not null,
  place_id text not null,
  action_id text not null,
  luma_generation_id text unique,
  status text not null default 'pending',
  video_url text,
  error text,
  updated_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists idx_motion_jobs_luma
  on public.motion_generation_jobs (luma_generation_id);

-- 크레딧 차감 세션 (감사·환불 추적)
create table if not exists public.credit_generation_sessions (
  session_id uuid primary key,
  user_id text not null,
  pet_id text not null,
  place_key text not null,
  place_id text not null,
  pet_image_url text not null,
  credits_charged int not null default 4,
  status text not null default 'processing',
  created_at timestamptz not null default now()
);

-- 원자적 차감 RPC (backend/services/wallet_service.py의 deduct_credits는 기본적으로
-- 낙관적 업데이트를 쓰지만, 이 RPC를 대신 쓰도록 바꿀 수도 있음).
create or replace function public.deduct_wallet_credits(p_user_id text, p_amount int)
returns int
language plpgsql
as $$
declare
  new_bal int;
begin
  update public.user_wallets
  set current_credits = current_credits - p_amount,
      updated_at = now()
  where user_id = p_user_id
    and current_credits >= p_amount
  returning current_credits into new_bal;

  if not found then
    raise exception 'insufficient_credits';
  end if;
  return new_bal;
end;
$$;

-- backend/services/wallet_service.py의 add_credits()가 호출하는 RPC. 원래
-- docs/supabase_payment_iap.sql 에만 정의되어 있었고 마이그레이션엔 없었음 —
-- IAP/구독 크레딧 충전 경로도 같은 이유(테이블/함수 부재)로 500이 날 수 있어
-- 여기서 같이 만들어 둔다.
create or replace function public.add_wallet_credits(p_user_id text, p_amount int)
returns int
language plpgsql
as $$
declare
  new_bal int;
begin
  insert into public.user_wallets (user_id, current_credits, updated_at)
  values (p_user_id, greatest(p_amount, 0), now())
  on conflict (user_id) do update
    set current_credits = public.user_wallets.current_credits + excluded.current_credits,
        updated_at = now()
  returning current_credits into new_bal;

  return new_bal;
end;
$$;
