/**
 * 테스트 앱 — Standard 구독 웹훅 목업 (SUBSCRIPTION_MOCK=1 백엔드)
 *
 * **신원은 토큰이 정한다.** 예전에는 localStorage 의 user_id 를 바디에 실어
 * 보냈는데, 그 값이 프리미엄 인가가 조회하는 신원과 어긋나면 구독을 켜도
 * "구독 없음" 으로 읽혔다. 이제 두 경로가 같은 토큰 신원을 쓴다.
 */
import {
  postSubscriptionWebhook,
  getSubscriptionStatus,
  type SubscriptionStatusResult,
  type SubscriptionWebhookResult,
} from "@/app/services/videoProcessingApi";
import { getPremiumAccessToken } from "@/lib/premium-auth-token";
import { SUBSCRIPTION_MOCK_ENABLED } from "@/lib/test-app-flags";

export type SubscriptionMockEvent =
  | "INITIAL_BUY"
  | "RENEWAL"
  | "EXPIRATION"
  | "CANCEL";

/** 인증이 없으면 구독 경로를 아예 시도하지 않는다 — 서버가 401 로 거절한다. */
export class SubscriptionAuthRequiredError extends Error {
  constructor() {
    super("로그인이 필요합니다. 구독 상태는 로그인한 계정에 묶입니다.");
    this.name = "SubscriptionAuthRequiredError";
  }
}

async function requireToken(): Promise<string> {
  const auth = await getPremiumAccessToken();
  if (!auth.token) throw new SubscriptionAuthRequiredError();
  return auth.token;
}

function mockTransactionId(event: SubscriptionMockEvent): string {
  return `mock_sub_${event.toLowerCase()}_${Date.now()}`;
}

export async function sendSubscriptionMockWebhook(
  event: SubscriptionMockEvent
): Promise<SubscriptionWebhookResult> {
  if (!SUBSCRIPTION_MOCK_ENABLED) {
    throw new Error(
      "구독 목업이 꺼져 있습니다. VITE_SUBSCRIPTION_MOCK=1 및 Render SUBSCRIPTION_MOCK=1"
    );
  }
  const token = await requireToken();
  // user_id 를 보내지 않는다 — 서버가 토큰에서 확정한다.
  return postSubscriptionWebhook(
    {
      notification_type: event,
      plan_id: "standard_subscription",
      transaction_id: mockTransactionId(event),
    },
    token
  );
}

export async function fetchSubscriptionStatus(): Promise<SubscriptionStatusResult> {
  const token = await requireToken();
  return getSubscriptionStatus(token);
}
