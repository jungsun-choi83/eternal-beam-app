-- 크레딧 **예약 → 확정/해제** (Phase 7).
--
--     Sleeping 선택 → 5 크레딧 예약 → 생성 작업 → 프로바이더 → 검증
--         PASS → commit  (-5 확정)  → 소유 자산 저장 → Sleeping #1 영구 소유
--         FAIL → release (+5 반환)  → 아무 일도 없던 것과 같아진다
--
-- ── 왜 "차감 후 환불"이 아니라 예약인가 ─────────────────────────────────────
-- 두 방식 모두 잔액은 같아진다. 다른 것은 **중간 상태를 설명할 수 있는가**이다.
--
--   차감 후 환불:  원장에 -5 가 있고, 실패하면 +5 가 따로 생긴다.
--                  그 사이 구간에서는 "왜 5가 없어졌는지" 를 자산으로 설명할 수
--                  없다. 그리고 환불이 실패하면 -5 만 남는다(Phase 1 이 고친 결함).
--
--   예약:          그 -5 가 RESERVED 로 **표시**돼 있다. 미결 예약을 조회하면
--                  "생성 중인 것 때문에 잡혀 있는 크레딧"이 정확히 나온다.
--                  해제는 상태 전이 + 보상 행이라 원자적이다.
--
-- ── 잔액은 예약 시점에 실제로 줄어든다 ──────────────────────────────────────
-- "예약"이지만 지갑에서는 즉시 빠진다. 그래야 5 크레딧으로 두 건을 동시에
-- 시작할 수 없다 — 잔액이 그대로면 두 요청이 각각 통과하고, 그중 하나는
-- 나중에 낼 수 없는 돈이 된다.

-- ── 예약 ────────────────────────────────────────────────────────────────────
--
-- RESERVED 원장 행 하나 + 잔액 차감. wallet_apply 가 둘을 함께 한다.
-- 반환: { ledger_id, balance_after, replayed }
create or replace function public.reserve_credits(
  p_user_id text,
  p_credits int,
  p_reason text,
  p_idempotency_key text,
  p_product_key text default null,
  p_ref_type text default null,
  p_ref_id text default null
)
returns jsonb
language plpgsql
as $$
begin
  if p_credits is null or p_credits <= 0 then
    raise exception 'invalid_amount';
  end if;

  -- 부호를 여기서 뒤집는다. 호출부는 "5 크레딧을 예약" 이라고 말하지
  -- "-5" 라고 말하지 않는다.
  return public.wallet_apply(
    p_user_id         => p_user_id,
    p_delta           => -p_credits,
    p_reason          => p_reason,
    p_idempotency_key => p_idempotency_key,
    p_product_key     => p_product_key,
    p_unit_price      => p_credits,
    p_state           => 'RESERVED',
    p_ref_type        => p_ref_type,
    p_ref_id          => p_ref_id
  );
end;
$$;

comment on function public.reserve_credits is
  '크레딧 예약: RESERVED 원장 행 + 즉시 차감. 같은 키는 재예약하지 않는다';

-- ── 확정 ────────────────────────────────────────────────────────────────────
--
-- 상태만 바꾼다. **잔액은 건드리지 않는다** — 예약 시점에 이미 빠졌다.
-- 여기서 또 빼면 두 번 청구하는 것이다.
create or replace function public.commit_reservation(p_ledger_id uuid)
returns jsonb
language plpgsql
as $$
declare
  l public.credit_ledger%rowtype;
begin
  select * into l from public.credit_ledger where ledger_id = p_ledger_id for update;
  if not found then
    raise exception 'reservation_not_found';
  end if;

  -- 이미 확정됐으면 멱등 성공이다 (웹훅 재전송·재시도).
  if l.state = 'COMMITTED' then
    return jsonb_build_object(
      'ledger_id', l.ledger_id, 'credits', -l.delta, 'replayed', true
    );
  end if;
  -- 해제된 예약은 되살릴 수 없다. 되살리면 반환한 크레딧을 다시 가져가는 셈이다.
  if l.state <> 'RESERVED' then
    raise exception 'reservation_not_open';
  end if;

  update public.credit_ledger
     set state = 'COMMITTED', settled_at = now()
   where ledger_id = p_ledger_id;

  return jsonb_build_object('ledger_id', l.ledger_id, 'credits', -l.delta, 'replayed', false);
end;
$$;

comment on function public.commit_reservation is
  '예약 확정. 상태만 바꾼다 — 잔액은 예약 시점에 이미 빠졌다';

-- ── 해제 ────────────────────────────────────────────────────────────────────
--
-- 상태 전이 + **보상 행**을 한 트랜잭션으로. 보상 행을 따로 만들지 않고 원래
-- 행을 지우면 "무슨 일이 있었는지" 가 사라진다 — 실패한 생성도 기록이다.
--
-- 보상 행의 사유는 refund 가 아니라 reservation_release 다. 둘은 다르다:
--     refund               제공한 것을 되돌린다
--     reservation_release  애초에 제공된 적이 없다
create or replace function public.release_reservation(p_ledger_id uuid, p_reason text default null)
returns jsonb
language plpgsql
as $$
declare
  l public.credit_ledger%rowtype;
  w jsonb;
begin
  select * into l from public.credit_ledger where ledger_id = p_ledger_id for update;
  if not found then
    raise exception 'reservation_not_found';
  end if;

  if l.state = 'RELEASED' then
    -- 이미 해제됐다. 보상 행도 이미 있으므로 아무것도 하지 않는다.
    select current_credits into strict w
      from public.user_wallets where user_id = l.user_id;
    return jsonb_build_object(
      'ledger_id', l.ledger_id, 'credits', -l.delta, 'replayed', true
    );
  end if;
  if l.state <> 'RESERVED' then
    -- 확정된 예약은 해제할 수 없다. 그건 환불이지 해제가 아니다.
    raise exception 'reservation_not_open';
  end if;

  update public.credit_ledger
     set state = 'RELEASED', settled_at = now()
   where ledger_id = p_ledger_id;

  -- 잡혀 있던 크레딧을 되돌린다. 멱등 키가 예약 id 에서 파생되므로 두 번
  -- 해제해도 보상 행은 하나뿐이다.
  w := public.wallet_apply(
    p_user_id         => l.user_id,
    p_delta           => -l.delta,
    p_reason          => 'reservation_release',
    p_idempotency_key => 'release:' || l.ledger_id::text,
    p_product_key     => l.product_key,
    p_unit_price      => l.unit_price,
    p_ref_type        => l.ref_type,
    p_ref_id          => l.ref_id
  );

  return jsonb_build_object(
    'ledger_id', l.ledger_id,
    'credits', -l.delta,
    'balance_after', (w ->> 'balance_after')::int,
    'replayed', false
  );
end;
$$;

comment on function public.release_reservation is
  '예약 해제: 상태 전이 + reservation_release 보상 행을 한 트랜잭션으로';

-- ── 생성 작업 ↔ 예약 ────────────────────────────────────────────────────────
--
-- **예약 없이 프로바이더에 제출할 수 없다** 를 스키마로 못박는다.
-- 유료 작업은 반드시 예약을 가리켜야 한다.
alter table if exists public.scene_generation_jobs
  add column if not exists reservation_ledger_id uuid,
  add column if not exists credits_reserved int not null default 0;

comment on column public.scene_generation_jobs.reservation_ledger_id is
  '이 작업을 뒷받침하는 예약. 유료 작업은 반드시 있어야 한다 — 없으면 제출하지 않는다';

alter table public.scene_generation_jobs
  drop constraint if exists scene_jobs_paid_has_reservation;
alter table public.scene_generation_jobs
  add constraint scene_jobs_paid_has_reservation
  check (credits_reserved = 0 or reservation_ledger_id is not null);

-- 미결 예약 조회 — 회수 배치가 쓴다. 프로세스가 죽어 해제되지 못한 예약을
-- 찾지 못하면 고객 크레딧이 영원히 잡혀 있는다.
create index if not exists credit_ledger_reserved_idx
  on public.credit_ledger (user_id, created_at)
  where state = 'RESERVED';

-- ── 생성 세션 ↔ 예약 ────────────────────────────────────────────────────────
--
-- 프리미엄 행동(아이들·액션)은 credit_generation_sessions 를 통해 제출되고,
-- 웹훅 종료 경로가 그 세션을 보고 판정한다. 예약을 여기 매달면 PASS/FAIL 판정과
-- 확정/해제가 **같은 곳에서** 일어난다 — 두 곳으로 갈라지면 한쪽이 빠진다.
alter table if exists public.credit_generation_sessions
  add column if not exists reservation_ledger_id uuid,
  add column if not exists product_key text;

comment on column public.credit_generation_sessions.reservation_ledger_id is
  '이 세션을 뒷받침하는 예약. 유료 생성은 반드시 있어야 한다 — 없으면 제출하지 않는다';

-- ── legacy_charge: 예약 이전 방식으로 과금된 세션 ───────────────────────────
--
-- ⚠️ 이 컬럼이 없으면 아래 CHECK 가 **기존 행에서 실패한다**:
--
--     ERROR: check constraint "credit_sessions_paid_has_reservation"
--            is violated by some row
--
-- 이유: credits_charged 는 `default 4` 인 **기존** 컬럼이다. 예약이 생기기 전의
-- 모든 세션이 4를 들고 있고 reservation_ledger_id 는 당연히 비어 있다.
-- (scene_generation_jobs 는 credits_reserved 가 `default 0` 인 **새** 컬럼이라
--  같은 문제가 없다. 그 비대칭이 이쪽만 깨진 이유다.)
--
-- 그 행들은 잘못 과금된 것이 아니다. 예약이 없던 시절의 차감-후-환불 방식으로
-- 정상 과금됐다. 제약이 "저 과금은 무효였다"고 말하게 두지 않는다.
--
-- **아직 살아 있는 경로이기도 하다.** 4크레딧 기기 생성 팩
-- (credit_generation_service.generate_with_credit)은 기기 호환성이 이전되지
-- 않아 은퇴하지 못했다(docs/LEGACY_RETIREMENT.md §5). 그 경로는 여전히
-- 예약 없이 차감한다. 예외를 두지 않으면 **운영에서** insert 가 막히는데,
-- 차감이 insert 보다 먼저 일어나므로 고객은 4크레딧을 잃고 아무것도 받지 못한다.
--
-- NOT VALID 로 우회하지 않는 이유: NOT VALID 여도 기존 행을 **UPDATE 할 때는**
-- 검사한다. 결제창처럼, 배포 시점에 processing 으로 떠 있던 레거시 세션의 웹훅이
-- 나중에 도착하면 그 UPDATE 가 막힌다 — 돈은 나갔는데 결과를 못 받는다.
-- 플래그는 그 UPDATE 를 통과시킨다.
--
-- 이 컬럼은 §5 가 끝나면 함께 사라진다. 그때까지 예외가 번지지 못하도록
-- backend/tests/test_legacy_charge_exemption.py 가 호출부를 하나로 고정한다.
alter table if exists public.credit_generation_sessions
  add column if not exists legacy_charge boolean not null default false;

comment on column public.credit_generation_sessions.legacy_charge is
  '예약 이전 방식(차감-후-환불)으로 과금됐다. 4크레딧 기기 팩 전용 — 은퇴 시 함께 삭제';

update public.credit_generation_sessions
   set legacy_charge = true
 where credits_charged > 0
   and reservation_ledger_id is null
   and legacy_charge = false;

alter table public.credit_generation_sessions
  drop constraint if exists credit_sessions_paid_has_reservation;
alter table public.credit_generation_sessions
  add constraint credit_sessions_paid_has_reservation
  check (credits_charged = 0 or legacy_charge or reservation_ledger_id is not null);

-- 제약을 건 뒤 스스로 확인한다 — 예약 시대의 행 중 예약 없이 과금된 것이 있으면
-- 그것은 백필 실수가 아니라 **버그**이므로 마이그레이션을 실패시킨다.
do $$
declare
  bad int;
begin
  select count(*) into bad
    from public.credit_generation_sessions
   where credits_charged > 0
     and reservation_ledger_id is null
     and legacy_charge = false;
  if bad > 0 then
    raise exception
      '예약 없이 과금된 세션이 % 건 남아 있다 — legacy_charge 백필을 확인할 것', bad;
  end if;
end $$;
