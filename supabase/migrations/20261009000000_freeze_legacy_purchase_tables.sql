-- 레거시 결제 표를 **읽기 전용으로 동결한다** (Phase 11).
--
-- ── 지우지 않는 이유 ────────────────────────────────────────────────────────
-- 새 아키텍처가 생겼다는 이유로 과거 구매 증거를 버리지 않는다. 고객이 "예전에
-- 이거 샀는데요" 라고 물을 때 답할 근거가 없으면, 그건 데이터를 정리한 것이 아니라
-- 고객과의 기록을 잃은 것이다. 환불·분쟁·회계 감사도 마찬가지다.
--
-- ── 그렇다고 열어 두지도 않는다 ─────────────────────────────────────────────
-- "이제 안 쓴다" 는 주석은 시간이 지나면 지켜지지 않는다. 쓰는 코드가 사라진
-- 지금이 **쓰기를 막기에 가장 안전한 시점**이다 — 막아서 깨질 것이 없다는 사실을
-- 지금은 확인할 수 있고, 6개월 뒤에는 확인하기 어렵다.
--
-- 트리거로 막는 이유(권한 REVOKE 대신): 이 서비스는 service-role 키로 접속하므로
-- 권한으로는 막히지 않는다. 트리거는 접속 주체와 무관하게 걸린다.
--
-- ⚠️ 되돌리기: drop trigger 두 줄이면 된다. 운영에서 정말 손봐야 할 일이 생기면
--    막힌 채로 우회하지 말고 **명시적으로 풀고, 고치고, 다시 걸 것.**

create or replace function public.reject_write_frozen_table()
returns trigger
language plpgsql
as $$
begin
  raise exception
    '% 은(는) Phase 11 에서 읽기 전용으로 동결됐습니다. '
    '과거 구매 증거 보존용이며 새 쓰기는 허용되지 않습니다. '
    '(근거: docs/PAYPAL_LEGACY.md · 해제하려면 트리거를 명시적으로 drop 할 것)',
    TG_TABLE_NAME;
end;
$$;

comment on function public.reject_write_frozen_table is
  '동결된 레거시 표에 대한 쓰기를 거부한다. 읽기는 그대로 가능하다';

-- ── purchased_slots (PayPal 시대 테마 소유권) ───────────────────────────────
--
-- 쓰던 유일한 함수(supabase_assets.record_theme_purchase)가 Phase 11 에서 삭제됐고,
-- 그 호출부(routers/paypal.py)도 함께 삭제됐다. 지금 이 표에 쓰는 코드는 없다.
--
-- 데이터는 남는다: services/supabase_assets.get_purchased_themes 로 계속 조회된다.
--
-- ⚠️ **표가 없을 수 있다** (실측: relation "public.purchased_slots" does not exist).
--    이 표는 20250302000000_user_assets_purchased_slots.sql 이 만든다. 그것을 적용한
--    적이 없는 환경 — 즉 PayPal 시대를 겪지 않은 데이터베이스 — 에는 표가 없다.
--
--    그때 이 마이그레이션이 실패하는 것은 옳지 않다. **없는 표를 동결하는 것은
--    오류가 아니라 할 일이 없는 것이다.** 여기서 멈추면 뒤따르는 동결·주석까지
--    함께 막혀서, 정작 존재하는 표가 열린 채로 남는다.
--
--    반대로 `create table if not exists` 로 만들어 놓고 동결하지도 않는다 —
--    보존할 과거가 없는 곳에 빈 표를 만드는 것은 증거 보존이 아니라 잡동사니다.
do $$
begin
  if to_regclass('public.purchased_slots') is null then
    raise notice
      'purchased_slots 가 없다 — 이 데이터베이스에는 PayPal 시대 소유권이 없다. 동결을 건너뛴다.';
    return;
  end if;

  drop trigger if exists purchased_slots_frozen on public.purchased_slots;
  create trigger purchased_slots_frozen
    before insert or update or delete on public.purchased_slots
    for each row execute function public.reject_write_frozen_table();

  comment on table public.purchased_slots is
    'PayPal 시대 테마 소유권 (동결·읽기 전용). 소유권 권위는 user_theme_entitlements';
end $$;

-- ── theme_purchase_orders 는 **아직 동결하지 않는다** ───────────────────────
--
-- POST /api/v1/themes/confirm 이 살아 있다: 배포 시점에 Toss 결제창에 머물러
-- 있던 고객의 승인을 받아 줄 곳이 필요하다. 그 경로가 이 표에 쓴다
-- (pending → paid).
--
-- 새 주문을 만드는 경로(/checkout, /purchase)는 삭제됐으므로 미결 주문은 시간이
-- 지나면 0 이 된다. 그때 아래 두 줄의 주석을 풀어 동결한다:
--
--   확인 방법:
--     select count(*) from public.theme_purchase_orders where status = 'pending';
--   0 이면 안전하다.
--
-- drop trigger if exists theme_purchase_orders_frozen on public.theme_purchase_orders;
-- create trigger theme_purchase_orders_frozen
--   before insert or update or delete on public.theme_purchase_orders
--   for each row execute function public.reject_write_frozen_table();

-- 위와 같은 이유로 표가 없을 수 있다(20260821000100 을 적용한 적 없는 환경).
-- 주석 하나 때문에 마이그레이션이 실패하지 않게 한다.
do $$
begin
  if to_regclass('public.theme_purchase_orders') is null then
    raise notice 'theme_purchase_orders 가 없다 — 드레인할 레거시 주문도 없다.';
    return;
  end if;

  comment on table public.theme_purchase_orders is
    '테마 KRW 주문 (레거시). 새 주문은 생성되지 않는다 — 미결 0건이 되면 동결할 것';
end $$;
