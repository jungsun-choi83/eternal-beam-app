/**
 * Toss 웹 정기결제 — 프론트 클라이언트.
 *
 * 흐름:
 *   1) POST /billing/checkout   → customerKey / orderId / clientKey (공개 키)
 *   2) Toss SDK requestBillingAuth(customerKey) → 카드 등록창 → 리다이렉트
 *   3) successUrl 로 돌아오면 POST /billing/confirm → 첫 청구 → 자격 ACTIVE
 *
 * ⚠️ **시크릿은 여기 없다.** 서버가 내려 주는 clientKey 는 결제창을 띄우기 위한
 * 공개 키다. billingKey(결제 수단)와 시크릿 키는 백엔드를 떠나지 않는다.
 *
 * ⚠️ 신원을 보내지 않는다. 서버가 토큰에서 확정한다 — 다른 구독 경로와 같은 규칙.
 */

import { getPremiumAccessToken } from "@/lib/premium-auth-token";

// 순수 헬퍼는 의존성 없는 모듈에 있다 — node --test 가 `@/` 별칭을 풀지 못해서다.
export { readBillingRedirectParams } from "./app-entry.ts";

/** Toss 결제 SDK. 번들에 넣지 않고 결제 시작 시점에만 불러온다. */
const TOSS_SDK_URL = "https://js.tosspayments.com/v1/payment";

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

export class BillingError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {
    super(message);
    this.name = "BillingError";
    this.code = code;
  }
}

async function authHeader(): Promise<Record<string, string>> {
  const auth = await getPremiumAccessToken();
  if (!auth.token) throw new BillingError("UNAUTHENTICATED", "로그인이 필요합니다.");
  return { Authorization: `Bearer ${auth.token}` };
}

async function readError(res: Response): Promise<BillingError> {
  try {
    const b = (await res.json()) as { detail?: { code?: string; message?: string } };
    return new BillingError(
      b?.detail?.code || `HTTP_${res.status}`,
      b?.detail?.message || `결제 요청이 실패했습니다 (${res.status}).`
    );
  } catch {
    return new BillingError(`HTTP_${res.status}`, `결제 요청이 실패했습니다 (${res.status}).`);
  }
}

export interface BillingConfig {
  provider: string;
  configured: boolean;
  clientKey: string;
  testMode: boolean;
}

export async function fetchBillingConfig(): Promise<BillingConfig> {
  const res = await fetch(`${apiBase()}/api/v1/billing/config`, { cache: "no-store" });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  return {
    provider: String(b.provider ?? "toss"),
    configured: Boolean(b.configured),
    clientKey: String(b.client_key ?? ""),
    testMode: Boolean(b.test_mode),
  };
}

export interface BillingStatus {
  provider: string;
  configured: boolean;
  billing: {
    status: string;
    cancel_at_period_end: boolean;
    current_period_end: string | null;
    has_payment_method: boolean;
    plan_id: string;
  } | null;
}

export async function fetchBillingStatus(): Promise<BillingStatus> {
  const res = await fetch(`${apiBase()}/api/v1/billing/status`, {
    cache: "no-store",
    headers: await authHeader(),
  });
  if (!res.ok) throw await readError(res);
  return (await res.json()) as BillingStatus;
}

interface CheckoutSession {
  clientKey: string;
  customerKey: string;
  orderId: string;
  orderName: string;
  amount: number;
  planId: string;
  successPath: string;
  failPath: string;
}

async function startCheckout(): Promise<CheckoutSession> {
  const res = await fetch(`${apiBase()}/api/v1/billing/checkout`, {
    method: "POST",
    headers: await authHeader(),
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  return {
    clientKey: String(b.client_key ?? ""),
    customerKey: String(b.customer_key ?? ""),
    orderId: String(b.order_id ?? ""),
    orderName: String(b.order_name ?? ""),
    amount: Number(b.amount ?? 0),
    planId: String(b.plan_id ?? ""),
    successPath: String(b.success_path ?? "/billing/success"),
    failPath: String(b.fail_path ?? "/billing/fail"),
  };
}

/** SDK 를 한 번만 로드한다. 실패하면 명확히 말한다 — 조용히 아무 일도 안 하면 고장과 구분되지 않는다. */
let sdkPromise: Promise<unknown> | null = null;
function loadTossSdk(): Promise<unknown> {
  const w = window as unknown as Record<string, unknown>;
  if (w.TossPayments) return Promise.resolve(w.TossPayments);
  if (sdkPromise) return sdkPromise;

  sdkPromise = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = TOSS_SDK_URL;
    s.async = true;
    s.onload = () => {
      const sdk = (window as unknown as Record<string, unknown>).TossPayments;
      if (sdk) resolve(sdk);
      else reject(new BillingError("SDK_LOAD_FAILED", "결제 모듈을 불러오지 못했습니다."));
    };
    s.onerror = () =>
      reject(new BillingError("SDK_LOAD_FAILED", "결제 모듈을 불러오지 못했습니다."));
    document.head.appendChild(s);
  });
  return sdkPromise;
}

/**
 * Start Membership — 결제창을 연다.
 *
 * 성공하면 **이 함수는 돌아오지 않는다** (Toss 가 페이지를 리다이렉트한다).
 * 돌아오는 경우는 사용자가 창을 닫았거나 오류가 난 경우뿐이다.
 */
export async function startMembershipCheckout(): Promise<void> {
  const session = await startCheckout();
  if (!session.clientKey) {
    throw new BillingError("NOT_CONFIGURED", "결제가 아직 설정되지 않았습니다.");
  }

  const sdk = (await loadTossSdk()) as (key: string) => {
    requestBillingAuth: (method: string, opts: Record<string, unknown>) => Promise<void>;
  };
  const toss = sdk(session.clientKey);

  // orderId 를 성공 URL 에 실어 보낸다 — 돌아왔을 때 어떤 주문인지 알아야
  // confirm 을 멱등하게 부를 수 있다.
  const origin = window.location.origin;
  const qs = `?orderId=${encodeURIComponent(session.orderId)}&planId=${encodeURIComponent(session.planId)}`;

  await toss.requestBillingAuth("카드", {
    customerKey: session.customerKey,
    successUrl: `${origin}${session.successPath}${qs}`,
    failUrl: `${origin}${session.failPath}`,
  });
}

export interface ConfirmOutcome {
  alreadyProcessed: boolean;
  entitled: boolean;
  subscriptionStatus: string | null;
}

/**
 * 리다이렉트 복귀 처리 — 첫 청구를 확정한다.
 *
 * 서버가 order_id 로 멱등 처리하므로 새로고침해도 두 번 결제되지 않는다.
 */
export async function confirmMembership(params: {
  authKey: string;
  customerKey: string;
  orderId: string;
  planId?: string;
}): Promise<ConfirmOutcome> {
  const res = await fetch(`${apiBase()}/api/v1/billing/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeader()) },
    body: JSON.stringify({
      auth_key: params.authKey,
      customer_key: params.customerKey,
      order_id: params.orderId,
      ...(params.planId ? { plan_id: params.planId } : {}),
    }),
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  return {
    alreadyProcessed: Boolean(b.already_processed),
    entitled: Boolean(b.entitled),
    subscriptionStatus: b.subscription_status == null ? null : String(b.subscription_status),
  };
}

export async function cancelMembership(): Promise<void> {
  const res = await fetch(`${apiBase()}/api/v1/billing/cancel`, {
    method: "POST",
    headers: await authHeader(),
  });
  if (!res.ok) throw await readError(res);
}

export async function resumeMembership(): Promise<void> {
  const res = await fetch(`${apiBase()}/api/v1/billing/resume`, {
    method: "POST",
    headers: await authHeader(),
  });
  if (!res.ok) throw await readError(res);
}

