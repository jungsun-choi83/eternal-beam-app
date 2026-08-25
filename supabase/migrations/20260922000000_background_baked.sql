-- Phase 27 — 배경이 구워진 자산인가, **영속적으로**.
--
--   대상 프로젝트: kdlukiujgclczwqmwvmk  (Eternal Beam)
--
-- ── 무엇이 없었는가 ─────────────────────────────────────────────────────────
-- `background_baked` 는 생성 응답에는 있었지만 **어느 테이블에도 없었다.**
-- 값은 브라우저 sessionStorage 한 곳에서만 살았고, 그래서 QR 재생(Shaker)은
-- 그 사실을 알 방법이 아예 없었다 — shaker-api.ts 가 `body.background_baked`
-- 를 읽고 있었는데 서버는 그 필드를 보낸 적이 없다.
--
-- 결과: 배경이 이미 들어 있는 영상을 QR 로 열면 블랙키 제거가 걸려, 장면의
-- 어두운 픽셀(그림자·나무 그늘)이 뚫린 채 재생됐다.
--
-- ── 왜 두 테이블인가 ────────────────────────────────────────────────────────
--   pets           펫에 대한 정본. 등록 시점에 **서버가** 판정해 적는다.
--   shaker_shares  인쇄된 QR 이 가리키는 자기완결 레코드. 이 표는 이미
--                  breathing_url·bucket·object_path 를 복제하고 있다 —
--                  같은 이유(발급 시점에 스스로 완결되어야 한다)로 이 값도
--                  복제한다. 공유는 종이에 인쇄되어 펫 행보다 오래 살 수 있다.
--
-- ── 백필하지 않는다 ─────────────────────────────────────────────────────────
-- default false = 레거시 = **오늘과 똑같은 동작**이다. 기존 자산이 정말로
-- 구워졌는지 알 방법이 없고, 추측해서 true 로 적으면 멀쩡히 재생되던 레거시
-- 영상이 검은 사각형인 채로 나간다. 모르면 레거시다 — 그쪽이 안전한 기본값이다.

alter table public.pets
  add column if not exists background_baked boolean not null default false;

comment on column public.pets.background_baked is
  '이 펫의 BREATHING 영상이 배경을 이미 담고 있는가. 재생 시 블랙키 제거·테마 배경을 하지 않는다. 추측으로 true 를 적지 말 것 — 서버가 자기 생성 기록으로 확인한 경우에만 true.';

alter table public.shaker_shares
  add column if not exists background_baked boolean not null default false;

comment on column public.shaker_shares.background_baked is
  '발급 시점 pets.background_baked 사본. 인쇄된 QR 이 자기완결이어야 하므로 breathing_url 과 같은 이유로 복제한다.';


-- ── 등록 시점 판정이 쓰는 조회 경로 ─────────────────────────────────────────
-- pet_registry.register 는 "이 콘텐츠에 대해 **우리가** 구운 영상을 만든 적이
-- 있는가"를 scene_generation_jobs 에서 확인한다. 브라우저 말을 믿지 않는다.
-- 그 조회가 전체 스캔이 되지 않도록 좁은 부분 인덱스를 둔다.
create index if not exists scene_generation_jobs_content_completed_idx
  on public.scene_generation_jobs (user_id, content_id)
  where status = 'completed';
