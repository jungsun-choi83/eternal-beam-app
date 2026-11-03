-- RETURN_HOME — COME_CLOSER 의 시스템 소유 무료 동반 복귀 모션 (2026-10-22, 하이브리드).
--
-- 코드 정본은 이미 넓혀졌다: motion_delivery_service.PACKAGEABLE_MOTIONS 에
-- RETURN_HOME 추가 + SYSTEM_MOTIONS(BREATHING, RETURN_HOME) 신설, motion_spec.MOTIONS
-- 에 RETURN_HOME(START_END, 시작=런별 파생 CLOSE_POSITION → 끝=STAND_READY). 여기서
-- DB 세 곳의 CHECK 를 일치시킨다. 넓히기만 한다 — 기존 값은 전부 그대로 유효하고,
-- 배포 순서가 어긋난 기간에는 fail-closed(거부)일 뿐 오염이 없다.
--
-- ⚠️ RETURN_HOME 은 **무료 시스템 모션**이다 — digital_products(카탈로그) 행을
--    추가하지 않는다. 행이 없으면 product_catalog.require_price 가 "판매 불가"로
--    막으므로 구매/과금 경로가 애초에 닿지 못한다.

-- 1) 생성 실행 motion CHECK
alter table public.pet_generation_runs
  drop constraint if exists pet_generation_runs_motion_id_check;
alter table public.pet_generation_runs
  add constraint pet_generation_runs_motion_id_check check (
    motion_id in (
      'BREATHING',
      'BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING',
      'COME_CLOSER', 'PET_HEAD', 'LOOK_UP',
      'LIE_DOWN', 'STAND_UP', 'LIE_IDLE',
      'RETURN_HOME'
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
      'COME_CLOSER', 'PET_HEAD', 'LOOK_UP',
      'LIE_DOWN', 'STAND_UP', 'LIE_IDLE',
      'RETURN_HOME'
    )
  );

-- 3) 재생 포인터 action_id CHECK — generated_motions_action_id_check 의 다음 정의
--    (계보: 20260814020000 → 20261023 → 20261024 → 20261025).
alter table if exists public.generated_motions
  drop constraint if exists generated_motions_action_id_check;
alter table if exists public.generated_motions
  add constraint generated_motions_action_id_check
  check (
    action_id in (
      'IDLE', 'TOUCH', 'VOICE', 'NFC',
      'COME_CLOSER',
      'BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING',
      'PET_HEAD', 'LOOK_UP',
      'LIE_DOWN', 'STAND_UP', 'LIE_IDLE',
      'RETURN_HOME'
    )
  );

comment on column public.generated_motions.action_id is
  '레거시 4종(IDLE/TOUCH/VOICE/NFC) + 웹 프리미엄/시스템 모션. RETURN_HOME 은 무료 시스템 복귀 모션(카탈로그 행 없음).';
