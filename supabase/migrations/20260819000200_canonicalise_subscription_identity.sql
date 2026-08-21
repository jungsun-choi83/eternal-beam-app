-- 구독 신원 정규화 백필 (Phase 3 의 남은 숙제).
--
-- 배경: Phase 3 이전에는 구독 웹훅이 앱/스토어가 준 user_id 를 **그대로** 저장했다.
-- 반면 프리미엄 인가는 resolve_identity 가 확정한 eb_user_id 로 조회하는데, 그 값은
-- 검증된 이메일의 **소문자**다. 대문자가 섞인 이메일로 저장된 행은 조회되지 않아
-- **결제한 사용자가 "구독 없음"으로 읽힌다.** 예외도 로그도 없이 조용히 틀린다.
--
-- Phase 3 에서 쓰기 경로는 고쳤다(subscription_webhook_parser → canonical_user_id).
-- 그 이전에 저장된 행은 여전히 어긋나 있으므로 여기서 한 번 맞춘다.
--
-- ── 안전 규칙 ────────────────────────────────────────────────────────────────
-- 이 백필은 **충돌하는 행을 건드리지 않는다.** 같은 사용자에 대해 소문자 행이
-- 이미 존재하면(양쪽에서 결제가 잡힌 경우) 소문자 쪽이 정답이므로, 대문자 행을
-- 덮어쓰거나 지우지 않고 그대로 남긴다 — 데이터를 잃는 것보다 중복을 남기고
-- 운영자가 보는 편이 낫다. 아래 SELECT 로 남은 충돌을 확인할 수 있다.
--
-- 멱등하다. 두 번 돌려도 두 번째는 0행이다.

-- ── 1) 충돌 없는 행만 소문자로 정규화 ────────────────────────────────────────
update public.user_subscriptions s
   set user_id = lower(s.user_id)
 where s.user_id like '%@%'
   and s.user_id <> lower(s.user_id)
   and not exists (
     select 1 from public.user_subscriptions t
      where t.user_id = lower(s.user_id)
   );

-- 지갑도 같은 규칙으로 키가 잡혀 있다(레거시 4코인 재원).
-- 잔액을 합치지 않는다 — 합산은 사람이 판단할 일이다.
update public.user_wallets w
   set user_id = lower(w.user_id)
 where w.user_id like '%@%'
   and w.user_id <> lower(w.user_id)
   and not exists (
     select 1 from public.user_wallets t
      where t.user_id = lower(w.user_id)
   );

-- 행동 선호도 사용자 키를 쓴다.
update public.behavior_preferences p
   set user_id = lower(p.user_id)
 where p.user_id like '%@%'
   and p.user_id <> lower(p.user_id)
   and not exists (
     select 1 from public.behavior_preferences t
      where t.user_id = lower(p.user_id)
        and t.pet_id = p.pet_id
        and t.action_id = p.action_id
   );

-- ── 2) 남은 충돌 확인용 (수동 실행) ──────────────────────────────────────────
--
-- 아래를 돌려 결과가 비어 있어야 백필이 완전하다. 행이 남으면 같은 사용자에게
-- 대/소문자 두 벌의 기록이 있다는 뜻이므로, 어느 쪽이 유효한 구독인지 확인한 뒤
-- 수동으로 정리한다.
--
--   select user_id, status, next_billing_date
--     from public.user_subscriptions
--    where user_id like '%@%' and user_id <> lower(user_id);
--
--   select user_id, current_credits from public.user_wallets
--    where user_id like '%@%' and user_id <> lower(user_id);

comment on table public.user_subscriptions is
  '구독 상태. user_id 는 정규 Eternal Beam 신원(검증 이메일은 소문자) — premium_entitlement 가 같은 키로 조회한다';
