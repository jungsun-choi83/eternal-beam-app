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
  const started = beginPhase1Intake(() => "stable-content");
  assert.deepEqual(started, {
    contentId: "stable-content",
    petId: "pet_stable-content",
  });
  assert.deepEqual(readPhase1Intake(), started);
  assert.deepEqual(requirePhase1Intake(), started);
});

test("a retry reuses the upload identity; a new upload gets a new one", () => {
  const first = beginPhase1Intake(() => "first");
  assert.deepEqual(requirePhase1Intake(), first);

  const second = beginPhase1Intake(() => "second");
  assert.notDeepEqual(second, first);
  assert.deepEqual(readPhase1Intake(), second);
});

test("invalid or cleared session cannot create a mismatched pet id", () => {
  sessionStorage.setItem(
    "eternal_beam_phase1_intake_v1",
    JSON.stringify({ contentId: "cid", petId: "pet_someone-else" }),
  );
  assert.equal(readPhase1Intake(), null);
  clearPhase1Intake();
  assert.equal(readPhase1Intake(), null);
});

