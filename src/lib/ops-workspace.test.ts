/**
 * Ops 워크스페이스 — 내비게이션과 대시보드 집계.
 *
 * 지키는 계약:
 *   * `/ops` 는 대시보드다. 검색은 화면이 아니라 주문의 기능이다.
 *   * 예전 링크(`/ops/search`)가 죽지 않는다.
 *   * 대시보드 집계와 주문 필터가 **같은 규칙**을 쓴다(두 화면이 갈라지면 안 된다).
 *   * "확인 필요"는 이미 있는 상태에서만 나온다 — 새 신호를 만들지 않는다.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  OPS_NAV,
  OPS_ORDERS_PATH,
  OPS_PARTNERS_PATH,
  OPS_ROOT,
  OPS_SHAKER_PATH,
  isOpsPath,
  opsRouteFor,
  pathForRoute,
} from "./ops-nav.ts";
import {
  countOrders,
  needsAttention,
  recentOrders,
  type DashboardOrder,
} from "./ops-dashboard.ts";

// ── 내비게이션 ───────────────────────────────────────────────────────────────

test("사이드바는 네 항목뿐이다 — 검색은 항목이 아니다", () => {
  assert.deepEqual(
    OPS_NAV.map((n) => n.route),
    ["dashboard", "orders", "partners", "shaker"]
  );
  assert.ok(
    !OPS_NAV.some((n) => n.path.includes("search")),
    "검색이 별도 스태프 개념으로 노출된다"
  );
});

test("/ops 는 대시보드다 (예전에는 생산 콘솔이었다)", () => {
  assert.equal(opsRouteFor(OPS_ROOT), "dashboard");
  assert.equal(opsRouteFor("/ops/"), "dashboard");
});

test("각 경로가 자기 화면으로 간다", () => {
  assert.equal(opsRouteFor(OPS_ORDERS_PATH), "orders");
  assert.equal(opsRouteFor(OPS_PARTNERS_PATH), "partners");
  assert.equal(opsRouteFor(OPS_SHAKER_PATH), "shaker");
});

test("예전 /ops/search 링크가 죽지 않는다 — 주문으로 접는다", () => {
  assert.equal(opsRouteFor("/ops/search"), "orders");
});

test("고객 경로를 삼키지 않는다", () => {
  for (const p of ["/", "/shaker", "/forest", "/orders/success", "/soul-trace/import"]) {
    assert.equal(isOpsPath(p), false, `${p} 가 운영으로 잘못 인식된다`);
  }
});

test("비슷하지만 다른 경로를 삼키지 않는다", () => {
  for (const p of ["/opsx", "/ops/production/extra", "/ops/searching", "/ops/partner"]) {
    assert.equal(isOpsPath(p), false, `${p} 를 잘못 삼킨다`);
  }
});

test("경로와 화면이 왕복한다", () => {
  for (const n of OPS_NAV) {
    assert.equal(pathForRoute(n.route), n.path);
    assert.equal(opsRouteFor(n.path), n.route);
  }
});

// ── 대시보드 집계 ────────────────────────────────────────────────────────────

function order(p: Partial<DashboardOrder> = {}): DashboardOrder {
  return {
    orderId: "o1",
    petId: "pet_1",
    productType: "LETTER",
    amount: 14900,
    paymentStatus: "paid",
    productionStatus: "pending",
    shippingStatus: "pending",
    trackingNumber: null,
    recipientName: "김보호",
    createdAt: null,
    ...p,
  };
}

test("네 칸이 서로 겹치지 않는다 — 한 주문은 한 칸에만 센다", () => {
  const rows = [
    order({ orderId: "a" }),
    order({ orderId: "b", productionStatus: "ready" }),
    order({ orderId: "c", productionStatus: "in_production" }),
    order({ orderId: "d", productionStatus: "produced" }),
    order({ orderId: "e", productionStatus: "produced", shippingStatus: "shipped" }),
    order({ orderId: "f", productionStatus: "produced", shippingStatus: "delivered" }),
  ];
  const c = countOrders(rows);
  assert.deepEqual(c, { paid: 1, preparing: 2, ready: 1, shipping: 2 });
  assert.equal(c.paid + c.preparing + c.ready + c.shipping, rows.length);
});

test("배송이 생산보다 우선한다 — 이미 나간 것은 '제작 완료'로 세지 않는다", () => {
  const c = countOrders([
    order({ productionStatus: "produced", shippingStatus: "delivered" }),
  ]);
  assert.equal(c.shipping, 1);
  assert.equal(c.ready, 0);
});

test("빈 목록도 안전하다", () => {
  assert.deepEqual(countOrders([]), { paid: 0, preparing: 0, ready: 0, shipping: 0 });
  assert.deepEqual(needsAttention([]), []);
  assert.deepEqual(recentOrders([]), []);
});

// ── 확인 필요 ────────────────────────────────────────────────────────────────

test("생산 준비 전 주문은 확인 필요다", () => {
  const items = needsAttention([order({ productionStatus: "pending" })]);
  assert.equal(items.length, 1);
  assert.equal(items[0].kind, "not_prepared");
});

test("제작이 끝났는데 송장이 없으면 확인 필요다", () => {
  const items = needsAttention([order({ productionStatus: "produced" })]);
  assert.equal(items[0].kind, "tracking_missing");
});

test("송장이 있으면 조용하다", () => {
  assert.deepEqual(
    needsAttention([order({ productionStatus: "produced", trackingNumber: "1234" })]),
    []
  );
});

test("정상 진행 중인 주문은 확인 필요가 아니다", () => {
  assert.deepEqual(needsAttention([order({ productionStatus: "in_production" })]), []);
  assert.deepEqual(
    needsAttention([
      order({ productionStatus: "produced", shippingStatus: "shipped", trackingNumber: "1" }),
    ]),
    []
  );
});

test("송장 없이 발송된 주문은 눈에 띄어야 한다", () => {
  const items = needsAttention([
    order({ productionStatus: "produced", shippingStatus: "shipped" }),
  ]);
  assert.equal(items[0].kind, "shipped_without_tracking");
});

test("한 주문은 이유를 하나만 낸다 — 목록이 중복으로 부풀지 않는다", () => {
  const items = needsAttention([
    order({ orderId: "x", productionStatus: "produced", shippingStatus: "shipped" }),
  ]);
  assert.equal(items.filter((i) => i.orderId === "x").length, 1);
});

test("모든 이유에 사람이 읽을 문장이 있다", () => {
  const items = needsAttention([
    order({ orderId: "a" }),
    order({ orderId: "b", productionStatus: "produced" }),
    order({ orderId: "c", productionStatus: "produced", shippingStatus: "shipped" }),
  ]);
  assert.equal(items.length, 3);
  for (const i of items) assert.ok(i.reason.length > 0, i.kind);
});

test("최근 주문은 created_at 내림차순이다 — 주문번호로 근사하지 않는다", () => {
  // 주문번호가 시간과 **반대로** 정렬되도록 일부러 꼬아 둔다.
  const rows = [
    order({ orderId: "zzz", createdAt: "2026-01-01T00:00:00Z" }),
    order({ orderId: "aaa", createdAt: "2026-03-01T00:00:00Z" }),
    order({ orderId: "mmm", createdAt: "2026-02-01T00:00:00Z" }),
  ];
  assert.deepEqual(
    recentOrders(rows).map((o) => o.orderId),
    ["aaa", "mmm", "zzz"],
    "주문번호 순으로 정렬됐다 — created_at 을 쓰지 않는다"
  );
  assert.equal(recentOrders(rows, 2).length, 2);
});

test("created_at 이 없는 행은 뒤로 — 진짜 최근 주문을 밀어내지 않는다", () => {
  const rows = [
    order({ orderId: "no-date" }),
    order({ orderId: "dated", createdAt: "2026-01-01T00:00:00Z" }),
  ];
  assert.deepEqual(
    recentOrders(rows).map((o) => o.orderId),
    ["dated", "no-date"]
  );
});

test("동률이면 주문번호로 갈라 호출마다 순서가 바뀌지 않는다", () => {
  const t = "2026-01-01T00:00:00Z";
  const rows = [order({ orderId: "a", createdAt: t }), order({ orderId: "b", createdAt: t })];
  assert.deepEqual(recentOrders(rows).map((o) => o.orderId), ["b", "a"]);
});

// ── 서버 판정 우선 ───────────────────────────────────────────────────────────

test("서버가 판정을 주면 그것이 정본이다", () => {
  // 행만 보면 조용한 주문인데 서버는 '확인 필요'라고 한다 —
  // 서버는 생산 패키지(메모리 박스 사진 원본)까지 보고 판단하기 때문이다.
  const items = needsAttention([
    order({
      productionStatus: "ready",
      needsAttention: true,
      attentionCode: "PHOTO_MISSING",
      attentionReason: "사진 카드 원본이 없어 카드와 패키지를 만들 수 없습니다.",
    }),
  ]);
  assert.equal(items.length, 1);
  assert.equal(items[0].kind, "server");
  assert.match(items[0].reason, /사진 카드/);
});

test("서버가 '아니다'라고 하면 프론트가 뒤집지 않는다", () => {
  // 행만 보면 not_prepared 로 보이지만 서버가 이미 아니라고 했다.
  // 두 규칙이 갈라지면 대시보드와 상세가 서로 다른 말을 한다.
  assert.deepEqual(
    needsAttention([order({ productionStatus: "pending", needsAttention: false })]),
    []
  );
});

test("서버 판정이 없으면(구버전 응답) 행에서 유도한다", () => {
  const items = needsAttention([order({ productionStatus: "pending" })]);
  assert.equal(items[0].kind, "not_prepared");
});

// ── 두 화면이 같은 규칙을 쓰는가 ────────────────────────────────────────────

test("주문 필터와 대시보드 집계가 같은 분류를 쓴다", () => {
  // 규칙이 갈라지면 대시보드가 '3건'이라 했는데 필터에는 5건이 뜬다.
  const src = readFileSync("src/components/memorial/ops/ops-orders-screen.tsx", "utf8");
  assert.ok(src.includes("export function bucketOf"), "주문 필터 분류가 없다");
  for (const token of ["shipped", "delivered", "produced", "in_production"]) {
    assert.ok(src.includes(token), `필터가 ${token} 를 모른다`);
  }
});

test("Ops 화면이 고객 앱 스타일로 되돌아가지 않는다", () => {
  // 콘솔 느낌(검정 배경)으로의 회귀 방지. 배경은 밝은 회색이어야 한다.
  const ui = readFileSync("src/components/memorial/ops/ops-ui.tsx", "utf8");
  assert.match(ui, /pageBg:\s*"#F6F4F1"/);
  assert.match(ui, /surface:\s*"#FFFFFF"/);
});

test("본문에 모노스페이스를 기본으로 쓰지 않는다", () => {
  const ui = readFileSync("src/components/memorial/ops/ops-ui.tsx", "utf8");
  // Field 는 mono 를 **선택**으로만 받는다 — 식별자에만 쓰기 위한 것이다.
  assert.match(ui, /mono\s*=\s*false/);
});


// ── 목록 응답 완전성 (Phase 23) ─────────────────────────────────────────────

test("주문 행 파서가 created_at·파트너·처리필요를 읽는다", async () => {
  const { parseOrderRow } = await import("./ops-production-api.ts");
  const row = parseOrderRow({
    order_id: "o1",
    pet_id: "pet_1",
    product_type: "MEMORY_BOX",
    amount: 49000,
    payment_status: "paid",
    production_status: "ready",
    shipping_status: "pending",
    created_at: "2026-03-01T00:00:00Z",
    needs_attention: true,
    attention_code: "PHOTO_MISSING",
    attention_reason: "사진 카드 원본이 없습니다.",
    partner_id: "ptn_hosp_001",
    partner_type: "HOSPITAL",
    partner_name: "silim hospital",
    partner_track: "memorial",
  });
  assert.equal(row.createdAt, "2026-03-01T00:00:00Z");
  assert.equal(row.needsAttention, true);
  assert.equal(row.attentionCode, "PHOTO_MISSING");
  assert.equal(row.partnerName, "silim hospital");
  assert.equal(row.partnerTrack, "memorial");
});

test("구버전 응답도 안전하게 읽힌다 — 없는 필드는 null/false", async () => {
  const { parseOrderRow } = await import("./ops-production-api.ts");
  const row = parseOrderRow({ order_id: "o1", pet_id: "p", product_type: "LETTER" });
  assert.equal(row.createdAt, null);
  assert.equal(row.needsAttention, false);
  assert.equal(row.partnerId, null);
});

test("파트너 필터가 요청 쿼리로 나간다 — 백엔드가 이미 지원하던 것이다", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync("src/lib/ops-production-api.ts", "utf8");
  assert.match(src, /qs\.set\("partner_id", params\.partnerId\)/);
  assert.match(src, /qs\.set\("partner_type", params\.partnerType\)/);
});

test("정산 비율이 주문 시점 값임을 화면이 말한다", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync("src/components/memorial/ops/ops-orders-screen.tsx", "utf8");
  assert.match(src, /정산 비율 \(주문 시점\)/);
  assert.match(src, /주문 시점 스냅샷/);
});

test("Shaker 에 클라이언트 purpose 필터를 넣지 않았다", async () => {
  // 서버가 권위 있는 필터를 지원하기 전까지는 넣지 않는다 — 목록이 잘리면
  // 클라이언트 필터가 사실과 다른 결과를 보여 준다.
  const { readFileSync } = await import("node:fs");
  const src = readFileSync("src/components/memorial/ops/ops-shaker-screen.tsx", "utf8");
  assert.ok(!/shares\.filter\(/.test(src), "클라이언트 purpose 필터가 들어갔다");
});
