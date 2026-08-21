/**
 * 물리 주문 클라이언트 — 파싱과 복귀 경로.
 *
 * 여기서 지키는 것: 실물 결제가 **구독·테마 복귀 경로와 섞이지 않는다**.
 * 섞이면 실물 결제가 남의 confirm 을 타고, 축 분리가 깨진다.
 */

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import { orderReturnUrls, parseOrder, parseProduct } from "./orders-api.ts";
import { themeReturnUrls } from "./theme-store-api.ts";
import { orderReturnEntry } from "./app-entry.ts";

describe("카탈로그 파싱", () => {
  it("PM 확정 가격을 그대로 옮긴다", () => {
    const p = parseProduct({
      product_type: "LETTER", price_krw: 14900, currency: "KRW",
      contents: ["printed_letter", "envelope", "qr"],
    });
    assert.equal(p.productType, "LETTER");
    assert.equal(p.priceKrw, 14900);
    assert.deepEqual(p.contents, ["printed_letter", "envelope", "qr"]);
  });

  it("contents 가 배열이 아니어도 죽지 않는다", () => {
    assert.deepEqual(parseProduct({ product_type: "LETTER" }).contents, []);
  });
});

describe("주문 파싱", () => {
  const row = {
    order_id: "eb_order_1", pet_id: "pet_abc", soul_trace_letter_id: "stl_1",
    product_type: "MEMORY_BOX", amount: 49000, currency: "KRW",
    payment_status: "paid", production_status: "pending", shipping_status: "pending",
    tracking_number: null, shaker_share_id: "shr_1", created_at: "2026-08-22T00:00:00Z",
  };

  it("세 상태를 따로 읽는다", () => {
    const o = parseOrder(row);
    assert.equal(o.paymentStatus, "paid");
    assert.equal(o.productionStatus, "pending");
    assert.equal(o.shippingStatus, "pending");
  });

  it("canonical 연결(펫·편지·공유)을 보존한다", () => {
    const o = parseOrder(row);
    assert.equal(o.petId, "pet_abc");
    assert.equal(o.soulTraceLetterId, "stl_1");
    assert.equal(o.shakerShareId, "shr_1");
  });

  it("없는 값은 null 이다", () => {
    const o = parseOrder({ order_id: "x" });
    assert.equal(o.soulTraceLetterId, null);
    assert.equal(o.trackingNumber, null);
    assert.equal(o.shakerShareId, null);
  });
});

describe("결제 복귀 경로 분리", () => {
  it("주문 복귀는 /orders/* 다", () => {
    const { successUrl, failUrl } = orderReturnUrls("https://eternalbeam.com");
    assert.equal(successUrl, "https://eternalbeam.com/orders/success");
    assert.equal(failUrl, "https://eternalbeam.com/orders/fail");
  });

  it("테마·구독 경로와 겹치지 않는다", () => {
    // **핵심 회귀**: 겹치면 실물 결제가 구독/테마 confirm 을 타게 된다.
    const order = orderReturnUrls("https://x.com").successUrl;
    const theme = themeReturnUrls("https://x.com").successUrl;
    assert.notEqual(order, theme);
    assert.ok(!order.includes("/billing/"));
    assert.ok(!order.includes("/themes/"));
  });

  it("origin 후행 슬래시를 중복시키지 않는다", () => {
    assert.equal(
      orderReturnUrls("https://x.com/").successUrl,
      "https://x.com/orders/success"
    );
  });

  it("window 가 없으면 진입으로 보지 않는다", () => {
    assert.equal(orderReturnEntry(), null);
  });
});
