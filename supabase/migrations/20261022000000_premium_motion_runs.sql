-- Phase 7H — 기존 상용 모션(아이들 4종 + COME_CLOSER)을 새 생성 실행으로 재지향.
--
-- product_key 는 판매/가격의 권위로 그대로 남는다 (digital_products 불변).
-- 이 마이그레이션은 **이행(fulfillment) 배관**만 연다:
--   * 실행 테이블이 5개 상용 모션 + PREMIUM_PRODUCT 요청 종류를 받는다
--   * 실행이 상거래 맥락(product_key / 예약 원장)을 계보로 보존한다
--   * 발행 원장이 BREATHING 전용 CHECK 를 벗는다 (프리미엄 발행 기록용)
--   * 소유 원장이 새 시스템 계보(jsonb)를 싣는다
--
-- 새 모션(PET_HEAD, RUN …)은 여기서 열지 않는다 — 카탈로그에 명시적으로
-- 추가되기 전까지 판매 불가라는 사실이 실행 테이블에서도 그대로 보이게 한다.

-- 1) pet_generation_runs — 상용 모션 + 요청 종류
alter table public.pet_generation_runs
  drop constraint if exists pet_generation_runs_motion_id_check;
alter table public.pet_generation_runs
  add constraint pet_generation_runs_motion_id_check check (
    motion_id in (
      'BREATHING',
      'BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING',
      'COME_CLOSER'
    )
  );

alter table public.pet_generation_runs
  drop constraint if exists pet_generation_runs_request_kind_check;
alter table public.pet_generation_runs
  add constraint pet_generation_runs_request_kind_check check (
    request_kind in ('FREE_HOME', 'PREMIUM_PRODUCT')
  );

-- 상거래 맥락. BREATHING/FREE_HOME 실행에서는 전부 null 이다.
alter table public.pet_generation_runs
  add column if not exists product_key text;
alter table public.pet_generation_runs
  add column if not exists reservation_ledger_id text;
alter table public.pet_generation_runs
  add column if not exists credits_reserved int not null default 0;

comment on column public.pet_generation_runs.product_key is
  'PREMIUM_PRODUCT 실행의 digital_products.product_key (판매 권위는 카탈로그, 여기는 계보)';
comment on column public.pet_generation_runs.reservation_ledger_id is
  '이 실행을 뒷받침하는 크레딧 예약(credit_ledger). 구독 모드/무료면 null';

-- 2) pet_motion_publications — BREATHING 전용 CHECK 해제 (배경 없는 테마 독립
--    모션만 발행되므로 background_baked=false CHECK 는 그대로 유효하다)
alter table public.pet_motion_publications
  drop constraint if exists pet_motion_publications_motion_id_check;
alter table public.pet_motion_publications
  add constraint pet_motion_publications_motion_id_check check (
    motion_id in (
      'BREATHING',
      'BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING',
      'COME_CLOSER'
    )
  );

-- 3) owned_generated_assets — 새 시스템 계보
--    (generation_run_id / motion_version_id / candidate_id / publication_id /
--     delivery bucket·path·format / product_key / reservation)
alter table public.owned_generated_assets
  add column if not exists lineage jsonb not null default '{}'::jsonb;

comment on column public.owned_generated_assets.lineage is
  'Phase 7H 이행 계보 — Phase 6 이 정본이고 이 값은 조인 열쇠 모음이다';
