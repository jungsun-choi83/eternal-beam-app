-- HEAD_TILTING / TAIL_WAGGING (나머지 두 아이들 이벤트) 을 허용한다. (Phase 4)
--
-- generated_motions_action_id_check 의 **다섯 번째** 정의다. 앞선 정의들:
--   20260721000200_hybrid_business_wallet.sql            레거시 4종 (인라인 CHECK)
--   20260810020000_allow_come_closer_action.sql          + COME_CLOSER
--   20260814000000_allow_blinking_idle_event.sql         + BLINKING
--   20260814010000_allow_ear_twitching_idle_event.sql    + EAR_TWITCHING
-- 기존 값은 전부 보존하고 두 개만 더한다. 좁히는 변경은 없다.
--
-- 이로써 선언된 아이들 이벤트 4종이 모두 저장 가능해진다. 저장 규칙은 앞의 둘과 동일:
--   * 테마 독립 → place_id 는 센티널 'any'
--   * unique (user_id, pet_id, place_id, action_id) 가 펫당 한 행을 보장한다
--     → 재제출이 와도 같은 키로 접히므로 멱등성이 그대로 유지된다
--
-- ACTION_ORDER / 4코인 계약 / device sync 는 **건드리지 않는다**.
-- 아이들 이벤트는 전부 ACTION_ORDER 밖이라 /device/sync 는 계속 레거시 4종만 요구한다.

alter table if exists public.generated_motions
  drop constraint if exists generated_motions_action_id_check;

alter table if exists public.generated_motions
  add constraint generated_motions_action_id_check
  check (
    action_id in (
      'IDLE', 'TOUCH', 'VOICE', 'NFC',
      'COME_CLOSER',
      'BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING'
    )
  );

comment on column public.generated_motions.action_id is
  'IDLE/TOUCH/VOICE/NFC = 레거시 4종 세트(device sync 대상). '
  'COME_CLOSER = 웹 전용 프리미엄 액션. '
  'BLINKING/EAR_TWITCHING/HEAD_TILTING/TAIL_WAGGING = 아이들 이벤트(테마 독립).';
