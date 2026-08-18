/**
 * Phase 1A — BLINKING 자산 확보 + 수동 트리거 (**개발 전용**).
 *
 * 이것은 제품 UI 가 아니다. IdleEvent 파이프라인이 실제로 도는지 확인하기 위한
 * 최소 경로다: 자산 조회/생성 → <video> 마운트 → trigger("BLINKING").
 *
 * 자발적 스케줄링은 여기 **없다**. 사람이 명시적으로 부를 때만 동작한다.
 *
 * come-closer-autogen.ts 를 일반화하지 않고 별도 모듈로 둔 이유: 그쪽은
 * 프로덕션 경로이고 멱등성 가드(inflight/attempted)와 폴링이 COME_CLOSER 계약에
 * 맞춰져 있다. Phase 1A 에서 그걸 건드리면 검증된 동작이 위험해진다. 개발용
 * 경로는 얇게 따로 간다.
 */

import type { CutoutPipelineLike } from "./cutout-remote-asset.ts";
import { ensureRemoteCutoutUrl, isRemoteAssetUrl } from "./cutout-remote-asset.ts";
import type { IdleEvent } from "./pet-runtime-events.ts";

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

export type IdleEventAssetState =
  | "idle"
  | "checking"
  /** 펫당 동시 생성 상한에 걸려 **아직 제출되지 않았다**. generating 이 아니다. */
  | "queued"
  | "generating"
  | "ready"
  | "unavailable"
  | "error";

export interface IdleEventAssetResult {
  state: IdleEventAssetState;
  url: string | null;
}

export interface EnsureIdleEventParams {
  userId: string;
  petId: string | null;
  eventId: IdleEvent;
  pipeline: CutoutPipelineLike | null;
  onState?: (state: IdleEventAssetState) => void;
}

/** 같은 키의 동시 호출은 하나로 접는다 (StrictMode 이중 effect). */
const inflight = new Map<string, Promise<IdleEventAssetResult>>();
/** 이번 세션에 **실제로 제출에 성공한** 키. 제출된 작업을 중복 제출하지 않기 위한 것이며,
 *  실패·큐대기는 여기 들어가지 않는다(들어가면 영영 재시도되지 않는다). */
const attempted = new Set<string>();
/**
 * 키별 **제출 실패** 횟수. 일시적 실패(5xx·네트워크)는 다시 시도해야 하지만,
 * 무한 재제출 루프는 막아야 한다 — 그 사이를 상한으로 가른다.
 */
const failures = new Map<string, number>();

/** 제출 시도 상한. 초과하면 더 이상 제출하지 않고 error 로 보고한다. */
export const MAX_SUBMIT_ATTEMPTS = 3;

export function idleEventKey(userId: string, petId: string | null, eventId: IdleEvent): string {
  return `${userId}|${petId ?? ""}|${eventId}`;
}

/** 테스트 전용 — 모듈 수준 가드 초기화. */
export function __resetIdleEventGuards(): void {
  inflight.clear();
  attempted.clear();
  failures.clear();
}

async function lookup(p: EnsureIdleEventParams): Promise<{ url: string | null; disabled: boolean }> {
  const qs = new URLSearchParams({ action_id: p.eventId });
  if (p.petId) qs.set("pet_id", p.petId);
  try {
    const res = await fetch(
      `${apiBase()}/api/v1/pet/dev/come-closer/${encodeURIComponent(p.userId)}?${qs}`,
      { method: "GET", cache: "no-store" }
    );
    if (res.status === 404) return { url: null, disabled: true };
    if (!res.ok) return { url: null, disabled: false };
    const b = (await res.json()) as { video_url?: string | null };
    return { url: b.video_url?.trim() || null, disabled: false };
  } catch {
    return { url: null, disabled: false };
  }
}

/**
 * 아이들 이벤트 자산을 확보한다. 없으면 정확히 1회 생성 제출.
 *
 * 서버가 멱등성의 최종 권위다(dev_premium 의 canonical/진행중 검사) — 여기 가드는
 * 불필요한 왕복을 줄일 뿐이다.
 */
export async function ensureIdleEventAsset(
  params: EnsureIdleEventParams
): Promise<IdleEventAssetResult> {
  const { userId, petId, eventId, onState } = params;
  const emit = (s: IdleEventAssetState) => onState?.(s);

  if (!userId?.trim()) {
    emit("idle");
    return { state: "idle", url: null };
  }

  const k = idleEventKey(userId, petId, eventId);
  const running = inflight.get(k);
  if (running) return running;

  const task = (async (): Promise<IdleEventAssetResult> => {
    emit("checking");

    const first = await lookup(params);
    if (first.url) {
      emit("ready");
      return { state: "ready", url: first.url };
    }
    if (first.disabled) {
      emit("unavailable");
      return { state: "unavailable", url: null };
    }
    if (attempted.has(k)) {
      emit("generating");
      return { state: "generating", url: null };
    }
    if ((failures.get(k) ?? 0) >= MAX_SUBMIT_ATTEMPTS) {
      // 반복 실패 — 재제출 루프를 돌지 않는다. **generating 이라고 거짓말하지도 않는다**:
      // 제출된 작업이 없는데 generating 으로 보고하면 영원히 생성 중으로 보인다.
      emit("error");
      return { state: "error", url: null };
    }

    // 백엔드가 가져갈 수 있으려면 누끼가 원격 URL 이어야 한다.
    let petImageUrl = params.pipeline?.dog_only_nobg_url ?? null;
    if (!isRemoteAssetUrl(petImageUrl)) {
      const ensured = await ensureRemoteCutoutUrl(params.pipeline ?? null, { userId });
      petImageUrl = ensured.url;
    }
    if (!petImageUrl) {
      emit("error");
      return { state: "error", url: null };
    }

    try {
      const res = await fetch(`${apiBase()}/api/v1/pet/dev/come-closer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: userId,
          pet_id: petId ?? undefined,
          action_id: eventId,
          pet_image_url: petImageUrl,
        }),
      });
      if (res.status === 404) {
        emit("unavailable");
        return { state: "unavailable", url: null };
      }
      if (!res.ok) {
        failures.set(k, (failures.get(k) ?? 0) + 1);
        emit("error");
        return { state: "error", url: null };
      }
      const b = (await res.json()) as { status?: string; video_url?: string | null };
      if (b.status === "ready" && b.video_url) {
        emit("ready");
        return { state: "ready", url: b.video_url };
      }
      if (b.status === "queued") {
        // 아직 프로바이더를 부르지 않았다 — attempted 에 넣지 않았으므로
        // 다음 스윕에서 자연히 다시 제출을 시도한다.
        emit("queued");
        return { state: "queued", url: null };
      }
      // **여기서만** 표시한다 — 프로바이더 작업이 실제로 생겼을 때만.
      // 예전에는 fetch 전에 표시해서, 404·5xx·네트워크 오류로 제출이 실패해도
      // 키가 남았다. 그러면 이후 모든 스윕이 재제출 없이 "generating" 만
      // 보고했고, 작업 행이 하나도 없는 이벤트가 영원히 생성 중으로 보였다.
      attempted.add(k);
      emit("generating");
      return { state: "generating", url: null };
    } catch {
      failures.set(k, (failures.get(k) ?? 0) + 1);
      emit("error");
      return { state: "error", url: null };
    }
  })();

  inflight.set(k, task);
  try {
    return await task;
  } finally {
    inflight.delete(k);
  }
}
