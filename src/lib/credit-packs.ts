/**
 * 테스트 크레딧 팩 정의 — **의존성이 없는 순수 데이터**.
 *
 * credit-topup.ts 에서 분리한 이유: 그쪽은 `@/app/services/...` 를 import 하는데
 * node --test 는 `@/` 별칭을 풀지 못한다(Vite 만 푼다). 값 자체를 여기 두면
 * 앱과 테스트가 같은 상수를 보고, 백엔드 카탈로그와의 일치도 테스트할 수 있다.
 *
 * product_id 와 credits 는 backend/data/iap_products.py 의 IAP_PRODUCTS 와
 * **반드시 같아야 한다** (credit-topup.test.ts 가 강제한다).
 */

export interface TestCreditPack {
  productId: string;
  credits: number;
}

export const TEST_CREDIT_PACKS: readonly TestCreditPack[] = [
  { productId: "credit_pack_test_2", credits: 2 },
  { productId: "credit_pack_test_5", credits: 5 },
];
