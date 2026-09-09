-- 생산 패키지가 **보관된 QR 산출물**로도 준비될 수 있게 한다 (Phase 13.1).
--
-- Phase 13 은 준비 시 운영이 qr_share_url 을 넘기도록 요구했다. 토큰이 해시로만
-- 저장되니 URL 을 복원할 수 없었고, 발급 탭을 닫았으면 생산 준비가 막혔다.
--
-- 이제 산출물(shaker_qr_artifacts)이 있으면 URL 없이도 준비할 수 있다. 그때
-- qr_share_url 은 비어 있고, QR 은 보관된 SVG/PNG 를 그대로 쓴다 — **이미
-- 인쇄된 QR 과 같은 바이트**다.

alter table public.production_packages
  alter column qr_share_url drop not null;

alter table public.production_packages
  add column if not exists qr_source text;

comment on column public.production_packages.qr_share_url is
  'QR 대상 URL. 산출물로 준비된 경우 null 이다 (토큰은 복원되지 않는다)';
comment on column public.production_packages.qr_source is
  'url = 운영이 URL 을 넘김 / artifact = 보관된 QR 산출물을 재사용';
