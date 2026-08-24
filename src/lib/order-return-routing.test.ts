/**
 * 결제 후 복귀 라우팅 (Phase 18).
 *
 * 지키는 계약: **결제를 마친 고객은 방금 산 그 아이에게 돌아간다.**
 * 로그인·QR 연결·사진 업로드·온보딩으로 절대 떨어지지 않는다.
 *
 * 왜 소스를 읽어 검사하는가: 이 버그는 렌더 결과가 아니라 **경로 결정**에 있었다.
 * `window.location.replace("/")` 한 줄이 전체 페이지를 재부팅했고, 루트의 폴백이
 * 온보딩이라 거기로 떨어졌다. 그 한 줄이 돌아오는 것을 막는 것이 이 파일의 일이다.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { fulfillmentLabel, fulfillmentStage } from "./order-checkout-flow.ts";
import { orderReturnEntry } from "./app-entry.ts";

const APP = readFileSync("src/app/EternalBeamApp.tsx", "utf8");
const ROOT = readFileSync("src/app/App.tsx", "utf8");
const SCREEN = readFileSync(
  "src/components/memorial/order-confirmation-screen.tsx",
  "utf8",
);

// ── 경로 인식 ────────────────────────────────────────────────────────────────

test("/orders/success·fail 만 결제 복귀로 인식한다", () => {
  const at = (path: string) => {
    (globalThis as { window?: unknown }).window = {
      location: { pathname: path },
    } as never;
    return orderReturnEntry();
  };
  assert.equal(at("/orders/success"), "success");
  assert.equal(at("/orders/success/"), "success");
  assert.equal(at("/orders/fail"), "fail");
  assert.equal(at("/"), null);
  assert.equal(at("/shaker"), null);
  delete (globalThis as { window?: unknown }).window;
});

// ── 앱 셸 **안에서** 처리된다 ────────────────────────────────────────────────

test("결제 복귀가 앱 셸 안의 화면으로 들어온다", () => {
  // 예전에는 App.tsx 가 EternalBeamApp 바깥에서 가로챘다. 바깥이면 나가는 길이
  // 루트 새로고침뿐이고, 루트는 온보딩으로 떨어진다.
  assert.match(
    APP,
    /if \(orderReturnEntry\(\)\) return 'orderResult'/,
    "resolveInitialScreen 이 결제 복귀를 알아보지 못한다",
  );
  assert.match(APP, /screen === 'orderResult'/, "orderResult 화면 분기가 없다");
  assert.doesNotMatch(
    ROOT,
    /if \(orderReturnEntry\(\)\) return <OrderConfirmationScreen/,
    "App.tsx 가 아직 앱 셸 밖에서 결제 복귀를 가로챈다",
  );
});

test("Soul Trace 핸드오프 잔재가 결제 복귀를 가로채지 않는다", () => {
  // 편지를 가져온 적 있는 고객은 핸드오프 흔적이 남아 있을 수 있다. 그 검사가
  // 결제 복귀보다 먼저 걸리면 결제 결과 대신 편지 가져오기 화면이 뜬다.
  assert.match(
    ROOT,
    /!orderReturnEntry\(\) && peekSoulTraceHandoffState\(\)/,
    "핸드오프 검사가 결제 복귀를 가로챈다",
  );
});

// ── 온보딩으로 떨어지지 않는다 ───────────────────────────────────────────────

test("확인 화면이 루트를 새로고침하지 않는다 — 그 한 줄이 버그였다", () => {
  assert.doesNotMatch(
    SCREEN,
    /window\.location\.replace/,
    "루트 새로고침이 남아 있다 — 다시 온보딩으로 떨어진다",
  );
  assert.doesNotMatch(SCREEN, /window\.location\.href\s*=/, "전체 이동이 남아 있다");
  assert.match(SCREEN, /onContinue/, "부모가 복귀를 결정하지 못한다");
});

test("복귀 처리가 온보딩·초기화 화면으로 보내지 않는다", () => {
  const i = APP.indexOf("const handleOrderReturn");
  assert.ok(i > 0, "handleOrderReturn 이 없다");
  const body = APP.slice(i, i + 1200);

  for (const forbidden of ["qrConnection", "signup", "photoUpload", "onboarding"]) {
    assert.doesNotMatch(
      body,
      new RegExp(forbidden),
      `복귀가 ${forbidden} 로 보낸다 — 고객 상태가 초기화된다`,
    );
  }
});

test("복귀가 펫·세션을 다시 만들지 않는다", () => {
  const i = APP.indexOf("const handleOrderReturn");
  const body = APP.slice(i, i + 1200);

  // 구독 복귀와 같은 금지 목록이다 — 복원은 기존 자산을 **가리키기만** 한다.
  for (const forbidden of [
    "setCutoutImage",
    "setUploadedImage",
    "handleReset",
    "finalizePreviewContent",
    "signOut",
    "clearEternalBeamIdentity",
  ]) {
    assert.doesNotMatch(
      body,
      new RegExp(forbidden),
      `복귀가 ${forbidden} 를 호출한다 — 펫/세션이 다시 시작된다`,
    );
  }
  assert.doesNotMatch(body, /removeItem\(ETERNAL_BEAM_PIPELINE_KEY\)/);
  assert.doesNotMatch(body, /sessionStorage\.clear/);
});

test("복귀는 구독 복귀와 **같은 스냅샷**을 쓴다 — 두 벌로 만들지 않는다", () => {
  const i = APP.indexOf("const handleOrderReturn");
  const body = APP.slice(i, i + 1200);
  assert.match(body, /resolveBillingReturn\(readBillingReturnState\(\)/);
  assert.match(body, /readStoredPipeline\(\)/, "펫 지문을 확인하지 않는다");
});

test("스냅샷이 없으면 기념품 화면으로 — 온보딩이 아니다", () => {
  const i = APP.indexOf("const handleOrderReturn");
  const body = APP.slice(i, i + 1200);
  assert.match(body, /navigateTo\('physicalOrder'\)/, "폴백이 기념품 화면이 아니다");
});

// ── 결제 성공은 절대 실패로 보이지 않는다 ───────────────────────────────────

test("결제됨 + 생산 대기 = Paid · Preparing (실패가 아니다)", () => {
  const stage = fulfillmentStage({
    paymentStatus: "paid",
    productionStatus: "pending",
    shippingStatus: "pending",
  });
  assert.equal(stage, "preparing");
  const label = fulfillmentLabel(stage);
  assert.match(label, /결제 완료/);
  assert.doesNotMatch(label, /실패/);
});

test("결제됨이면 어떤 생산 상태에서도 실패 문구가 나오지 않는다", () => {
  for (const productionStatus of ["pending", "ready", "in_production", "produced"]) {
    for (const shippingStatus of ["pending", "shipped", "delivered"]) {
      const label = fulfillmentLabel(
        fulfillmentStage({ paymentStatus: "paid", productionStatus, shippingStatus }),
      );
      assert.match(label, /결제 완료/, `${productionStatus}/${shippingStatus}`);
      assert.doesNotMatch(label, /실패|대기 중입니다/, `${productionStatus}/${shippingStatus}`);
    }
  }
});

test("생산 완료는 Ready 로 보인다", () => {
  assert.equal(
    fulfillmentStage({
      paymentStatus: "paid",
      productionStatus: "ready",
      shippingStatus: "pending",
    }),
    "ready",
  );
});

test("결제 전에는 paid 문구를 쓰지 않는다", () => {
  assert.equal(
    fulfillmentStage({
      paymentStatus: "pending",
      productionStatus: "pending",
      shippingStatus: "pending",
    }),
    "pending",
  );
  assert.doesNotMatch(fulfillmentLabel("pending"), /결제 완료/);
});

// ── 두 갈래 출구 ─────────────────────────────────────────────────────────────

test("주문 보기와 다른 상품 경로가 남아 있다", () => {
  assert.match(SCREEN, /onViewOrders/, "주문 보기 출구가 없다");
  assert.match(APP, /onViewOrders=\{\(\) => \{/, "부모가 주문 보기를 연결하지 않는다");
});
