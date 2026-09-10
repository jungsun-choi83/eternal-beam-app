/**
 * Phase 7G — 생성 실행(generation-run) 클라이언트.
 *
 * 새 펫 흐름의 생성은 이제 이 API 하나로 나간다:
 *
 *   POST /v1/pet/generation-runs           실행 생성/재사용 (멱등)
 *   GET  /v1/pet/generation-runs/{id}      상태 폴링 (워커가 Phase 2–6 을 수행)
 *   GET  /v1/pet/generation-runs/{id}/playback   재생 해석 (발행 or 개발/REVIEW)
 *
 * 브라우저는 Phase 2–6 을 **직접 오케스트레이션하지 않는다** — 실행 하나를
 * 만들고 상태만 본다. 레거시 생성 엔드포인트는 이 모듈이 절대 호출하지
 * 않으며, 실패 시에도 그쪽으로 떨어지지 않는다.
 */

import { getPremiumAccessToken } from "./premium-auth-token.ts";

export interface GenerationRun {
  run_id: string;
  status: string;
  current_stage: string;
  pet_id: string;
  motion_version_id?: string | null;
  selected_candidate_id?: string | null;
  publication_id?: string | null;
  last_error?: { code?: string; message?: string } | null;
  [key: string]: unknown;
}

export interface RunPlayback {
  run_id: string;
  status: string;
  /** true = Phase 7A 발행 재생. false = 발행 없는 개발/현재-실행 재생(REVIEW). */
  published: boolean;
  /** 데이터베이스의 실제 QA 결정 (PASS | REVIEW). 가공되지 않는다. */
  qa_decision: string;
  url: string;
  delivery_format?: string | null;
  background_baked: boolean;
  motion_version_id?: string | null;
  candidate_id?: string | null;
}

export interface RunApiDeps {
  fetchFn?: typeof globalThis.fetch;
  getToken?: typeof getPremiumAccessToken;
  apiBase?: string;
}

export class GenerationRunError extends Error {
  readonly code?: string;
  readonly status?: number;

  constructor(message: string, code?: string, status?: number) {
    super(message);
    this.name = "GenerationRunError";
    this.code = code;
    this.status = status;
  }
}

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

async function authedRequest<T>(
  path: string,
  init: RequestInit,
  deps: RunApiDeps
): Promise<T> {
  const fetchFn = deps.fetchFn ?? globalThis.fetch;
  const getToken = deps.getToken ?? getPremiumAccessToken;
  const auth = await getToken();
  if (!auth.token) {
    throw new GenerationRunError("로그인이 필요합니다.", "NO_SESSION", 401);
  }
  const base = deps.apiBase ?? apiBase();
  const response = await fetchFn(`${base}${path}`, {
    ...init,
    headers: {
      ...(init.headers as Record<string, string> | undefined),
      Authorization: `Bearer ${auth.token}`,
    },
  });
  if (!response.ok) {
    let code: string | undefined;
    let message = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: { code?: string; message?: string } };
      code = body.detail?.code;
      message = body.detail?.message || message;
    } catch {
      /* status 만으로도 유용하다 */
    }
    throw new GenerationRunError(message, code, response.status);
  }
  return (await response.json()) as T;
}

/** 실행 생성/재사용. 같은 idempotency_key 는 같은 실행을 돌려준다 — 이중 과금 없음. */
export async function startGenerationRun(
  params: { petId: string; idempotencyKey: string },
  deps: RunApiDeps = {}
): Promise<GenerationRun> {
  return authedRequest<GenerationRun>(
    "/api/v1/pet/generation-runs",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pet_id: params.petId,
        motion_id: "BREATHING",
        request_kind: "FREE_HOME",
        idempotency_key: params.idempotencyKey,
      }),
    },
    deps
  );
}

export async function getGenerationRun(
  runId: string,
  deps: RunApiDeps = {}
): Promise<GenerationRun> {
  return authedRequest<GenerationRun>(
    `/api/v1/pet/generation-runs/${encodeURIComponent(runId)}`,
    { method: "GET" },
    deps
  );
}

export async function getRunPlayback(
  runId: string,
  deps: RunApiDeps = {}
): Promise<RunPlayback> {
  return authedRequest<RunPlayback>(
    `/api/v1/pet/generation-runs/${encodeURIComponent(runId)}/playback`,
    { method: "GET" },
    deps
  );
}

/** 실행이 끝났는가 — 워커가 더 진행시키지 않는 상태들. */
export function isTerminalRunStatus(status: string): boolean {
  return ["PUBLISHED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"].includes(status);
}

export interface PollOptions {
  /** 폴링 간격(ms). 기본 5초 — 워커 tick(10초)의 절반. */
  intervalMs?: number;
  /** 총 대기 한도(ms). 기본 30분 — 레거시 25분 동기 대기와 같은 규모. */
  timeoutMs?: number;
  onProgress?: (run: GenerationRun) => void;
  /** 테스트 주입용 sleep. */
  sleep?: (ms: number) => Promise<void>;
}

/** 실행이 종료 상태가 될 때까지 폴링한다. 타임아웃이면 GenerationRunError. */
export async function pollGenerationRun(
  runId: string,
  options: PollOptions = {},
  deps: RunApiDeps = {}
): Promise<GenerationRun> {
  const interval = Math.max(1000, options.intervalMs ?? 5000);
  const timeout = Math.max(interval, options.timeoutMs ?? 30 * 60 * 1000);
  const sleep =
    options.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const startedAt = Date.now();
  for (;;) {
    const run = await getGenerationRun(runId, deps);
    options.onProgress?.(run);
    if (isTerminalRunStatus(run.status)) return run;
    if (Date.now() - startedAt >= timeout) {
      throw new GenerationRunError(
        "생성 실행이 제한 시간 안에 끝나지 않았습니다.",
        "RUN_POLL_TIMEOUT",
        408
      );
    }
    await sleep(interval);
  }
}
