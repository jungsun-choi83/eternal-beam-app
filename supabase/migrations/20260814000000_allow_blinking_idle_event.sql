-- BLINKING(첫 아이들 이벤트) 을 generated_motions 에 허용한다. (Phase 1A)
--
-- 배경: generated_motions.action_id 의 CHECK 는 지금까지 두 번 정의됐다.
--   20260721000200_hybrid_business_wallet.sql  → 인라인 CHECK, 레거시 4종만
--       (컬럼 인라인 CHECK 라 Postgres 가 generated_motions_action_id_check 로 자동 명명)
--   20260810020000_allow_come_closer_action.sql → drop 후 재생성, + COME_CLOSER
-- 이 마이그레이션은 그 뒤를 잇는 **세 번째 정의**이고, 앞의 값들을 전부 보존한 채
-- BLINKING 하나만 더한다. 좁히는 변경은 없다.
--
-- BLINKING 은 COME_CLOSER 와 같은 저장 규칙을 쓴다:
--   * 테마 독립 → place_id 는 센티널 'any' (펫당 한 행으로 접힌다)
--   * unique (user_id, pet_id, place_id, action_id) 가 그대로 멱등성을 보장한다
--
-- ACTION_ORDER / 4코인 계약 / device sync 는 **건드리지 않는다**.
-- BLINKING 은 ACTION_ORDER 밖이라 /device/sync 는 계속 레거시 4종만 요구한다.
--
-- motion_generation_jobs.action_id 에는 CHECK 가 없다(같은 파일에서 확인) —
-- 진행 중 작업 행은 이미 BLINKING 을 받으므로 그쪽은 변경이 필요 없다.

alter table if exists public.generated_motions
  drop constraint if exists generated_motions_action_id_check;

alter table if exists public.generated_motions
  add constraint generated_motions_action_id_check
  check (action_id in ('IDLE', 'TOUCH', 'VOICE', 'NFC', 'COME_CLOSER', 'BLINKING'));

comment on column public.generated_motions.action_id is
  'IDLE/TOUCH/VOICE/NFC = 레거시 4종 세트(device sync 대상). '
  'COME_CLOSER = 웹 전용 프리미엄 액션. BLINKING = 아이들 이벤트(테마 독립).';
