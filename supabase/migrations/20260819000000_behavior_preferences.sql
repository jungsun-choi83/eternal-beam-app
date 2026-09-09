-- 프리미엄 행동 ON/OFF 선호.
--
-- 왜 **별도 테이블**인가: 선호는 생성물도 결제 기록도 아니다.
--   generated_motions      = "만들어졌는가" (canonical 자산)
--   premium_purchases      = "과금했는가"   (크레딧 시대 원장)
--   behavior_preferences   = "켜 두고 싶은가" (여기)
--
-- 이 분리가 요구사항을 구조로 보장한다: **구독이 만료돼도 선호는 지워지지 않는다.**
-- 만료는 user_subscriptions.status 만 바꾸고 이 테이블을 건드리지 않으므로, 갱신하면
-- 예전 설정이 그대로 돌아온다. 삭제 경로를 아예 만들지 않는 것이 가장 확실한 보장이다.
--
-- READY 여부와도 독립이다. 아직 만들지 않은 행동에 대한 선호도 저장할 수 있다 —
-- "READY 와 선호는 별개 상태"라는 규칙을 스키마 수준에서 지킨다. UI 가 READY 인
-- 행동에만 토글을 보여 주는 것은 표시 규칙이지 저장 규칙이 아니다.

create table if not exists public.behavior_preferences (
  user_id text not null,
  pet_id text not null,
  -- PREMIUM_ACTIONS 의 canonical id: BLINKING / EAR_TWITCHING / HEAD_TILTING /
  -- TAIL_WAGGING / COME_CLOSER. 레거시 IDLE/TOUCH/VOICE/NFC 는 여기 들어오지 않는다.
  action_id text not null,
  enabled boolean not null default true,
  updated_at timestamptz not null default now(),
  primary key (user_id, pet_id, action_id)
);

-- 한 펫의 전체 선호를 한 번에 읽는 것이 유일한 조회 패턴이다.
create index if not exists behavior_preferences_user_pet_idx
  on public.behavior_preferences (user_id, pet_id);

comment on table public.behavior_preferences is
  '프리미엄 행동 ON/OFF 선호. 구독 만료·갱신과 무관하게 보존된다 (삭제 경로 없음)';
comment on column public.behavior_preferences.enabled is
  '켬(true)/끔(false). 행이 없으면 기본 켬 — 만든 행동은 켜져 있는 것이 기대값이다';
comment on column public.behavior_preferences.action_id is
  'PREMIUM_ACTIONS 의 canonical 행동 id. 재생 연결은 아직 없다 (Phase 5 는 저장까지)';
