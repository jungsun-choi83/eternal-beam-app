/**
 * 편지 → 펫 연결.
 *
 * 이 한 번의 호출이 빠져 있었다. 서버에는 /letter/link-pet 이 처음부터 있었지만
 * 부르는 쪽이 없어 soul_trace_letters.pet_id 가 언제나 NULL 이었고, 그래서 결제
 * 화면의 "이 펫의 편지" 조회는 100% 실패해 아무 편지나 집는 폴백으로 떨어졌다.
 */

import assert from "node:assert/strict";
import { describe, it, beforeEach } from "node:test";
import { readFileSync } from "node:fs";

import { linkPendingSoulTraceLetter, type LetterLinkDeps } from "./soul-trace-letter-link.ts";
import { OrderApiError } from "./orders-api.ts";

const LETTER = "stl_4784_hospital";

function installDom(contentId = "") {
  const local = new Map<string, string>();
  if (contentId) local.set("eternal_beam_content_id", contentId);
  (globalThis as Record<string, unknown>).localStorage = {
    getItem: (k: string) => local.get(k) ?? null,
    setItem: (k: string, v: string) => void local.set(k, v),
    removeItem: (k: string) => void local.delete(k),
  };
  return local;
}

/** 기본 주입: 편지 하나가 대기 중이고, 토큰이 있고, 링크가 성공한다. */
function deps(over: LetterLinkDeps = {}) {
  const calls: Array<{ letterId: string; petId: string }> = [];
  let pending: { letterId: string; contentIdAtClaim: string } | null = {
    letterId: LETTER,
    contentIdAtClaim: "",
  };
  const base: LetterLinkDeps = {
    readPending: () => pending,
    clearPending: () => void (pending = null),
    getToken: async () => ({ token: "jwt", source: "supabase" as const }),
    link: async (p) => {
      calls.push({ letterId: p.letterId, petId: p.petId });
      return { letterId: p.letterId, petId: p.petId };
    },
  };
  return { deps: { ...base, ...over }, calls, isCleared: () => pending === null };
}

describe("대기 중인 편지를 새 펫에 붙인다", () => {
  beforeEach(() => installDom());

  it("5) 신규 가입 → 클레임 → 펫 생성 → **그 편지**가 그 펫에 붙는다", async () => {
    const d = deps();
    const r = await linkPendingSoulTraceLetter({ petId: "pet_new", contentId: "content_new" }, d.deps);
    assert.deepEqual(r, { state: "LINKED", letterId: LETTER, petId: "pet_new" });
    assert.deepEqual(d.calls, [{ letterId: LETTER, petId: "pet_new" }]);
  });

  it("성공하면 표식을 버린다 — 다음 업로드에 따라붙지 않는다", async () => {
    const d = deps();
    await linkPendingSoulTraceLetter({ petId: "pet_new", contentId: "content_new" }, d.deps);
    assert.ok(d.isCleared(), "표식이 남아 있다 — 펫 두 마리가 한 편지를 나눠 갖는다");

    // 두 번째 펫: 붙일 편지가 없다.
    const again = await linkPendingSoulTraceLetter(
      { petId: "pet_second", contentId: "content_second" },
      d.deps,
    );
    assert.deepEqual(again, { state: "NO_PENDING_LETTER" });
    assert.equal(d.calls.length, 1, "같은 편지를 두 번 붙였다");
  });

  it("Soul Trace 를 거치지 않은 사용자는 아무 일도 일어나지 않는다", async () => {
    const d = deps({ readPending: () => null });
    const r = await linkPendingSoulTraceLetter({ petId: "pet_x", contentId: "c" }, d.deps);
    assert.deepEqual(r, { state: "NO_PENDING_LETTER" });
    assert.equal(d.calls.length, 0);
  });
});

describe("교차 연결 방지", () => {
  beforeEach(() => installDom());

  it("2) 클레임 **이전**에 있던 펫에는 붙지 않는다", async () => {
    // 편지 B 를 가져온 뒤 예전 펫 A 의 미리보기를 다시 열기만 해도 B 가 A 에
    // 붙는다면, A 의 상자에 남의 편지가 인쇄되어 나간다.
    const d = deps({
      readPending: () => ({ letterId: LETTER, contentIdAtClaim: "content_old" }),
    });
    const r = await linkPendingSoulTraceLetter(
      { petId: "pet_old", contentId: "content_old" },
      d.deps,
    );
    assert.deepEqual(r, { state: "NOT_THIS_PET", reason: "pet-predates-claim" });
    assert.equal(d.calls.length, 0, "예전 펫에 새 편지를 붙였다");
  });

  it("클레임 **이후**에 만들어진 펫에는 붙는다", async () => {
    const d = deps({
      readPending: () => ({ letterId: LETTER, contentIdAtClaim: "content_old" }),
    });
    const r = await linkPendingSoulTraceLetter(
      { petId: "pet_new", contentId: "content_new" },
      d.deps,
    );
    assert.equal(r.state, "LINKED");
    assert.deepEqual(d.calls, [{ letterId: LETTER, petId: "pet_new" }]);
  });

  it("content_id 는 인자가 없으면 저장소에서 읽는다", async () => {
    installDom("content_old");
    const d = deps({
      readPending: () => ({ letterId: LETTER, contentIdAtClaim: "content_old" }),
    });
    const r = await linkPendingSoulTraceLetter({ petId: "pet_old" }, d.deps);
    assert.equal(r.state, "NOT_THIS_PET");
  });
});

describe("실패는 흐름을 막지 않는다", () => {
  beforeEach(() => installDom());

  it("6) 세션이 아직 없으면 기다린다 — 표식을 버리지 않는다", async () => {
    // 호출부(pet-registry / preview-screen)가 SIGNED_IN 에서 다시 부른다.
    const d = deps({
      getToken: async () => ({ token: null, source: "none" as const, reason: "no-session" as const }),
    });
    const r = await linkPendingSoulTraceLetter({ petId: "pet_new", contentId: "c" }, d.deps);
    assert.deepEqual(r, { state: "PENDING_AUTH" });
    assert.ok(!d.isCleared(), "세션 복원 전에 편지를 버렸다 — 되찾을 길이 없다");
  });

  it("일시적 실패는 표식을 남긴다 — 다음 시도에서 붙는다", async () => {
    const d = deps({
      link: async () => {
        throw new OrderApiError("UNKNOWN", "boom", 500);
      },
    });
    const r = await linkPendingSoulTraceLetter({ petId: "pet_new", contentId: "c" }, d.deps);
    assert.equal(r.state, "FAILED");
    assert.ok(!d.isCleared());
  });

  it("편지가 없거나 내 것이 아니면 표식을 버린다 — 무한 재시도 금지", async () => {
    const d = deps({
      link: async () => {
        throw new OrderApiError("LETTER_NOT_FOUND", "없다", 404);
      },
    });
    const r = await linkPendingSoulTraceLetter({ petId: "pet_new", contentId: "c" }, d.deps);
    assert.equal(r.state, "FAILED");
    assert.ok(d.isCleared(), "죽은 편지를 계속 붙이려고 서버를 때린다");
  });

  it("petId 가 없으면 아무 것도 하지 않는다", async () => {
    const d = deps();
    assert.deepEqual(
      await linkPendingSoulTraceLetter({ petId: "  ", contentId: "c" }, d.deps),
      { state: "NO_PENDING_LETTER" },
    );
    assert.equal(d.calls.length, 0);
  });
});

/**
 * 구조 고정 — **연결 호출이 다시 사라지지 않게 한다.**
 *
 * 이 버그의 본질은 로직 오류가 아니라 "부르는 쪽이 없었다"였다. 그래서 호출부가
 * 실제로 존재하는지를 소스에서 확인한다.
 */
describe("구조 고정 — 호출부가 존재한다", () => {
  it("펫이 canonical 로 확정된 직후 링크를 시도한다", () => {
    const registry = readFileSync("src/lib/pet-registry-api.ts", "utf8");
    assert.ok(
      registry.includes("linkPendingSoulTraceLetter"),
      "펫 등록 성공 지점에서 편지 연결을 부르지 않는다 — pet_id 가 다시 NULL 로 굳는다",
    );
  });

  it("클레임 성공 시 letter_id 를 저장한다", () => {
    const screen = readFileSync("src/components/memorial/soul-trace-import-screen.tsx", "utf8");
    assert.ok(
      screen.includes("saveActiveSoulTraceLetter"),
      "클레임한 letter_id 를 버리고 있다 — 어느 편지였는지 아무도 모르게 된다",
    );
  });

  it("결제 화면은 rows[0] 폴백을 쓰지 않는다", () => {
    const screen = readFileSync("src/components/memorial/physical-order-screen.tsx", "utf8");
    assert.ok(screen.includes("selectLetterForPet"), "선택 판정이 화면으로 되돌아갔다");
    // 주석은 폴백이 왜 사라졌는지 설명하므로 rows[0] 을 언급한다 — 코드만 본다.
    const code = screen
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*(\/\/|\*).*$/gm, "");
    assert.ok(!/rows\[0\]/.test(code), "임의의 rows[0] 폴백이 되살아났다");
  });
});
