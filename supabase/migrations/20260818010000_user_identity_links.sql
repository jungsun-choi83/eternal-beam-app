-- Supabase 계정(sub) ↔ Eternal Beam 안정 신원(eb_user_id) 연결.
--
-- 왜 필요한가: 기존 데이터는 전부 **텍스트 user_id** 로 키가 잡혀 있다
--   user_wallets / generated_motions / motion_generation_jobs /
--   credit_generation_sessions / premium_purchases
-- 그리고 그 값은 프론트가 localStorage 에 쓰던 것이다 — 로그인 시 소문자 이메일,
-- 아니면 익명 `user_<base36>`.
--
-- Supabase 의 sub 는 UUID 다. 신원을 sub 로 **갈아치우면** 기존 지갑·생성 자산·
-- 구매 원장이 통째로 고아가 된다. 그래서 교체하지 않고 **연결**한다:
--   sub → eb_user_id (기존 값 그대로)
--
-- 연결 규칙은 identity_service.resolve_identity 에 있다. 요약:
--   검증된 이메일이 있으면 eb_user_id = 소문자 이메일  (= 예전 auth-screen 이 쓰던 값)
--   그 외에는 eb_user_id = sub                          (새 신원, 물려받을 데이터 없음)

create table if not exists public.user_identity_links (
  -- Supabase auth.users.id (JWT 의 sub)
  auth_user_id text primary key,
  -- 이 계정이 실제로 쓰는 Eternal Beam 신원. 기존 데이터의 user_id 와 같은 값.
  eb_user_id text not null,
  -- 'email'(검증된 이메일로 기존 신원 승계) | 'new'(새 신원 = sub)
  linked_via text not null,
  email text,
  created_at timestamptz not null default now()
);

-- 한 Eternal Beam 신원에는 **계정이 하나만** 붙는다.
-- 이것이 없으면 두 계정이 같은 이메일 신원을 주장해 남의 지갑·자산에 접근할 수 있다.
create unique index if not exists user_identity_links_eb_uniq
  on public.user_identity_links (eb_user_id);

comment on table public.user_identity_links is
  'Supabase 계정 → Eternal Beam 신원 매핑. 기존 user_id 키 데이터를 고아로 만들지 않기 위한 연결 계층';
comment on column public.user_identity_links.eb_user_id is
  '기존 테이블들의 user_id 와 동일한 값. 한 번 정해지면 바뀌지 않는다';
comment on column public.user_identity_links.linked_via is
  'email = 검증된 이메일로 기존 신원 승계 / new = 새로 만든 신원(sub)';
