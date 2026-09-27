/**
 * Phase 7G — 새로고침 안전 재개(refresh-safe resume).
 *
 * 확인(Confirm)을 눌러 generation-run 이 **실제로 시작된 적**이 있으면, 그
 * 사실과 content_id/pet_id/run_id 를 durable(localStorage) 하게 남긴다.
 * 앱이 다시 뜰 때(새로고침 포함) 이 마커가 있으면 백엔드에서 현재 실행
 * 상태를 다시 조회해 화면을 복원한다 — 처음부터 다시 시작하지 않는다.
 *
 * 마커가 없으면(아직 확인을 누르지 않은 사용자) 아무 일도 하지 않는다 —
 * 새 사용자/활성 실행 없음 동작은 그대로다.
 *
 * ── sessionStorage 만 믿지 않는다 ─────────────────────────────────────────
 * content_id 해석은 sessionStorage(pending cutout meta — 같은 탭에서는
 * 항상 있다) 를 우선하되, 없으면 durable localStorage 결속(pet-identity.ts)
 * 으로 떨어진다 — 탭을 완전히 새로 열어도 마지막으로 확정된 content_id 를
 * 찾는다. 마커 자체도 처음부터 localStorage 에만 쓴다.
 */

import { getPendingCutoutMeta } from "./pending-generation.ts";
import { getEternalBeamPetId } from "./pet-identity.ts";

const ACTIVE_GENERATION_KEY = "eternal_beam_active_generation_v1";

/**
 * Phase 9 — 유령 마커 정리.
 *
 * 실행은 보통 분 단위로 끝난다. 이만큼 지나도 마커가 남아 있으면 그건 완료
 * 처리(clearActiveGeneration)가 빠졌거나 사용자가 아예 돌아오지 않은
 * 흔적이지, "아직도 재개해야 할 실행"이 아니다 — 조용히 지운다.
 */
const ACTIVE_GENERATION_STALE_MS = 48 * 60 * 60 * 1000;

/** pet-identity.ts 의 durable content_id 키와 같다(순서대로 시도). */
const CONTENT_ID_FALLBACK_KEYS = [
  "eternal_beam_current_content_id",
  "eternal_beam_content_id",
];

export type ActiveGenerationRecord = {
  contentId: string;
  petId: string;
  motionId: string;
  /** startGenerationRun 이 돌려준 실제 run_id — 있으면 재개가 곧장 GET 하나로 끝난다. */
  runId?: string;
  startedAt: string;
};

type ActiveGenerationStore = Record<string, ActiveGenerationRecord>;

function readAll(): ActiveGenerationStore {
  try {
    const raw = localStorage.getItem(ACTIVE_GENERATION_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return parsed as ActiveGenerationStore;
  } catch {
    return {};
  }
}

function writeAll(all: ActiveGenerationStore): void {
  try {
    localStorage.setItem(ACTIVE_GENERATION_KEY, JSON.stringify(all));
  } catch {
    /* 용량 초과 등 — 재개는 다음 조회에서도 idempotency_key 경로로 여전히 동작한다 */
  }
}

/** 확인(Confirm)이 실제로 generation-run 을 시작/재사용시켰다는 durable 기록. */
export function markGenerationStarted(record: {
  contentId: string;
  petId: string;
  motionId?: string;
  runId?: string;
}): void {
  const contentId = (record.contentId || "").trim();
  const petId = (record.petId || "").trim();
  if (!contentId || !petId) return;
  const all = readAll();
  all[contentId] = {
    contentId,
    petId,
    motionId: record.motionId || "BREATHING",
    runId: record.runId,
    startedAt: new Date().toISOString(),
  };
  writeAll(all);
}

export function readActiveGeneration(contentId: string): ActiveGenerationRecord | null {
  const id = (contentId || "").trim();
  if (!id) return null;
  const all = readAll();
  const record = all[id] ?? null;
  if (!record) return null;
  const startedAt = Date.parse(record.startedAt);
  if (Number.isFinite(startedAt) && Date.now() - startedAt > ACTIVE_GENERATION_STALE_MS) {
    delete all[id];
    writeAll(all);
    return null;
  }
  return record;
}

export function hasActiveGeneration(contentId: string): boolean {
  return readActiveGeneration(contentId) != null;
}

/** 새 업로드를 시작하는 등, 이 content_id 의 재개 기록을 명시적으로 지운다. */
export function clearActiveGeneration(contentId: string): void {
  const id = (contentId || "").trim();
  if (!id) return;
  const all = readAll();
  if (!(id in all)) return;
  delete all[id];
  writeAll(all);
}

/**
 * 지금 세션의 "재개 대상" content_id.
 *
 * sessionStorage(pending cutout meta — 같은 탭 새로고침에서는 항상 있다)
 * 를 우선하고, 없으면 durable localStorage 결속으로 떨어진다 — 탭을 새로
 * 열어도(sessionStorage 소실) 마지막으로 확정된 content_id 를 찾는다.
 */
export function resolveResumeContentId(): string | null {
  try {
    const fromPending = getPendingCutoutMeta()?.contentId?.trim();
    if (fromPending) return fromPending;
  } catch {
    /* ignore */
  }
  try {
    for (const key of CONTENT_ID_FALLBACK_KEYS) {
      const value = localStorage.getItem(key)?.trim();
      if (value) return value;
    }
  } catch {
    /* ignore */
  }
  return null;
}

/** content_id → pet_id. 결속이 있으면 그것, 없으면 순수 파생(pet-identity.ts). */
export function resolveResumePetId(contentId: string): string | null {
  return getEternalBeamPetId(contentId);
}

/** 테스트에서 모듈 상태(=localStorage 마커)를 초기화하기 위한 훅. */
export function __resetActiveGenerationForTest(): void {
  try {
    localStorage.removeItem(ACTIVE_GENERATION_KEY);
  } catch {
    /* ignore */
  }
}
