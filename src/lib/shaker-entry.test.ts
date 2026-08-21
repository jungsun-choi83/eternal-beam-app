/**
 * Shaker 진입 파싱 — QR 이 인쇄된 뒤에는 고칠 수 없다는 전제로 관대하게 읽는다.
 */

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import {
  isPlausibleShareToken,
  isShakerPath,
  readShakerParams,
  resolveShakerEntry,
} from "./shaker-entry.ts";

const GOOD = "kJ3nQ7xR2mVpL8sT4wYzB6cD9fG1hN5aE0iU7oP3rSw";

describe("Shaker 경로 감지", () => {
  it("/shaker 와 후행 슬래시를 모두 인정한다", () => {
    assert.equal(isShakerPath("/shaker"), true);
    assert.equal(isShakerPath("/shaker/"), true);
    assert.equal(isShakerPath("/shaker//"), true);
  });

  it("비슷하지만 다른 경로는 인정하지 않는다", () => {
    for (const p of ["/", "/forest", "/shaker/extra", "/shakers", "/billing/success"]) {
      assert.equal(isShakerPath(p), false, p);
    }
  });
});

describe("파라미터 파싱", () => {
  it("petId 와 share 를 읽는다", () => {
    const r = readShakerParams(`?petId=pet_abc&share=${GOOD}`);
    assert.equal(r.petId, "pet_abc");
    assert.equal(r.token, GOOD);
  });

  it("pet_id 표기도 받는다 — QR 생성 도구의 표기가 아직 확정되지 않았다", () => {
    const r = readShakerParams(`?pet_id=pet_abc&share=${GOOD}`);
    assert.equal(r.petId, "pet_abc");
  });

  it("없는 값은 null 이다 (빈 문자열이 아니다)", () => {
    const r = readShakerParams("");
    assert.equal(r.petId, null);
    assert.equal(r.token, null);
  });

  it("공백만 있는 값도 null 로 본다", () => {
    const r = readShakerParams("?petId=%20%20&share=%20");
    assert.equal(r.petId, null);
    assert.equal(r.token, null);
  });

  it("파라미터 순서와 추가 파라미터에 영향받지 않는다", () => {
    const r = readShakerParams(`?utm_source=letter&share=${GOOD}&petId=pet_abc&v=2`);
    assert.equal(r.token, GOOD);
    assert.equal(r.petId, "pet_abc");
  });
});

describe("토큰 형식 검사", () => {
  it("서버가 발급한 형태를 통과시킨다", () => {
    assert.equal(isPlausibleShareToken(GOOD), true);
  });

  it("잘렸거나 이상한 토큰을 거른다", () => {
    for (const bad of ["", "  ", "short", "a".repeat(19), "a".repeat(129), "has space", "has/slash", "has+plus"]) {
      assert.equal(isPlausibleShareToken(bad), false, JSON.stringify(bad));
    }
  });

  it("형식 검사는 보안이 아니다 — 형식만 맞으면 통과한다", () => {
    // 서버가 해시 조회로 진짜 판정을 한다. 여기서 통과하는 것은 정상이다.
    assert.equal(isPlausibleShareToken("a".repeat(43)), true);
  });
});

describe("진입 상태 판정", () => {
  it("토큰이 있으면 ready — petId 는 있어도 없어도 된다", () => {
    const withPet = resolveShakerEntry({ petId: "pet_abc", token: GOOD });
    assert.deepEqual(withPet, { kind: "ready", token: GOOD, petId: "pet_abc" });

    const withoutPet = resolveShakerEntry({ petId: null, token: GOOD });
    assert.equal(withoutPet.kind, "ready");
  });

  it("토큰이 없으면 missing-token", () => {
    assert.equal(resolveShakerEntry({ petId: "pet_abc", token: null }).kind, "missing-token");
  });

  it("형식이 틀리면 malformed-token — missing 과 구분한다", () => {
    // 구분하는 이유: 문구가 달라야 한다. "링크가 없습니다" vs "링크가 손상됐습니다".
    assert.equal(resolveShakerEntry({ petId: null, token: "abc" }).kind, "malformed-token");
  });

  it("petId 만 있고 토큰이 없으면 절대 ready 가 되지 않는다", () => {
    // 핵심 회귀: petId 만으로는 어떤 경우에도 펫이 열리지 않는다.
    const state = resolveShakerEntry({ petId: "pet_victim", token: null });
    assert.notEqual(state.kind, "ready");
  });
});
