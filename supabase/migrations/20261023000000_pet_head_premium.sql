-- PET_HEAD — 첫 INTERACTION 상용 모션 (TOUCH 트리거의 목적지).
--
-- 코드 정본은 이미 넓혀졌다:
--   * motion_delivery_service.PACKAGEABLE_MOTIONS (+PET_HEAD)
--     → premium_motion_finalization.PREMIUM_MOTIONS 파생
--   * pet_scenarios.PET_ACTIONS (+PET_HEAD) → 구매 kind ACTION:PET_HEAD,
--     상품 키 action:PET_HEAD (owned_assets.product_key_for_action 규약)
--   * product_catalog._SEED (+action:PET_HEAD, 1 크레딧)
--
-- 이 마이그레이션은 DB 쪽 세 가지를 코드와 일치시킨다:
--   1) pet_generation_runs.motion_id CHECK 확장
--   2) pet_motion_publications.motion_id CHECK 확장
--   3) digital_products 에 action:PET_HEAD 행 (행 없음 = 판매 불가 = 이행 불가)
--
-- 배포 순서 내성: 코드가 먼저 배포되고 이 마이그레이션이 늦어도, 실행 생성이
-- CHECK 위반으로 실패할 뿐(fail-closed) 데이터가 오염되지 않는다. 반대 순서는
-- 무해하다 — CHECK 가 넓어도 코드 검증이 먼저 거른다.

-- 1) 생성 실행 motion CHECK
alter table public.pet_generation_runs
  drop constraint if exists pet_generation_runs_motion_id_check;
alter table public.pet_generation_runs
  add constraint pet_generation_runs_motion_id_check check (
    motion_id in (
      'BREATHING',
      'BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING',
      'COME_CLOSER',
      'PET_HEAD'
    )
  );

-- 2) 발행 원장 motion CHECK
alter table public.pet_motion_publications
  drop constraint if exists pet_motion_publications_motion_id_check;
alter table public.pet_motion_publications
  add constraint pet_motion_publications_motion_id_check check (
    motion_id in (
      'BREATHING',
      'BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING',
      'COME_CLOSER',
      'PET_HEAD'
    )
  );

-- 3) 재생 포인터 action_id CHECK — generated_motions_action_id_check 의
--    **여섯 번째** 정의 (계보는 20260814020000 참조). 이행 확정이 PET_HEAD
--    포인터를 기록하려면 여기가 열려 있어야 한다 — 닫혀 있으면 승격 INSERT 가
--    조용히 실패한다 (test_action_id_check_constraint 가 강제).
alter table if exists public.generated_motions
  drop constraint if exists generated_motions_action_id_check;
alter table if exists public.generated_motions
  add constraint generated_motions_action_id_check
  check (
    action_id in (
      'IDLE', 'TOUCH', 'VOICE', 'NFC',
      'COME_CLOSER',
      'BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING',
      'PET_HEAD'
    )
  );

-- 4) 카탈로그 행 — 가격의 권위는 이 표다 (product_catalog._SEED 와 동일 값).
insert into public.digital_products (product_key, product_type, credit_price, display_name)
values
  ('action:PET_HEAD', 'ACTION', 1, 'Pet Head')
on conflict (product_key) do nothing;
