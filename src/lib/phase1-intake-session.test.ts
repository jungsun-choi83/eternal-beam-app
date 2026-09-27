import { strict as assert } from "node:assert";
import { afterEach, beforeEach, test } from "node:test";

import {
  beginPhase1Intake,
  clearPhase1Intake,
  readPhase1Intake,
  requirePhase1Intake,
} from "./phase1-intake-session.ts";

class MemoryStorage {
  private data = new Map<string, string>();
  getItem(key: string) { return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { this.data.set(key, String(value)); }
  removeItem(key: string) { this.data.delete(key); }
  clear() { this.data.clear(); }
  key(index: number) { return [...this.data.keys()][index] ?? null; }
  get length() { return this.data.size; }
}

const previous = globalThis.sessionStorage;
const PET_1 = "pet_slot_1";
const PET_2 = "pet_slot_2";

beforeEach(() => {
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value: new MemoryStorage(),
  });
});

afterEach(() => {
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value: previous,
  });
});

test("one upload creates one stable content_id and derived pet_id", () => {
  const started = beginPhase1Intake(PET_1, () => "stable-content");
  assert.deepEqual(started, {
    contentId: "stable-content",
    petId: "pet_stable-content",
  });
  assert.deepEqual(readPhase1Intake(PET_1), started);
  assert.deepEqual(requirePhase1Intake(PET_1), started);
});

test("a retry reuses the upload identity; a new upload gets a new one", () => {
  const first = beginPhase1Intake(PET_1, () => "first");
  assert.deepEqual(requirePhase1Intake(PET_1), first);

  const second = beginPhase1Intake(PET_1, () => "second");
  assert.notDeepEqual(second, first);
  assert.deepEqual(readPhase1Intake(PET_1), second);
});

test("invalid or cleared session cannot create a mismatched pet id", () => {
  sessionStorage.setItem(
    "eternal_beam_phase1_intake_v1",
    JSON.stringify({ [PET_1]: { contentId: "cid", petId: "pet_someone-else" } }),
  );
  assert.equal(readPhase1Intake(PET_1), null);
  clearPhase1Intake(PET_1);
  assert.equal(readPhase1Intake(PET_1), null);
});

test("two pets never share a content_id, and pet 2 does not disturb pet 1", () => {
  const one = beginPhase1Intake(PET_1, () => "content-one");
  const two = beginPhase1Intake(PET_2, () => "content-two");

  assert.notEqual(one.contentId, two.contentId);
  assert.notEqual(one.petId, two.petId);
  // 펫 2를 발급한 뒤에도 펫 1의 칸은 그대로다 — 예전에는 전역 한 칸이라 덮였다.
  assert.deepEqual(readPhase1Intake(PET_1), one);
  assert.deepEqual(requirePhase1Intake(PET_1), one);
});

test("an empty slot never falls back to another pet's identity", () => {
  const one = beginPhase1Intake(PET_1, () => "content-one");
  const fresh = requirePhase1Intake(PET_2);

  assert.notEqual(fresh.contentId, one.contentId);
  assert.notEqual(fresh.petId, one.petId);
  assert.deepEqual(readPhase1Intake(PET_2), fresh);
});

test("a stale identity handed in from another pet cannot overwrite this slot", () => {
  const one = beginPhase1Intake(PET_1, () => "content-one");
  const two = beginPhase1Intake(PET_2, () => "content-two");

  // 화면 전환 타이밍 때문에 펫 1의 신원이 펫 2의 실행으로 흘러들어도,
  // 저장된 이 슬롯의 값이 이긴다.
  assert.deepEqual(requirePhase1Intake(PET_2, one), two);
});

test("clearing one slot leaves the other pet intact; clearing all wipes both", () => {
  const one = beginPhase1Intake(PET_1, () => "content-one");
  beginPhase1Intake(PET_2, () => "content-two");

  clearPhase1Intake(PET_2);
  assert.equal(readPhase1Intake(PET_2), null);
  assert.deepEqual(readPhase1Intake(PET_1), one);

  clearPhase1Intake();
  assert.equal(readPhase1Intake(PET_1), null);
});
