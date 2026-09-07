/**
 * Stable identity for one upload attempt.
 *
 * It is allocated when the image is committed, before preprocessing starts, and
 * retained for retries in this browser session. The backend independently
 * derives the same pet id from the content id.
 */
import { derivePetIdFromContent } from "./pet-identity.ts";

const INTAKE_KEY = "eternal_beam_phase1_intake_v1";

export type Phase1IntakeIdentity = {
  contentId: string;
  petId: string;
};

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

export function beginPhase1Intake(
  createContentId: () => string = defaultContentId,
): Phase1IntakeIdentity {
  const contentId = createContentId().trim();
  if (!contentId) throw new Error("업로드 식별자를 만들지 못했습니다.");
  const identity = { contentId, petId: derivePetIdFromContent(contentId) };
  try {
    sessionStorage.setItem(INTAKE_KEY, JSON.stringify(identity));
  } catch {
    /* React state still carries the identity for the active upload. */
  }
  return identity;
}

export function readPhase1Intake(): Phase1IntakeIdentity | null {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(INTAKE_KEY) || "null") as unknown;
    return isValid(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function requirePhase1Intake(
  current?: Phase1IntakeIdentity | null,
): Phase1IntakeIdentity {
  if (isValid(current)) return current;
  return readPhase1Intake() ?? beginPhase1Intake();
}

export function clearPhase1Intake(): void {
  try {
    sessionStorage.removeItem(INTAKE_KEY);
  } catch {
    /* ignore */
  }
}

