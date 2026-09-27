-- 획득 보너스 크레딧 (Phase 9).
--
--     Soul Trace 핸드오프  → +5   고객을 데려온다
--     LETTER   ₩14,900     → +3   다시 오게 한다
--     MEMORY BOX ₩49,000   → +10
--
-- ── 보너스도 서버가 정한다 ──────────────────────────────────────────────────
-- digital_products 와 같은 원칙이다: 숫자가 코드에 있으면 바꾸는 데 배포가 필요하고,
-- 마케팅이 값을 조정할 때마다 엔지니어를 거쳐야 한다. 표로 두면 UPDATE 한 줄이다.
--
-- ⚠️ 보너스는 **상품이 아니다.** digital_products 에 넣지 않는 이유가 이것이다 —
--    그 표는 "얼마를 내고 사는가"이고, 여기는 "무엇을 하면 받는가"다. 섞으면
--    카탈로그 조회가 살 수 없는 항목을 돌려주게 된다.

create table if not exists public.credit_bonus_rules (
  -- 'soultrace_handoff' | 'physical:LETTER' | 'physical:MEMORY_BOX'
  bonus_key    text primary key,
  credits      int not null check (credits > 0),
  display_name text,
  -- 중단은 삭제가 아니라 표시다. 지우면 이미 지급된 보너스의 근거가 사라진다.
  active       boolean not null default true,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

comment on table public.credit_bonus_rules is
  '획득 보너스 크레딧 규칙. 상품이 아니라 "무엇을 하면 받는가" — 서버가 유일한 권위';

insert into public.credit_bonus_rules (bonus_key, credits, display_name)
values
  ('soultrace_handoff',      5,  'Soul Trace 편지를 가져왔을 때'),
  ('physical:LETTER',        3,  'LETTER 구매 보너스'),
  ('physical:MEMORY_BOX',    10, 'MEMORY BOX 구매 보너스')
on conflict (bonus_key) do update
   set display_name = excluded.display_name,
       updated_at = now();
-- ⚠️ credits 를 덮어쓰지 않는다 — 운영에서 조정한 값을 재배포가 되돌리면 안 된다.

-- ── 지급 ────────────────────────────────────────────────────────────────────
--
-- 보너스 지급 + 원장을 **한 트랜잭션으로**. 멱등 키가 방어의 전부다:
--
--     soultrace:{source_letter_id}   편지 하나당 한 번 — **전역으로**
--     physical_bonus:{order_id}      주문 하나당 한 번
--
-- 왜 임시 핸드오프 토큰이 아니라 편지 id 인가: 토큰은 편지 하나에 대해 **몇 번이든
-- 새로 발급**된다(POST /api/handoff 에 횟수 제한이 없다 — 실패한 핸드오프를 다시
-- 시도할 수 있어야 하므로 그것이 옳다). 토큰을 키로 삼으면 토큰을 다시 받는 것만으로
-- 보너스를 다시 받는다.
--
-- 그리고 **우리 쪽 파생 letter_id 가 아니라 Soul Trace 의 원본 id** 를 쓴다.
-- 파생 id 에는 user_id 가 들어 있어서, 같은 편지를 여러 계정으로 가져가면 계정마다
-- 보너스가 나간다. 원본 id 로 잡으면 편지 하나에 보너스 하나다.
create or replace function public.grant_acquisition_bonus(
  p_user_id text,
  p_bonus_key text,
  p_idempotency_key text,
  p_ref_type text default null,
  p_ref_id text default null
)
returns jsonb
language plpgsql
as $$
declare
  rule public.credit_bonus_rules%rowtype;
  w jsonb;
  reason text;
begin
  if p_user_id is null or btrim(p_user_id) = '' then
    raise exception 'invalid_user';
  end if;

  select * into rule from public.credit_bonus_rules
   where bonus_key = p_bonus_key and active;

  -- 규칙이 없거나 꺼져 있으면 **아무 일도 하지 않는다.** 오류가 아니다 —
  -- 보너스를 중단했다고 편지 가져오기나 실물 결제가 실패해서는 안 된다.
  if not found then
    return jsonb_build_object('granted', 0, 'skipped', true);
  end if;

  reason := case
    when p_bonus_key = 'soultrace_handoff' then 'soultrace_bonus'
    else 'physical_product_bonus'
  end;

  w := public.wallet_apply(
    p_user_id         => p_user_id,
    p_delta           => rule.credits,
    p_reason          => reason,
    p_idempotency_key => p_idempotency_key,
    p_product_key     => p_bonus_key,
    p_ref_type        => p_ref_type,
    p_ref_id          => p_ref_id
  );

  return jsonb_build_object(
    'granted', case when (w ->> 'replayed')::boolean then 0 else rule.credits end,
    'credits_remaining', (w ->> 'balance_after')::int,
    'replayed', (w ->> 'replayed')::boolean,
    'skipped', false
  );
end;
$$;

comment on function public.grant_acquisition_bonus is
  '획득 보너스 지급 + 원장을 한 트랜잭션으로. 규칙이 없으면 조용히 건너뛴다(오류 아님)';
