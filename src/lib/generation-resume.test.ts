/**
 * Phase 7G — 새로고침 안전 재개 마커/해석 (generation-resume.ts).
 *
 * 확인된 문제의 회귀 가드: 확인을 누른 적 없는 content_id 는 절대 "재개
 * 대상"이 아니다(새 사용자 동작 불변). 확인을 누른 적 있는 content_id 는
 * sessionStorage 가 지워져도(새 탭) durable localStorage 결속으로 찾는다.
 */
import { strict as assert } from "node:assert";
import { afterEach, beforeEach, test } from "node:test";

import {
  __resetActiveGenerationForTest,
  clearActiveGeneration,
  hasActiveGeneration,
  markGenerationStarted,
  readActiveGeneration,
  resolveResumeContentId,
} from "./generation-resume.ts";
import { __resetPendingCutoutForTest, setPendingCutout } from "./pending-generation.ts";

class MemoryStorage {
  private data = new Map<string, string>();
  getItem(key: string) {
    return this.data.get(key) ?? null;
  }
  setItem(key: string, value: string) {
    this.data.set(key, String(value));
  }
  removeItem(key: string) {
    this.data.delete(key);
  }
  clear() {
    this.data.clear();
  }
  key(index: number) {
    return [...this.data.keys()][index] ?? null;
  }
  get length() {
    return this.data.size;
  }
}

const previousSession = globalThis.sessionStorage;
const previousLocal = globalThis.localStorage;

beforeEach(() => {
  for (const name of ["sessionStorage", "localStorage"]) {
    Object.defineProperty(globalThis, name, {
      configurable: true,
      value: new MemoryStorage(),
    });
  }
  __resetPendingCutoutForTest();
  __resetActiveGenerationForTest();
});

afterEach(() => {
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value: previousSession,
  });
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: previousLocal,
  });
});

test("확인 전에는 재개 대상이 아니다 — 마커가 없으면 null", () => {
  assert.equal(readActiveGeneration("cid-never-confirmed"), null);
  assert.equal(hasActiveGeneration("cid-never-confirmed"), false);
});

test("확인이 눌리면 durable(localStorage) 마커가 남고, 재개 시점에 그대로 읽힌다", () => {
  markGenerationStarted({ contentId: "cid-1", petId: "pet_cid-1", runId: "run-1" });

  assert.equal(hasActiveGeneration("cid-1"), true);
  const record = readActiveGeneration("cid-1");
  assert.equal(record?.contentId, "cid-1");
  assert.equal(record?.petId, "pet_cid-1");
  assert.equal(record?.runId, "run-1");
  assert.equal(record?.motionId, "BREATHING");

  // sessionStorage 가 아니라 localStorage 에 남는다 — 탭을 새로 열어도 산다.
  assert.equal(sessionStorage.getItem("eternal_beam_active_generation_v1"), null);
  assert.ok(localStorage.getItem("eternal_beam_active_generation_v1"));
});

test("서로 다른 content_id(펫)는 마커를 공유하지 않는다", () => {
  markGenerationStarted({ contentId: "cid-a", petId: "pet_cid-a", runId: "run-a" });
  markGenerationStarted({ contentId: "cid-b", petId: "pet_cid-b", runId: "run-b" });

  assert.equal(readActiveGeneration("cid-a")?.runId, "run-a");
  assert.equal(readActiveGeneration("cid-b")?.runId, "run-b");

  clearActiveGeneration("cid-a");
  assert.equal(hasActiveGeneration("cid-a"), false);
  assert.equal(hasActiveGeneration("cid-b"), true, "다른 펫의 마커까지 지우면 안 된다");
});

test("resolveResumeContentId: 같은 탭에서는 sessionStorage(pending cutout meta) 우선", () => {
  setPendingCutout(null, "cid-session", "https://cdn/thumb.png");
  localStorage.setItem("eternal_beam_content_id", "cid-stale-local");

  assert.equal(resolveResumeContentId(), "cid-session");
});

test("resolveResumeContentId: sessionStorage 가 없으면 durable localStorage 로 떨어진다(새 탭)", () => {
  localStorage.setItem("eternal_beam_current_content_id", "cid-from-local");

  assert.equal(resolveResumeContentId(), "cid-from-local");
});

test("resolveResumeContentId: eternal_beam_current_content_id 가 eternal_beam_content_id 보다 우선", () => {
  localStorage.setItem("eternal_beam_content_id", "cid-legacy");
  localStorage.setItem("eternal_beam_current_content_id", "cid-current");

  assert.equal(resolveResumeContentId(), "cid-current");
});

test("resolveResumeContentId: 아무 흔적도 없으면 null — 새 사용자 동작 불변", () => {
  assert.equal(resolveResumeContentId(), null);
});

test("clearActiveGeneration: 마커가 없는 content_id 를 지워도 안전하다(no-op)", () => {
  clearActiveGeneration("cid-never-existed");
  assert.equal(hasActiveGeneration("cid-never-existed"), false);
});

test("Phase 9 — 48시간이 지난 마커는 유령 취급하고 조용히 지운다", () => {
  const stale = new Date(Date.now() - 49 * 60 * 60 * 1000).toISOString();
  localStorage.setItem(
    "eternal_beam_active_generation_v1",
    JSON.stringify({
      "cid-ghost": {
        contentId: "cid-ghost",
        petId: "pet_cid-ghost",
        motionId: "BREATHING",
        startedAt: stale,
      },
    })
  );

  assert.equal(hasActiveGeneration("cid-ghost"), false);
  assert.equal(readActiveGeneration("cid-ghost"), null);
  // 읽는 순간 저장소에서도 지워진다 — 다음 조회마다 다시 유령을 걸러내지 않는다.
  assert.equal(localStorage.getItem("eternal_beam_active_generation_v1"), "{}");
});

test("Phase 9 — 48시간이 안 지난 마커는 그대로 살아있다", () => {
  const recent = new Date(Date.now() - 60 * 60 * 1000).toISOString();
  localStorage.setItem(
    "eternal_beam_active_generation_v1",
    JSON.stringify({
      "cid-recent": {
        contentId: "cid-recent",
        petId: "pet_cid-recent",
        motionId: "BREATHING",
        startedAt: recent,
      },
    })
  );

  assert.equal(hasActiveGeneration("cid-recent"), true);
  assert.equal(readActiveGeneration("cid-recent")?.contentId, "cid-recent");
});
