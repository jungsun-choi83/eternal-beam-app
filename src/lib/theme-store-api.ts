/**
 * 유료 테마 스토어 클라이언트 (Phase 11).
 *
 * 서버 계약: backend/routers/theme_store_v1.py
 *
 * ⚠️ 이 모듈에는 **구독이 없다.** 테마 소유권은 다른 축이고, 응답에도 구독
 * 상태가 실리지 않는다. 크레딧도 없다 — 테마는 일회성 결제다.
 *
 * 경로가 둘이다. 저장된 카드는 **선택**이다:
 *   1) checkout → 결제창 → confirm   구독한 적 없는 사용자도 쓸 수 있다 (기본)
 *   2) purchase                       카드가 이미 있으면 즉시 (단축키)
 */

import type { ThemeOffer } from "./theme-ownership.ts";

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

export type ThemeStoreErrorCode =
  | "UNAUTHENTICATED"
  /** 유료 테마인데 가격이 아직 설정되지 않았다 (PM 미정). */
  | "THEME_PRICE_NOT_SET"
  /** 저장된 카드가 없다. **오류가 아니라 안내** — 결제창 경로로 가라는 신호다. */
  | "PAYMENT_METHOD_UNAVAILABLE"
  | "THEME_ALREADY_OWNED"
  | "THEME_ORDER_NOT_FOUND"
  | "THEME_ORDER_NOT_PENDING"
  | "THEME_AMOUNT_MISMATCH"
  | "THEME_IS_FREE"
  | "THEME_UNKNOWN"
  | "THEME_PAYMENT_FAILED"
  | "THEME_ENTITLEMENTS_UNAVAILABLE"
  | "UNKNOWN";

export class ThemeStoreError extends Error {
  // 파라미터 프로퍼티를 쓰지 않는다 — node --test 타입 스트립이 지원하지 않는다.
  readonly code: ThemeStoreErrorCode;
  readonly status: number;

  constructor(code: ThemeStoreErrorCode, message: string, status: number) {
    super(message);
    this.name = "ThemeStoreError";
    this.code = code;
    this.status = status;
  }
}

async function readError(res: Response): Promise<ThemeStoreError> {
  let code: ThemeStoreErrorCode = "UNKNOWN";
  let message = `HTTP ${res.status}`;
  try {
    const b = (await res.json()) as { detail?: { code?: string; message?: string } };
    if (b?.detail?.code) code = b.detail.code as ThemeStoreErrorCode;
    if (b?.detail?.message) message = b.detail.message;
  } catch {
    /* 상태 코드로 충분하다 */
  }
  if (res.status === 401) code = "UNAUTHENTICATED";
  return new ThemeStoreError(code, message, res.status);
}

/** 서버 응답 한 줄 → 프론트 모델. 순수 함수라 테스트가 그대로 부른다. */
export function parseThemeOffer(row: Record<string, unknown>): ThemeOffer {
  return {
    themeKey: String(row.theme_key ?? "").trim(),
    free: Boolean(row.free),
    owned: Boolean(row.owned),
    priceKrw: row.price_krw == null ? null : Number(row.price_krw),
    purchasable: Boolean(row.purchasable),
  };
}

/** 카탈로그 + 내 보유 상태. **읽기 전용 — 결제도 생성도 없다.** */
export async function fetchThemeCatalog(params: {
  accessToken: string;
  signal?: AbortSignal;
}): Promise<ThemeOffer[]> {
  const res = await fetch(`${apiBase()}/api/v1/themes/catalog`, {
    method: "GET",
    cache: "no-store",
    headers: { Authorization: `Bearer ${params.accessToken}` },
    signal: params.signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  const rows = Array.isArray(b.themes) ? b.themes : [];
  return rows
    .map((r) => parseThemeOffer(r as Record<string, unknown>))
    .filter((o) => o.themeKey);
}

export interface ThemePurchaseOutcome {
  themeKey: string;
  /** **이번 호출이 실제로 청구한 금액.** 멱등 호출이면 0. */
  charged: number;
  alreadyOwned: boolean;
  orderId: string | null;
}

/**
 * 유료 테마 구매. **사용자 조작에서만 부른다** — effect / 마운트 / 폴링에서
 * 부르면 화면을 열었다는 이유로 결제가 일어난다.
 *
 * 멱등성은 서버가 쥔다(order_id + 소유권 PK). 두 번 눌러도 charged=0 이다.
 */
export async function purchaseTheme(params: {
  themeKey: string;
  accessToken: string;
  signal?: AbortSignal;
}): Promise<ThemePurchaseOutcome> {
  const res = await fetch(`${apiBase()}/api/v1/themes/purchase`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${params.accessToken}`,
    },
    body: JSON.stringify({ theme_key: params.themeKey }),
    signal: params.signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  return {
    themeKey: String(b.theme_key ?? params.themeKey),
    charged: Number(b.charged ?? 0),
    alreadyOwned: Boolean(b.already_owned),
    orderId: b.order_id == null ? null : String(b.order_id),
  };
}

// ── 일회성 결제 (구독·카드 등록 없이) ────────────────────────────────────────
//
//   1) POST /themes/checkout  → orderId / amount / clientKey (공개 키)
//   2) Toss SDK requestPayment → 결제창 → successUrl 로 리다이렉트
//   3) POST /themes/confirm    → 서버 검증 → 소유권
//
// ⚠️ 금액은 **서버가 확정한 주문 금액**이 기준이다. 아래 값들은 결제창을 띄우고
//    돌아온 뒤 대조하는 용도이며, 승인 기준이 아니다.

export interface ThemeCheckout {
  orderId: string;
  themeKey: string;
  amount: number;
  orderName: string;
  currency: string;
  /** Toss 결제창용 **공개** 키. 시크릿은 백엔드를 떠나지 않는다. */
  clientKey: string;
}

/** 결제창에 필요한 값 발급. **아직 아무 돈도 움직이지 않는다.** */
export async function startThemeCheckout(params: {
  themeKey: string;
  accessToken: string;
  signal?: AbortSignal;
}): Promise<ThemeCheckout> {
  const res = await fetch(`${apiBase()}/api/v1/themes/checkout`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${params.accessToken}`,
    },
    body: JSON.stringify({ theme_key: params.themeKey }),
    signal: params.signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  return {
    orderId: String(b.order_id ?? ""),
    themeKey: String(b.theme_key ?? params.themeKey),
    amount: Number(b.amount ?? 0),
    orderName: String(b.order_name ?? ""),
    currency: String(b.currency ?? "KRW"),
    clientKey: String(b.client_key ?? ""),
  };
}

/**
 * 결제창 승인 후 서버 확인. **여기서 소유권이 생긴다.**
 *
 * 같은 주문으로 다시 불러도(새로고침) charged=0 으로 돌아온다.
 */
export async function confirmThemePayment(params: {
  paymentKey: string;
  orderId: string;
  amount?: number | null;
  accessToken: string;
  signal?: AbortSignal;
}): Promise<ThemePurchaseOutcome> {
  const res = await fetch(`${apiBase()}/api/v1/themes/confirm`, {
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
    themeKey: String(b.theme_key ?? ""),
    charged: Number(b.charged ?? 0),
    alreadyOwned: Boolean(b.already_owned),
    orderId: b.order_id == null ? null : String(b.order_id),
  };
}

/** Toss 결제 SDK. 번들에 넣지 않고 결제 시작 시점에만 불러온다. */
const TOSS_SDK_URL = "https://js.tosspayments.com/v1/payment";

async function loadTossSdk(clientKey: string): Promise<{
  requestPayment: (method: string, opts: Record<string, unknown>) => Promise<void>;
}> {
  const w = window as unknown as {
    TossPayments?: (key: string) => { requestPayment: never };
  };
  if (!w.TossPayments) {
    await new Promise<void>((resolve, reject) => {
      const el = document.createElement("script");
      el.src = TOSS_SDK_URL;
      el.onload = () => resolve();
      el.onerror = () => reject(new ThemeStoreError("UNKNOWN", "결제 모듈을 불러오지 못했습니다.", 0));
      document.head.appendChild(el);
    });
  }
  if (!w.TossPayments) {
    throw new ThemeStoreError("UNKNOWN", "결제 모듈을 불러오지 못했습니다.", 0);
  }
  return w.TossPayments(clientKey) as unknown as {
    requestPayment: (method: string, opts: Record<string, unknown>) => Promise<void>;
  };
}

/** 결제 후 돌아올 경로. app-entry.ts 의 themeReturnEntry() 와 짝이다. */
export function themeReturnUrls(origin?: string): { successUrl: string; failUrl: string } {
  const base = (origin || (typeof window !== "undefined" ? window.location.origin : "")).replace(
    /\/$/,
    ""
  );
  return { successUrl: `${base}/themes/success`, failUrl: `${base}/themes/fail` };
}

/**
 * 결제창 열기 — **페이지가 이동한다.** 이 함수 뒤의 코드는 실행되지 않는다.
 *
 * 카드 등록(requestBillingAuth)이 아니라 일회성 결제(requestPayment)다.
 * 그래서 구독한 적 없는 사용자도 쓸 수 있고, 카드가 저장되지도 않는다.
 */
export async function openThemePaymentWindow(checkout: ThemeCheckout): Promise<void> {
  if (!checkout.clientKey) {
    throw new ThemeStoreError("UNKNOWN", "결제 설정이 준비되지 않았습니다.", 0);
  }
  const toss = await loadTossSdk(checkout.clientKey);
  const { successUrl, failUrl } = themeReturnUrls();
  await toss.requestPayment("카드", {
    amount: checkout.amount,
    orderId: checkout.orderId,
    orderName: checkout.orderName,
    successUrl,
    failUrl,
  });
}
