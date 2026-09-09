-- Shaker QR 산출물 보관 (Phase 13.1) — **토큰은 여전히 해시만 저장한다.**
--
-- ── 무엇을 푸는가 ───────────────────────────────────────────────────────────
-- Phase 10 은 공유 토큰을 sha256 으로만 저장한다(유출 시 피해를 없애기 위해).
-- 옳은 결정이지만 운영에 실질적 문제를 만들었다: 발급 탭을 닫으면 **같은 QR 을
-- 다시 뽑을 수 없다.** 그래서 재발급 → 새 토큰 → 이미 인쇄된 QR 무효화 →
-- 재인쇄라는, 아무도 원하지 않는 경로만 남았다.
--
-- 해결: 토큰이 아니라 **렌더된 QR 산출물**을 보관한다. 재다운로드는 이 파일을
-- 그대로 내보내므로 토큰을 복원하지 않고, 새 공유도 만들지 않으며, 이미 인쇄된
-- QR 도 그대로 유효하다.
--
-- ⚠️ 솔직하게: QR 은 **디코딩 가능하다.** 이 산출물을 읽을 수 있는 사람은
--    스캔해서 URL 을 얻을 수 있다 — 인쇄된 카드를 가진 사람과 같은 수준이다.
--    보호 수단은 암호가 아니라 **접근 제어**다(운영 allowlist 전용 경로).
--    그래도 원문 토큰 컬럼은 만들지 않는다: DB 덤프에서 문자열 검색으로 전체
--    토큰을 긁어 가는 것과, QR 이미지를 한 장씩 디코딩하는 것은 다른 난이도다.
--
-- ── 왜 스토리지가 아니라 DB 인가 ────────────────────────────────────────────
-- QR SVG 는 ~3KB, PNG 는 ~1KB 다. 스토리지에 두면 서명 URL 수명을 또 관리해야
-- 하는데 그 문제로 이미 두 번 데였다(Phase 10 재서명, Phase 11 주문). 작고
-- 불변인 산출물이므로 행에 그대로 둔다 — 새 만료 표면이 생기지 않는다.

create table if not exists public.shaker_qr_artifacts (
  -- shaker_shares.share_id 와 1:1. 비밀이 아닌 식별자다.
  share_id text primary key,
  -- 어느 공유의 것인지 확인용. **원문 토큰이 아니다.**
  token_hash text not null,
  pet_id text not null,
  -- 인쇄용 벡터. QR 의 정본이다.
  qr_svg text not null,
  -- 화면 미리보기·붙여넣기용(선택). base64.
  qr_png_base64 text,
  -- 생성 당시 QR 이 가리킨 호스트. base URL 이 바뀌었는지 운영이 알 수 있어야 한다
  -- (이미 인쇄된 QR 은 옛 호스트를 가리킨 채로 남는다).
  target_host text,
  -- CUSTOMER | OPS | LETTER | MEMORY_BOX — 인쇄물용인지 구분한다.
  purpose text,
  created_at timestamptz not null default now()
);

create index if not exists shaker_qr_artifacts_pet_idx
  on public.shaker_qr_artifacts (pet_id);

comment on table public.shaker_qr_artifacts is
  '렌더된 QR 산출물. 토큰 원문은 저장하지 않는다 — 재다운로드용이며 운영만 접근한다';
comment on column public.shaker_qr_artifacts.qr_svg is
  '인쇄용 벡터 QR. 재다운로드는 이 값을 그대로 내보내 이미 인쇄된 QR 과 동일하다';
comment on column public.shaker_qr_artifacts.target_host is
  '생성 당시 대상 호스트. base URL 변경을 운영이 알아차리기 위한 값';
