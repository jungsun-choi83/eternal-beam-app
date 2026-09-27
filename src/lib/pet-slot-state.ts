/**
 * 펫 슬롯별 인테이크 상태 보관 — 그리고 **활성 펫 투영.**
 *
 * ── 무엇이 어긋나 있었나 ────────────────────────────────────────────────────
 * 화면 상태(업로드 이미지·누끼 미리보기)는 슬롯별 React state 였는데, 그 뒤의
 * 저장소는 전부 **앱 전체에 한 칸씩**이었다:
 *
 *   eternal_beam_pipeline_v1          누끼/생성 결과 (content_id, 영상 URL)
 *   eternal_beam_pending_cutout_v1    생성에 올릴 원본 해상도 누끼
 *   eternal_beam_content_id           지금 콘텐츠
 *   eternal_beam_current_content_id   〃
 *
 * 그래서 펫 2를 처리하면 그 칸들이 펫 2로 덮였고, 펫 1 탭으로 돌아가 미리보기·
 * 생성으로 가면 **펫 2가 생성됐다.** 화면에는 펫 1이 보이는 채로.
 *
 * ── 규칙 ────────────────────────────────────────────────────────────────────
 * 저장소의 그 칸들은 이제 "활성 펫의 값"이라는 **파생 뷰**다. 진짜 보관함은
 * `eternal_beam_pet_slot_archive_v1`(슬롯 → 스냅샷)이고, 펫을 바꿀 때
 * `switchPetSlotState` 가 한 번에 갈아 끼운다: 지금 칸을 떠나는 슬롯에 담고,
 * 들어오는 슬롯의 값으로 칸을 다시 채운다(없으면 비운다).
 *
 * 덕분에 파이프라인을 읽는 화면들(preview / theme / devicePlay / 생성 요청)은
 * **한 줄도 바뀌지 않는다** — 그들이 보는 칸이 언제나 활성 펫의 것이기 때문이다.
 */

// 명시적 확장자 — Vite 도 node:test 도 그대로 해석한다(@/ 별칭은 Node 에서 못 푼다).
import { activatePendingCutoutSlot } from "./pending-generation.ts";

export const MAX_PET_SLOTS = 3;
export const PET_SLOT_ARCHIVE_KEY = "eternal_beam_pet_slot_archive_v1";

/**
 * 활성 펫의 화면 상태(사진·누끼·장별 진행) 직렬화본.
 *
 * React state 는 새로고침으로 사라진다. 이 칸이 있으면 같은 세션에서 새로고침
 * 하거나 결제를 다녀와도 **각 펫이 자기 사진으로** 돌아온다. 다른 칸들과 똑같이
 * ACTIVE_PET_KEYS 에 등록돼 있으므로, 전환할 때 함께 갈아 끼워진다.
 */
export const PET_SLOT_SNAPSHOT_KEY = "eternal_beam_pet_slot_snapshot_v1";

/** 마지막으로 보고 있던 자리 번호. 펫별 값이 아니라 **앱의 커서**다. */
export const ACTIVE_PET_SLOT_KEY = "eternal_beam_active_pet_slot_v1";

/**
 * 슬롯 식별자는 **자리에서 결정된다** — 시계나 난수가 아니라.
 *
 * 예전에는 `pet_slot_${Date.now()}_${random}` 이라 같은 펫이 새로고침마다 다른
 * 이름을 얻었고, 보관함 키로 쓸 수 없었다(= 복원 불가). 슬롯은 추가만 되고
 * 중간에서 사라지지 않으므로 자리 번호가 안정적인 이름이다.
 */
export function petSlotIdForIndex(index: number): string {
  return `pet_slot_${index + 1}`;
}

type StorageScope = "session" | "local";
type ScopedKey = {
  key: string;
  scope: StorageScope;
  /**
   * 보관함에 담는가.
   *
   * `false` 면 이 칸은 활성 펫의 **파생 뷰**일 뿐이다 — 전환할 때 비우고, 화면
   * 상태에서 다시 적는다. 원본 사진처럼 무거운 값을 세 마리분 들고 있지 않기
   * 위한 구분이다.
   */
  archive?: boolean;
};

/**
 * 활성 펫 한 마리분으로 취급하는 저장소 칸들.
 *
 * **펫마다 답이 달라지는 값은 전부 여기에 등록한다.** 등록하지 않으면 그 칸은
 * 앱 전체에 하나로 남아, 마지막으로 만진 펫의 답이 다른 펫의 화면·생성으로
 * 새어 나간다. `pet-storage-key-coverage.test.ts` 가 새 키를 분류 없이
 * 들여오지 못하게 막는다.
 */
const ACTIVE_PET_KEYS: ScopedKey[] = [
  // ── 인테이크 ────────────────────────────────────────────────────────────
  { key: "eternal_beam_pipeline_v1", scope: "session" },
  { key: "eternal_beam_pending_cutout_v1", scope: "session" },
  { key: PET_SLOT_SNAPSHOT_KEY, scope: "session" },
  { key: "eternal_beam_content_id", scope: "local" },
  { key: "eternal_beam_current_content_id", scope: "local" },
  // ── 활성 펫의 미디어 (파생 뷰 — 보관하지 않는다) ─────────────────────────
  { key: "eternal_beam_main_photo", scope: "local", archive: false },
  { key: "eternal_beam_main_video_url", scope: "local", archive: false },
  { key: "eternal_beam_media_type", scope: "local", archive: false },
  // ── 신원 포인터 ─────────────────────────────────────────────────────────
  { key: "eternal_beam_pet_id", scope: "local" },
  { key: "eternal_beam_pet_binding", scope: "local" },
  { key: "eternal_beam_pet_name", scope: "local" },
  // ── 테마 선택 ───────────────────────────────────────────────────────────
  { key: "eternal_beam_theme_key", scope: "local" },
  { key: "eternal_beam_theme_id", scope: "local" },
  { key: "eternal_beam_selected_theme_id", scope: "local" },
  { key: "eternal_beam_background_theme_id", scope: "local" },
  { key: "eternal_beam_background_theme_name", scope: "local" },
  { key: "eternal_beam_custom_bg_video_url", scope: "local" },
  { key: "eternal_beam_custom_bg_job_id", scope: "local" },
  { key: "eternal_beam_custom_bg_content_id", scope: "local" },
  // ── 장면 / 기기 송출 / NFC ──────────────────────────────────────────────
  { key: "eternal_beam_canonical_scene_v1", scope: "local" },
  { key: "eternal_beam_nfc_payload", scope: "local" },
  { key: "eternal_beam_current_video_id", scope: "local" },
  { key: "eternal_beam_hologram_video_id", scope: "local" },
];

/** 커버리지 테스트가 읽는 등록부. 순서는 의미 없다. */
export const ACTIVE_PET_STORAGE_KEYS: readonly string[] = ACTIVE_PET_KEYS.map(
  (scoped) => scoped.key,
);

/** 보관 대상 — 파생 뷰는 빠진다. */
export const ARCHIVED_PET_STORAGE_KEYS: readonly string[] = ACTIVE_PET_KEYS.filter(
  (scoped) => scoped.archive !== false,
).map((scoped) => scoped.key);

/**
 * 한 칸에 담는 보관 값의 상한.
 *
 * 보관함은 **가벼운 것만** 담는다: id·주소·선택값. 원본 사진이나 누끼 data: URL
 * 처럼 메가바이트 단위의 값이 들어오면 세 마리분이 세션 용량을 넘기고, 그때
 * 넘치는 것은 방금 만든 펫의 파이프라인이다. 상한을 넘는 칸은 담지 않는다 —
 * 그 값은 복원되지 않을 뿐, 다른 펫의 값으로 둔갑하지 않는다.
 */
const ARCHIVE_VALUE_LIMIT = 256 * 1024;

/**
 * JSON 값 안에서 data: URL 을 이고 올 수 있는 필드들(브라우저 누끼 경로).
 *
 * 이 칸들은 **반드시** 보관해야 한다 — content_id 와 영상 주소, 인테이크 영수증이
 * 여기 있고, 그것이 펫을 가르는 값이다. 상한을 넘을 때만 표시용 사본을 비운다.
 * 화면이 쓰는 누끼는 React state 의 사본이라 여기서 잃지 않고, 생성이 쓰는
 * 원본 해상도 누끼 File 은 슬롯별 메모리에 따로 있다(pending-generation).
 */
const JSON_DISPLAY_FIELDS: Record<string, string[]> = {
  eternal_beam_pipeline_v1: ["cutout_display_url", "dog_only_nobg_url"],
  eternal_beam_pending_cutout_v1: ["displayUrl"],
};

/** 보관용으로 값을 가볍게 만든다. 가벼워지지 않으면 담지 않는다(null). */
function leanArchiveValue(key: string, value: string): string | null {
  if (value.length <= ARCHIVE_VALUE_LIMIT) return value;

  const fields = JSON_DISPLAY_FIELDS[key];
  if (!fields) return null;
  try {
    const parsed = JSON.parse(value) as Record<string, unknown>;
    for (const field of fields) {
      const current = parsed[field];
      if (typeof current === "string" && current.startsWith("data:")) parsed[field] = "";
    }
    const lean = JSON.stringify(parsed);
    return lean.length <= ARCHIVE_VALUE_LIMIT ? lean : null;
  } catch {
    return null;
  }
}

/** 값이 없는 칸은 스냅샷에서도 **없다**(빈 문자열로 만들지 않는다). */
type SlotSnapshot = Record<string, string>;
type SlotArchive = Record<string, SlotSnapshot>;

function store(scope: StorageScope): Storage | null {
  try {
    return scope === "session" ? sessionStorage : localStorage;
  } catch {
    return null;
  }
}

function readRaw({ key, scope }: ScopedKey): string | null {
  try {
    return store(scope)?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function writeRaw({ key, scope }: ScopedKey, value: string | null): void {
  try {
    const s = store(scope);
    if (!s) return;
    if (value == null) s.removeItem(key);
    else s.setItem(key, value);
  } catch {
    /* 용량 초과는 일어난다. 화면은 React state 로 계속 동작한다. */
  }
}

export function readPetSlotArchive(): SlotArchive {
  try {
    const raw = sessionStorage.getItem(PET_SLOT_ARCHIVE_KEY);
    const parsed = JSON.parse(raw || "null") as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out: SlotArchive = {};
    for (const [slotId, snapshot] of Object.entries(parsed as Record<string, unknown>)) {
      if (!snapshot || typeof snapshot !== "object" || Array.isArray(snapshot)) continue;
      const clean: SlotSnapshot = {};
      for (const [key, value] of Object.entries(snapshot as Record<string, unknown>)) {
        if (typeof value === "string") clean[key] = value;
      }
      out[slotId] = clean;
    }
    return out;
  } catch {
    return {};
  }
}

function writeArchive(archive: SlotArchive): boolean {
  try {
    sessionStorage.setItem(PET_SLOT_ARCHIVE_KEY, JSON.stringify(archive));
    return true;
  } catch {
    return false;
  }
}

/**
 * 지금 저장소 칸에 있는 것을 이 슬롯의 것으로 기록한다.
 *
 * 사진 원본(data: URL)은 무겁다 — 3마리 × 3장이면 세션 용량을 넘길 수 있다.
 * 넘치면 **조용히 낡은 기록을 남기지 않는다**: 무거운 칸을 빼고 다시 시도하고,
 * 그래도 안 되면 이 자리의 기록을 지운다. 복원되지 않는 편이, 남의 사진이
 * 이 자리로 돌아오는 것보다 낫다.
 */
export function capturePetSlotState(slotId: string): void {
  if (!slotId) return;
  const snapshot: SlotSnapshot = {};
  for (const scoped of ACTIVE_PET_KEYS) {
    if (scoped.archive === false) continue;
    const value = readRaw(scoped);
    if (value == null) continue;
    const lean = leanArchiveValue(scoped.key, value);
    if (lean != null) snapshot[scoped.key] = lean;
  }
  const archive = readPetSlotArchive();
  if (writeArchive({ ...archive, [slotId]: snapshot })) return;

  // 상한을 지켰는데도 용량이 모자라다 — 이 자리의 기록을 지운다. 복원되지 않는
  // 편이, 낡은 기록이 남아 다음 복원에서 남의 값이 돌아오는 것보다 낫다.
  const pruned = { ...archive };
  delete pruned[slotId];
  writeArchive(pruned);
}

/**
 * 이 자리의 화면 상태 직렬화본. 활성 자리는 살아 있는 칸이 최신이고,
 * 나머지는 보관함이 답이다.
 */
export function readPetSlotSnapshot(slotId: string, isActive: boolean): string | null {
  return readPetSlotValue(slotId, PET_SLOT_SNAPSHOT_KEY, isActive);
}

/**
 * 이 자리의 등록된 칸 하나를 읽는다. 활성 자리는 살아 있는 칸이 최신이고,
 * 나머지는 보관함이 답이다. 등록되지 않은 키는 **자리별 값이 아니므로** null.
 */
export function readPetSlotValue(
  slotId: string,
  key: string,
  isActive: boolean,
): string | null {
  const scoped = ACTIVE_PET_KEYS.find((entry) => entry.key === key);
  if (!scoped) return null;
  if (isActive) {
    const live = readRaw(scoped);
    if (live != null) return live;
  }
  if (scoped.archive === false) return null;
  return readPetSlotArchive()[slotId]?.[key] ?? null;
}

/**
 * 활성 자리의 화면 상태를 적는다.
 *
 * 쓰기가 실패하면(용량) 그 칸을 **지운다.** 옛 값이 남아 있으면 다음 복원에서
 * 이 자리가 지난 펫의 사진으로 되살아난다.
 */
export function writeActivePetSlotSnapshot(value: string | null): void {
  const scoped: ScopedKey = { key: PET_SLOT_SNAPSHOT_KEY, scope: "session" };
  if (value == null) {
    writeRaw(scoped, null);
    return;
  }
  try {
    sessionStorage.setItem(PET_SLOT_SNAPSHOT_KEY, value);
  } catch {
    writeRaw(scoped, null);
  }
}

/** 마지막으로 보고 있던 자리. 범위를 벗어난 값은 0 으로 떨어뜨린다. */
export function readActivePetSlotIndex(): number {
  try {
    const raw = Number(sessionStorage.getItem(ACTIVE_PET_SLOT_KEY));
    if (!Number.isInteger(raw) || raw < 0 || raw >= MAX_PET_SLOTS) return 0;
    return raw;
  } catch {
    return 0;
  }
}

export function writeActivePetSlotIndex(index: number): void {
  try {
    sessionStorage.setItem(ACTIVE_PET_SLOT_KEY, String(index));
  } catch {
    /* ignore */
  }
}

/**
 * 저장소는 그대로 두고 **모듈 메모리의 자리 표시만** 맞춘다.
 *
 * 새로고침 직후에 쓴다: 살아 있는 칸들은 이미 그 펫의 것이므로 투영을 다시
 * 할 이유가 없고, 여기서 activatePetSlotState 를 부르면 결제 복귀처럼 살아 있는
 * 칸에만 있는 상태를 보관함의 옛 값으로 덮어쓸 수 있다.
 */
export function alignActivePetSlot(slotId: string): void {
  if (!slotId) return;
  activatePendingCutoutSlot(slotId);
}

/**
 * 이 슬롯의 값으로 저장소 칸을 다시 채운다.
 *
 * 보관된 값이 없는 칸은 **지운다.** 남겨 두면 그게 곧 직전 펫의 잔상이고,
 * 새 펫의 미리보기가 남의 파이프라인을 읽는다.
 */
export function activatePetSlotState(slotId: string): void {
  if (!slotId) return;
  const snapshot = readPetSlotArchive()[slotId] ?? {};
  for (const scoped of ACTIVE_PET_KEYS) {
    writeRaw(scoped, snapshot[scoped.key] ?? null);
  }
  // 누끼 File 은 메모리에만 있다 — 저장소 투영으로는 옮겨지지 않는다.
  activatePendingCutoutSlot(slotId);
}

/** 펫 전환의 **단 하나의 지점**: 떠나는 칸을 담고, 들어오는 칸을 채운다. */
export function switchPetSlotState(fromSlotId: string, toSlotId: string): void {
  if (fromSlotId) capturePetSlotState(fromSlotId);
  activatePetSlotState(toSlotId);
}

/** 새 슬롯 — 보관된 것도 활성 칸도 비운 상태로 시작한다. */
export function clearPetSlotState(slotId: string): void {
  if (!slotId) return;
  const archive = readPetSlotArchive();
  delete archive[slotId];
  writeArchive(archive);
}

/** 리셋·로그아웃. */
export function clearAllPetSlotState(): void {
  for (const key of [PET_SLOT_ARCHIVE_KEY, ACTIVE_PET_SLOT_KEY]) {
    try {
      sessionStorage.removeItem(key);
    } catch {
      /* ignore */
    }
  }
  for (const scoped of ACTIVE_PET_KEYS) writeRaw(scoped, null);
}
