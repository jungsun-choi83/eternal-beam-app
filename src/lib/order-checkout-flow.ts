/**
 * 실물 구매 흐름의 **순수 모델** — 어느 단계인지, 무엇이 모자란지.
 *
 *   제품 선택 → 배송지 → 주문 확인 → Toss 결제창 → (복귀) 확인 완료
 *
 * 여기에 네트워크도 React 도 없다. 단계 전이와 "지금 결제해도 되는가" 판정을
 * 순수 함수로 모아 node --test 로 덮는다 — 결제 직전 판정이라 조용히 틀리면
 * 실물이 잘못 나간다.
 *
 * ⚠️ 편지를 만들지 않는다. 펫을 만들지 않는다. 이 모듈은 **이미 있는** petId 와
 *    Soul Trace 편지 id 를 들고 다니기만 한다.
 */

import type { ShippingAddress } from "./finalize-preview-content.ts";

export const STEP_PRODUCT = "product";
export const STEP_SHIPPING = "shipping";
export const STEP_REVIEW = "review";
export const STEP_PAYING = "paying";
export const STEP_DONE = "done";

export type OrderStep =
  | typeof STEP_PRODUCT
  | typeof STEP_SHIPPING
  | typeof STEP_REVIEW
  | typeof STEP_PAYING
  | typeof STEP_DONE;

/** 결제를 시작하지 못하는 이유. 화면이 문구를 정한다. */
export type OrderBlocker =
  /** canonical petId 가 없다 — 아직 펫을 만들지 않은 사용자. */
  | "no-pet"
  /** Soul Trace 편지가 연결되지 않았다. **여기서 만들지 않는다.** */
  | "no-letter"
  | "no-product"
  | "incomplete-shipping"
  | "signed-out";

export interface OrderDraft {
  /** 이미 존재하는 canonical petId. */
  petId: string | null;
  /** 이미 연결된 Soul Trace 편지 id. */
  soulTraceLetterId: string | null;
  productType: string | null;
  shipping: ShippingAddress | null;
  hasAuth: boolean;
}

/** 배송지가 실물을 보내기에 충분한가. */
export function shippingComplete(a: ShippingAddress | null | undefined): boolean {
  if (!a) return false;
  return Boolean(
    (a.recipientName || "").trim() &&
      (a.phone || "").trim() &&
      (a.postalCode || "").trim() &&
      (a.addressLine1 || "").trim()
  );
}

/**
 * 지금 결제를 시작하지 못하는 이유들 (없으면 빈 배열).
 *
 * 순서가 화면 안내 순서다: 로그인 → 펫 → 편지 → 제품 → 배송지.
 * 앞의 것이 없으면 뒤를 안내해도 소용없다.
 */
export function orderBlockers(draft: OrderDraft): OrderBlocker[] {
  const out: OrderBlocker[] = [];
  if (!draft.hasAuth) out.push("signed-out");
  if (!(draft.petId || "").trim()) out.push("no-pet");
  if (!(draft.soulTraceLetterId || "").trim()) out.push("no-letter");
  if (!(draft.productType || "").trim()) out.push("no-product");
  if (!shippingComplete(draft.shipping)) out.push("incomplete-shipping");
  return out;
}

export function canPay(draft: OrderDraft): boolean {
  return orderBlockers(draft).length === 0;
}

/**
 * 이 단계에서 다음으로 갈 수 있는가.
 *
 * 단계를 건너뛰지 못하게 하는 것이 요점이다 — 배송지를 채우지 않고 확인 화면에
 * 도달하면 사용자는 빈 주소를 승인하게 된다.
 */
export function canAdvance(step: OrderStep, draft: OrderDraft): boolean {
  if (step === STEP_PRODUCT) return Boolean((draft.productType || "").trim());
  if (step === STEP_SHIPPING) return shippingComplete(draft.shipping);
  if (step === STEP_REVIEW) return canPay(draft);
  return false;
}

export function nextStep(step: OrderStep): OrderStep {
  if (step === STEP_PRODUCT) return STEP_SHIPPING;
  if (step === STEP_SHIPPING) return STEP_REVIEW;
  if (step === STEP_REVIEW) return STEP_PAYING;
  return STEP_DONE;
}

export function previousStep(step: OrderStep): OrderStep | null {
  if (step === STEP_SHIPPING) return STEP_PRODUCT;
  if (step === STEP_REVIEW) return STEP_SHIPPING;
  return null;
}

/** 주문 확인 화면에 그릴 값들. */
export interface OrderReview {
  productType: string;
  priceKrw: number;
  petId: string;
  soulTraceLetterId: string;
  recipientName: string;
  phone: string;
  address: string;
}

export function buildReview(
  draft: OrderDraft,
  priceKrw: number
): OrderReview | null {
  if (!canPay(draft)) return null;
  const a = draft.shipping as ShippingAddress;
  return {
    productType: draft.productType as string,
    priceKrw,
    petId: draft.petId as string,
    soulTraceLetterId: draft.soulTraceLetterId as string,
    recipientName: a.recipientName.trim(),
    phone: a.phone.trim(),
    address: [a.postalCode, a.addressLine1, a.addressLine2]
      .map((x) => (x || "").trim())
      .filter(Boolean)
      .join(" "),
  };
}

/** 원화 표시. Phase 11 과 같은 형식을 쓴다. */
export function formatKrw(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v) || v <= 0) return "";
  return `₩${Math.round(v).toLocaleString("ko-KR")}`;
}

/** 주문 상태를 사람이 읽는 한 줄로. 세 상태를 섞지 않는다. */
export function describeOrderStatus(o: {
  paymentStatus: string;
  productionStatus: string;
  shippingStatus: string;
  trackingNumber?: string | null;
}): string {
  if (o.paymentStatus !== "paid") {
    return o.paymentStatus === "failed" ? "결제 실패" : "결제 대기";
  }
  if (o.shippingStatus === "shipped" || o.shippingStatus === "delivered") {
    return o.trackingNumber ? `배송 중 · ${o.trackingNumber}` : "배송 중";
  }
  if (o.productionStatus !== "pending") return "제작 중";
  return "결제 완료 · 제작 대기";
}
