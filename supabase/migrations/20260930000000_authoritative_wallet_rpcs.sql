-- 지갑 RPC의 **유일한 권위 정의** (Phase 1 — 재무 안전).
--
-- ── 무엇이 문제였나 ─────────────────────────────────────────────────────────
-- `add_wallet_credits` 의 정의가 저장소에 **두 벌** 있었다:
--
--   supabase/migrations/20260721000200_hybrid_business_wallet.sql
--       → greatest(p_amount, 0) — 0 과 음수를 조용히 통과시킨다(검증 없음)
--   docs/supabase_payment_iap.sql
--       → p_amount <= 0 이면 raise 'invalid_amount'
--
-- 둘 다 `create or replace` 라, **실제 배포된 것은 SQL Editor 에서 나중에 붙여넣은
-- 쪽**이다. 즉 프로덕션의 돈 관련 동작이 사람이 파일을 실행한 순서에 달려 있었다.
-- 이것이 실제로 사고를 냈다: 20260819000400 의 헤더가 기록하듯, 엄격한 쪽이 이겨서
-- 0 크레딧 웹 멤버십 갱신이 P0001 invalid_amount 로 통째로 실패했다.
--
-- ── 이 파일이 하는 일 ───────────────────────────────────────────────────────
-- 두 함수를 **마이그레이션 순서상 마지막**에 다시 정의해, 앞선 어떤 정의가 어떤
-- 순서로 적용됐든 최종 상태를 하나로 못박는다. 새 환경이든 이미 돌고 있는 DB든
-- 이 파일을 적용한 뒤에는 정의가 정확히 하나다.
--
-- ⚠️ **동작을 바꾸지 않는다.** 아래 semantics 는 현재 프로덕션에 배포돼 있는
--    (= 엄격한) 쪽과 동일하다. 이 마이그레이션의 목적은 "무엇이 배포됐는지"를
--    추측하지 않아도 되게 만드는 것이지, 계약을 바꾸는 것이 아니다.
--
-- ── 왜 관대한 쪽이 아니라 엄격한 쪽인가 ─────────────────────────────────────
-- p_amount <= 0 은 언제나 호출부 버그다. 0원 충전도, 음수 충전도 정상적인 재무
-- 연산이 아니다. 관대한 정의를 고르면 그 버그가 조용히 no-op 이 되어 로그에도
-- 남지 않는다 — 크레딧이 안 들어왔다는 사실을 고객이 알려 줘야 알게 된다.
--
-- 호출부는 이미 전부 이 계약에 맞춰져 있다(그래서 안전하다):
--   process_subscription_renewal   `if p_credits > 0` 으로 감싼다 (20260819000400)
--   process_iap_charge             payment_history CHECK 가 credits_added > 0 강제
--   wallet_service.add_credits     amount <= 0 이면 ValueError (RPC 도달 전)
--   wallet_service.refund_credits  amount <= 0 이면 조기 반환 (RPC 도달 전)

-- ── 충전 (원자적 증분) ──────────────────────────────────────────────────────
--
-- insert-then-update 2단계인 이유: `on conflict do update set ... + excluded` 는
-- 신규 행일 때 "0 + p_amount" 와 "p_amount" 를 구분해 쓰기 어렵고, 20260721000200
-- 의 정의가 바로 그 방식으로 greatest() 를 섞어 검증을 잃었다. 행 보장과 증분을
-- 나누면 증분이 언제나 `current_credits + p_amount` 한 문장이라 읽기 쉽고,
-- 두 문장은 같은 함수 본문 = 같은 트랜잭션이라 원자성은 그대로다.
--
-- 동시 호출은 update 의 행 잠금으로 직렬화된다 — lost update 가 발생하지 않는다.
create or replace function public.add_wallet_credits(p_user_id text, p_amount int)
returns int
language plpgsql
as $$
declare
  new_bal int;
begin
  -- 0·음수·null 은 호출부 버그다. 지갑에 닿기 전에 끊는다.
  if p_amount is null or p_amount <= 0 then
    raise exception 'invalid_amount';
  end if;

  insert into public.user_wallets (user_id, current_credits, updated_at)
  values (p_user_id, 0, now())
  on conflict (user_id) do nothing;

  update public.user_wallets
  set current_credits = current_credits + p_amount,
      updated_at = now()
  where user_id = p_user_id
  returning current_credits into new_bal;

  -- 행 보장 직후이므로 여기 도달할 수 없어야 한다. 그래도 조용히 null 을
  -- 돌려주지는 않는다 — 호출부(wallet_service)는 "잔액을 못 받았다"를
  -- WalletUnavailableError 로 올려 fail closed 한다.
  if new_bal is null then
    raise exception 'wallet_row_missing';
  end if;

  return new_bal;
end;
$$;

comment on function public.add_wallet_credits is
  '지갑 크레딧 원자적 증분. p_amount <= 0 은 invalid_amount 로 거절 — 유일한 권위 정의(20260930000000)';

-- ── 차감 (원자적 조건부 감소) ───────────────────────────────────────────────
--
-- 20260721000200 의 정의와 **동일하다.** 여기 다시 두는 이유는 두 지갑 RPC 가
-- 한 파일에서 함께 읽히게 하기 위해서다 — 충전만 재정의하고 차감을 다른 파일에
-- 남겨 두면, 다음 사람이 "차감도 두 벌인가?" 를 다시 조사해야 한다.
--
-- `where current_credits >= p_amount` 가 조건과 갱신을 한 문장에 묶으므로,
-- 읽기-쓰기 사이에 끼어들 틈이 없다(잔액 초과 인출 불가).
--
-- ⚠️ 충전 쪽과 달리 여기에는 `p_amount <= 0` 가드를 **일부러 넣지 않았다.**
--    원본에 없었고, 이 마이그레이션의 임무는 "배포된 정의를 확정하는 것"이지
--    계약을 손보는 것이 아니다. 재무 안전 작업에서 관계없는 계약을 함께 바꾸면,
--    사고가 났을 때 원인이 안전 수정인지 곁다리 변경인지 갈라낼 수 없다.
--    호출부 방어는 이미 있다: wallet_service.deduct_credits 가 amount <= 0 에
--    ValueError 를 던져 RPC 에 도달하지 못한다.
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

  -- 행이 없거나 잔액이 부족하다. 둘을 구분하지 않는 것은 의도적이다 —
  -- 호출부에 필요한 답은 "차감하지 못했다" 하나이고, 지갑 행의 존재 여부는
  -- 사용자에게 알려 줄 정보가 아니다.
  if not found then
    raise exception 'insufficient_credits';
  end if;
  return new_bal;
end;
$$;

comment on function public.deduct_wallet_credits is
  '지갑 크레딧 원자적 조건부 차감. 부족하면 insufficient_credits — 유일한 권위 정의(20260930000000)';
