-- 클린 플레이트 (Clean Plate) — 하류 생성 입력을 raw 에서 분리한다.
--
-- ── 무엇이 어긋나 있었나 ────────────────────────────────────────────────────
-- Phase 4 정본과 Phase 5 키프레임은 후보의 **raw**(프로바이더 원본)를 그대로
-- 하류 생성에 먹였다. raw 에는 이미지 모델이 그려 넣은 접지(contact)/투영(cast)
-- 그림자와 벽→바닥 그라디언트가 남아 있고, 그 픽셀이 키프레임 → 모션 →
-- packed-alpha 매트까지 전파돼 발밑 얼룩/바닥 슬래브로 나타났다.
-- 누끼(cutout)는 이미 있었지만 QA 보조 자산이었을 뿐 생성 입력이 아니었다.
--
-- ── 이 마이그레이션이 하는 일 ───────────────────────────────────────────────
-- 후보 테이블에 plate_bucket / plate_object_path 를 더한다. 플레이트는
-- "누끼 전경 + 고정 중립 배경"으로 합성된 **불투명 RGB PNG** 다
-- (backend/services/clean_plate_service.py, CLEAN_PLATE_VERSION).
--
-- ── 원칙 ────────────────────────────────────────────────────────────────────
-- * raw 는 그대로다. 파괴하지 않고, 덮지 않고, 계보/QA 증거로 남는다.
-- * 플레이트는 파생물이다 — 기존 행은 NULL 이고, 소비 시점에 누끼에서
--   지연 백필된다 (프로바이더 재호출 없음).
-- * 컬럼 추가뿐이다. 되돌릴 때도 데이터 손실이 없다.

alter table public.pet_canonical_candidates
  add column if not exists plate_bucket text;
alter table public.pet_canonical_candidates
  add column if not exists plate_object_path text;

alter table public.pet_action_keyframe_candidates
  add column if not exists plate_bucket text;
alter table public.pet_action_keyframe_candidates
  add column if not exists plate_object_path text;

comment on column public.pet_canonical_candidates.plate_object_path is
  '클린 플레이트(누끼+고정 중립 배경) — 키프레임 생성이 실제로 먹는 입력. raw 는 증거로만 남는다';
comment on column public.pet_action_keyframe_candidates.plate_object_path is
  '클린 플레이트(누끼+고정 중립 배경) — 모션 영상 생성이 실제로 먹는 입력. raw 는 증거로만 남는다';
