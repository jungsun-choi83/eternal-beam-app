-- LOOK_UP — VOICE 트리거 상용 모션 (MICRO, NEUTRAL_IDLE 키프레임 재사용).
--
-- 20261023(PET_HEAD)과 같은 형태: 코드 정본(PACKAGEABLE_MOTIONS / PET_ACTIONS /
-- product_catalog._SEED)은 이미 넓혀졌고, 여기서 DB 세 곳을 일치시킨다.
-- 배포 순서 내성도 동일하다 — 어긋난 기간에는 fail-closed 일 뿐 오염이 없다.

-- 1) 생성 실행 motion CHECK
alter table public.pet_generation_runs
  drop constraint if exists pet_generation_runs_motion_id_check;
alter table public.pet_generation_runs
  add constraint pet_generation_runs_motion_id_check check (
    motion_id in (
      'BREATHING',
      'BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING',
      'COME_CLOSER',
      'PET_HEAD',
      'LOOK_UP'
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
      'PET_HEAD',
      'LOOK_UP'
    )
  );

-- 3) 재생 포인터 action_id CHECK — generated_motions_action_id_check 의
--    **일곱 번째** 정의 (계보: 20260814020000 → 20261023 참조).
alter table if exists public.generated_motions
  drop constraint if exists generated_motions_action_id_check;
alter table if exists public.generated_motions
  add constraint generated_motions_action_id_check
  check (
    action_id in (
      'IDLE', 'TOUCH', 'VOICE', 'NFC',
      'COME_CLOSER',
      'BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING',
      'PET_HEAD',
      'LOOK_UP'
    )
  );

-- 4) 카탈로그 행 (product_catalog._SEED 와 동일 값).
insert into public.digital_products (product_key, product_type, credit_price, display_name)
values
  ('action:LOOK_UP', 'ACTION', 1, 'Look Up')
on conflict (product_key) do nothing;
