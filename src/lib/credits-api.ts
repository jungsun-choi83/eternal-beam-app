/**
 * Beam Credit 지갑·팩 클라이언트 (Phase 5).
 *
 * 서버 계약: backend/routers/credits_v1.py
 *
 *     packs → checkout → Toss 결제창 → confirm → 지갑 + 원장
 *
 * ⚠️ **이 파일에 가격이 없다.** 팩 구성과 금액은 서버(credit_packs)가 정하고
 * 화면은 받은 목록을 그대로 그린다. 가격이 브라우저 번들에 있으면 바꾸는 데
 * 배포가 필요하고, 서버와 어긋나면 눌러도 거절당하는 버튼이 생긴다 —
 * themes.ts 의 "$2.99" 가 정확히 그 문제였다.
 */

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

export type CreditsErrorCode =
  | "UNAUTHENTICATED"
  | "CREDIT_PACKS_UNAVAILABLE"
  | "CREDIT_PACK_UNKNOWN"
  | "CREDIT_ORDER_NOT_FOUND"
  | "CREDIT_ORDER_NOT_PENDING"
  | "CREDIT_AMOUNT_MISMATCH"
  | "CREDIT_PAYMENT_FAILED"
  | "CREDIT_CONFIRM_UNAVAILABLE"
  | "WALLET_UNAVAILABLE"
  | "UNKNOWN";

export class CreditsError extends Error {
  readonly code: CreditsErrorCode;
  readonly status: number;

  constructor(code: CreditsErrorCode, message: string, status: number) {
    super(message);
    this.name = "CreditsError";
    this.code = code;
    this.status = status;
  }
}

async function readError(res: Response): Promise<CreditsError> {
  let code: CreditsErrorCode = "UNKNOWN";
  let message = `HTTP ${res.status}`;
  try {
    const b = (await res.json()) as { detail?: { code?: string; message?: string } };
    if (b?.detail?.code) code = b.detail.code as CreditsErrorCode;
    if (b?.detail?.message) message = b.detail.message;
  } catch {
    /* 상태 코드로 충분하다 */
  }
  if (res.status === 401) code = "UNAUTHENTICATED";
  return new CreditsError(code, message, res.status);
}

export interface CreditPack {
  packKey: string;
  credits: number;
  priceKrw: number;
  displayName: string | null;
}

/** 서버 응답 한 줄 → 프론트 모델. 순수 함수라 테스트가 그대로 부른다. */
export function parseCreditPack(row: Record<string, unknown>): CreditPack {
  return {
    packKey: String(row.pack_key ?? "").trim(),
    credits: Number(row.credits ?? 0),
    priceKrw: Number(row.price_krw ?? 0),
    displayName: row.display_name == null ? null : String(row.display_name),
  };
}

/** 판매 중인 팩. **가격은 서버가 정한다.** */
export async function fetchCreditPacks(params: {
  accessToken: string;
  signal?: AbortSignal;
}): Promise<CreditPack[]> {
  const res = await fetch(`${apiBase()}/api/v1/credits/packs`, {
    method: "GET",
    cache: "no-store",
    headers: { Authorization: `Bearer ${params.accessToken}` },
    signal: params.signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  const rows = Array.isArray(b.packs) ? b.packs : [];
  return rows
    .map((r) => parseCreditPack(r as Record<string, unknown>))
    .filter((p) => p.packKey && p.credits > 0);
}

export interface CreditsWallet {
  balance: number;
  entries: {
    delta: number;
    balanceAfter: number;
    reason: string;
    productKey: string | null;
    createdAt: string | null;
  }[];
}

export async function fetchWallet(params: {
  accessToken: string;
  signal?: AbortSignal;
}): Promise<CreditsWallet> {
  const res = await fetch(`${apiBase()}/api/v1/credits/wallet`, {
    method: "GET",
    cache: "no-store",
    headers: { Authorization: `Bearer ${params.accessToken}` },
    signal: params.signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  const rows = Array.isArray(b.entries) ? b.entries : [];
  return {
    balance: Number(b.balance ?? 0),
    entries: rows.map((r) => {
      const e = r as Record<string, unknown>;
      return {
        delta: Number(e.delta ?? 0),
        balanceAfter: Number(e.balance_after ?? 0),
        reason: String(e.reason ?? ""),
        productKey: e.product_key == null ? null : String(e.product_key),
        createdAt: e.created_at == null ? null : String(e.created_at),
      };
    }),
  };
}

export interface CreditCheckout {
  orderId: string;
  packKey: string;
  amount: number;
  credits: number;
  orderName: string;
  currency: string;
  /** Toss 결제창용 **공개** 키. 시크릿은 백엔드를 떠나지 않는다. */
  clientKey: string;
}

/** 결제창에 필요한 값 발급. **아직 아무 돈도 움직이지 않는다.** */
export async function startCreditCheckout(params: {
  packKey: string;
  accessToken: string;
  signal?: AbortSignal;
}): Promise<CreditCheckout> {
  const res = await fetch(`${apiBase()}/api/v1/credits/checkout`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${params.accessToken}`,
    },
    body: JSON.stringify({ pack_key: params.packKey }),
    signal: params.signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  return {
    orderId: String(b.order_id ?? ""),
    packKey: String(b.pack_key ?? params.packKey),
    amount: Number(b.amount ?? 0),
    credits: Number(b.credits ?? 0),
    orderName: String(b.order_name ?? ""),
    currency: String(b.currency ?? "KRW"),
    clientKey: String(b.client_key ?? ""),
  };
}

export interface CreditConfirmOutcome {
  orderId: string;
  packKey: string;
  /** **이번 호출이 실제로 지급한 크레딧.** 재확인이면 0. */
  creditsAdded: number;
  creditsRemaining: number;
  amount: number;
  alreadyConfirmed: boolean;
}

/**
 * 결제창 승인 후 서버 확인. **여기서 크레딧이 들어온다.**
 *
 * 같은 주문으로 다시 불러도(새로고침) creditsAdded=0 으로 돌아온다.
 */
export async function confirmCreditPayment(params: {
  paymentKey: string;
  orderId: string;
  amount?: number | null;
  accessToken: string;
  signal?: AbortSignal;
}): Promise<CreditConfirmOutcome> {
  const res = await fetch(`${apiBase()}/api/v1/credits/confirm`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${params.accessToken}`,
    },
    body: JSON.stringify({
      payment_key: params.paymentKey,
      order_id: params.orderId,
      amount: params.amount ?? undefined,
    }),
    signal: params.signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  return {
    orderId: String(b.order_id ?? params.orderId),
    packKey: String(b.pack_key ?? ""),
    creditsAdded: Number(b.credits_added ?? 0),
    creditsRemaining: Number(b.credits_remaining ?? 0),
    amount: Number(b.amount ?? 0),
    alreadyConfirmed: Boolean(b.already_confirmed),
  };
}

// ── Toss 결제창 ──────────────────────────────────────────────────────────────

const TOSS_SDK_URL = "https://js.tosspayments.com/v1/payment";

async function loadTossSdk(clientKey: string): Promise<{
  requestPayment: (method: string, opts: Record<string, unknown>) => Promise<void>;
}> {
  const w = window as unknown as { TossPayments?: (key: string) => unknown };
  if (!w.TossPayments) {
    await new Promise<void>((resolve, reject) => {
      const el = document.createElement("script");
      el.src = TOSS_SDK_URL;
      el.onload = () => resolve();
      el.onerror = () =>
        reject(new CreditsError("UNKNOWN", "결제 모듈을 불러오지 못했습니다.", 0));
      document.head.appendChild(el);
    });
  }
  if (!w.TossPayments) {
    throw new CreditsError("UNKNOWN", "결제 모듈을 불러오지 못했습니다.", 0);
  }
  return w.TossPayments(clientKey) as {
    requestPayment: (method: string, opts: Record<string, unknown>) => Promise<void>;
  };
}

/**
 * 결제 복귀 경로.
 *
 * 테마(/themes/*)·구독(/billing/*)·실물(/orders/*)과 **경로를 나눈다.** 네 흐름은
 * 확인 엔드포인트도 결과 화면도 다르다. 경로를 공유하면 크레딧 결제가 테마
 * confirm 을 타게 되고, 그건 잘못된 주문을 승인하려는 시도가 된다.
 */
export function creditsReturnUrls(origin?: string): {
  successUrl: string;
  failUrl: string;
} {
  const base = (
    origin || (typeof window !== "undefined" ? window.location.origin : "")
  ).replace(/\/$/, "");
  return { successUrl: `${base}/credits/success`, failUrl: `${base}/credits/fail` };
}

/** 결제창 열기 — **페이지가 이동한다.** 이 함수 뒤의 코드는 실행되지 않는다. */
export async function openCreditPaymentWindow(checkout: CreditCheckout): Promise<void> {
  if (!checkout.clientKey) {
    throw new CreditsError("UNKNOWN", "결제 설정이 준비되지 않았습니다.", 0);
  }
  const toss = await loadTossSdk(checkout.clientKey);
  const { successUrl, failUrl } = creditsReturnUrls();
  await toss.requestPayment("카드", {
    amount: checkout.amount,
    orderId: checkout.orderId,
    orderName: checkout.orderName,
    successUrl,
    failUrl,
  });
}
