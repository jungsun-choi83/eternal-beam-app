-- Phase 7F — 명시적 전달 포맷 (delivery format).
--
-- 왜 필요한가: 브라우저 재생기는 세 모드를 가진다 (baked / blackkey / packed).
-- 지금까지 판정은 background_baked 불리언 + 파일명/크로마 휴리스틱이었다.
-- Phase 6 산출물은 중립 회색 배경이라 blackkey 로도 baked 로도 재생될 수 없고,
-- packed-alpha 파생물로 포장된다. 새 자산의 모드는 추측이 아니라 **명시된 값**
-- 이어야 한다 — 휴리스틱 오판은 영상을 반토막 내거나 회색 사각형을 남긴다.
--
-- raw_video_path 는 생성/QA 증거로 불변이다. 포장 결과는 derived_video_path 에
-- 들어가고, 이 컬럼은 그 파생물의 포맷을 선언한다. NULL = 파생 포장 없음(레거시).

alter table public.pet_motion_candidates
  add column if not exists delivery_format text;

alter table public.pet_motion_candidates
  drop constraint if exists pet_motion_candidates_delivery_format_check;
alter table public.pet_motion_candidates
  add constraint pet_motion_candidates_delivery_format_check check (
    delivery_format is null or delivery_format in ('packed_alpha')
  );

comment on column public.pet_motion_candidates.delivery_format is
  'derived_video_path 의 제품 전달 포맷. packed_alpha = vstack(상단 RGB, 하단 알파 매트) MP4. NULL = 파생 포장 없음';
