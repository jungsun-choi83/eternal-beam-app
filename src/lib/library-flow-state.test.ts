import { strict as assert } from "node:assert";
import { afterEach, beforeEach, test } from "node:test";

import {
  clearLibraryFlowState,
  hasLibraryFlowMarker,
  readLibraryFlowState,
  resolveLibraryFlowRestore,
  saveLibraryFlowState,
} from "./library-flow-state.ts";

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

beforeEach(() => {
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value: new MemoryStorage(),
  });
});

afterEach(() => {
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value: previousSession,
  });
});

test("저장 후 그대로 읽힌다", () => {
  saveLibraryFlowState({ step: "themes", petId: "pet_1", motionId: "BREATHING", themeId: 3 });
  assert.deepEqual(readLibraryFlowState(), {
    step: "themes",
    petId: "pet_1",
    motionId: "BREATHING",
    themeId: 3,
  });
  assert.equal(hasLibraryFlowMarker(), true);
});

test("petId 가 없으면 저장하지 않는다(아직 모션을 확정하지 않은 library 단계)", () => {
  saveLibraryFlowState({ step: "library", petId: "", motionId: null, themeId: null });
  assert.equal(readLibraryFlowState(), null);
});

test("clearLibraryFlowState 이후에는 표식이 없다", () => {
  saveLibraryFlowState({ step: "themes", petId: "pet_1", motionId: null, themeId: null });
  clearLibraryFlowState();
  assert.equal(readLibraryFlowState(), null);
});

test("모양이 어긋난 값은 신뢰하지 않는다", () => {
  sessionStorage.setItem("eternal_beam_library_flow_v1", JSON.stringify({ step: "not-a-step", petId: "pet_1" }));
  assert.equal(readLibraryFlowState(), null);
});

test("resolveLibraryFlowRestore: 표식이 없으면 none", () => {
  assert.deepEqual(resolveLibraryFlowRestore(null, [], []), { status: "none" });
});

test("resolveLibraryFlowRestore: library 단계 표식은 복원할 것이 없다(단일 그리드)", () => {
  const saved = { step: "library" as const, petId: "pet_1", motionId: null, themeId: null };
  assert.deepEqual(resolveLibraryFlowRestore(saved, [{ petId: "pet_1" }], []), { status: "none" });
});

test("resolveLibraryFlowRestore: 레지스트리 조회 전에는 pending", () => {
  const saved = { step: "themes" as const, petId: "pet_1", motionId: "BREATHING", themeId: null };
  assert.deepEqual(resolveLibraryFlowRestore(saved, null, null), { status: "pending" });
});

test("resolveLibraryFlowRestore: 표식의 펫이 레지스트리에 없으면 restore-library", () => {
  const saved = { step: "themes" as const, petId: "pet_gone", motionId: "BREATHING", themeId: 1 };
  assert.deepEqual(resolveLibraryFlowRestore(saved, [{ petId: "pet_1" }], []), {
    status: "restore-library",
  });
});

test("resolveLibraryFlowRestore: 펫은 있는데 모션 조회가 아직이면 pending", () => {
  const saved = { step: "themes" as const, petId: "pet_1", motionId: "BREATHING", themeId: 1 };
  assert.deepEqual(resolveLibraryFlowRestore(saved, [{ petId: "pet_1" }], null), {
    status: "pending",
  });
});

test("resolveLibraryFlowRestore: themes/preview 는 모션이 더 이상 없으면 restore-library", () => {
  const saved = { step: "preview" as const, petId: "pet_1", motionId: "GONE", themeId: 2 };
  assert.deepEqual(
    resolveLibraryFlowRestore(saved, [{ petId: "pet_1" }], [{ motionId: "BREATHING" }]),
    { status: "restore-library" }
  );
});

test("resolveLibraryFlowRestore: 펫·모션이 모두 지금도 유효하면 그대로 되살린다", () => {
  const saved = { step: "preview" as const, petId: "pet_1", motionId: "BREATHING", themeId: 2 };
  assert.deepEqual(
    resolveLibraryFlowRestore(saved, [{ petId: "pet_1" }], [{ motionId: "BREATHING" }]),
    { status: "restore", petId: "pet_1", motionId: "BREATHING", step: "preview", themeId: 2 }
  );
});
