/**
 * 테스트 앱 — credit_pack_4 목업 결제 (PAYMENT_MOCK=1 백엔드 필요)
 */
import {
  verifyAndChargeIAP,
  type VerifyAndChargeResult,
} from "@/app/services/videoProcessingApi";
import { IAP_MOCK_ENABLED } from "@/lib/test-app-flags";

export function createMockReceiptToken(): string {
  return `mock_receipt_${Date.now()}_${Math.random().toString(36).slice(2, 14)}`;
}

export async function chargeCreditPackMock(
  userId: string,
  storeType: "apple" | "google" = "apple"
): Promise<VerifyAndChargeResult> {
  if (!IAP_MOCK_ENABLED) {
    throw new Error(
      "목업 결제가 꺼져 있습니다. VITE_IAP_MOCK=1 또는 스토어 IAP를 연동하세요."
    );
  }
  return verifyAndChargeIAP({
    user_id: userId,
    receipt_data: createMockReceiptToken(),
    store_type: storeType,
    product_id: "credit_pack_4",
  });
}
