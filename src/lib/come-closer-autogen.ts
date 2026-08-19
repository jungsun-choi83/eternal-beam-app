/**
 * 새 업로드 → COME_CLOSER 자동 생성 (정확히 1회).
 *
 * 배경: 사진을 새로 올리면 content_id → pet_id 가 바뀌는데, COME_CLOSER 는 예전
 * pet 아래에만 있었다. 그래서 새 펫에서는 액션 <video> 가 아예 안 붙고 더블탭이
 * 조용히 아무 일도 하지 않았다. 이 모듈이 그 빈칸을 메운다.
 *
 * 생성 키는 **(user_id, pet_id, COME_CLOSER)** — 테마 독립이다. 검정 플레이트 위
 * 펫만 생성하므로 결과물이 배경과 무관하고, 같은 펫이면 어떤 테마에서도 같은
 * 클립 하나를 재사용한다. 테마를 바꿔도 재조회·재생성이 일어나지 않는다.
 *
 * 다만 **펫이 바뀌면 절대 재사용하지 않는다** — 예전 업로드의 자산을 새 content 에
 * 붙이면 다른 사진에서 만든 클립이라 펫이 바뀌어 보인다.
 *
 * ── 중복 제출 방지 ────────────────────────────────────────────────────────
 * 서버가 최종 권위다 (dev_premium 의 canonical/진행중 검사). 여기 가드는 불필요한
 * 왕복을 줄이기 위한 것:
 *   inflight  — 같은 키의 동시 호출은 하나의 프라미스를 공유 (StrictMode 이중 effect)
 *   attempted — **제출에 성공한** 키만 기록. 실패/큐대기는 기록하지 않아 재시도된다
 */

// 확장자를 명시한다: node --test 는 확장자 없는 로컬 import 를 풀지 못한다
// (Vite 는 푼다). tsconfig 의 allowImportingTsExtensions 덕에 양쪽 다 동작한다.
import {
  ensureRemoteCutoutUrl,
  isRemoteAssetUrl,
  type CutoutPipelineLike,
} from "./cutout-remote-asset.ts";

export type ComeCloserState =
  | "idle"          // 아직 확인 전
  | "checking"      // canonical 존재 확인 중
  | "queued"        // 펫당 동시 생성 상한 대기 — **아직 제출되지 않았다**
  | "generating"    // 제출됨 / 진행 중 — 아직 재생 불가
  | "ready"         // canonical 확보 — 더블탭 가능
  | "unavailable"   // 생성 경로가 꺼져 있음(dev 트리거 off 등)
  | "error";        // 실패 — 자동 재제출하지 않는다

export type EnsureParams = {
  userId: string;
  petId: string | null;
  pipeline: CutoutPipelineLike | null;
  onState?: (state: ComeCloserState) => void;
};

export type EnsureResult = { state: ComeCloserState; url: string | null };

const inflight = new Map<string, Promise<EnsureResult>>();
const attempted = new Set<string>();
/**
 * 키별 **제출 실패** 횟수. 일시적 실패(5xx·네트워크)는 다시 시도해야 하지만,
 * 무한 재제출 루프는 막아야 한다 — 그 사이를 상한으로 가른다.
 */
const failures = new Map<string, number>();

/** 제출 시도 상한. 초과하면 더 이상 제출하지 않고 error 로 보고한다. */
export const MAX_SUBMIT_ATTEMPTS = 3;

/**
 * 생성 키 — 백엔드 논리 키와 같은 구성이다: (user_id, pet_id, COME_CLOSER).
 *
 * place 가 **없다**. COME_CLOSER 는 검정 플레이트 위 펫만 생성하므로 결과물이
 * 배경과 무관하고, 같은 펫이면 어떤 테마에서도 같은 클립 하나를 재사용한다.
 * 테마를 바꿔도 키가 그대로라 재조회·재생성이 일어나지 않는다.
 */
export function comeCloserKey(userId: string, petId: string | null): string {
  return `${userId}|${petId ?? ""}|COME_CLOSER`;
}

/** 테스트 전용 — 모듈 수준 가드 초기화. */
export function __resetAutogenGuards(): void {
  inflight.clear();
  attempted.clear();
  failures.clear();
}

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

type LookupBody = { come_closer_video_url?: string | null; ready?: boolean };

/** GET 조회 — 승격된 canonical 이 있으면 URL. 없으면 null. 404 는 경로 꺼짐. */
async function lookup(
  p: EnsureParams
): Promise<{ url: string | null; disabled: boolean }> {
  const qs = new URLSearchParams();
  if (p.petId) qs.set("pet_id", p.petId);
  try {
    const res = await fetch(
      `${apiBase()}/api/v1/pet/dev/come-closer/${encodeURIComponent(p.userId)}?${qs}`,
      { method: "GET", cache: "no-store" }
    );
    if (res.status === 404) return { url: null, disabled: true };
    if (!res.ok) return { url: null, disabled: false };
    const b = (await res.json()) as LookupBody;
    const url = b.come_closer_video_url?.trim();
    return { url: url || null, disabled: false };
  } catch {
    return { url: null, disabled: false };
  }
}

/**
 * COME_CLOSER 자산 **조회 전용**. 절대 생성하지 않고 절대 과금하지 않는다.
 *
 * 화면 마운트/폴링 경로가 쓰는 함수다. 확정된 사업 모델에서 COME_CLOSER 는
 * 1 크레딧짜리 액션 구매이므로, 화면을 열었다는 이유로 생성이 시작되면 안 된다.
 * 새 생성은 lib/premium-assets.ts 의 purchasePremium(actionKind("COME_CLOSER")) 이
 * 사용자 조작에서만 한다.
 */
export async function lookupComeCloserAsset(
  params: EnsureParams
): Promise<EnsureResult> {
  const { userId, petId, onState } = params;
  const emit = (s: ComeCloserState) => onState?.(s);

  if (!userId?.trim()) {
    emit("idle");
    return { state: "idle", url: null };
  }

  emit("checking");
  const found = await lookup(params);
  if (found.url) {
    emit("ready");
    return { state: "ready", url: found.url };
  }
  if (found.disabled) {
    emit("unavailable");
    return { state: "unavailable", url: null };
  }
  const pending = attempted.has(comeCloserKey(userId, petId));
  emit(pending ? "generating" : "idle");
  return { state: pending ? "generating" : "idle", url: null };
}

/**
 * 정확히 1회 자동 생성.
 *
 * ⚠️ **무과금 개발 경로 전용이다** (/v1/pet/dev, ENABLE_DEV_PREMIUM_TRIGGER).
 * 프로덕션 유료 생성은 purchasePremium() 을 쓴다 — 인증·소유권·1크레딧 과금·
 * 구매 원장 멱등성을 거친다. 화면 effect 에서 이 함수를 부르면 안 된다.
 *
 * 생성한다:  canonical 없음 + 진행 중 없음 + 이번 세션에 시도한 적 없음
 * 아무것도 안 한다: canonical 있음 / 진행 중 / 이미 시도함 / 신원·누끼 없음 / 경로 꺼짐
 */
export async function ensureComeCloser(params: EnsureParams): Promise<EnsureResult> {
  const { userId, petId, onState } = params;
  const emit = (s: ComeCloserState) => onState?.(s);

  if (!userId?.trim()) {
    emit("idle");
    return { state: "idle", url: null };
  }

  const k = comeCloserKey(userId, petId);
  const running = inflight.get(k);
  if (running) return running; // StrictMode 이중 effect·동시 렌더 → 한 번만

  const task = (async (): Promise<EnsureResult> => {
    emit("checking");

    // 1) 이미 승격된 자산이 있으면 끝.
    const first = await lookup(params);
    if (first.url) {
      emit("ready");
      return { state: "ready", url: first.url };
    }
    if (first.disabled) {
      emit("unavailable");
      return { state: "unavailable", url: null };
    }

    // 2) 이번 세션에 이미 시도했으면 다시 제출하지 않는다 (실패 루프 금지).
    //    서버 쪽 진행 중 작업은 어차피 아래 POST 가 멱등으로 걸러 준다.
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

    // 3) 누끼가 원격이어야 백엔드가 가져갈 수 있다. data: 면 기존 바이트를 1회
    //    업로드한다 — 재누끼/모델 재실행은 하지 않는다.
    let petImageUrl = params.pipeline?.dog_only_nobg_url ?? null;
    if (!isRemoteAssetUrl(petImageUrl)) {
      const ensured = await ensureRemoteCutoutUrl(params.pipeline ?? null, { userId });
      petImageUrl = ensured.url;
    }
    if (!petImageUrl) {
      emit("error");
      return { state: "error", url: null };
    }

    // 4) 제출. 서버가 canonical/진행중을 다시 검사하므로 여기서 새는 호출이
    //    있어도 프로바이더가 두 번 불리지는 않는다.
    try {
      const res = await fetch(`${apiBase()}/api/v1/pet/dev/come-closer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: userId,
          pet_id: petId ?? undefined,
          // selected_place_id 는 보내지 않는다 — 이 액션은 테마 독립이다.
          pet_image_url: petImageUrl,
        }),
      });
      if (res.status === 404) {
        emit("unavailable");
        return { state: "unavailable", url: null };
      }
      if (res.status === 400) {
        // 테마 독립이 된 뒤로 COME_CLOSER 에서는 PLACE_NOT_SUPPORTED 가 나오지
        // 않는다(장소를 아예 보내지 않는다). 다른 프리미엄 액션이 생겼을 때를
        // 대비한 가드로만 남긴다 — 재시도해도 성공할 수 없는 400 은 error 가 아니다.
        let code = "";
        try {
          const b = (await res.json()) as { detail?: { code?: string } };
          code = b?.detail?.code || "";
        } catch {
          /* 본문이 없어도 판단은 아래에서 한다 */
        }
        const unsupported = code === "PLACE_NOT_SUPPORTED";
        emit(unsupported ? "unavailable" : "error");
        return { state: unsupported ? "unavailable" : "error", url: null };
      }
      if (!res.ok) {
        failures.set(k, (failures.get(k) ?? 0) + 1);
        emit("error");
        return { state: "error", url: null };
      }
      const b = (await res.json()) as {
        status?: string;
        come_closer_video_url?: string | null;
      };
      if (b.status === "ready" && b.come_closer_video_url) {
        emit("ready");
        return { state: "ready", url: b.come_closer_video_url };
      }
      if (b.status === "queued") {
        // 프로바이더 호출이 아직 없었다 — attempted 에 넣지 않았으므로 재시도된다.
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

/**
 * 생성이 끝날 때까지 조회를 반복한다 (요구사항 9 — 수동 새로고침 없이 반영).
 * 유한하다: attempts 회까지만. 취소되면 즉시 멈춘다.
 */
export async function pollComeCloserUntilReady(
  params: EnsureParams & { attempts?: number; intervalMs?: number; isCancelled?: () => boolean }
): Promise<string | null> {
  const attempts = params.attempts ?? 40;
  const intervalMs = params.intervalMs ?? 15_000;
  for (let i = 0; i < attempts; i += 1) {
    if (params.isCancelled?.()) return null;
    await new Promise((r) => setTimeout(r, intervalMs));
    if (params.isCancelled?.()) return null;
    const { url } = await lookup(params);
    if (url) {
      params.onState?.("ready");
      return url;
    }
  }
  return null;
}
