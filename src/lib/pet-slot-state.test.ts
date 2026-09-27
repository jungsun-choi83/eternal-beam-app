import { strict as assert } from "node:assert";
import { afterEach, beforeEach, test } from "node:test";

import {
  MAX_PET_SLOTS,
  capturePetSlotState,
  clearAllPetSlotState,
  clearPetSlotState,
  petSlotIdForIndex,
  readActivePetSlotIndex,
  readPetSlotSnapshot,
  switchPetSlotState,
  writeActivePetSlotIndex,
  writeActivePetSlotSnapshot,
} from "./pet-slot-state.ts";
import {
  __resetPendingCutoutForTest,
  getActivePendingCutoutSlotId,
  readStoredPipeline,
} from "./pending-generation.ts";

class MemoryStorage {
  private data = new Map<string, string>();
  getItem(key: string) { return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { this.data.set(key, String(value)); }
  removeItem(key: string) { this.data.delete(key); }
  clear() { this.data.clear(); }
  key(index: number) { return [...this.data.keys()][index] ?? null; }
  get length() { return this.data.size; }
}

const previousSession = globalThis.sessionStorage;
const previousLocal = globalThis.localStorage;

const PET_1 = petSlotIdForIndex(0);
const PET_2 = petSlotIdForIndex(1);

const PIPELINE_KEY = "eternal_beam_pipeline_v1";
const CONTENT_KEY = "eternal_beam_content_id";

function writePipeline(contentId: string, idleUrl: string) {
  sessionStorage.setItem(
    PIPELINE_KEY,
    JSON.stringify({ content_id: contentId, idle_video_url: idleUrl }),
  );
  localStorage.setItem(CONTENT_KEY, contentId);
}

function activeContentId(): string | null {
  return readStoredPipeline()?.content_id ?? null;
}

beforeEach(() => {
  for (const name of ["sessionStorage", "localStorage"]) {
    Object.defineProperty(globalThis, name, {
      configurable: true,
      value: new MemoryStorage(),
    });
  }
  __resetPendingCutoutForTest();
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

test("slot ids come from the seat, not the clock", () => {
  assert.equal(petSlotIdForIndex(0), "pet_slot_1");
  assert.equal(petSlotIdForIndex(2), "pet_slot_3");
  assert.equal(petSlotIdForIndex(0), petSlotIdForIndex(0));
});

test("switching to a fresh pet empties the active pipeline instead of inheriting", () => {
  writePipeline("content-one", "https://cdn/one_idle.mp4");

  switchPetSlotState(PET_1, PET_2);

  assert.equal(sessionStorage.getItem(PIPELINE_KEY), null);
  assert.equal(localStorage.getItem(CONTENT_KEY), null);
  assert.equal(getActivePendingCutoutSlotId(), PET_2);
});

test("pet 1 → pet 2 → pet 1 restores pet 1's own pipeline and content id", () => {
  writePipeline("content-one", "https://cdn/one_idle.mp4");
  switchPetSlotState(PET_1, PET_2);

  writePipeline("content-two", "https://cdn/two_idle.mp4");
  switchPetSlotState(PET_2, PET_1);

  assert.equal(activeContentId(), "content-one");
  assert.equal(localStorage.getItem(CONTENT_KEY), "content-one");

  // 그리고 펫 2는 사라지지 않았다.
  switchPetSlotState(PET_1, PET_2);
  assert.equal(activeContentId(), "content-two");
  assert.equal(localStorage.getItem(CONTENT_KEY), "content-two");
});

test("two pets never share a content_id through the active projection", () => {
  writePipeline("content-one", "https://cdn/one_idle.mp4");
  capturePetSlotState(PET_1);
  switchPetSlotState(PET_1, PET_2);
  writePipeline("content-two", "https://cdn/two_idle.mp4");
  capturePetSlotState(PET_2);

  switchPetSlotState(PET_2, PET_1);
  const first = activeContentId();
  switchPetSlotState(PET_1, PET_2);
  const second = activeContentId();

  assert.equal(first, "content-one");
  assert.equal(second, "content-two");
  assert.notEqual(first, second);
});

test("a reused seat starts empty once its archive is cleared", () => {
  writePipeline("content-one", "https://cdn/one_idle.mp4");
  switchPetSlotState(PET_1, PET_2);
  writePipeline("content-two", "https://cdn/two_idle.mp4");
  capturePetSlotState(PET_2);

  clearPetSlotState(PET_2);
  switchPetSlotState(PET_1, PET_2);
  assert.equal(activeContentId(), null);
});

test("reset wipes every pet's archive and the active projection", () => {
  writePipeline("content-one", "https://cdn/one_idle.mp4");
  capturePetSlotState(PET_1);

  clearAllPetSlotState();

  assert.equal(sessionStorage.getItem(PIPELINE_KEY), null);
  assert.equal(localStorage.getItem(CONTENT_KEY), null);
  switchPetSlotState(PET_2, PET_1);
  assert.equal(activeContentId(), null);
});

// ── 새로고침 복원 ───────────────────────────────────────────────────────────
//
// 아래 테스트들은 EternalBeamApp 의 복원 규칙을 저장소 수준에서 재현한다:
//   · 활성 자리는 살아 있는 칸이, 나머지는 보관함이 답이다
//   · 자리는 이어져 있다 — 중간이 비면 거기서 끝난다
//   · 자리 하나를 비우는 것은 그 자리만 비운다

/**
 * 한 마리분의 화면 상태를 그 자리의 활성 칸으로 적고 보관한다.
 *
 * 스냅샷은 EternalBeamApp.serializePetSlot 과 같은 모양이다 — **내용이 아니라
 * 주소와 장수만** 담는다.
 */
function stagePet(index: number, imageUrls: string[], cutoutUrl: string | null) {
  writeActivePetSlotSnapshot(
    JSON.stringify({ imageCount: imageUrls.length, imageUrls, cutoutUrl }),
  );
  writePipeline(`content-${index + 1}`, `https://cdn/${index + 1}_idle.mp4`);
  localStorage.setItem("eternal_beam_theme_id", String(index + 10));
  localStorage.setItem("eternal_beam_theme_key", `theme-${index + 1}`);
  localStorage.setItem("eternal_beam_nfc_payload", `nfc-${index + 1}`);
  localStorage.setItem("eternal_beam_canonical_scene_v1", `scene-${index + 1}`);
  localStorage.setItem("eternal_beam_current_video_id", `video-${index + 1}`);
  localStorage.setItem("eternal_beam_pet_binding", `binding-${index + 1}`);
  capturePetSlotState(petSlotIdForIndex(index));
}

/** EternalBeamApp.restorePetSlots 와 같은 규칙. */
function restoreSlots(activeIndex: number): Array<{ images: string[]; cutout: string | null }> {
  const slots: Array<{ images: string[]; cutout: string | null }> = [];
  for (let index = 0; index < MAX_PET_SLOTS; index += 1) {
    const raw = readPetSlotSnapshot(petSlotIdForIndex(index), index === activeIndex);
    if (!raw) break;
    const parsed = JSON.parse(raw) as {
      imageCount?: number;
      imageUrls?: string[];
      cutoutUrl?: string | null;
    };
    const urls = parsed.imageUrls ?? [];
    slots.push({
      images: urls.length === (parsed.imageCount ?? 0) ? urls : [],
      cutout: parsed.cutoutUrl ?? null,
    });
  }
  return slots;
}

/** 세 마리를 순서대로 채운다 — 앱이 자리를 늘리는 순서 그대로. */
function stageThreePets() {
  stagePet(0, ["https://cdn/one-a.png", "https://cdn/one-b.png"], "https://cdn/cut-one.png");
  switchPetSlotState(petSlotIdForIndex(0), petSlotIdForIndex(1));
  stagePet(1, ["https://cdn/two-a.png"], "https://cdn/cut-two.png");
  switchPetSlotState(petSlotIdForIndex(1), petSlotIdForIndex(2));
  stagePet(2, ["https://cdn/three-a.png", "https://cdn/three-b.png", "https://cdn/three-c.png"], null);
  writeActivePetSlotIndex(2);
}

test("pets 1–3 survive a reload, each with its own images and cutout", () => {
  stageThreePets();

  const activeIndex = readActivePetSlotIndex();
  assert.equal(activeIndex, 2);

  const restored = restoreSlots(activeIndex);
  assert.equal(restored.length, 3);
  assert.deepEqual(restored[0].images, [
    "https://cdn/one-a.png",
    "https://cdn/one-b.png",
  ]);
  assert.deepEqual(restored[1].images, ["https://cdn/two-a.png"]);
  assert.equal(restored[2].images.length, 3);
  assert.equal(restored[0].cutout, "https://cdn/cut-one.png");
  assert.equal(restored[1].cutout, "https://cdn/cut-two.png");
  assert.equal(restored[2].cutout, null);
});

test("a reload restores each pet's own pipeline, not the last one processed", () => {
  stageThreePets();

  // 복원 직후 활성 자리(펫 3)의 파이프라인이 살아 있는 칸에 그대로 있다.
  assert.equal(activeContentId(), "content-3");

  switchPetSlotState(petSlotIdForIndex(2), petSlotIdForIndex(0));
  assert.equal(activeContentId(), "content-1");
  switchPetSlotState(petSlotIdForIndex(0), petSlotIdForIndex(1));
  assert.equal(activeContentId(), "content-2");
});

test("no pet's snapshot leaks into another seat across a reload", () => {
  stageThreePets();

  const restored = restoreSlots(readActivePetSlotIndex());
  const seen = restored.flatMap((slot) => slot.images);
  assert.equal(new Set(seen).size, seen.length, "같은 사진이 두 자리에 나타난다");
  for (const image of restored[0].images) {
    assert.ok(!restored[1].images.includes(image));
    assert.ok(!restored[2].images.includes(image));
  }
});

test("restore stops at the first empty seat and never exceeds 3 pets", () => {
  stageThreePets();
  // 2번 자리가 비면 3번은 되살아나지 않는다 — 자리 번호가 어긋나면 그 자체가
  // 남의 파이프라인을 읽는 경로다.
  clearPetSlotState(petSlotIdForIndex(1));
  assert.equal(restoreSlots(0).length, 1);

  // 세 자리가 모두 차 있어도 넷째는 없다.
  stageThreePets();
  assert.ok(restoreSlots(readActivePetSlotIndex()).length <= MAX_PET_SLOTS);
  assert.equal(readPetSlotSnapshot(petSlotIdForIndex(3), false), null);
});

test("clearing one seat leaves the other two untouched", () => {
  stageThreePets();

  clearPetSlotState(petSlotIdForIndex(2));

  assert.equal(readPetSlotSnapshot(petSlotIdForIndex(2), false), null);
  const first = restoreSlots(0);
  assert.equal(first.length, 2);
  assert.deepEqual(first[1].images, ["https://cdn/two-a.png"]);
});

test("a failed snapshot write removes the key instead of leaving a stale one", () => {
  writeActivePetSlotSnapshot(JSON.stringify({ imageCount: 1, imageUrls: ["https://cdn/old.png"] }));
  assert.ok(readPetSlotSnapshot(petSlotIdForIndex(0), true));

  const store = sessionStorage as unknown as { setItem: (k: string, v: string) => void };
  const realSet = store.setItem;
  store.setItem = (key: string) => {
    if (key === "eternal_beam_pet_slot_snapshot_v1") throw new Error("QuotaExceededError");
    return realSet.call(sessionStorage, key, "");
  };
  try {
    writeActivePetSlotSnapshot(JSON.stringify({ imageCount: 1, imageUrls: ["https://cdn/new.png"] }));
  } finally {
    store.setItem = realSet;
  }

  assert.equal(readPetSlotSnapshot(petSlotIdForIndex(0), true), null);
});

test("reset clears the seat cursor as well as every archive", () => {
  stageThreePets();
  clearAllPetSlotState();

  assert.equal(readActivePetSlotIndex(), 0);
  assert.equal(restoreSlots(0).length, 0);
});

// ── 테마 / NFC / 장면 / 영상 / 바인딩 격리 ──────────────────────────────────

test("switching pets restores that pet's theme, NFC, scene, video and binding", () => {
  stageThreePets();

  switchPetSlotState(petSlotIdForIndex(2), petSlotIdForIndex(0));
  assert.equal(localStorage.getItem("eternal_beam_theme_id"), "10");
  assert.equal(localStorage.getItem("eternal_beam_theme_key"), "theme-1");
  assert.equal(localStorage.getItem("eternal_beam_nfc_payload"), "nfc-1");
  assert.equal(localStorage.getItem("eternal_beam_canonical_scene_v1"), "scene-1");
  assert.equal(localStorage.getItem("eternal_beam_current_video_id"), "video-1");
  assert.equal(localStorage.getItem("eternal_beam_pet_binding"), "binding-1");

  switchPetSlotState(petSlotIdForIndex(0), petSlotIdForIndex(1));
  assert.equal(localStorage.getItem("eternal_beam_theme_id"), "11");
  assert.equal(localStorage.getItem("eternal_beam_nfc_payload"), "nfc-2");
  assert.equal(localStorage.getItem("eternal_beam_canonical_scene_v1"), "scene-2");
  assert.equal(localStorage.getItem("eternal_beam_current_video_id"), "video-2");
  assert.equal(localStorage.getItem("eternal_beam_pet_binding"), "binding-2");
});

test("a pet with no theme/NFC of its own inherits nothing from the previous pet", () => {
  stagePet(0, ["https://cdn/one-a.png"], null);

  switchPetSlotState(petSlotIdForIndex(0), petSlotIdForIndex(1));

  for (const key of [
    "eternal_beam_theme_id",
    "eternal_beam_theme_key",
    "eternal_beam_nfc_payload",
    "eternal_beam_canonical_scene_v1",
    "eternal_beam_current_video_id",
    "eternal_beam_hologram_video_id",
    "eternal_beam_pet_binding",
    "eternal_beam_pet_id",
    "eternal_beam_custom_bg_video_url",
  ]) {
    assert.equal(localStorage.getItem(key), null, `${key} 가 앞 펫에서 넘어왔다`);
  }
});

test("the active pet's raw media is projected but never archived for three pets", () => {
  stagePet(0, ["https://cdn/one-a.png"], null);
  localStorage.setItem("eternal_beam_main_photo", "data:image/png;base64,HEAVY");
  localStorage.setItem("eternal_beam_media_type", "image");
  capturePetSlotState(petSlotIdForIndex(0));

  const archive = sessionStorage.getItem("eternal_beam_pet_slot_archive_v1") ?? "";
  assert.ok(!archive.includes("HEAVY"), "원본 사진이 보관함에 들어갔다");
  assert.ok(!archive.includes("data:"), "보관함에 data: URL 이 들어갔다");

  // 전환하면 파생 뷰는 비워진다 — 다음 펫이 앞 펫의 사진을 배경으로 쓰지 않는다.
  switchPetSlotState(petSlotIdForIndex(0), petSlotIdForIndex(1));
  assert.equal(localStorage.getItem("eternal_beam_main_photo"), null);
  assert.equal(localStorage.getItem("eternal_beam_media_type"), null);
});

test("a pipeline carrying a data: cutout is archived lean, keeping content_id", () => {
  const heavy = "data:image/png;base64," + "A".repeat(400_000);
  sessionStorage.setItem(
    "eternal_beam_pipeline_v1",
    JSON.stringify({
      content_id: "content-heavy",
      idle_video_url: "https://cdn/heavy_idle.mp4",
      cutout_display_url: heavy,
      dog_only_nobg_url: heavy,
    }),
  );
  capturePetSlotState(petSlotIdForIndex(0));

  const archive = sessionStorage.getItem("eternal_beam_pet_slot_archive_v1") ?? "";
  assert.ok(!archive.includes("data:image/png"), "무거운 누끼가 보관함에 들어갔다");

  switchPetSlotState(petSlotIdForIndex(0), petSlotIdForIndex(1));
  switchPetSlotState(petSlotIdForIndex(1), petSlotIdForIndex(0));
  // 펫을 가르는 값은 살아 있다 — 잃은 것은 표시용 사본뿐이다.
  assert.equal(activeContentId(), "content-heavy");
  assert.equal(readStoredPipeline()?.idle_video_url, "https://cdn/heavy_idle.mp4");
  assert.equal(readStoredPipeline()?.cutout_display_url, "");
});

test("a single pet that never switches keeps everything it wrote", () => {
  stagePet(0, ["https://cdn/only.png"], "https://cdn/only-cut.png");
  localStorage.setItem("eternal_beam_main_photo", "data:image/png;base64,LIVE");

  // 전환도 추가도 없다 — 살아 있는 칸이 그대로 답이다.
  assert.equal(activeContentId(), "content-1");
  assert.equal(localStorage.getItem("eternal_beam_theme_id"), "10");
  assert.equal(localStorage.getItem("eternal_beam_main_photo"), "data:image/png;base64,LIVE");
  assert.deepEqual(restoreSlots(0), [
    { images: ["https://cdn/only.png"], cutout: "https://cdn/only-cut.png" },
  ]);
});

test("a slot whose photos have no restorable address comes back empty, not wrong", () => {
  // 브라우저 누끼/파일 선택 경로 — 화면은 data: URL 만 들고 있다.
  writeActivePetSlotSnapshot(JSON.stringify({ imageCount: 2, imageUrls: [], cutoutUrl: null }));
  writePipeline("content-local", "https://cdn/local_idle.mp4");
  capturePetSlotState(petSlotIdForIndex(0));

  const restored = restoreSlots(0);
  assert.equal(restored.length, 1, "자리 자체는 남는다");
  assert.deepEqual(restored[0].images, [], "돌아올 수 없는 사진을 지어내지 않는다");
  // 만들어 둔 펫(파이프라인·content_id)은 잃지 않는다.
  assert.equal(activeContentId(), "content-local");
});

test("a heavy pending cutout keeps its contentId so generation still resolves the pet", () => {
  const heavy = "data:image/png;base64," + "B".repeat(400_000);
  sessionStorage.setItem(
    "eternal_beam_pending_cutout_v1",
    JSON.stringify({ contentId: "content-pending", displayUrl: heavy }),
  );
  capturePetSlotState(petSlotIdForIndex(0));
  switchPetSlotState(petSlotIdForIndex(0), petSlotIdForIndex(1));
  switchPetSlotState(petSlotIdForIndex(1), petSlotIdForIndex(0));

  const meta = JSON.parse(sessionStorage.getItem("eternal_beam_pending_cutout_v1") ?? "null") as {
    contentId?: string;
    displayUrl?: string;
  } | null;
  assert.equal(meta?.contentId, "content-pending", "생성이 펫을 못 찾게 된다");
  assert.equal(meta?.displayUrl, "", "표시용 사본만 비운다");
});

test("a small cutout display url survives archiving untouched", () => {
  sessionStorage.setItem(
    "eternal_beam_pending_cutout_v1",
    JSON.stringify({ contentId: "content-small", displayUrl: "data:image/png;base64,SMALL" }),
  );
  capturePetSlotState(petSlotIdForIndex(0));
  switchPetSlotState(petSlotIdForIndex(0), petSlotIdForIndex(1));
  switchPetSlotState(petSlotIdForIndex(1), petSlotIdForIndex(0));

  const meta = JSON.parse(sessionStorage.getItem("eternal_beam_pending_cutout_v1") ?? "null") as {
    displayUrl?: string;
  } | null;
  assert.equal(meta?.displayUrl, "data:image/png;base64,SMALL");
});
