/**
 * 소유자용 공유 링크 관리 — 순수 부분(요약 파싱·URL 조립)만 검증한다.
 */

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import { absoluteShareUrl, parseShareSummary } from "./shaker-share.ts";

describe("공유 URL 조립", () => {
  it("origin 과 경로를 붙인다", () => {
    assert.equal(
      absoluteShareUrl("/shaker?petId=pet_a&share=tok", "https://eternalbeam.com"),
      "https://eternalbeam.com/shaker?petId=pet_a&share=tok"
    );
  });

  it("origin 의 후행 슬래시를 중복시키지 않는다", () => {
    assert.equal(
      absoluteShareUrl("/shaker?share=tok", "https://eternalbeam.com/"),
      "https://eternalbeam.com/shaker?share=tok"
    );
  });
});

describe("목록 요약 파싱", () => {
  const base = {
    share_id: "shr_abc",
    pet_id: "pet_goya",
    pet_name: "고야",
    created_at: "2026-08-20T00:00:00+00:00",
    revoked_at: null,
    expires_at: null,
  };

  it("만료도 폐기도 없으면 활성이다", () => {
    const s = parseShareSummary(base);
    assert.equal(s.active, true);
    assert.equal(s.shareId, "shr_abc");
    assert.equal(s.petName, "고야");
  });

  it("폐기된 링크는 비활성이다", () => {
    const s = parseShareSummary({ ...base, revoked_at: "2026-08-20T01:00:00+00:00" });
    assert.equal(s.active, false);
  });

  it("만료된 링크는 비활성이다", () => {
    const s = parseShareSummary({ ...base, expires_at: "2020-01-01T00:00:00+00:00" });
    assert.equal(s.active, false);
  });

  it("미래 만료는 아직 활성이다", () => {
    const future = new Date(Date.now() + 86_400_000).toISOString();
    assert.equal(parseShareSummary({ ...base, expires_at: future }).active, true);
  });

  it("토큰 필드가 존재하지 않는다 — 서버가 저장하지 않으므로 줄 수도 없다", () => {
    const s = parseShareSummary({ ...base, token: "leaked-token" } as Record<string, unknown>);
    assert.equal((s as unknown as { token?: string }).token, undefined);
    assert.equal(JSON.stringify(s).includes("leaked-token"), false);
  });

  it("빈 이름은 null 로 정규화한다", () => {
    assert.equal(parseShareSummary({ ...base, pet_name: "  " }).petName, null);
  });
});
