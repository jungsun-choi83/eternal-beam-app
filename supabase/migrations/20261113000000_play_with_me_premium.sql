-- PLAY_WITH_ME ("Let's Play!") — species-aware user-triggered play invitation.
-- One commercial motion id; the DOG (PLAY_BOW) / CAT (PLAY_CROUCH) variant lives
-- only inside the generation contract, never in a stored motion id.
-- This only widens the existing motion registries and adds one priced product;
-- legacy IDLE/TOUCH/VOICE/NFC and every existing motion remain unchanged.

alter table public.pet_generation_runs
  drop constraint if exists pet_generation_runs_motion_id_check;
alter table public.pet_generation_runs
  add constraint pet_generation_runs_motion_id_check check (
    motion_id in (
      'BREATHING',
      'BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING',
      'COME_CLOSER', 'PET_HEAD', 'PAW_HELLO', 'LOOK_UP',
      'LIE_DOWN', 'STAND_UP', 'LIE_IDLE',
      'RETURN_HOME',
      'PLAY_WITH_ME'
    )
  );

alter table public.pet_motion_publications
  drop constraint if exists pet_motion_publications_motion_id_check;
alter table public.pet_motion_publications
  add constraint pet_motion_publications_motion_id_check check (
    motion_id in (
      'BREATHING',
      'BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING',
      'COME_CLOSER', 'PET_HEAD', 'PAW_HELLO', 'LOOK_UP',
      'LIE_DOWN', 'STAND_UP', 'LIE_IDLE',
      'RETURN_HOME',
      'PLAY_WITH_ME'
    )
  );

alter table if exists public.generated_motions
  drop constraint if exists generated_motions_action_id_check;
alter table if exists public.generated_motions
  add constraint generated_motions_action_id_check check (
    action_id in (
      'IDLE', 'TOUCH', 'VOICE', 'NFC',
      'COME_CLOSER',
      'BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING',
      'PET_HEAD', 'PAW_HELLO', 'LOOK_UP',
      'LIE_DOWN', 'STAND_UP', 'LIE_IDLE',
      'RETURN_HOME',
      'PLAY_WITH_ME'
    )
  );

insert into public.digital_products (product_key, product_type, credit_price, display_name)
values ('action:PLAY_WITH_ME', 'ACTION', 1, 'Let''s Play!')
on conflict (product_key) do nothing;

comment on column public.generated_motions.action_id is
  'Legacy IDLE/TOUCH/VOICE/NFC plus web premium/system motions, including PAW_HELLO and PLAY_WITH_ME.';
