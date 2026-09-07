-- 개시 잔액 백필 (Phase 2).
--
-- ── 왜 필요한가 ─────────────────────────────────────────────────────────────
-- 원장은 오늘 생겼고, 기존 지갑은 그 전부터 있었다. 백필하지 않으면 첫날부터
-- 모든 기존 사용자에 대해 불변식이 깨진다:
--
--     지갑 17,  원장 합계 0   →  credit_ledger_drift() 가 전원을 보고한다
--
-- 그러면 대조가 무의미해진다. 진짜 사고가 났을 때 노이즈에 묻히기 때문이다.
--
-- 그래서 지갑마다 `legacy_migration` 한 줄을 만들어 **현재 잔액을 개시 잔액으로**
-- 선언한다. 이 행은 "이 잔액이 어디서 왔는지 원장은 모른다"는 사실을 정직하게
-- 기록하는 것이지, 없던 설명을 지어내는 것이 아니다.
--
-- ── 왜 잔액을 재구성하지 않는가 ─────────────────────────────────────────────
-- payment_history · premium_purchases · subscription_webhook_events 를 합쳐
-- 과거 움직임을 복원하고 싶어진다. 하지 않는다:
--
--   * 그 표들은 서로 겹치고(같은 충전이 두 곳에 있을 수 있다) 빠진 것도 있다
--     (레거시 4코인 차감은 어디에도 금액이 남지 않는다)
--   * PAYMENT_MOCK=1 / SUBSCRIPTION_MOCK=1 로 만들어진 행이 섞여 있어 실 매출과
--     구분되지 않는다 (backend/scripts/audit_financial_records.py 참고)
--   * 복원한 합계가 지갑과 어긋나면 **어느 쪽이 맞는지 판단할 근거가 없다**
--
-- 지갑 잔액은 고객이 실제로 쓸 수 있는 값이고, 그것만이 확실하다. 확실한 것
-- 하나를 개시점으로 두고 **오늘 이후를 정확히** 기록하는 편이, 불확실한 과거를
-- 그럴듯하게 재구성하는 것보다 낫다.
--
-- ── 멱등성 ──────────────────────────────────────────────────────────────────
-- 키가 'legacy:<user_id>' 라 두 번 돌려도 두 번째는 아무것도 하지 않는다.
-- 나중에 만들어진 지갑에도 안전하게 다시 돌릴 수 있다.

create or replace function public.credit_ledger_backfill_opening(p_dry_run boolean default false)
returns table (user_id text, opening_balance int, action text)
language plpgsql
as $$
declare
  r record;
begin
  for r in
    select w.user_id, w.current_credits
      from public.user_wallets w
     where not exists (
       select 1 from public.credit_ledger l where l.user_id = w.user_id
     )
     order by w.user_id
  loop
    if p_dry_run then
      user_id := r.user_id;
      opening_balance := r.current_credits;
      action := 'WOULD_INSERT';
      return next;
      continue;
    end if;

    -- wallet_apply 를 쓰지 않는다. 그 함수는 잔액을 **바꾸는** 것이 일이고,
    -- 개시 행은 잔액을 바꾸지 않고 **현재 값을 선언**하는 것이다. 여기서
    -- wallet_apply 를 쓰면 잔액이 두 배가 된다.
    insert into public.credit_ledger (
      user_id, delta, balance_after, reason,
      state, idempotency_key, ref_type, ref_id, settled_at
    ) values (
      r.user_id, r.current_credits, r.current_credits, 'legacy_migration',
      'COMMITTED', 'legacy:' || r.user_id, 'user_wallets', r.user_id, now()
    )
    on conflict (idempotency_key) do nothing;

    user_id := r.user_id;
    opening_balance := r.current_credits;
    action := 'INSERTED';
    return next;
  end loop;
end;
$$;

comment on function public.credit_ledger_backfill_opening is
  '원장이 없는 지갑에 legacy_migration 개시 행을 만든다. 멱등 — 여러 번 돌려도 안전';

-- ── 지금 적용 ───────────────────────────────────────────────────────────────
-- 마이그레이션 시점의 지갑 전체를 처리한다. 이 시점 이후에 생기는 지갑은
-- wallet_ensure 가 starter_bonus 로 원장을 함께 만들므로 백필이 필요 없다.
select public.credit_ledger_backfill_opening();

-- ── 확인 ────────────────────────────────────────────────────────────────────
-- 백필 직후에는 drift 가 비어 있어야 한다. 비어 있지 않으면 무언가 잘못됐으므로
-- **마이그레이션을 여기서 실패시킨다** — 어긋난 원장을 안고 배포를 끝내면,
-- 그 어긋남이 정상 상태로 굳어져 이후의 진짜 사고를 가린다.
do $$
declare
  n int;
begin
  select count(*) into n from public.credit_ledger_drift();
  if n > 0 then
    raise exception
      '원장 백필 후에도 어긋난 지갑이 % 개 있다. select * from public.credit_ledger_drift(); 로 확인할 것', n;
  end if;
end;
$$;
