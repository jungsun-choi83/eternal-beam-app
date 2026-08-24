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

// ── 어느 편지를 이 펫의 주문에 실을 것인가 ──────────────────────────────────

/** 선택에 필요한 최소한. orders-api 의 LinkedLetter 가 그대로 들어맞는다. */
export interface SelectableLetter {
  letterId: string;
  petId: string | null;
}

/**
 * **이 펫의 편지**를 고른다. 없으면 null — 아무 편지나 집지 않는다.
 *
 * ── 예전 동작과 무엇이 다른가 ──────────────────────────────────────────────
 * 예전 코드는 `rows.find((l) => l.petId === petId) ?? rows[0]` 이었다. 그런데
 * /letter/link-pet 을 아무도 부르지 않아 **모든 편지의 pet_id 가 NULL** 이었고,
 * find 는 언제나 실패해 `rows[0]` 으로 떨어졌다. 목록에는 정렬도 없었으므로
 * `rows[0]` 은 힙 순서상 가장 오래된 편지였다 — 새 편지를 몇 번을 가져오든
 * 결제에는 늘 같은 옛날 편지가 실렸고, 그 편지에는 파트너 귀속이 없어
 * physical_orders.partner_id 까지 NULL 로 굳었다.
 *
 * ── 우선순위 ────────────────────────────────────────────────────────────────
 *   1. 이 펫에 **연결된** 편지          ← 정답. 편지↔펫은 1:1 이다
 *   2. 아직 어느 펫에도 안 붙은 **활성 편지** (방금 클레임했고 링크가 아직 안 붙음)
 *   3. 아직 어느 펫에도 안 붙은 편지 중 **가장 최근 것** (서버가 최신순으로 준다)
 *
 * ── 절대 하지 않는 것 ──────────────────────────────────────────────────────
 * **다른 펫에 연결된 편지는 어떤 경우에도 고르지 않는다.** 이것이 교차 재사용을
 * 막는 유일한 규칙이다 — 이게 없으면 A 의 편지가 B 의 상자에 인쇄되어 나간다.
 * 종이라 되돌릴 수 없다.
 */
export function selectLetterForPet(input: {
  letters: SelectableLetter[];
  petId: string | null;
  /** 클레임 직후 저장해 둔 letter_id (soul-trace-handoff). 없으면 null. */
  activeLetterId?: string | null;
}): string | null {
  const pet = (input.petId || "").trim();
  const rows = input.letters.filter((l) => (l.letterId || "").trim());
  if (!pet) return null;

  const linkedToThisPet = rows.find((l) => (l.petId || "").trim() === pet);
  if (linkedToThisPet) return linkedToThisPet.letterId;

  // 여기부터는 **미연결 편지만** 후보다. 남의 펫 편지는 후보에서 빠진다.
  const unlinked = rows.filter((l) => !(l.petId || "").trim());

  const active = (input.activeLetterId || "").trim();
  if (active) {
    const stillUnlinked = unlinked.find((l) => l.letterId === active);
    if (stillUnlinked) return stillUnlinked.letterId;
  }

  // 방어선일 뿐이다 — 서버가 imported_at 내림차순으로 준다는 계약에 기댄다.
  return unlinked[0]?.letterId ?? null;
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
