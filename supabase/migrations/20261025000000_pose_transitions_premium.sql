-- 자세 전이 3종 — LIE_DOWN(전이) / LIE_IDLE(누운 홈 루프) / STAND_UP(복귀).
-- 20261023/24 와 같은 형태: 코드 정본은 이미 넓혀졌고 DB 세 곳을 일치시킨다.

alter table public.pet_generation_runs
  drop constraint if exists pet_generation_runs_motion_id_check;
alter table public.pet_generation_runs
  add constraint pet_generation_runs_motion_id_check check (
    motion_id in (
      'BREATHING',
      'BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING',
      'COME_CLOSER', 'PET_HEAD', 'LOOK_UP',
      'LIE_DOWN', 'STAND_UP', 'LIE_IDLE'
    )
  );

alter table public.pet_motion_publications
  drop constraint if exists pet_motion_publications_motion_id_check;
alter table public.pet_motion_publications
  add constraint pet_motion_publications_motion_id_check check (
    motion_id in (
      'BREATHING',
      'BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING',
      'COME_CLOSER', 'PET_HEAD', 'LOOK_UP',
      'LIE_DOWN', 'STAND_UP', 'LIE_IDLE'
    )
  );

-- generated_motions_action_id_check 의 **여덟 번째** 정의 (계보: 20260814020000
-- → 20261023 → 20261024).
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
      'LIE_DOWN', 'STAND_UP', 'LIE_IDLE'
    )
  );

insert into public.digital_products (product_key, product_type, credit_price, display_name)
values
  ('action:LIE_DOWN', 'ACTION', 1, 'Lie Down'),
  ('action:STAND_UP', 'ACTION', 1, 'Stand Up'),
  ('action:LIE_IDLE', 'ACTION', 1, 'Lie Idle')
on conflict (product_key) do nothing;
