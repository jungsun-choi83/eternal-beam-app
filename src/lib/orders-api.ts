/**
 * 물리 제품 주문 클라이언트 (Phase 12) — LETTER / MEMORY BOX.
 *
 * 서버 계약: backend/routers/orders_v1.py
 *
 * ⚠️ 이 모듈에는 **구독도 테마도 크레딧도 없다.** 실물 주문은 네 번째 축이고,
 *    결제가 성공해도 바뀌는 것은 주문 한 행의 상태뿐이다.
 *
 * ⚠️ 편지를 **만들지 않는다.** Soul Trace 가 만든 본문을 연결(link)할 뿐이다.
 *
 * 결제는 Phase 11 테마와 같은 일회성 Toss 흐름을 쓴다:
 *   checkout → 결제창 → /orders/success → confirm
 * 멤버십도 저장된 카드도 필요 없다.
 */

import { ThemeStoreError } from "./theme-store-api.ts";

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

export const PRODUCT_LETTER = "LETTER";
export const PRODUCT_MEMORY_BOX = "MEMORY_BOX";

export interface PhysicalProduct {
  productType: string;
  priceKrw: number;
  currency: string;
  contents: string[];
}

export interface PhysicalOrder {
  orderId: string;
  petId: string;
  soulTraceLetterId: string | null;
  productType: string;
  amount: number;
  currency: string;
  paymentStatus: string;
  productionStatus: string;
  shippingStatus: string;
  trackingNumber: string | null;
  shakerShareId: string | null;
  createdAt: string | null;
}

export interface OrderCheckout {
  orderId: string;
  productType: string;
  amount: number;
  orderName: string;
  currency: string;
  clientKey: string;
  petId: string;
  soulTraceLetterId: string | null;
}

export type OrderErrorCode =
  | "UNAUTHENTICATED"
  | "PRODUCT_UNKNOWN"
  | "PET_REQUIRED"
  /** Soul Trace 편지를 먼저 연결해야 한다. */
  | "LETTER_REQUIRED"
  | "LETTER_NOT_FOUND"
  /** 본문 없이 편지를 연결하려 했다 — 우리는 편지를 만들지 않는다. */
  | "LETTER_BODY_REQUIRED"
  | "SHIPPING_INCOMPLETE"
  | "ORDER_NOT_FOUND"
  | "ORDER_NOT_PENDING"
  | "ORDER_AMOUNT_MISMATCH"
  | "ORDER_PAYMENT_FAILED"
  | "UNKNOWN";

export class OrderApiError extends Error {
  readonly code: OrderErrorCode;
  readonly status: number;

  constructor(code: OrderErrorCode, message: string, status: number) {
    super(message);
    this.name = "OrderApiError";
    this.code = code;
    this.status = status;
  }
}

async function readError(res: Response): Promise<OrderApiError> {
  let code: OrderErrorCode = "UNKNOWN";
  let message = `HTTP ${res.status}`;
  try {
    const b = (await res.json()) as { detail?: { code?: string; message?: string } };
    if (b?.detail?.code) code = b.detail.code as OrderErrorCode;
    if (b?.detail?.message) message = b.detail.message;
  } catch {
    /* 상태 코드로 충분하다 */
  }
  if (res.status === 401) code = "UNAUTHENTICATED";
  return new OrderApiError(code, message, res.status);
}

function auth(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

/** 순수 파서 — 테스트가 그대로 부른다. */
export function parseProduct(row: Record<string, unknown>): PhysicalProduct {
  return {
    productType: String(row.product_type ?? ""),
    priceKrw: Number(row.price_krw ?? 0),
    currency: String(row.currency ?? "KRW"),
    contents: Array.isArray(row.contents) ? row.contents.map(String) : [],
  };
}

export function parseOrder(row: Record<string, unknown>): PhysicalOrder {
  return {
    orderId: String(row.order_id ?? ""),
    petId: String(row.pet_id ?? ""),
    soulTraceLetterId:
      row.soul_trace_letter_id == null ? null : String(row.soul_trace_letter_id),
    productType: String(row.product_type ?? ""),
    amount: Number(row.amount ?? 0),
    currency: String(row.currency ?? "KRW"),
    paymentStatus: String(row.payment_status ?? "pending"),
    productionStatus: String(row.production_status ?? "pending"),
    shippingStatus: String(row.shipping_status ?? "pending"),
    trackingNumber: row.tracking_number == null ? null : String(row.tracking_number),
    shakerShareId: row.shaker_share_id == null ? null : String(row.shaker_share_id),
    createdAt: row.created_at == null ? null : String(row.created_at),
  };
}

/** 카탈로그. 인증이 필요 없다 — 가격은 공개 정보다. */
export async function fetchProducts(signal?: AbortSignal): Promise<PhysicalProduct[]> {
  const res = await fetch(`${apiBase()}/api/v1/orders/products`, {
    cache: "no-store",
    signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  const rows = Array.isArray(b.products) ? b.products : [];
  return rows.map((r) => parseProduct(r as Record<string, unknown>));
}

/**
 * Soul Trace 핸드오프를 교환해 편지를 가져온다.
 *
 * **본문을 보내지 않는다.** 보낼 수도 없다 — 서버가 Soul Trace 에서 서버 대
 * 서버로 정본을 가져오고, 이 요청은 traceId 와 불투명 토큰만 나른다. 예전
 * linkSoulTraceLetter 는 브라우저가 준 letter_body 를 그대로 저장했고 그것이
 * A5 로 인쇄되어 배송됐다 — 그 경로는 삭제됐다.
 *
 * 토큰은 1회용이다. 실패해도 **자동으로 재시도하지 않는다** — 두 번째 교환은
 * 서버에서 거절되므로, 사용자가 Soul Trace 에서 다시 시작해야 한다.
 */
export async function claimSoulTraceLetter(params: {
  traceId: string;
  handoff: string;
  accessToken: string;
}): Promise<{ letterId: string; petId: string | null }> {
  const res = await fetch(`${apiBase()}/api/v1/orders/letter/claim`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...auth(params.accessToken) },
    body: JSON.stringify({ trace_id: params.traceId, handoff: params.handoff }),
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  return {
    letterId: String(b.letter_id ?? ""),
    petId: b.pet_id == null ? null : String(b.pet_id),
  };
}

/**
 * 가져온 편지에 canonical petId 를 붙인다. **펫을 만들지 않는다.**
 *
 * 서버가 편지 소유권과 펫 소유권을 모두 확인한다 — 남의 펫에는 붙지 않는다.
 */
export async function linkLetterToPet(params: {
  letterId: string;
  petId: string;
  accessToken: string;
}): Promise<{ letterId: string; petId: string | null }> {
  const res = await fetch(`${apiBase()}/api/v1/orders/letter/link-pet`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...auth(params.accessToken) },
    body: JSON.stringify({ letter_id: params.letterId, pet_id: params.petId }),
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  return {
    letterId: String(b.letter_id ?? ""),
    petId: b.pet_id == null ? null : String(b.pet_id),
  };
}

export interface ShippingInput {
  recipientName: string;
  recipientPhone: string;
  postalCode: string;
  addressLine1: string;
  addressLine2?: string | null;
}

/** 주문 생성 + 결제창 값. **아직 아무 돈도 움직이지 않는다.** */
export async function startOrderCheckout(params: {
  petId: string;
  productType: string;
  soulTraceLetterId: string;
  shipping: ShippingInput;
  accessToken: string;
}): Promise<OrderCheckout> {
  const res = await fetch(`${apiBase()}/api/v1/orders/checkout`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...auth(params.accessToken) },
    body: JSON.stringify({
      pet_id: params.petId,
      product_type: params.productType,
      soul_trace_letter_id: params.soulTraceLetterId,
      recipient_name: params.shipping.recipientName,
      recipient_phone: params.shipping.recipientPhone,
      postal_code: params.shipping.postalCode,
      address_line1: params.shipping.addressLine1,
      address_line2: params.shipping.addressLine2 ?? undefined,
    }),
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  return {
    orderId: String(b.order_id ?? ""),
    productType: String(b.product_type ?? params.productType),
    amount: Number(b.amount ?? 0),
    orderName: String(b.order_name ?? ""),
    currency: String(b.currency ?? "KRW"),
    clientKey: String(b.client_key ?? ""),
    petId: String(b.pet_id ?? params.petId),
    soulTraceLetterId:
      b.soul_trace_letter_id == null ? null : String(b.soul_trace_letter_id),
  };
}

/** 결제 검증 → 주문 PAID. 같은 주문을 다시 확인해도 charged=0 이다. */
export async function confirmOrderPayment(params: {
  paymentKey: string;
  orderId: string;
  amount?: number | null;
  accessToken: string;
}): Promise<{ orderId: string; productType: string; charged: number; alreadyPaid: boolean }> {
  const res = await fetch(`${apiBase()}/api/v1/orders/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...auth(params.accessToken) },
    body: JSON.stringify({
      payment_key: params.paymentKey,
      order_id: params.orderId,
      amount: params.amount ?? undefined,
    }),
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  return {
    orderId: String(b.order_id ?? params.orderId),
    productType: String(b.product_type ?? ""),
    charged: Number(b.charged ?? 0),
    alreadyPaid: Boolean(b.already_paid),
  };
}

export async function fetchMyOrders(params: {
  accessToken: string;
  signal?: AbortSignal;
}): Promise<PhysicalOrder[]> {
  const res = await fetch(`${apiBase()}/api/v1/orders`, {
    cache: "no-store",
    headers: auth(params.accessToken),
    signal: params.signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  const rows = Array.isArray(b.orders) ? b.orders : [];
  return rows.map((r) => parseOrder(r as Record<string, unknown>));
}

/** 결제 후 돌아올 경로. app-entry.ts 의 orderReturnEntry() 와 짝이다. */
export function orderReturnUrls(origin?: string): { successUrl: string; failUrl: string } {
  const base = (origin || (typeof window !== "undefined" ? window.location.origin : "")).replace(
    /\/$/,
    ""
  );
  return { successUrl: `${base}/orders/success`, failUrl: `${base}/orders/fail` };
}

/**
 * 결제창 열기 — **페이지가 이동한다.**
 *
 * Phase 11 이 쓰는 것과 같은 일회성 결제(requestPayment)다. 카드가 저장되지
 * 않으므로 멤버십 흐름과 섞이지 않는다.
 */
export async function openOrderPaymentWindow(checkout: OrderCheckout): Promise<void> {
  if (!checkout.clientKey) {
    throw new OrderApiError("UNKNOWN", "결제 설정이 준비되지 않았습니다.", 0);
  }
  const w = window as unknown as {
    TossPayments?: (key: string) => {
      requestPayment: (m: string, o: Record<string, unknown>) => Promise<void>;
    };
  };
  if (!w.TossPayments) {
    await new Promise<void>((resolve, reject) => {
      const el = document.createElement("script");
      el.src = "https://js.tosspayments.com/v1/payment";
      el.onload = () => resolve();
      el.onerror = () =>
        reject(new OrderApiError("UNKNOWN", "결제 모듈을 불러오지 못했습니다.", 0));
      document.head.appendChild(el);
    });
  }
  if (!w.TossPayments) {
    throw new OrderApiError("UNKNOWN", "결제 모듈을 불러오지 못했습니다.", 0);
  }
  const { successUrl, failUrl } = orderReturnUrls();
  await w.TossPayments(checkout.clientKey).requestPayment("카드", {
    amount: checkout.amount,
    orderId: checkout.orderId,
    orderName: checkout.orderName,
    successUrl,
    failUrl,
  });
}

/** 결제 오류를 화면 문구로. ThemeStoreError 와 같은 모양을 유지한다. */
export function isOrderError(e: unknown): e is OrderApiError | ThemeStoreError {
  return e instanceof OrderApiError || e instanceof ThemeStoreError;
}

/**
 * 내 미결 주문을 Toss 와 맞춘다 — **브라우저가 돌아오지 못한 결제의 안전망.**
 *
 * 결제창 승인 직후 브라우저가 닫히면 successUrl 로 돌아오지 못하고, Toss 에는
 * 승인된 결제가 있는데 우리 주문은 pending 으로 남는다(돈은 받고 물건은 만들지
 * 않는 상태). 앱이 다시 열릴 때 이 호출이 그것을 정리한다.
 *
 * 실패해도 조용히 넘어간다 — 이건 보조 경로이고, 실패가 화면을 막으면 안 된다.
 */
export async function reconcileMyOrders(params: {
  accessToken: string;
  signal?: AbortSignal;
}): Promise<string[]> {
  try {
    const res = await fetch(`${apiBase()}/api/v1/orders/reconcile`, {
      method: "POST",
      headers: auth(params.accessToken),
      signal: params.signal,
    });
    if (!res.ok) return [];
    const b = (await res.json()) as Record<string, unknown>;
    return Array.isArray(b.confirmed_order_ids) ? b.confirmed_order_ids.map(String) : [];
  } catch {
    return [];
  }
}

/** 주문 하나를 목록에서 찾는다 (전용 단건 조회 엔드포인트를 늘리지 않는다). */
export async function findMyOrder(params: {
  orderId: string;
  accessToken: string;
}): Promise<PhysicalOrder | null> {
  const rows = await fetchMyOrders({ accessToken: params.accessToken });
  return rows.find((o) => o.orderId === params.orderId) ?? null;
}


export interface LinkedLetter {
  letterId: string;
  petId: string | null;
  childName: string | null;
  letterExcerpt: string | null;
}

/**
 * 내가 연결한 Soul Trace 편지들.
 *
 * ⚠️ 본문은 오지 않는다 — 인쇄용이지 화면용이 아니다. 그리고 **여기서 편지를
 *    만들지 않는다**: 목록이 비어 있으면 Soul Trace 에서 편지를 받아 연결해야 한다.
 */
export async function fetchMyLetters(params: {
  accessToken: string;
  signal?: AbortSignal;
}): Promise<LinkedLetter[]> {
  const res = await fetch(`${apiBase()}/api/v1/orders/letters`, {
    cache: "no-store",
    headers: auth(params.accessToken),
    signal: params.signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  const rows = Array.isArray(b.letters) ? b.letters : [];
  return rows.map((r) => {
    const x = r as Record<string, unknown>;
    return {
      letterId: String(x.letter_id ?? ""),
      petId: x.pet_id == null ? null : String(x.pet_id),
      childName: x.child_name == null ? null : String(x.child_name),
      letterExcerpt: x.letter_excerpt == null ? null : String(x.letter_excerpt),
    };
  });
}
