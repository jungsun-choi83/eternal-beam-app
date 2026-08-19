-- 프리미엄 구매 원장(ledger).
--
-- 왜 새 테이블인가: 아이들 번들이 **1 크레딧에 N개 자산**이라, 과금 단위가 자산
-- 단위(generated_motions)와도 세션 단위(credit_generation_sessions)와도 다르다.
-- 기존 두 테이블 중 어느 쪽에 얹어도 레거시 4코인 경로의 환불 정책을 건드리게 되어
-- 분리한다.
--
-- ⚠️ 이것은 **재생 권한 테이블이 아니다.** 재생 접근권은 예전 그대로
-- generated_motions 의 canonical 행이 정한다 — 잔액이 0이 되어도 이미 만들어진
-- 자산은 계속 재생된다. 이 원장은 "새 생성에 과금해야 하는가"만 답한다.

create table if not exists public.premium_purchases (
  purchase_id uuid primary key,
  user_id text not null,
  pet_id text not null,
  -- 'IDLE_BUNDLE' | 'ACTION:<ACTION_ID>'
  kind text not null,
  credits_charged int not null,
  created_at timestamptz not null default now(),
  refunded_at timestamptz
);

-- 서버 권위 멱등성. 같은 (user, pet, kind) 로 환불되지 않은 구매는 **하나뿐**이다.
--
-- 이 인덱스가 이중 과금 방어의 전부다: 새로고침·다중 탭·Preview/Memorial 중복·
-- 재시도가 동시에 들어와도 insert 는 하나만 성공하고, 나머지는 unique 위반으로
-- 떨어져 "이미 구매함 → 0 크레딧" 으로 처리된다. 클라이언트 협조가 필요 없다.
--
-- refunded_at is null 조건 덕에, 환불된 구매는 인덱스에서 빠져 재구매가 가능하다.
create unique index if not exists premium_purchases_active_uniq
  on public.premium_purchases (user_id, pet_id, kind)
  where refunded_at is null;

create index if not exists premium_purchases_user_pet_idx
  on public.premium_purchases (user_id, pet_id);

comment on table public.premium_purchases is
  '프리미엄 구매 원장. 과금 멱등성 전용 — 재생 권한은 generated_motions 가 정한다';
comment on column public.premium_purchases.kind is
  'IDLE_BUNDLE(등록된 아이들 이벤트 전체, 1크레딧) 또는 ACTION:<ID>(액션 1건, 1크레딧)';
comment on column public.premium_purchases.refunded_at is
  '환불 시각. 설정되면 활성 unique 인덱스에서 빠져 재구매가 가능해진다';
