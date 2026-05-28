/**
 * 테스트 앱 — Standard 구독 웹훅 목업 (SUBSCRIPTION_MOCK=1 백엔드)
 */
import {
  postSubscriptionWebhook,
  getSubscriptionStatus,
  type SubscriptionStatusResult,
  type SubscriptionWebhookResult,
} from "@/app/services/videoProcessingApi";
import { SUBSCRIPTION_MOCK_ENABLED } from "@/lib/test-app-flags";

export type SubscriptionMockEvent =
  | "INITIAL_BUY"
  | "RENEWAL"
  | "EXPIRATION"
  | "CANCEL";

function mockTransactionId(event: SubscriptionMockEvent): string {
  return `mock_sub_${event.toLowerCase()}_${Date.now()}`;
}

export async function sendSubscriptionMockWebhook(
  userId: string,
  event: SubscriptionMockEvent
): Promise<SubscriptionWebhookResult> {
  if (!SUBSCRIPTION_MOCK_ENABLED) {
    throw new Error(
      "구독 목업이 꺼져 있습니다. VITE_SUBSCRIPTION_MOCK=1 및 Render SUBSCRIPTION_MOCK=1"
    );
  }
  return postSubscriptionWebhook({
    store_type: "mock",
    notification_type: event,
    user_id: userId,
    plan_id: "standard_subscription",
    transaction_id: mockTransactionId(event),
  });
}

export async function fetchSubscriptionStatus(
  userId: string
): Promise<SubscriptionStatusResult> {
  return getSubscriptionStatus(userId);
}
