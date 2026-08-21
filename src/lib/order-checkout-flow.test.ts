/**
 * 실물 구매 흐름 모델.
 *
 * 결제 직전 판정이라 조용히 틀리면 **실물이 잘못 나간다** — 빈 주소로 인쇄되거나,
 * 편지 없이 편지 상품이 나가거나, 남의 펫을 가리킨다.
 */

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import {
  STEP_PRODUCT,
  STEP_REVIEW,
  STEP_SHIPPING,
  buildReview,
  canAdvance,
  canPay,
  describeOrderStatus,
  formatKrw,
  nextStep,
  orderBlockers,
  previousStep,
  shippingComplete,
  type OrderDraft,
} from "./order-checkout-flow.ts";

const SHIPPING = {
  recipientName: "김보호",
  phone: "010-1234-5678",
  postalCode: "06236",
  addressLine1: "서울시 강남구 테헤란로 1",
  addressLine2: "101동",
};

function draft(over: Partial<OrderDraft> = {}): OrderDraft {
  return {
    petId: "pet_abc123",
    soulTraceLetterId: "stl_1",
    productType: "LETTER",
    shipping: SHIPPING,
    hasAuth: true,
    ...over,
  };
}

describe("배송지 완결성", () => {
  it("네 항목이 모두 있어야 한다", () => {
    assert.equal(shippingComplete(SHIPPING), true);
  });

  it("하나라도 비면 불완전하다 — 빈 주소로 인쇄되면 안 된다", () => {
    for (const k of ["recipientName", "phone", "postalCode", "addressLine1"] as const) {
      assert.equal(shippingComplete({ ...SHIPPING, [k]: "   " }), false, k);
    }
  });

  it("addressLine2 는 선택이다", () => {
    assert.equal(shippingComplete({ ...SHIPPING, addressLine2: "" }), true);
  });

  it("null 은 불완전하다", () => {
    assert.equal(shippingComplete(null), false);
  });
});

describe("결제 차단 사유", () => {
  it("모두 갖춰지면 없다", () => {
    assert.deepEqual(orderBlockers(draft()), []);
    assert.equal(canPay(draft()), true);
  });

  it("펫이 없으면 막는다 — 주문은 기존 펫을 가리킨다", () => {
    assert.ok(orderBlockers(draft({ petId: null })).includes("no-pet"));
    assert.equal(canPay(draft({ petId: "  " })), false);
  });

  it("Soul Trace 편지가 없으면 막는다 — **여기서 만들지 않는다**", () => {
    assert.ok(orderBlockers(draft({ soulTraceLetterId: null })).includes("no-letter"));
    assert.equal(canPay(draft({ soulTraceLetterId: null })), false);
  });

  it("제품·배송지·로그인이 없으면 각각 막는다", () => {
    assert.ok(orderBlockers(draft({ productType: null })).includes("no-product"));
    assert.ok(
      orderBlockers(draft({ shipping: { ...SHIPPING, phone: "" } })).includes(
        "incomplete-shipping"
      )
    );
    assert.ok(orderBlockers(draft({ hasAuth: false })).includes("signed-out"));
  });

  it("안내 순서는 로그인 → 펫 → 편지 순이다", () => {
    // 앞의 것이 없으면 뒤를 안내해도 소용없다.
    const b = orderBlockers({
      petId: null, soulTraceLetterId: null, productType: null,
      shipping: null, hasAuth: false,
    });
    assert.deepEqual(b.slice(0, 3), ["signed-out", "no-pet", "no-letter"]);
  });
});

describe("단계 전이", () => {
  it("제품을 고르지 않으면 다음으로 못 간다", () => {
    assert.equal(canAdvance(STEP_PRODUCT, draft({ productType: null })), false);
    assert.equal(canAdvance(STEP_PRODUCT, draft()), true);
  });

  it("배송지가 불완전하면 확인 화면으로 못 간다", () => {
    // 건너뛰면 사용자가 빈 주소를 승인하게 된다.
    assert.equal(canAdvance(STEP_SHIPPING, draft({ shipping: null })), false);
    assert.equal(canAdvance(STEP_SHIPPING, draft()), true);
  });

  it("확인 화면에서 결제로 가려면 모든 조건이 필요하다", () => {
    assert.equal(canAdvance(STEP_REVIEW, draft({ soulTraceLetterId: null })), false);
    assert.equal(canAdvance(STEP_REVIEW, draft()), true);
  });

  it("순서대로 나아가고 되돌아온다", () => {
    assert.equal(nextStep(STEP_PRODUCT), STEP_SHIPPING);
    assert.equal(nextStep(STEP_SHIPPING), STEP_REVIEW);
    assert.equal(previousStep(STEP_SHIPPING), STEP_PRODUCT);
    assert.equal(previousStep(STEP_PRODUCT), null);
  });
});

describe("주문 확인 화면", () => {
  it("결제 전에 보여 줄 값을 모은다", () => {
    const r = buildReview(draft(), 14900);
    assert.ok(r);
    assert.equal(r.productType, "LETTER");
    assert.equal(r.priceKrw, 14900);
    assert.equal(r.petId, "pet_abc123");
    assert.equal(r.soulTraceLetterId, "stl_1");
    assert.equal(r.recipientName, "김보호");
    assert.equal(r.address, "06236 서울시 강남구 테헤란로 1 101동");
  });

  it("조건이 모자라면 확인 화면을 만들지 않는다", () => {
    assert.equal(buildReview(draft({ shipping: null }), 14900), null);
    assert.equal(buildReview(draft({ petId: null }), 14900), null);
  });

  it("빈 addressLine2 가 주소에 공백을 남기지 않는다", () => {
    const r = buildReview(draft({ shipping: { ...SHIPPING, addressLine2: "" } }), 14900);
    assert.equal(r?.address, "06236 서울시 강남구 테헤란로 1");
  });
});

describe("가격 표시", () => {
  it("PM 확정 가격을 원화로", () => {
    assert.equal(formatKrw(14900), "₩14,900");
    assert.equal(formatKrw(49000), "₩49,000");
  });

  it("없는 값은 빈 문자열", () => {
    assert.equal(formatKrw(null), "");
    assert.equal(formatKrw(0), "");
  });
});

describe("주문 상태 문구", () => {
  const base = { paymentStatus: "paid", productionStatus: "pending", shippingStatus: "pending" };

  it("세 상태를 섞지 않는다", () => {
    assert.equal(describeOrderStatus({ ...base }), "결제 완료 · 제작 대기");
    assert.equal(
      describeOrderStatus({ ...base, productionStatus: "printed" }),
      "제작 중"
    );
    assert.equal(
      describeOrderStatus({ ...base, shippingStatus: "shipped" }),
      "배송 중"
    );
  });

  it("송장이 있으면 함께 보여 준다", () => {
    assert.equal(
      describeOrderStatus({ ...base, shippingStatus: "shipped", trackingNumber: "1234" }),
      "배송 중 · 1234"
    );
  });

  it("미결제·실패를 구분한다", () => {
    assert.equal(describeOrderStatus({ ...base, paymentStatus: "pending" }), "결제 대기");
    assert.equal(describeOrderStatus({ ...base, paymentStatus: "failed" }), "결제 실패");
  });

  it("결제 전에는 생산·배송 상태를 말하지 않는다", () => {
    // 결제되지 않았는데 "제작 중"이라고 하면 거짓말이다.
    assert.equal(
      describeOrderStatus({
        paymentStatus: "pending", productionStatus: "printed", shippingStatus: "shipped",
      }),
      "결제 대기"
    );
  });
});
