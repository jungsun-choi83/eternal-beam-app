-- Phase 7G — 실행 단계에 DELIVERY(패킹, Phase 7F) 추가.
--
-- QA 를 통과(또는 REVIEW)한 후보는 발행/개발 재생 전에 packed-alpha 파생물로
-- 포장된다. 그 구간은 QA 도 PUBLICATION 도 아니므로 단계 이름을 따로 가진다 —
-- 진행 표시와 실패 지점 진단이 정확해야 재시도가 올바른 곳에서 시작된다.

alter table public.pet_generation_runs
  drop constraint if exists pet_generation_runs_current_stage_check;
alter table public.pet_generation_runs
  add constraint pet_generation_runs_current_stage_check check (
    current_stage in (
      'QUEUED', 'IDENTITY', 'REFERENCE_SET', 'CANONICAL', 'KEYFRAMES',
      'MOTION_SPEC', 'MOTION_GENERATION', 'QA', 'DELIVERY', 'PUBLICATION',
      'PUBLISHED'
    )
  );
