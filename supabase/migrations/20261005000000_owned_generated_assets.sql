-- 생성 자산 **영구 소유 원장** (Phase 6).
--
--     Sleeping #1   owned
--     Sleeping #2   owned      ← 셋 다 공존한다
--     Sleeping #3   owned
--     Paw Wave #1   owned
--     Paw Wave #2   owned
--
-- ── 왜 필요한가 ─────────────────────────────────────────────────────────────
-- 지금까지 소유의 근거는 `generated_motions` 였는데, 그 표는
--
--     unique (user_id, pet_id, place_id, action_id)
--
-- 이고 승격 경로가 **upsert** 한다(_record_promoted_motion). 그래서 같은 행동을
-- 두 번 만들면 **두 번째가 첫 번째를 덮어쓴다.** 고객이 각각 값을 낸 자산인데
-- 하나만 남는다 — 사업 모델과 정면으로 충돌한다.
--
-- ── 역할을 나눈다 ───────────────────────────────────────────────────────────
--     owned_generated_assets   **무엇을 샀는가** (추가만 한다. 절대 덮어쓰지 않는다)
--     generated_motions        **지금 무엇을 재생하는가** (포인터. 계속 upsert 한다)
--
-- 이 분리는 premium_purchases 가 이미 쓰고 있는 것과 같은 모양이다: 과금 원장과
-- 재생 권한을 나누는 것. 여기서는 그 원장이 자산 단위로 내려온 것이다.
--
-- ── 유일성은 "무엇인가"가 아니라 "무엇이 만들었는가"에 건다 ─────────────────
-- (user, pet, product_key) 에 unique 를 걸면 이 표를 만든 이유가 사라진다.
-- 그렇다고 제약이 아예 없으면 웹훅 재전송 한 번이 소유 자산을 두 배로 만든다.
--
-- 그래서 **source_job_id** 에 건다. 생성 작업 하나가 자산 하나를 만든다.
-- 서로 다른 작업은 서로 다른 자산이므로, Sleeping 을 세 번 만들면 세 행이 남는다.

create table if not exists public.owned_generated_assets (
  asset_id      uuid primary key default gen_random_uuid(),
  user_id       text not null,
  pet_id        text not null,

  -- digital_products.product_key 와 같은 규약 ('idle:SLEEPING', 'action:PAW_WAVE').
  -- 카탈로그·원장·소유가 같은 문자열로 조인된다.
  product_key   text not null,

  -- 이 자산이 나온 정본 장면. 같은 장면에서 나온 자산들은 배경이 일치한다.
  scene_id      text,

  video_url     text not null,
  -- 재서명용 정본 경로. URL 은 만료되지만 경로는 만료되지 않는다
  -- (shaker_shares 가 같은 이유로 갖고 있는 컬럼이다).
  object_path   text,
  bucket        text,

  -- **이 자산에 실제로 지불된 크레딧.** 0 은 무료이거나 레거시라는 뜻이다.
  credits_spent int not null default 0 check (credits_spent >= 0),
  -- 그 지불을 설명하는 원장 행. 무료·레거시면 null.
  ledger_id     uuid,

  -- 이 자산이 어떻게 생겼는가.
  --   'purchase'          크레딧을 내고 만들었다
  --   'legacy_migration'  원장 도입 이전부터 있던 자산 (**소급 과금하지 않는다**)
  --   'free'              무료 행동 (BREATHING 등)
  --
  -- ⚠️ 지시된 컬럼 목록에는 없지만 추가했다. "credits_spent = 0" 만으로는
  --    무료 상품과 레거시 자산과 환불된 구매가 구분되지 않는다. 그 셋은 운영에서
  --    다르게 다뤄야 하고, 나중에 되짚을 방법이 없으면 영영 구분할 수 없다.
  source        text not null default 'purchase',

  -- 생성 작업 식별자. **유일성의 축**이다 (아래 인덱스 참고).
  source_job_id text,

  created_at    timestamptz not null default now(),
  -- 폐기는 삭제가 아니라 표시다. 지우면 "환불됨"과 "가진 적 없음"이 같아진다.
  revoked_at    timestamptz
);

alter table public.owned_generated_assets drop constraint if exists owned_assets_source_check;
alter table public.owned_generated_assets add constraint owned_assets_source_check
  check (source in ('purchase', 'legacy_migration', 'free'));

-- 지불한 자산은 원장이 설명해야 한다. 값을 냈는데 근거가 없으면 대조가 불가능하다.
alter table public.owned_generated_assets drop constraint if exists owned_assets_paid_has_ledger;
alter table public.owned_generated_assets add constraint owned_assets_paid_has_ledger
  check (credits_spent = 0 or ledger_id is not null);

-- 레거시·무료 자산은 **절대 과금된 것으로 기록되지 않는다.**
-- 이 제약이 "옛 고객에게 소급 청구하지 않는다"를 스키마 수준에서 못박는다.
alter table public.owned_generated_assets drop constraint if exists owned_assets_free_is_free;
alter table public.owned_generated_assets add constraint owned_assets_free_is_free
  check (source = 'purchase' or credits_spent = 0);

-- ── 유일성 ──────────────────────────────────────────────────────────────────
--
-- ⚠️ (user_id, pet_id, product_key) 에 unique 를 **걸지 않는다.** 그것이 이 표의
--    존재 이유다 — 같은 행동의 여러 버전이 공존해야 한다.
--
-- 대신 "생성 작업 하나 = 자산 하나" 를 건다. 웹훅 재전송·재시도가 소유 자산을
-- 늘리지 못하면서, 새 작업은 언제나 새 자산이 된다.
create unique index if not exists owned_assets_job_uidx
  on public.owned_generated_assets (source_job_id)
  where source_job_id is not null;

-- 라이브러리 조회: "이 펫으로 내가 가진 것 전부" (최신순).
create index if not exists owned_assets_pet_idx
  on public.owned_generated_assets (user_id, pet_id, created_at desc)
  where revoked_at is null;

-- 상품별 조회: "Sleeping 을 몇 개나 갖고 있나".
create index if not exists owned_assets_product_idx
  on public.owned_generated_assets (user_id, pet_id, product_key)
  where revoked_at is null;

-- 원장 → 자산 역추적.
create index if not exists owned_assets_ledger_idx
  on public.owned_generated_assets (ledger_id)
  where ledger_id is not null;

comment on table public.owned_generated_assets is
  '생성 자산 영구 소유 원장. 추가만 한다 — 같은 행동의 여러 버전이 공존한다';
comment on column public.owned_generated_assets.source_job_id is
  '유일성의 축. "무엇인가"가 아니라 "무엇이 만들었는가"에 건다 — 버전 공존을 위해';
comment on column public.owned_generated_assets.source is
  'purchase | legacy_migration | free. legacy_migration 은 소급 과금하지 않는다';
comment on column public.owned_generated_assets.credits_spent is
  '실제 지불 크레딧. 레거시·무료는 0 이며 제약이 그것을 강제한다';

-- ── 백필 ────────────────────────────────────────────────────────────────────
--
-- 기존 generated_motions 의 canonical 행을 소유 자산으로 옮긴다.
--
--     credits_spent = 0
--     source        = 'legacy_migration'
--     ledger_id     = null
--
-- **옛 고객에게 소급 과금하지 않는다.** 이 자산들이 언제 어떻게 만들어졌는지는
-- 원장 도입 이전이라 알 수 없고, 모르는 것을 청구로 기록하지 않는다.
--
-- 멱등하다: source_job_id 를 'legacy:{user}:{pet}:{place}:{action}' 로 결정적으로
-- 만들어, 두 번 돌려도 unique 인덱스가 두 번째를 막는다.
create or replace function public.backfill_owned_assets(p_dry_run boolean default false)
returns table (user_id text, pet_id text, product_key text, action_taken text)
language plpgsql
as $$
declare
  r record;
  v_product text;
  v_job text;
begin
  for r in
    select m.user_id, m.pet_id, m.place_id, m.action_id, m.video_url, m.created_at
      from public.generated_motions m
     where m.video_url is not null and btrim(m.video_url) <> ''
     order by m.user_id, m.pet_id, m.action_id
  loop
    -- 레거시 4종(IDLE/TOUCH/VOICE/NFC)은 기기 재생용 모션이고 개별 상품이 아니다.
    -- 상품 키 규약은 premium_purchase._product_key 와 같다: 아이들 이벤트는
    -- idle:, 그 밖은 action:.
    v_product := case
      when upper(r.action_id) in ('BLINKING','EAR_TWITCHING','HEAD_TILTING','TAIL_WAGGING','BREATHING')
        then 'idle:' || upper(r.action_id)
      else 'action:' || upper(r.action_id)
    end;
    v_job := 'legacy:' || r.user_id || ':' || r.pet_id || ':' || r.place_id || ':' || r.action_id;

    if p_dry_run then
      user_id := r.user_id; pet_id := r.pet_id; product_key := v_product;
      action_taken := 'WOULD_INSERT';
      return next;
      continue;
    end if;

    insert into public.owned_generated_assets (
      user_id, pet_id, product_key, video_url,
      credits_spent, ledger_id, source, source_job_id, created_at
    ) values (
      r.user_id, r.pet_id, v_product, r.video_url,
      0, null, 'legacy_migration', v_job, coalesce(r.created_at, now())
    )
    -- ⚠️ where 절이 **반드시** 있어야 한다. owned_assets_job_uidx 는 부분 인덱스라
    --    (where source_job_id is not null), 술어를 빼면 Postgres 가 대응하는 제약을
    --    찾지 못하고 "no unique or exclusion constraint matching" 으로 실패한다.
    on conflict (source_job_id) where source_job_id is not null do nothing;

    user_id := r.user_id; pet_id := r.pet_id; product_key := v_product;
    action_taken := 'INSERTED';
    return next;
  end loop;
end;
$$;

comment on function public.backfill_owned_assets is
  '기존 generated_motions → 소유 자산. credits_spent=0 / legacy_migration. 멱등';

-- 지금 적용한다. 이 시점 이후의 승격은 서비스가 직접 소유 자산을 기록한다.
select public.backfill_owned_assets();
