-- COME_PLAY ("Come Play!") — DOG-only user-triggered approach + play-bow invitation.
-- STAND_READY (home) → playful trot toward the viewer → near play bow → return home →
-- STAND_READY. One commercial motion id; the species variant (COME_PLAY_BOW) and the
-- near play-bow keyframe (PLAY_BOW_NEAR) live only in the generation contract.
-- This only widens the existing motion registries and adds one priced product;
-- PLAY_WITH_ME, COME_CLOSER, RETURN_HOME and every other motion remain unchanged.

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
      'PLAY_WITH_ME',
      'COME_PLAY'
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
      'PLAY_WITH_ME',
      'COME_PLAY'
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
      'PLAY_WITH_ME',
      'COME_PLAY'
    )
  );

insert into public.digital_products (product_key, product_type, credit_price, display_name)
values ('action:COME_PLAY', 'ACTION', 1, 'Come Play!')
on conflict (product_key) do nothing;

comment on column public.generated_motions.action_id is
  'Legacy IDLE/TOUCH/VOICE/NFC plus web premium/system motions, including PAW_HELLO, PLAY_WITH_ME and COME_PLAY.';
