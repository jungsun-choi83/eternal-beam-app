-- 유료 테마 소유권 (Phase 11).
--
-- ── 왜 **별도 테이블**인가 ───────────────────────────────────────────────────
-- 구독 자격과 테마 소유권은 **완전히 다른 것**이다. 한 테이블에 섞으면 반드시
-- 한쪽이 다른 쪽을 오염시킨다:
--
--   user_subscriptions      "이번 달 회원인가"   → 만료된다, 갱신된다
--   user_theme_entitlements "이 테마를 샀는가"   → 여기 (한 번 사면 남는다)
--   premium_purchases       "크레딧을 썼는가"    → 레거시 원장
--
-- 이 분리가 요구사항을 구조로 보장한다: **구독이 끊겨도 산 테마는 남는다.**
-- 만료는 user_subscriptions.status 만 바꾸고 이 테이블을 건드리지 않는다.
-- 반대로 테마를 사도 구독 상태는 한 글자도 바뀌지 않는다.
--
-- ── 멱등성의 축은 order_id 다 ────────────────────────────────────────────────
-- 결제 1건 = order_id 1개. 같은 주문이 두 번 도착해도(네트워크 재시도, 사용자
-- 더블클릭, 웹훅 중복) 두 번 청구되지 않는다. 부분 unique 인덱스가 그것을 DB
-- 수준에서 보장한다 — 애플리케이션 락에 기대지 않는다.
--
-- ── expires_at 은 **PM 미결**이다 ────────────────────────────────────────────
-- 기본은 null(영구)이다. 목표 UX 가 "Beach OWNED [Use]" 이므로 영구가 가장
-- 자연스러운 해석이고, 기간제가 오히려 새로운 발명이다. 다만 PM 이 기간제를
-- 정할 수 있도록 컬럼은 지금 만들어 둔다 — 나중에 마이그레이션하지 않기 위해서다.
-- 값을 넣는 규칙(THEME_ENTITLEMENT_TTL_DAYS)은 설정으로 남긴다.

create table if not exists public.user_theme_entitlements (
  user_id text not null,
  -- themes.ts 의 themeKey. **id 가 아니라 key 로 잡는다** — 프론트의 숫자 id 는
  -- 현재 충돌이 있고(beach 와 custom_photo_bg 가 둘 다 9), 재번호는 저장된
  -- 선택값·기기 동기화를 깨뜨린다. key 는 안정적이고 백엔드 place_id 와도 같다.
  theme_key text not null,
  -- 'owned' | 'revoked' | 'refunded'. 행을 지우지 않는 이유는 폐기와 마찬가지로
  -- "환불됨"과 "산 적 없음"을 구분해 설명해야 하기 때문이다.
  status text not null default 'owned',
  -- 결제 출처. 지금은 'toss' 뿐이지만 provider-neutral 계약을 따른다.
  provider text,
  -- 이 소유권을 만든 주문. 멱등성의 축.
  order_id text,
  payment_key text,
  -- 실제 청구 금액·통화 (표시가 아니라 **일어난 일**의 기록).
  amount integer,
  currency text default 'KRW',
  purchased_at timestamptz not null default now(),
  -- null = 영구 (기본). PM 이 기간제를 정하면 여기에 값이 들어간다.
  expires_at timestamptz,
  primary key (user_id, theme_key)
);

-- 같은 주문이 두 번 처리되지 않는다. 애플리케이션이 아니라 **DB** 가 막는다.
create unique index if not exists user_theme_entitlements_order_idx
  on public.user_theme_entitlements (order_id)
  where order_id is not null;

-- 한 사용자의 소유 목록을 한 번에 읽는 것이 유일한 조회 패턴이다.
create index if not exists user_theme_entitlements_user_idx
  on public.user_theme_entitlements (user_id);

comment on table public.user_theme_entitlements is
  '유료 테마 소유권. 구독(user_subscriptions)과 완전히 독립 — 구독이 끊겨도 남는다';
comment on column public.user_theme_entitlements.theme_key is
  'themes.ts 의 themeKey. 숫자 id 는 충돌이 있어 쓰지 않는다';
comment on column public.user_theme_entitlements.order_id is
  '멱등성의 축. 부분 unique 인덱스로 같은 주문의 이중 처리를 DB 가 막는다';
comment on column public.user_theme_entitlements.expires_at is
  'null = 영구(기본). 기간제 여부는 PM 미결 — THEME_ENTITLEMENT_TTL_DAYS 참고';
