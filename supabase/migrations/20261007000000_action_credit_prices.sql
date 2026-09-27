-- 액션 크레딧 가격 (Phase 8).
--
--     Gentle Look       2
--     Paw Wave          4
--     Special Greeting  8
--
-- ── 상품마다 다른 가격, 같은 경로 ───────────────────────────────────────────
-- 액션용 지갑도 액션용 원장도 액션용 멱등 모델도 만들지 않는다. 액션이 아이들·
-- 테마와 갈라지는 것은 **이 표의 행 하나**뿐이다:
--
--     theme:aurora        THEME   5   → theme_purchase      → user_theme_entitlements
--     idle:BLINKING       IDLE    1   → idle_generation     → owned_generated_assets
--     action:COME_CLOSER  ACTION  1   → action_generation   → owned_generated_assets
--
-- 셋 다 같은 지갑에서 나가고 같은 원장에 남으며 같은 멱등 축(idempotency_key)을
-- 쓴다. 코드에서 갈라지는 지점은 generation_credits.reason_for() 한 줄이다.
--
-- ── 지시받은 예시 이름은 아직 레지스트리에 없다 ─────────────────────────────
-- Gentle Look / Paw Wave / Special Greeting 은 **아직 구현된 액션이 아니다.**
-- backend/scenarios/pet_scenarios.PET_ACTIONS 에 있는 것은 COME_CLOSER 하나뿐이고,
-- 프롬프트·검증·저장 규칙이 그 레지스트리를 따라 움직인다.
--
-- 그래서 여기서는 **존재하는 액션에만** 가격을 매긴다. 없는 액션에 가격을 넣으면
-- 카탈로그에는 보이는데 생성은 되지 않는 상품이 생기고, 그건 "살 수 있는데 받을
-- 수 없는 것"을 파는 것이다.
--
-- 새 액션을 추가하는 것은 두 단계이고 순서가 있다:
--     1) pet_scenarios 에 액션 등록 (프롬프트·검증 포함)
--     2) 여기에 가격 행 추가 — docs/PRICING.md 의 UPDATE 한 줄
--
-- 1 없이 2 만 하면 팔 수 없는 것을 파는 것이고, 2 없이 1 만 하면 만들 수 없다
-- (product_credit_price 가 null → PRODUCT_NOT_SOLD).

insert into public.digital_products (product_key, product_type, credit_price, display_name)
values
  -- 현재 유효 가격을 그대로 유지한다 — 이 마이그레이션은 구조를 정리하지
  -- 지금 팔리는 값을 바꾸지 않는다. 실제 가격표는 PM 이 UPDATE 로 정한다.
  ('action:COME_CLOSER', 'ACTION', 1, 'Come Closer')
on conflict (product_key) do update
   set product_type = excluded.product_type,
       display_name = excluded.display_name,
       updated_at = now();
-- ⚠️ credit_price 를 **덮어쓰지 않는다.** 이미 운영에서 값을 바꿨다면 이 마이그레이션이
--    되돌리면 안 된다. 가격은 언제나 마이그레이션이 아니라 UPDATE 로 바뀐다.
