/**
 * 운영 콘솔 진입 — 경로 감지와 화면 상태.
 *
 * ⚠️ 여기 있는 어떤 것도 **보안 경계가 아니다.** 인가는 서버가 한다
 * (JWT + SHAKER_OPS_USER_IDS). 이 테스트가 지키는 것은 UX 다: 권한이 없는
 * 사람에게 빈 화면 대신 이유를 보여 준다.
 */

import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

import {
  deriveOpsPhase,
  isOpsProductionPath,
  isOpsShakerPath,
  OPS_SHAKER_PATH,
} from "./shaker-ops-entry.ts";
import { isShakerPath } from "./shaker-entry.ts";

describe("운영 경로 감지", () => {
  it("/ops/shaker 와 후행 슬래시를 인정한다", () => {
    assert.equal(isOpsShakerPath("/ops/shaker"), true);
    assert.equal(isOpsShakerPath("/ops/shaker/"), true);
  });

  it("비슷한 경로는 인정하지 않는다", () => {
    for (const p of ["/ops", "/shaker", "/ops/shakerx", "/ops/shaker/extra", "/"]) {
      assert.equal(isOpsShakerPath(p), false, p);
    }
  });

  it("공개 Shaker 경로와 겹치지 않는다", () => {
    // 겹치면 고객이 QR 로 들어왔을 때 운영 화면이 뜬다 — 정보 노출이다.
    assert.equal(isShakerPath(OPS_SHAKER_PATH), false);
    assert.equal(isOpsShakerPath("/shaker"), false);
  });
});

describe("운영 화면 상태", () => {
  it("토큰이 없으면 signed-out", () => {
    assert.equal(deriveOpsPhase({ hasAuth: false, errorCode: null }), "signed-out");
  });

  it("서버가 401 을 주면 signed-out 으로 수렴한다", () => {
    // 토큰이 만료된 경우 — 로그인하라고 말해야지 "권한 없음"이라고 하면 안 된다.
    assert.equal(
      deriveOpsPhase({ hasAuth: true, errorCode: "UNAUTHENTICATED" }),
      "signed-out"
    );
  });

  it("allowlist 밖이면 forbidden — 이유를 구분해 보여 준다", () => {
    assert.equal(deriveOpsPhase({ hasAuth: true, errorCode: "OPS_FORBIDDEN" }), "forbidden");
  });

  it("그 외 오류는 error", () => {
    assert.equal(deriveOpsPhase({ hasAuth: true, errorCode: "PET_NOT_FOUND" }), "error");
  });

  it("정상이면 ready", () => {
    assert.equal(deriveOpsPhase({ hasAuth: true, errorCode: null }), "ready");
  });
});

/**
 * 회귀 방지 — **스태프가 고객 온보딩으로 떨어지지 않는다.**
 *
 * 실제로 있었던 일: `/ops` 는 어떤 조건에도 걸리지 않아 EternalBeamApp 폴백으로
 * 떨어졌고, 거기서 qrConnection → 로그인 → photoUpload 가 그려졌다.
 * 스태프에게 고객용 사진 업로드 화면이 뜬 이유다.
 */
describe("운영 진입 경로 — 고객 앱으로 새지 않는다", () => {
  it("/ops · /ops/search · /ops/production 은 모두 주문 콘솔이다", () => {
    for (const p of ["/ops", "/ops/", "/ops/search", "/ops/search/", "/ops/production"]) {
      assert.equal(isOpsProductionPath(p), true, `${p} 가 인식되지 않는다`);
    }
  });

  it("/ops/shaker 는 Shaker 콘솔이고 주문 콘솔이 아니다", () => {
    assert.equal(isOpsShakerPath("/ops/shaker"), true);
    assert.equal(isOpsProductionPath("/ops/shaker"), false);
  });

  it("고객 경로는 운영으로 인식되지 않는다 — 직접 진입은 그대로다", () => {
    for (const p of ["/", "/shaker", "/forest", "/orders/success", "/soul-trace/import"]) {
      assert.equal(isOpsProductionPath(p), false, `${p} 가 운영으로 잘못 인식된다`);
      assert.equal(isOpsShakerPath(p), false, `${p} 가 운영으로 잘못 인식된다`);
    }
  });

  it("비슷하지만 다른 경로를 삼키지 않는다", () => {
    for (const p of ["/opsx", "/ops/production/extra", "/ops/searching"]) {
      assert.equal(isOpsProductionPath(p), false, `${p} 를 잘못 삼킨다`);
    }
  });
});

describe("구조 고정 — 인증은 셸 한 곳에만 있다", () => {
  // 예전에는 화면마다 같은 인증 코드가 있었다(생산·Shaker·파트너). 네 벌이면
  // 한 곳만 고쳐지는 날이 오고, 그 한 곳이 인가면 조용히 열린 문이 된다.
  // 이제 OpsLayout 하나가 그 판정을 갖는다.
  const layout = readFileSync("src/components/memorial/ops/ops-layout.tsx", "utf8");

  it("signed-out 이면 로그인 화면을 그린다 (막다른 안내가 아니다)", () => {
    assert.ok(
      /if \(phase === "signed-out"\)[\s\S]{0,400}<AuthScreen/.test(layout),
      "로그인 수단 없이 안내만 띄우면 스태프가 앱 루트로 나가고, 루트는 고객 온보딩이다",
    );
  });

  it("forbidden 은 로그인 화면을 그리지 않는다", () => {
    const i = layout.indexOf('phase === "forbidden"');
    assert.ok(i > 0, "forbidden 분기가 없다");
    assert.ok(
      !layout.slice(i, i + 600).includes("<AuthScreen"),
      "권한 없는 계정에 로그인 화면을 반복해 띄운다",
    );
  });

  it("로그인 직후 새로고침 없이 들어간다 — 토큰만 다시 읽는다", () => {
    assert.ok(
      /onAuthComplete=\{readToken\}/.test(layout),
      "로그인 후 페이지를 다시 불러오면 원래 가려던 Ops 경로를 잃는다",
    );
    assert.ok(
      !/window\.location\.(replace|reload|href)/.test(layout),
      "셸이 전체 페이지 이동을 한다 — 상태가 사라진다",
    );
  });

  it("인가는 여전히 서버 판정을 따른다 (allowlist 를 약화하지 않는다)", () => {
    // 화면은 서버가 준 코드를 읽을 뿐, 스스로 권한을 결정하지 않는다.
    assert.ok(layout.includes("deriveOpsPhase"));
    assert.ok(layout.includes("OPS_FORBIDDEN"));
    assert.ok(!/SHAKER_OPS_USER_IDS\s*=/.test(layout), "프론트가 allowlist 를 흉내 낸다");
  });

  it("모든 Ops 화면이 같은 셸을 쓴다", () => {
    for (const f of [
      "ops-dashboard-screen",
      "ops-orders-screen",
      "ops-partners-screen",
      "ops-shaker-screen",
    ]) {
      const src = readFileSync(`src/components/memorial/ops/${f}.tsx`, "utf8");
      assert.ok(src.includes("<OpsLayout"), `${f} 가 공용 셸을 쓰지 않는다`);
      assert.ok(
        !src.includes("getPremiumAccessToken"),
        `${f} 가 인증을 따로 들고 있다 — 셸로 모아야 한다`,
      );
    }
  });
});

describe("구조 고정 — App.tsx 분기 순서", () => {
  const app = readFileSync("src/app/App.tsx", "utf8");

  it("운영 분기가 EternalBeamApp 폴백보다 **먼저** 온다", () => {
    const fallback = app.indexOf("return <EternalBeamApp />");
    const at = app.indexOf("currentOpsRoute()");
    assert.ok(at > 0, "Ops 경로 판정이 없다");
    assert.ok(at < fallback, "Ops 분기가 폴백 뒤에 있어 실행되지 않는다");
    for (const route of ["dashboard", "orders", "partners", "shaker"]) {
      assert.ok(app.includes(`'${route}'`), `${route} 분기가 없다`);
    }
  });
});
