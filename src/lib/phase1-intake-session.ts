/**
 * Stable identity for one upload attempt — **per pet slot.**
 *
 * It is allocated when the image is committed, before preprocessing starts, and
 * retained for retries in this browser session. The backend independently
 * derives the same pet id from the content id.
 *
 * ── 왜 슬롯별인가 ───────────────────────────────────────────────────────────
 * 예전에는 이 신원이 **앱 전체에 하나**였다(세션 키 한 칸). 펫 2를 올리는 순간
 * 그 칸을 덮어썼고, 펫 1로 돌아가면 `requirePhase1Intake` 가 그 칸을 읽어
 * **펫 2의 content_id** 를 펫 1의 업로드에 붙였다. content_id 에서 pet_id 가
 * 파생되므로, 두 아이가 같은 pet_id 하나를 나눠 갖는 결함이었다.
 *
 * 이제 저장 형태는 `{ [slotId]: identity }` 다. 슬롯 키가 없으면 **없는 것**이고,
 * 다른 슬롯의 값으로 대신하지 않는다.
 */
import { derivePetIdFromContent } from "./pet-identity.ts";

const INTAKE_KEY = "eternal_beam_phase1_intake_v1";

export type Phase1IntakeIdentity = {
  contentId: string;
  petId: string;
};

type IntakeBySlot = Record<string, Phase1IntakeIdentity>;

function defaultContentId(): string {
  const id = globalThis.crypto?.randomUUID?.();
  if (id) return id;
  return `upload_${Date.now()}_${Math.random().toString(36).slice(2, 12)}`;
}

function isValid(value: unknown): value is Phase1IntakeIdentity {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<Phase1IntakeIdentity>;
  const contentId = (candidate.contentId || "").trim();
  return Boolean(contentId && candidate.petId === derivePetIdFromContent(contentId));
}

function requireSlotId(slotId: string): string {
  const id = (slotId || "").trim();
  if (!id) throw new Error("펫 슬롯을 지정하지 않고 업로드 신원을 다룰 수 없습니다.");
  return id;
}

function readAll(): IntakeBySlot {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(INTAKE_KEY) || "null") as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out: IntakeBySlot = {};
    for (const [slotId, value] of Object.entries(parsed as Record<string, unknown>)) {
      // 슬롯 하나가 망가져도 나머지는 살린다. 단, 망가진 칸을 **다른 칸으로
      // 메우지는 않는다** — 그게 곧 펫이 섞이는 길이다.
      if (isValid(value)) out[slotId] = value;
    }
    return out;
  } catch {
    return {};
  }
}

function writeAll(all: IntakeBySlot): void {
  try {
    sessionStorage.setItem(INTAKE_KEY, JSON.stringify(all));
  } catch {
    /* React state still carries the identity for the active upload. */
  }
}

export function beginPhase1Intake(
  slotId: string,
  createContentId: () => string = defaultContentId,
): Phase1IntakeIdentity {
  const slot = requireSlotId(slotId);
  const contentId = createContentId().trim();
  if (!contentId) throw new Error("업로드 식별자를 만들지 못했습니다.");
  const identity = { contentId, petId: derivePetIdFromContent(contentId) };
  writeAll({ ...readAll(), [slot]: identity });
  return identity;
}

/**
 * 사진을 **더할 때** 쓸 신원.
 *
 * - 자리에 이미 사진이 있으면 같은 아이에 장을 더하는 것이다 — 신원을 그대로 쓴다.
 * - 자리가 비어 있으면 **언제나 새 신원**이다. 남아 있던 신원(화면 상태든 저장된
 *   값이든)을 물려받지 않는다.
 *
 * 두 번째 규칙이 잠금과 맞물린다: 새로고침 뒤에는 사진이 복원되지 않아 자리가
 * 비어 돌아오는데, 그 자리에는 이미 생성이 시작된(잠긴) 아이의 신원이 남아 있을
 * 수 있다. 빈 자리에서 고른 사진은 그 아이를 고치는 것이 아니라 **새 아이**다 —
 * 새 content_id/pet_id 로 올라가고, 잠긴 아이와 그 계보는 손대지 않는다.
 */
export function identityForAddedPhotos(
  slotId: string,
  existingPhotoCount: number,
  current?: Phase1IntakeIdentity | null,
  createContentId: () => string = defaultContentId,
): Phase1IntakeIdentity {
  if (existingPhotoCount > 0) {
    return current ?? readPhase1Intake(slotId) ?? beginPhase1Intake(slotId, createContentId);
  }
  return beginPhase1Intake(slotId, createContentId);
}

export function readPhase1Intake(slotId: string): Phase1IntakeIdentity | null {
  const slot = requireSlotId(slotId);
  return readAll()[slot] ?? null;
}

/**
 * 이 슬롯의 신원을 확정한다.
 *
 * 저장된 값이 있으면 **그것이 이깁니다** — 호출부가 (화면 전환 타이밍 때문에)
 * 다른 펫의 신원을 들고 들어와도 여기서 막힌다. 저장된 값도 없고 넘어온 값도
 * 없으면 이 슬롯 전용으로 새로 만든다. 다른 슬롯은 절대 읽지 않는다.
 */
export function requirePhase1Intake(
  slotId: string,
  current?: Phase1IntakeIdentity | null,
): Phase1IntakeIdentity {
  const slot = requireSlotId(slotId);
  const stored = readAll()[slot] ?? null;
  if (stored) return stored;
  if (isValid(current)) {
    writeAll({ ...readAll(), [slot]: current });
    return current;
  }
  return beginPhase1Intake(slot);
}

/** 슬롯을 지정하면 그 칸만, 지정하지 않으면 전부(리셋·로그아웃) 지운다. */
export function clearPhase1Intake(slotId?: string): void {
  if (!slotId) {
    try {
      sessionStorage.removeItem(INTAKE_KEY);
    } catch {
      /* ignore */
    }
    return;
  }
  const all = readAll();
  delete all[requireSlotId(slotId)];
  writeAll(all);
}
