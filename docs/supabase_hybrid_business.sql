-- Eternal Beam: NFC 장소 카드 + 구독 크레딧 (하이브리드 모델)
-- Supabase SQL Editor 에서 실행

-- 지갑 (월 구독으로 충전되는 코인)
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

-- 더미 데이터 (개발)
insert into public.user_wallets (user_id, current_credits)
values
  ('demo-user', 12),
  ('premium-user', 40),
  ('broke-user', 2)
on conflict (user_id) do update set current_credits = excluded.current_credits;

-- 원자적 차감 RPC (선택, 서버에서 낙관적 업데이트 대신 사용 가능)
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
