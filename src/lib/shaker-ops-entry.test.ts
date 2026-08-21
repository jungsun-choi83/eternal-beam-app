/**
 * 운영 콘솔 진입 — 경로 감지와 화면 상태.
 *
 * ⚠️ 여기 있는 어떤 것도 **보안 경계가 아니다.** 인가는 서버가 한다
 * (JWT + SHAKER_OPS_USER_IDS). 이 테스트가 지키는 것은 UX 다: 권한이 없는
 * 사람에게 빈 화면 대신 이유를 보여 준다.
 */

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import { deriveOpsPhase, isOpsShakerPath, OPS_SHAKER_PATH } from "./shaker-ops-entry.ts";
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
