-- Shaker 공유: 서명 URL 대신 **스토리지 객체 경로**를 정본으로 둔다.
--
-- 문제: breathing_url / poster_url 에 저장되는 값은 업로드 시점에 만든 7일짜리
-- 서명 URL 이다(services/supabase_assets.py). 그런데 QR 은 **종이에 인쇄된다.**
-- 8일째에 QR 을 찍은 사람은 유효한 토큰을 들고 있는데도 영상이 재생되지 않는다 —
-- 링크도 살아 있고 자산도 살아 있는데 그 사이의 서명만 죽은 상태다.
--
-- 해결: 객체 경로를 따로 저장하고, **해석할 때마다** 새 서명을 만든다.
-- 경로는 만료되지 않으므로 공유 링크의 수명이 서명 수명과 분리된다.
--
--     저장: bucket + object path   (만료 없음)
--     응답: 매번 새로 만든 서명 URL (짧은 수명)
--
-- URL 컬럼을 지우지 않는 이유: 외부 CDN 이나 공개 버킷 URL 처럼 재서명 대상이
-- 아닌 값도 들어올 수 있고, 그때는 저장된 URL 이 유일한 정본이다. 경로가 있으면
-- 경로를 쓰고, 없으면 URL 을 그대로 쓴다.

alter table public.shaker_shares
  add column if not exists breathing_bucket text,
  add column if not exists breathing_object_path text,
  add column if not exists poster_bucket text,
  add column if not exists poster_object_path text;

comment on column public.shaker_shares.breathing_object_path is
  'BREATHING 의 스토리지 객체 경로. 만료되지 않는 정본 — 해석 시 새 서명을 만든다';
comment on column public.shaker_shares.breathing_bucket is
  '객체가 있는 버킷. 버킷명이 바뀌어도 예전 행이 계속 해석되도록 함께 저장한다';
comment on column public.shaker_shares.poster_object_path is
  '포스터의 스토리지 객체 경로. 없으면 poster_url 을 그대로 쓴다';
