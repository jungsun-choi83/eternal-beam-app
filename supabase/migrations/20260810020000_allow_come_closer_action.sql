-- COME_CLOSER(프리미엄 액션) 을 generated_motions 에 허용한다.
--
-- generated_motions.action_id 에는 4종 레거시 액션만 허용하는 CHECK 가 걸려 있어
-- COME_CLOSER 행이 INSERT 단계에서 거부된다. 제약을 **넓히기만** 한다:
--   * 기존 4종은 그대로 유효
--   * ACTION_ORDER / 4코인 계약 / device sync 는 건드리지 않는다
--     (COME_CLOSER 는 ACTION_ORDER 밖이라 /device/sync 는 계속 4종만 요구한다)

alter table if exists public.generated_motions
  drop constraint if exists generated_motions_action_id_check;

alter table if exists public.generated_motions
  add constraint generated_motions_action_id_check
  check (action_id in ('IDLE', 'TOUCH', 'VOICE', 'NFC', 'COME_CLOSER'));

comment on column public.generated_motions.action_id is
  'IDLE/TOUCH/VOICE/NFC = 레거시 4종 세트(device sync 대상). COME_CLOSER = 웹 전용 프리미엄 액션';
