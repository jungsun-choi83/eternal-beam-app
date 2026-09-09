/**
 * 운영 생산 콘솔의 버튼 활성 판정.
 *
 * 서버가 최종 판정을 하므로 여기가 틀려도 잘못된 전이는 일어나지 않는다.
 * 여기서 지키는 것은 **눌러도 409 가 나는 버튼을 보여 주지 않는 것**이다 —
 * 운영이 "왜 안 되지"로 시간을 쓰지 않게.
 */

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import {
  fileName,
  isPaid,
  opsActions,
  productionStep,
  shipBlockedReason,
  type OpsOrderView,
} from "./ops-production-flow.ts";
import { isOpsProductionPath, isOpsShakerPath } from "./shaker-ops-entry.ts";

function view(over: Partial<OpsOrderView> = {}): OpsOrderView {
  return {
    paymentStatus: "paid",
    productionStatus: "pending",
    shippingStatus: "pending",
    trackingNumber: null,
    packageReady: false,
    files: [],
    ...over,
  };
}

describe("결제 게이트", () => {
  it("미결제 주문은 모든 동작이 잠긴다", () => {
    // 돈을 받기 전에 인쇄하면 취소 시 그대로 손실이다.
    const a = opsActions(view({ paymentStatus: "pending", packageReady: true }));
    assert.equal(a.canPrepare, false);
    assert.equal(a.canStart, false);
    assert.equal(a.canDownload, false);
    assert.ok(a.blockedReason);
  });

  it("실패한 결제도 잠긴다", () => {
    assert.equal(opsActions(view({ paymentStatus: "failed" })).canPrepare, false);
    assert.equal(isPaid(view({ paymentStatus: "failed" })), false);
  });
});

describe("생산 단계", () => {
  it("준비 전에는 미리보기·내려받기·시작이 잠긴다", () => {
    const a = opsActions(view());
    assert.equal(a.canPrepare, true);
    assert.equal(a.canPreview, false);
    assert.equal(a.canDownload, false);
    assert.equal(a.canStart, false);
  });

  it("준비되면 미리보기·내려받기·시작이 열린다", () => {
    const a = opsActions(view({ packageReady: true, productionStatus: "ready" }));
    assert.equal(a.canPreview, true);
    assert.equal(a.canDownload, true);
    assert.equal(a.canStart, true);
  });

  it("준비는 멱등이라 다시 눌러도 된다", () => {
    // 서버가 같은 패키지를 돌려주므로 막을 이유가 없다.
    assert.equal(opsActions(view({ packageReady: true })).canPrepare, true);
  });

  it("IN_PRODUCTION 에서만 '생산 완료'가 열린다", () => {
    assert.equal(opsActions(view({ productionStatus: "ready" })).canMarkProduced, false);
    assert.equal(
      opsActions(view({ productionStatus: "in_production" })).canMarkProduced,
      true
    );
    assert.equal(opsActions(view({ productionStatus: "produced" })).canMarkProduced, false);
  });

  it("단계를 건너뛰는 버튼은 켜지지 않는다", () => {
    // PENDING 에서 바로 '생산 완료'가 눌리면 불가능한 이력이 남는다.
    const a = opsActions(view({ productionStatus: "pending", packageReady: true }));
    assert.equal(a.canMarkProduced, false);
  });

  it("진척 표시가 상태를 따른다", () => {
    assert.equal(productionStep("pending"), 0);
    assert.equal(productionStep("ready"), 1);
    assert.equal(productionStep("in_production"), 2);
    assert.equal(productionStep("produced"), 3);
    assert.equal(productionStep("nonsense"), 0);
  });
});

describe("배송 게이트", () => {
  const produced = { productionStatus: "produced", packageReady: true };

  it("생산 완료 + 송장이 있어야 발송할 수 있다", () => {
    assert.equal(opsActions(view({ ...produced })).canShip, false);
    assert.equal(
      opsActions(view({ ...produced, trackingNumber: "1234" })).canShip,
      true
    );
  });

  it("생산 전에는 송장이 있어도 발송할 수 없다", () => {
    // 만들지 않은 것을 보낼 수는 없다.
    const a = opsActions(view({ productionStatus: "ready", trackingNumber: "1234" }));
    assert.equal(a.canShip, false);
  });

  it("송장 등록은 언제든 가능하다 — 발송보다 먼저 받아 둔다", () => {
    assert.equal(opsActions(view()).canAddTracking, true);
  });

  it("SHIPPED 에서만 배송 완료가 열린다", () => {
    assert.equal(opsActions(view({ shippingStatus: "pending" })).canMarkDelivered, false);
    assert.equal(opsActions(view({ shippingStatus: "shipped" })).canMarkDelivered, true);
  });

  it("발송이 막힌 구체적 이유를 알려 준다", () => {
    assert.equal(
      shipBlockedReason(view({ productionStatus: "ready" })),
      "생산이 완료되어야 발송할 수 있습니다."
    );
    assert.equal(
      shipBlockedReason(view({ ...produced })),
      "송장 번호를 먼저 등록하세요."
    );
    assert.equal(shipBlockedReason(view({ ...produced, trackingNumber: "1" })), null);
  });
});

describe("파일명", () => {
  it("서버 Content-Disposition 과 같은 규칙", () => {
    assert.equal(fileName("eb_order_1", "letter_pdf"), "eb_order_1-letter-a5.pdf");
    assert.equal(fileName("eb_order_1", "photo_card"), "eb_order_1-photo-card-85x55.png");
    assert.equal(fileName("eb_order_1", "qr_card"), "eb_order_1-qr-card-85x55.png");
  });
});

describe("운영 경로", () => {
  it("생산 콘솔과 QR 콘솔이 겹치지 않는다", () => {
    assert.equal(isOpsProductionPath("/ops/production"), true);
    assert.equal(isOpsProductionPath("/ops/production/"), true);
    assert.equal(isOpsProductionPath("/ops/shaker"), false);
    assert.equal(isOpsShakerPath("/ops/production"), false);
  });

  it("고객 경로와도 겹치지 않는다", () => {
    for (const p of ["/shaker", "/orders/success", "/themes/success", "/"]) {
      assert.equal(isOpsProductionPath(p), false, p);
    }
  });
});
