/**
 * 크레딧 충전 (목업) — **기존 IAP 경로를 그대로 재사용한다.**
 *
 * 두 번째 지갑 시스템을 만들지 않는다. 여기서 하는 일은 이미 있는 것을 부르는 것뿐:
 *
 *   verifyAndChargeIAP  → POST /api/v1/payment/verify-and-charge
 *                       → iap_verification_service (PAYMENT_MOCK=1 이면 목업 검증)
 *                       → iap_charge_service       (중복 영수증 멱등 처리)
 *                       → wallet_service.add_credits (원자적 RPC)
 *
 * 즉 충전은 프리미엄 구매가 차감하는 **바로 그 지갑**에 들어간다.
 *
 * ⚠️ 영수증 문자열은 매번 달라야 한다. 백엔드가 receipt_data 를 해시해
 * transaction_id 를 만들고, 같은 transaction_id 는 idempotent_replay 로 처리해
 * 크레딧을 **추가하지 않는다**(정상 동작이다 — 스토어 재전송 방어). 같은 문자열을
 * 재사용하면 두 번째 클릭부터 잔액이 그대로여서 버그처럼 보인다.
 */

import { verifyAndChargeIAP } from "@/app/services/videoProcessingApi";
import { IAP_MOCK_ENABLED } from "@/lib/test-app-flags";
import { TEST_CREDIT_PACKS } from "./credit-packs.ts";

// 팩 정의는 의존성 없는 모듈에 있다 — node --test 가 `@/` 별칭을 풀지 못해서다.
export { TEST_CREDIT_PACKS, type TestCreditPack } from "./credit-packs.ts";

export interface TopUpResult {
  creditsAdded: number;
  creditsRemaining: number;
  /** 같은 영수증이 재전송돼 크레딧이 늘지 않은 경우 */
  replay: boolean;
}

function mockReceipt(productId: string): string {
  // 클릭마다 고유해야 한다 — 위 주석 참고. 8자 미만이면 목업 검증이 거절한다.
  const rand = Math.random().toString(36).slice(2, 10);
  return `mock_receipt_${productId}_${Date.now()}_${rand}`;
}

export function isTopUpAvailable(): boolean {
  return IAP_MOCK_ENABLED;
}

/**
 * 테스트 크레딧 충전. 성공하면 갱신된 잔액을 돌려준다.
 *
 * 목업이 꺼져 있으면 던진다 — 실 결제를 흉내 내지 않는다.
 */
export async function addTestCredits(
  userId: string,
  productId: string
): Promise<TopUpResult> {
  if (!IAP_MOCK_ENABLED) {
    throw new Error(
      "결제 목업이 꺼져 있습니다. VITE_IAP_MOCK=1 및 백엔드 PAYMENT_MOCK=1 이 필요합니다."
    );
  }
  const r = await verifyAndChargeIAP({
    user_id: userId,
    receipt_data: mockReceipt(productId),
    store_type: "google",
    product_id: productId,
  });
  return {
    creditsAdded: r.credits_added,
    creditsRemaining: r.credits_remaining,
    replay: Boolean(r.idempotent_replay),
  };
}
