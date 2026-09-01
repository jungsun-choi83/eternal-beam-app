import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

import {
  clearThemePurchaseReturnState,
  confirmThemePurchaseReturn,
  readThemePurchaseReturnState,
  saveThemePurchaseReturnState,
} from "./theme-purchase-return-state.ts";

function installSessionStorage() {
  const values = new Map<string, string>();
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    },
  });
}

test("Toss 왕복 상태는 선택 테마를 보존하고 confirm 전후를 구분한다", () => {
  installSessionStorage();
  saveThemePurchaseReturnState("aurora");
  assert.deepEqual(readThemePurchaseReturnState(), {
    themeKey: "aurora",
    confirmed: false,
  });

  confirmThemePurchaseReturn("aurora");
  assert.deepEqual(readThemePurchaseReturnState(), {
    themeKey: "aurora",
    confirmed: true,
  });

  clearThemePurchaseReturnState();
  assert.equal(readThemePurchaseReturnState(), null);
  delete (globalThis as { sessionStorage?: unknown }).sessionStorage;
});

test("프리미엄 카드 탭은 강조만 하고 구매는 하단 버튼에서 시작한다", () => {
  const screen = readFileSync("src/components/memorial/theme-selection-screen.tsx", "utf8");
  const start = screen.indexOf("const selectTheme");
  const end = screen.indexOf("const snapSelectTheme", start);
  const tapBody = screen.slice(start, end);
  assert.match(tapBody, /setHighlightTheme\(theme\.id\)/);
  assert.doesNotMatch(tapBody, /ownership\.buy|onContinue|onSelectTheme/);

  const primaryStart = screen.indexOf("const handlePrimaryAction");
  const primaryEnd = screen.indexOf("// 커스텀 배경", primaryStart);
  const primaryBody = screen.slice(primaryStart, primaryEnd);
  assert.match(primaryBody, /activeRow\.action === "buy"/);
  assert.match(primaryBody, /ownership\.buy\(previewTheme\.themeKey\)/);
});

test("구매 CTA는 서버 크레딧 가격을 쓰고 OWNED 면 미리보기 CTA로 바뀐다", () => {
  // ── Phase 4 에서 통화가 바뀌었다 ────────────────────────────────────────
  // 예전에는 CTA 가 formatPriceKrw(activeRow.priceKrw) 로 ₩4,900 을 그렸고,
  // 가격의 출처가 render.yaml 의 THEME_PRICE_<KEY>_KRW 였다.
  //
  // 이제 테마는 Beam Credit 으로 팔린다. 가격의 출처는 digital_products 이고
  // (Phase 3), CTA 는 크레딧을 그린다. render.yaml 을 더 이상 보지 않는 것이
  // 요점이다 — 가격이 환경변수를 떠났다는 사실 자체가 이 단계의 성과다.
  const screen = readFileSync("src/components/memorial/theme-selection-screen.tsx", "utf8");
  assert.match(screen, /formatCredits\(activeRow\?\.creditPrice/);
  assert.match(screen, /tc\.buyFor\(activePrice/);
  assert.match(screen, /activeRow\?\.action === "buy"[\s\S]*tc\.continueFree/);
  // 잔액이 CTA 위에 보인다 — "잔액 12 / 가격 5" 를 보고 누르는 화면이다.
  assert.match(screen, /tc\.balanceLabel\(ownership\.balance\)/);
});

test("Toss 성공 뒤 테마 선택·누끼·테마 id를 복원한다", () => {
  const app = readFileSync("src/app/EternalBeamApp.tsx", "utf8");
  const returned = readFileSync(
    "src/components/memorial/theme-purchase-return-screen.tsx",
    "utf8",
  );
  assert.match(returned, /confirmThemePurchaseReturn\(r\.themeKey\)/);
  assert.match(app, /if \(readThemePurchaseReturnState\(\)\) return 'themeSelection'/);
  assert.match(app, /getPendingCutoutMeta\(\)\?\.displayUrl/);
  assert.match(app, /getMemorialThemeByKey\(themePurchaseReturn\?\.themeKey\)\?\.id/);
});

test("PayPal 결제는 앱에서도 백엔드에서도 사라졌다", () => {
  // ── Phase 11: 닫힌 것에서 **삭제된 것**으로 바뀌었다 ──────────────────────
  // 예전에는 라우터가 410 PAYPAL_DISABLED 를 돌려주는 것을 확인했다. 그 라우터는
  // 이제 없다 — 마운트된 적도 없어 실 결제가 코드 배치상 불가능했고(근거:
  // docs/PAYPAL_LEGACY.md), 닫아 둔 코드는 언젠가 다시 열린다.
  //
  // **표는 남는다.** 과거 구매 증거는 새 아키텍처가 생겼다는 이유로 버리지 않는다
  // (supabase/migrations/20261009000000_freeze_legacy_purchase_tables.sql).
  const app = readFileSync("src/app/EternalBeamApp.tsx", "utf8");
  const main = readFileSync("backend/main.py", "utf8");
  assert.doesNotMatch(app, /PaymentScreen|payment-screen|screen === 'checkout'/);
  assert.doesNotMatch(main, /include_router\(paypal/);

  for (const gone of [
    "backend/routers/paypal.py",
    "backend/services/paypal_service.py",
    "src/lib/paypal-api.ts",
    "src/lib/paypal-sdk.ts",
    "src/components/memorial/payment-screen.tsx",
  ]) {
    assert.equal(existsSync(gone), false, `${gone} 가 돌아왔다`);
  }
});

test("KRW 테마 구매 시작 경로가 프론트에서 사라졌다", () => {
  // 새 주문을 만들 수 있는 호출이 남아 있으면 화면 하나만 되살려도 결제가 다시
  // 열린다. 테마를 사는 방법은 크레딧 하나뿐이다.
  //
  // ⚠️ confirmThemePayment 는 **남는다** — 배포 시점에 Toss 결제창에 머물러 있던
  // 고객의 승인을 받아 주는 드레인 경로다 (backend POST /api/v1/themes/confirm).
  const api = readFileSync("src/lib/theme-store-api.ts", "utf8");
  assert.doesNotMatch(api, /themes\/checkout/);
  assert.doesNotMatch(api, /themes\/purchase["']/);
  assert.match(api, /themes\/purchase-with-credits/);
  assert.match(api, /themes\/confirm/);
});
