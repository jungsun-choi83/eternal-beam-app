/**
 * canonical 펫 등록 클라이언트 (Phase 13.2).
 *
 * ── 왜 필요한가 ──────────────────────────────────────────────────────────────
 * 예전에는 서버에 **무료 펫의 기록이 없었다.** BREATHING 은 스토리지에만 올라가고
 * 펫 자체는 브라우저 sessionStorage 에만 있었다. 그래서 운영 콘솔이 그 펫을 찾을
 * 수 없었고, QR·편지·메모리 박스 파이프라인에 들어올 수 없었다 — QR 제품의 주
 * 고객(기기 없는 무료 사용자)이 막혀 있었던 셈이다.
 *
 * ⚠️ **생성 로직을 건드리지 않는다.** 파이프라인이 끝난 **뒤에** 결과를 등록할 뿐이다.
 * ⚠️ 실패해도 사용자 흐름을 막지 않는다 — 등록은 보조 경로이고, 실패하면 운영이
 *    수동 백필할 수 있다. 재생·미리보기는 등록과 무관하게 동작한다.
 */

import { getPremiumAccessToken } from "./premium-auth-token.ts";
import { getEternalBeamPetId } from "./pet-identity.ts";

export type PetRegistrationResult =
  | { state: "REGISTERED" }
  | { state: "PENDING_AUTH"; reason: "no-session" | "no-client" }
  | { state: "FAILED"; status?: number; code?: string; message: string };

export interface PetRegistrationDeps {
  getToken?: typeof getPremiumAccessToken;
  fetch?: typeof globalThis.fetch;
}

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

/** 등록할 수 있는 상태인가 — 순수 판정이라 테스트가 그대로 부른다. */
export function canRegisterPet(input: {
  petId: string | null | undefined;
  breathingUrl: string | null | undefined;
}): boolean {
  const pet = (input.petId || "").trim();
  const url = (input.breathingUrl || "").trim();
  if (!pet || !url) return false;
  // 서버는 스토리지 경로를 뽑아야 한다. data:/blob: 는 경로가 없다.
  return url.startsWith("http://") || url.startsWith("https://");
}

/**
 * 펫을 canonical 레지스트리에 등록한다. **멱등이며 실패해도 조용하다.**
 *
 * 성공 여부를 boolean 으로 돌려준다 — 호출부가 흐름을 바꾸지 않게 하기 위해서다.
 */
export async function ensurePetRegistered(params: {
  petId: string;
  contentId?: string | null;
  breathingUrl: string;
}, deps: PetRegistrationDeps = {}): Promise<PetRegistrationResult> {
  if (!canRegisterPet(params)) {
    const result: PetRegistrationResult = {
      state: "FAILED",
      code: "PET_REGISTER_INVALID",
      message: "canonical petId and an HTTP(S) BREATHING URL are required",
    };
    console.warn("[pet-registry] registration skipped: invalid READY pet", {
      petId: params.petId,
      hasBreathingUrl: Boolean(params.breathingUrl),
    });
    return result;
  }
  try {
    const auth = await (deps.getToken ?? getPremiumAccessToken)();
    // 소유권은 반드시 서버가 검증한 토큰에서 정한다. 세션 복원 뒤 호출부가 재시도한다.
    if (!auth.token) {
      console.info("[pet-registry] registration pending authenticated session", {
        petId: params.petId,
        reason: auth.reason,
      });
      return { state: "PENDING_AUTH", reason: auth.reason };
    }

    const res = await (deps.fetch ?? globalThis.fetch)(`${apiBase()}/api/v1/pet/registry/register`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${auth.token}`,
      },
      body: JSON.stringify({
        pet_id: params.petId,
        content_id: params.contentId ?? undefined,
        breathing_url: params.breathingUrl,
      }),
    });
    if (res.ok) return { state: "REGISTERED" };

    let code: string | undefined;
    let message = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as {
        detail?: { code?: string; message?: string };
      };
      code = body.detail?.code;
      message = body.detail?.message || message;
    } catch {
      /* status is still useful */
    }
    console.warn("[pet-registry] registration failed", {
      petId: params.petId,
      status: res.status,
      code,
      message,
    });
    return { state: "FAILED", status: res.status, code, message };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.warn("[pet-registry] registration request failed", {
      petId: params.petId,
      message,
    });
    return { state: "FAILED", message };
  }
}

/** Phase 13.2 name retained for callers outside the main Preview flow. */
export const registerPet = ensurePetRegistered;

/**
 * 앱 셸의 인증 복원 경로용. BREATHING 결과는 기존 session pipeline 을 그대로 읽고,
 * 별도 pending 레코드나 서명 URL 복제본을 만들지 않는다.
 */
export async function ensureStoredReadyPetRegistered(): Promise<PetRegistrationResult | null> {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem("eternal_beam_pipeline_v1");
    if (!raw) return null;
    const pipeline = JSON.parse(raw) as {
      content_id?: string;
      idle_video_url?: string;
    };
    const contentId = (pipeline.content_id || "").trim();
    const breathingUrl = (pipeline.idle_video_url || "").trim();
    if (!contentId || !breathingUrl) return null;
    return ensurePetRegistered({
      petId: getEternalBeamPetId(contentId) ?? `pet_${contentId}`,
      contentId,
      breathingUrl,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.warn("[pet-registry] stored READY pet could not be read", { message });
    return { state: "FAILED", message };
  }
}
