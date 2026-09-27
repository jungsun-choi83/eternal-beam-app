/**
 * 유료 테마 스토어 클라이언트 (Phase 11).
 *
 * 서버 계약: backend/routers/theme_store_v1.py
 *
 * ⚠️ 이 모듈에는 **구독이 없다.** 테마 소유권은 다른 축이고, 응답에도 구독
 * 상태가 실리지 않는다. 멤버십은 크레딧을 지급할 뿐 소유권을 주지 않는다.
 *
 * ── KRW 직접 구매는 은퇴했다 (Phase 11) ────────────────────────────────────
 * 테마는 **Beam Credit** 으로만 산다(purchaseThemeWithCredits). 새 KRW 주문을
 * 만들던 startThemeCheckout / openThemePaymentWindow / purchaseTheme 은 삭제됐다.
 *
 * confirmThemePayment 는 남는다 — 배포 시점에 결제창에 머물러 있던 고객의 승인을
 * 받아 줄 곳이 필요하다. 새 주문이 생기지 않으므로 곧 쓸 일이 없어진다.
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
  /** 레거시 KRW 가격 미설정. 드레인 경로에서만 나올 수 있다. */
  | "THEME_PRICE_NOT_SET"
  | "THEME_ALREADY_OWNED"
  | "THEME_ORDER_NOT_FOUND"
  | "THEME_ORDER_NOT_PENDING"
  | "THEME_AMOUNT_MISMATCH"
  | "THEME_IS_FREE"
  | "THEME_UNKNOWN"
  | "THEME_PAYMENT_FAILED"
  /** 크레딧 부족 — 화면은 "크레딧 받기"로 안내한다. */
  | "INSUFFICIENT_CREDITS"
  /** 크레딧으로 팔지 않는 테마 (가격 미설정). **무료가 아니다.** */
  | "THEME_PRODUCT_NOT_SOLD"
  | "THEME_PURCHASE_UNAVAILABLE"
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
    // null 을 0 으로 접지 않는다 — 미설정은 무료가 아니라 판매 불가다.
    creditPrice: row.credit_price == null ? null : Number(row.credit_price),
    purchasable: Boolean(row.purchasable),
  };
}

export interface ThemeCatalog {
  offers: ThemeOffer[];
  /** 지금 잔액. 조회하지 못했으면 null — 0 과 구분한다. */
  creditBalance: number | null;
}

/** 카탈로그 + 내 보유 상태 + 잔액. **읽기 전용 — 결제도 생성도 없다.** */
export async function fetchThemeCatalog(params: {
  accessToken: string;
  signal?: AbortSignal;
}): Promise<ThemeCatalog> {
  const res = await fetch(`${apiBase()}/api/v1/themes/catalog`, {
    method: "GET",
    cache: "no-store",
    headers: { Authorization: `Bearer ${params.accessToken}` },
    signal: params.signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  const rows = Array.isArray(b.themes) ? b.themes : [];
  return {
    offers: rows
      .map((r) => parseThemeOffer(r as Record<string, unknown>))
      .filter((o) => o.themeKey),
    creditBalance: b.credit_balance == null ? null : Number(b.credit_balance),
  };
}

/**
 * **Beam Credit 으로 테마를 산다.**
 *
 *     Aurora → 5 크레딧 → 소유권 (영구)
 *
 * 서버에서 차감·원장·소유권이 **한 트랜잭션**으로 일어난다. 부분 성공이 없으므로
 * 이 호출이 성공하면 셋 다 됐고, 실패하면 셋 다 안 됐다.
 *
 * 결제창이 없다 — 페이지가 이동하지 않고 응답이 바로 돌아온다. 그래서 Toss 경로가
 * 필요로 하던 왕복 상태 저장(theme-purchase-return-state)이 이 경로에는 없다.
 *
 * 멱등성은 서버가 쥔다((사용자, 테마) 키). 두 번 눌러도 charged=0 이다.
 */
export async function purchaseThemeWithCredits(params: {
  themeKey: string;
  accessToken: string;
  signal?: AbortSignal;
}): Promise<ThemePurchaseOutcome> {
  const res = await fetch(`${apiBase()}/api/v1/themes/purchase-with-credits`, {
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
    creditsRemaining: b.credits_remaining == null ? null : Number(b.credits_remaining),
  };
}

export interface ThemePurchaseOutcome {
  themeKey: string;
  /** **이번 호출이 실제로 청구한 금액/크레딧.** 멱등 호출이면 0. */
  charged: number;
  alreadyOwned: boolean;
  orderId: string | null;
  /** 크레딧 구매에서만 채워진다 — 화면이 재조회 없이 "잔액 7" 을 그린다. */
  creditsRemaining?: number | null;
}

// ── 레거시 KRW 결제의 **드레인 창구** (Phase 11) ─────────────────────────────
//
// 주문을 만드는 1단계는 삭제됐다. 남은 것은 마지막 단계뿐이다:
//
//   (배포 전에 열려 있던 결제창) → successUrl 로 리다이렉트
//   → POST /themes/confirm → 서버 검증 → 소유권
//
// 배포하는 순간 결제창을 띄워 둔 고객이 [승인] 을 누르면 **돈은 나간다.** 받아 줄
// 곳이 없으면 결제만 되고 테마는 못 받는다. 새 주문이 생기지 않으므로 미결 주문은
// 시간이 지나면 0 이 되고, 그때 이 함수도 사라진다.
//
// ⚠️ 금액은 **서버가 확정한 주문 금액**이 기준이다. 아래 amount 는 결제창에서
//    돌아온 값을 대조하는 용도이며, 승인 기준이 아니다.

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

/** 결제 후 돌아올 경로. app-entry.ts 의 themeReturnEntry() 와 짝이다. */
export function themeReturnUrls(origin?: string): { successUrl: string; failUrl: string } {
  const base = (origin || (typeof window !== "undefined" ? window.location.origin : "")).replace(
    /\/$/,
    ""
  );
  return { successUrl: `${base}/themes/success`, failUrl: `${base}/themes/fail` };
}

