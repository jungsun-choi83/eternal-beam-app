import { strict as assert } from "node:assert";
import { afterEach, beforeEach, test } from "node:test";

import {
  beginPhase1Intake,
  clearPhase1Intake,
  identityForAddedPhotos,
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

// ── 빈 자리 복구 (Stage 1c 후속) ─────────────────────────────────────────────

test("an empty slot always gets a NEW pet identity, even when a locked pet's identity is left on it", () => {
  // 새로고침 뒤의 모습: 사진은 복원되지 않아 자리가 비었고, 생성이 시작된(잠긴)
  // 아이의 신원만 화면 상태와 저장소에 남아 있다.
  const lockedPet = beginPhase1Intake(PET_1, () => "locked-content");
  assert.deepEqual(readPhase1Intake(PET_1), lockedPet);

  const fresh = identityForAddedPhotos(PET_1, 0, lockedPet, () => "fresh-content");

  assert.notEqual(fresh.petId, lockedPet.petId);
  assert.notEqual(fresh.contentId, lockedPet.contentId);
  assert.deepEqual(fresh, { contentId: "fresh-content", petId: "pet_fresh-content" });
  // 저장소의 신원도 새 것으로 바뀐다 — 처리 화면(requirePhase1Intake)은 저장된
  // 값을 따르므로, 업로드는 잠긴 아이가 아니라 새 아이 앞으로 간다.
  assert.deepEqual(readPhase1Intake(PET_1), fresh);
  assert.deepEqual(requirePhase1Intake(PET_1, lockedPet), fresh);
});

test("an empty slot gets a new identity with the default generator too", () => {
  const lockedPet = beginPhase1Intake(PET_1);
  const fresh = identityForAddedPhotos(PET_1, 0, lockedPet);
  assert.notEqual(fresh.petId, lockedPet.petId);
  assert.equal(fresh.petId, `pet_${fresh.contentId}`);
});

test("adding to a slot that already has photos keeps the same pet", () => {
  const pet = beginPhase1Intake(PET_1, () => "same-content");
  assert.deepEqual(identityForAddedPhotos(PET_1, 2, pet, () => "never-used"), pet);
  // 화면 상태에 신원이 없으면 저장된 값을 쓴다.
  assert.deepEqual(identityForAddedPhotos(PET_1, 1, null, () => "never-used"), pet);
  // 다른 자리는 건드리지 않는다.
  assert.equal(readPhase1Intake(PET_2), null);
});
