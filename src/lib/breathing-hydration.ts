/**
 * 발행된 BREATHING 하이드레이션 (Phase 7F).
 *
 * ── 왜 필요한가 ──────────────────────────────────────────────────────────────
 * 지금까지 브라우저의 BREATH 소스는 sessionStorage 파이프라인에 저장된 서명
 * URL 하나였다. 서명은 만료된다 — 자산과 발행 포인터는 살아 있는데 그 사이의
 * 서명만 죽는 상태가 생긴다. 그리고 Phase 7A 가 서버에 발행한 BREATHING
 * (pets.breathing_*)은 브라우저로 돌아오는 읽기 경로가 아예 없었다.
 *
 * 이 모듈이 그 읽기 경로다:
 *
 *   발행 포인터(pets.breathing_*) → GET /v1/pet/motions/{pet}/BREATHING/published
 *   → 지금 유효한 서명 URL + 명시 delivery_format
 *   → StoredPipeline.idle_video_url / background_baked / delivery_format 갱신
 *   → 기존 재생 경로 그대로
 *
 * ── 하지 않는 것 ─────────────────────────────────────────────────────────────
 * 생성·발행·포장을 트리거하지 않는다. identity/canonical/keyframe/후보 같은
 * 서버 내부 계보는 브라우저로 가져오지 않는다 — 재생에 필요한 값만 받는다.
 * 실패는 조용히 무시된다: 하이드레이션은 개선이지 게이트가 아니다. 서버가
 * 404(미발행)를 주면 기존 파이프라인이 그대로 재생된다.
 */

import { getPremiumAccessToken } from "./premium-auth-token.ts";
import { getEternalBeamPetId } from "./pet-identity.ts";

/** ai-processing-screen 의 StoredPipeline 과 같은 키. (come-closer-asset 과 같은 관례) */
export const PIPELINE_STORAGE_KEY = "eternal_beam_pipeline_v1";

/** 하이드레이션이 읽고 쓰는 필드만 — 나머지 파이프라인 필드는 그대로 보존된다. */
export interface HydratablePipeline {
  content_id?: string | null;
  idle_video_url?: string | null;
  background_baked?: boolean;
  delivery_format?: string | null;
  phase1_intake?: { pet_id?: string | null } | null;
  [key: string]: unknown;
}

export interface PublishedBreathingResponse {
  pet_id: string;
  motion_id: string;
  breathing_bucket: string;
  breathing_object_path: string;
  url: string;
  background_baked: boolean;
  motion_version_id?: string | null;
  delivery_format?: string | null;
  publication_id?: string | null;
  content_id?: string | null;
}

export interface HydrationDeps {
  fetchFn?: typeof globalThis.fetch;
  getToken?: typeof getPremiumAccessToken;
  apiBase?: string;
}

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

/** 하이드레이션 대상 펫 — Phase 7B 영수증이 1순위, 없으면 결정론 파생 id. */
export function resolveHydrationPetId(
  pipeline: HydratablePipeline | null | undefined
): string | null {
  const receipt = (pipeline?.phase1_intake?.pet_id || "").toString().trim();
  if (receipt) return receipt;
  return getEternalBeamPetId(pipeline?.content_id ?? null);
}

/**
 * 서버 응답을 파이프라인에 적용한다. **순수 함수** — 나머지 필드는 그대로다.
 *
 * 서버 포인터가 정본이다: URL(새 서명), background_baked(pets 행),
 * delivery_format(명시 선언) 세 값만 바뀐다. 응답 URL 이 비면 아무것도
 * 바꾸지 않는다 — 죽은 값으로 살아 있는 재생을 덮을 이유가 없다.
 */
export function applyPublishedBreathing<T extends HydratablePipeline>(
  pipeline: T,
  published: PublishedBreathingResponse
): T {
  const url = (published.url || "").trim();
  if (!url) return pipeline;
  return {
    ...pipeline,
    idle_video_url: url,
    background_baked: published.background_baked === true,
    delivery_format: published.delivery_format ?? null,
  };
}

/**
 * 발행된 BREATHING 을 서버에서 읽는다. 실패 시 null — 절대 던지지 않는다.
 *
 * 404 = 미발행(정상), 401/403 = 세션 없음/남의 펫 — 전부 "하이드레이션 없음"
 * 으로 같게 다룬다. 이 함수의 실패로 재생이 나빠질 수는 없다(기존 값 유지).
 */
export async function fetchPublishedBreathing(
  petId: string,
  deps: HydrationDeps = {}
): Promise<PublishedBreathingResponse | null> {
  const fetchFn = deps.fetchFn ?? globalThis.fetch;
  if (!fetchFn || !petId) return null;
  const getToken = deps.getToken ?? getPremiumAccessToken;
  try {
    const auth = await getToken();
    if (!auth.token) return null;
    const base = deps.apiBase ?? apiBase();
    const response = await fetchFn(
      `${base}/api/v1/pet/motions/${encodeURIComponent(petId)}/BREATHING/published`,
      { headers: { Authorization: `Bearer ${auth.token}` } }
    );
    if (!response.ok) return null;
    const body = (await response.json()) as PublishedBreathingResponse;
    if (!body || typeof body.url !== "string" || !body.url.trim()) return null;
    return body;
  } catch {
    return null;
  }
}

function readStoredPipeline(): HydratablePipeline | null {
  try {
    const raw = sessionStorage.getItem(PIPELINE_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as HydratablePipeline) : null;
  } catch {
    return null;
  }
}

function writeStoredPipeline(pipeline: HydratablePipeline): void {
  try {
    sessionStorage.setItem(PIPELINE_STORAGE_KEY, JSON.stringify(pipeline));
  } catch {
    /* 저장 실패해도 반환값으로 화면은 갱신된다 */
  }
}

/**
 * sessionStorage 파이프라인을 발행 포인터로 하이드레이션한다.
 *
 * 갱신이 있었으면 새 파이프라인을 반환하고(저장까지 마친 뒤), 발행이 없거나
 * 달라진 것이 없으면 null 을 반환한다 — 호출부는 null 이면 아무것도 안 해도
 * 된다.
 */
export async function hydrateStoredPipeline(
  deps: HydrationDeps = {}
): Promise<HydratablePipeline | null> {
  const pipeline = readStoredPipeline();
  if (!pipeline) return null;
  const petId = resolveHydrationPetId(pipeline);
  if (!petId) return null;
  const published = await fetchPublishedBreathing(petId, deps);
  if (!published) return null;
  const next = applyPublishedBreathing(pipeline, published);
  const unchanged =
    next.idle_video_url === pipeline.idle_video_url &&
    next.background_baked === pipeline.background_baked &&
    (next.delivery_format ?? null) === (pipeline.delivery_format ?? null);
  if (unchanged) return null;
  writeStoredPipeline(next);
  return next;
}
