/**
 * 현재 사진 집합 동기화 (stale-reference hotfix, Stage 1b).
 *
 * 배경: UI 에서 사진을 빼거나 바꿔도 서버 대장에는 예전 원본이 accepted 로 남아
 * 있었다. 그 사진이 계속 신원에 기여하고, 자리(MAX 3)를 차지해 교체한 사진이
 * 409 PHASE1_ORIGINAL_LIMIT 로 막혔다.
 *
 * 한 번의 처리 패스는 이렇게 흐른다:
 *   1. 지금 UI 에 있는 **모든** 사진의 sha256 을 구한다
 *   2. 업로드 루프 **전에** 동기화 — 빠진 사진을 물려 자리를 비운다 (주 경로)
 *   3. 사진별 업로드 루프 (호출자)
 *   4. 루프 **뒤에** 같은 목록으로 한 번 더 동기화 — 멱등한 안전망
 *
 * 규칙:
 *   - 부분 목록은 절대 보내지 않는다. 한 장이라도 해시를 못 구하면 동기화를
 *     통째로 건너뛴다 — 목록에 없는 사진은 서버에서 물러나기 때문이다.
 *   - 동기화 실패는 패스를 막지 않는다. 알림만 올린다.
 *   - 같은 펫의 패스는 한 번에 하나만 돈다 (동기화와 업로드가 엇갈리지 않게).
 *
 * 해시는 업로드와 **같은 바이트**에서 나온다: persistPhase1Intake 가 올리는
 * 파일은 decodeDataUrl(dataUrl).bytes 그대로이고, 서버는 받은 파일 바이트의
 * sha256 을 content_hash 로 기록한다 (pet_reference_service.record_original).
 *
 * (순수 모듈 테스트를 위해 상대 경로 import 규칙을 따른다 — original-reference.ts 참고)
 */

import { decodeDataUrl } from "./original-reference.ts";
import { isPhase1LockedError } from "./pet-input-lock.ts";

/** 동기화가 패스를 붙잡지 못하게 하는 상한. 넘기면 실패로 보고 계속 간다. */
export const REFERENCE_SYNC_TIMEOUT_MS = 15_000;

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

/** 바이트의 sha256 hex (소문자). WebCrypto 가 없으면(비보안 컨텍스트) throw. */
export async function sha256Hex(bytes: Uint8Array<ArrayBuffer>): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new Error("WebCrypto is unavailable");
  const digest = new Uint8Array(await subtle.digest("SHA-256", bytes));
  let hex = "";
  for (let i = 0; i < digest.length; i++) hex += digest[i].toString(16).padStart(2, "0");
  return hex;
}

/**
 * 사진(data: URL)들의 content hash. **전부 아니면 전무** — 한 장이라도 실패하면
 * null 이다. 순서는 입력과 같고, 같은 바이트의 중복은 그대로 둔다(서버가 집합으로 본다).
 */
export async function hashIntakePhotos(photos: readonly string[]): Promise<string[] | null> {
  if (!photos || photos.length === 0) return null;
  const hashes: string[] = [];
  for (const photo of photos) {
    const decoded = decodeDataUrl(photo);
    if (!decoded || decoded.bytes.length === 0) return null;
    try {
      hashes.push(await sha256Hex(decoded.bytes));
    } catch {
      return null;
    }
  }
  return hashes;
}

export class ReferenceSyncError extends Error {
  readonly status?: number;
  readonly code?: string;

  constructor(message: string, status?: number, code?: string) {
    super(message);
    this.name = "ReferenceSyncError";
    this.status = status;
    this.code = code;
  }
}

export type ReferenceSyncResult = {
  petId: string;
  active: Array<{ referenceId: string; contentHash: string | null }>;
  rejectedReferenceIds: string[];
  /** 보낸 해시 중 서버에 살아 있는 원본이 아직 없는 것. */
  missingHashes: string[];
};

/** POST /api/v1/pet/references/{pet_id}/sync. 실패는 ReferenceSyncError 로 throw. */
export async function syncPetReferences(params: {
  petId: string;
  contentHashes: readonly string[];
  accessToken: string;
}): Promise<ReferenceSyncResult> {
  const petId = (params.petId || "").trim();
  const token = (params.accessToken || "").trim();
  if (!petId || !token || params.contentHashes.length === 0) {
    throw new ReferenceSyncError("Reference sync input is invalid.");
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REFERENCE_SYNC_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(`${apiBase()}/api/v1/pet/references/${encodeURIComponent(petId)}/sync`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ content_hashes: [...params.contentHashes] }),
      signal: controller.signal,
    });
  } catch (error) {
    throw new ReferenceSyncError(
      error instanceof Error ? error.message : "Reference sync network failure.",
    );
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) {
    let message = `Reference sync failed (${res.status})`;
    let code: string | undefined;
    try {
      const body = (await res.json()) as { detail?: string | { code?: string; message?: string } };
      if (typeof body.detail === "object" && body.detail) {
        code = body.detail.code;
        message = body.detail.message || message;
      } else if (typeof body.detail === "string") {
        message = body.detail;
      }
    } catch {
      /* keep default */
    }
    throw new ReferenceSyncError(message, res.status, code);
  }

  const body = (await res.json()) as {
    pet_id?: string;
    active?: Array<{ reference_id?: string; content_hash?: string | null }>;
    rejected_reference_ids?: string[];
    missing_hashes?: string[];
  };
  return {
    petId: String(body.pet_id || petId),
    active: (body.active || []).map((item) => ({
      referenceId: String(item.reference_id || ""),
      contentHash: item.content_hash ?? null,
    })),
    rejectedReferenceIds: body.rejected_reference_ids || [],
    missingHashes: body.missing_hashes || [],
  };
}

// ── 펫당 한 번에 한 패스 ──────────────────────────────────────────────────────

/** 펫별 대기열의 꼬리. 모듈 수준이라 화면이 다시 마운트돼도 이어진다. */
const passTails = new Map<string, Promise<void>>();

/**
 * 이 펫의 앞선 패스가 끝날 때까지 기다린 뒤 release 함수를 돌려준다.
 * release 는 여러 번 불러도 한 번만 동작한다.
 */
function acquireIntakePass(petId: string): Promise<() => void> {
  const previous = passTails.get(petId) ?? Promise.resolve();
  let open!: () => void;
  const mine = new Promise<void>((resolve) => {
    open = resolve;
  });
  const tail = previous.then(() => mine);
  passTails.set(petId, tail);
  return previous.then(() => () => {
    open();
    if (passTails.get(petId) === tail) passTails.delete(petId);
  });
}

/** 테스트 전용 — 걸려 있는 패스 대기열을 비운다. */
export function __resetIntakePassesForTests(): void {
  passTails.clear();
}

export type IntakeSyncNotice =
  /** 현재 사진 중 해시를 못 구한 것이 있어 동기화를 건너뛰었다. */
  | { kind: "hash_failed" }
  /** 동기화 호출이 실패했다. 패스는 계속된다. */
  | { kind: "sync_failed"; phase: "before" | "after"; error: ReferenceSyncError };

export type IntakePass = {
  /** 이번 패스에서 보낸(또는 보내려던) 전체 해시 목록. 해시 실패면 null. */
  hashes: string[] | null;
  /**
   * 서버가 PHASE1_LOCKED 로 답했다 — 생성이 이미 시작되어 사진을 바꿀 수 없다.
   * 오류가 아니라 예상된 답이다(알림을 올리지 않는다). 호출자는 업로드하지 않는다.
   */
  locked: boolean;
  /** 루프 전 동기화가 성공했는가. false 면 교체한 사진이 409 로 막힐 수 있다. */
  preSyncOk: boolean;
  /**
   * 패스를 닫는다. **반드시** 한 번 부른다(finally) — 부르지 않으면 이 펫의 다음
   * 패스가 영원히 기다린다. syncAfter 가 참이면 같은 목록으로 한 번 더 동기화한다.
   */
  finish(options: { syncAfter: boolean }): Promise<void>;
};

/**
 * 처리 패스를 연다: 앞선 패스를 기다리고 → 전체 해시를 구하고 → 루프 전 동기화.
 *
 * 기다리는 동안 이 패스가 낡았으면(isCurrent() === false) 아무것도 하지 않고
 * null 을 돌려준다 — 낡은 사진 목록으로 동기화하면 안 된다. null 이면 호출자도
 * 업로드하지 말아야 한다.
 */
export async function beginIntakePass(params: {
  petId: string;
  /** 지금 UI 에 있는 **모든** 사진. 업로드 대상만 추린 목록이 아니다. */
  photos: readonly string[];
  accessToken: string;
  isCurrent?: () => boolean;
  onNotice?: (notice: IntakeSyncNotice) => void;
}): Promise<IntakePass | null> {
  const petId = (params.petId || "").trim();
  const isCurrent = params.isCurrent ?? (() => true);
  const release = await acquireIntakePass(petId);

  let locked = false;
  const sync = async (hashes: string[], phase: "before" | "after"): Promise<boolean> => {
    try {
      await syncPetReferences({ petId, contentHashes: hashes, accessToken: params.accessToken });
      return true;
    } catch (error) {
      if (isPhase1LockedError(error)) {
        // 생성이 시작된 뒤의 동기화는 잠김으로 답한다 — 예상된 답이지 실패가 아니다.
        locked = true;
        return false;
      }
      const syncError =
        error instanceof ReferenceSyncError
          ? error
          : new ReferenceSyncError(error instanceof Error ? error.message : String(error));
      console.warn(`[reference-sync] ${phase}-loop sync failed`, syncError.code, syncError.message);
      params.onNotice?.({ kind: "sync_failed", phase, error: syncError });
      return false;
    }
  };

  let hashes: string[] | null = null;
  let preSyncOk = false;
  try {
    if (!isCurrent()) {
      release();
      return null;
    }
    hashes = await hashIntakePhotos(params.photos);
    if (!hashes) {
      console.warn("[reference-sync] could not hash every current photo — sync skipped");
      params.onNotice?.({ kind: "hash_failed" });
    } else if (isCurrent()) {
      preSyncOk = await sync(hashes, "before");
    }
    if (!isCurrent()) {
      release();
      return null;
    }
  } catch (error) {
    release();
    throw error;
  }

  let finished = false;
  return {
    hashes,
    locked,
    preSyncOk,
    async finish({ syncAfter }) {
      if (finished) return;
      finished = true;
      try {
        if (syncAfter && hashes && isCurrent()) await sync(hashes, "after");
      } finally {
        release();
      }
    },
  };
}
