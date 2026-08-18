/**
 * COME_CLOSER (프리미엄 액션) 자산을 세션 파이프라인에 합쳐 넣는다.
 *
 * 배경: dev 트리거가 생성·승격까지 끝내고 GET 으로 URL 을 돌려주지만, 그 값을
 * sessionStorage 의 파이프라인에 넣어 주는 코드가 없었다. 그래서
 * preview-screen 이 항상 come_closer_video_url = null 을 보고, 더블탭이
 * 아무 일도 하지 않았다. 이 모듈이 그 한 칸을 잇는다.
 *
 * 서버 쪽 트리거가 꺼져 있으면(GET 404) 조용히 null 을 돌려준다 — 기존 BREATH
 * 전용 동작 그대로다. 프롬프트·프로바이더·과금과는 무관하다.
 */

/**
 * 세션 파이프라인 키. ai-processing-screen.tsx 의 ETERNAL_BEAM_PIPELINE_KEY 와
 * **같은 문자열**이어야 한다. 여기서 import 하지 않는 이유는 그쪽이 `@/` 별칭을
 * 쓰는 컴포넌트라 node:test 에서 해석되지 않기 때문이다(pet-ready-payload.ts 와
 * 같은 이유). 아래 테스트가 두 값이 어긋나지 않게 잡아 준다.
 */
export const PIPELINE_STORAGE_KEY = "eternal_beam_pipeline_v1";

/** 이 모듈이 실제로 건드리는 필드만 구조적으로 요구한다. */
export type PipelineLike = {
  idle_video_url?: string;
  come_closer_video_url?: string | null;
  /** 위 URL 이 **어느 펫의 것인지**. 없으면 출처 불명 = 신뢰하지 않는다. */
  come_closer_pet_id?: string | null;
  [k: string]: unknown;
};

/**
 * 캐시된 COME_CLOSER URL 을 그대로 믿어도 되는가.
 *
 * 예전에는 값이 truthy 이기만 하면 조회를 건너뛰었다. 그래서 새 사진을 올려
 * pet 이 바뀌어도 이전 펫의 URL 이 세션에 남아 조회를 영원히 막았고, 만료된
 * 서명 URL 도 갱신될 길이 없었다. 출처 펫이 일치할 때만 신뢰한다.
 */
export function isComeCloserCacheValid(
  pipeline: PipelineLike | null,
  petId: string | null,
): boolean {
  if (!pipeline?.come_closer_video_url) return false;
  const owner = (pipeline.come_closer_pet_id || "").trim();
  return !!owner && owner === (petId || "").trim();
}

/** dev GET 응답에서 우리가 쓰는 부분만. */
type ComeCloserLookup = {
  come_closer_video_url?: string | null;
  ready?: boolean;
};

/**
 * 조회가 실패하는 방식은 여러 가지인데 전부 null 로 뭉개면 브라우저에서
 * 원인을 구분할 수 없다. 실제로 이것 때문에 "dev 트리거가 꺼진 것"과
 * "신원이 안 맞는 것"과 "아직 생성 전인 것"을 눈으로 구분하지 못했다.
 *
 *   no-identity   user_id / place_id 가 비어 요청조차 하지 않았다
 *   disabled      HTTP 404 — ENABLE_DEV_PREMIUM_TRIGGER 꺼짐 또는 API base 불일치
 *   http-error    그 외 4xx/5xx
 *   network       fetch 자체 실패 (오프라인·CORS·프록시)
 *   not-generated 200 이지만 아직 승격된 자산이 없다 (신원/테마 불일치도 여기)
 *   ok            URL 확보
 */
export type ComeCloserReason =
  | "ok"
  | "no-identity"
  | "disabled"
  | "http-error"
  | "network"
  | "not-generated";

export type ComeCloserLookupResult = {
  url: string | null;
  reason: ComeCloserReason;
  /** HTTP 상태 (요청이 성립한 경우만). */
  status?: number;
  /** 실제로 조회에 쓴 키 — 불일치 진단의 핵심이라 항상 되돌려 준다. */
  query: { userId: string; placeId: string; petId: string | null };
};

/**
 * 조회 + 실패 원인. 절대 throw 하지 않는다.
 * fetchComeCloserUrl 은 이걸 감싸 URL 만 돌려주는 얇은 래퍼다.
 */
export async function lookupComeCloser(params: {
  userId: string;
  placeId: string;
  petId?: string | null;
}): Promise<ComeCloserLookupResult> {
  const userId = (params.userId || "").trim();
  const placeId = (params.placeId || "").trim();
  const petId = params.petId?.trim() || null;
  const query = { userId, placeId, petId };

  if (!userId || !placeId) return { url: null, reason: "no-identity", query };

  const qs = new URLSearchParams({ place_id: placeId });
  if (petId) qs.set("pet_id", petId);

  let res: Response;
  try {
    res = await fetch(
      `${apiBase()}/api/v1/pet/dev/come-closer/${encodeURIComponent(userId)}?${qs}`,
      { method: "GET", cache: "no-store" },
    );
  } catch {
    return { url: null, reason: "network", query };
  }

  if (!res.ok) {
    return {
      url: null,
      reason: res.status === 404 ? "disabled" : "http-error",
      status: res.status,
      query,
    };
  }

  let body: ComeCloserLookup;
  try {
    body = (await res.json()) as ComeCloserLookup;
  } catch {
    return { url: null, reason: "http-error", status: res.status, query };
  }

  const url = body.come_closer_video_url?.trim();
  return url
    ? { url, reason: "ok", status: res.status, query }
    : { url: null, reason: "not-generated", status: res.status, query };
}

function apiBase(): string {
  // import.meta.env 는 Vite 번들에서만 존재한다. node:test / SSR 에서는 없으므로
  // 접근 자체를 방어한다 — 예전에는 여기서 TypeError 가 나 catch 에 삼켜졌다.
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

/**
 * 승격된 COME_CLOSER URL 조회. 없거나 트리거가 꺼져 있으면 null.
 * 절대 throw 하지 않는다 — 프리미엄 액션이 없다고 미리보기가 깨지면 안 된다.
 */
export async function fetchComeCloserUrl(params: {
  userId: string;
  placeId: string;
  petId?: string | null;
}): Promise<string | null> {
  return (await lookupComeCloser(params)).url;
}

/** 세션 파이프라인에 come_closer_video_url 을 병합해 저장한다. */
export function mergeComeCloserIntoPipeline<T extends PipelineLike>(
  pipeline: T,
  comeCloserUrl: string | null,
  petId?: string | null,
): T {
  // 출처 펫을 같이 남긴다 — 이것이 없으면 다음 업로드에서 캐시를 무효화할 수 없다.
  const next = {
    ...pipeline,
    come_closer_video_url: comeCloserUrl,
    come_closer_pet_id: comeCloserUrl ? (petId ?? null) : null,
  } as T;
  try {
    sessionStorage.setItem(PIPELINE_STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* quota — 메모리 상태만으로도 재생에는 충분하다 */
  }
  return next;
}

/**
 * 이미 URL 이 있으면 조회하지 않는다(중복 요청 방지).
 * 새로 찾았을 때만 갱신된 파이프라인을 돌려준다.
 */
export async function resolveComeCloserForPipeline<T extends PipelineLike>(
  pipeline: T | null,
  params: {
    userId: string;
    placeId: string;
    petId?: string | null;
    /** 조회 결과 통보 (DEV 로깅용). 반환값 의미는 바뀌지 않는다. */
    onLookup?: (result: ComeCloserLookupResult) => void;
  },
): Promise<T | null> {
  if (!pipeline) return null;
  if (pipeline.come_closer_video_url) return null; // 이미 있음

  const result = await lookupComeCloser(params);
  params.onLookup?.(result);
  if (!result.url) return null;
  return mergeComeCloserIntoPipeline(pipeline, result.url);
}
