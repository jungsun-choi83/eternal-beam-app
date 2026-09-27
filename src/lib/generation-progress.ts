/**
 * Phase 4 (UI) — generation-run 상태를 사용자용 진행 단계로 옮긴다.
 *
 * 백엔드 원본 값(backend/services/pet_generation_run_service.py):
 *   status:        QUEUED | RUNNING | WAITING_PROVIDER | RECOVERY_REQUIRED |
 *                  PUBLISHED | FAILED | CANCELLED
 *   current_stage: QUEUED | IDENTITY | REFERENCE_SET | CANONICAL | KEYFRAMES |
 *                  MOTION_SPEC | MOTION_GENERATION | QA | DELIVERY |
 *                  PUBLICATION | PUBLISHED
 *   provider_state: { [jobId]: { provider_operation, provider_status, ... } }
 *     provider_status 는 provider 원문(fal 큐 등)이라 IN_QUEUE / IN_PROGRESS /
 *     COMPLETED 처럼 현재_stage 와 다른 어휘를 쓴다
 *     (backend/services/video_motion_providers.py 참고).
 *
 * 이 모듈은 그 어휘를 화면이 보여줄 7단계로 접는다. 프로바이더 이름(fal, Wan,
 * GPT Image 등)은 절대 여기서 밖으로 나가지 않는다 — provider_operation 값만
 * "지금 뭘 만들고 있는가"를 고르는 내부 키로 쓰인다.
 */

export type GenerationStageKey =
  | "preparing_pet"
  | "creating_appearance"
  | "waiting_capacity"
  | "generating_motion"
  | "checking_result"
  | "rendering"
  | "complete";

/** 화면에 그리는 순서 그대로 — stepper 의 index 가 곧 이 배열의 index. */
export const GENERATION_STAGE_ORDER: readonly GenerationStageKey[] = [
  "preparing_pet",
  "creating_appearance",
  "waiting_capacity",
  "generating_motion",
  "checking_result",
  "rendering",
  "complete",
];

export interface ProviderJobSummary {
  provider_operation?: string | null;
  provider_status?: string | null;
  submitted_at?: string | null;
  last_polled_at?: string | null;
  [key: string]: unknown;
}

export interface GenerationProgressRunLike {
  status?: string | null;
  current_stage?: string | null;
  provider_state?: Record<string, ProviderJobSummary> | null;
  last_error?: { code?: string | null; message?: string | null } | null;
}

export type GenerationProgressView =
  | { kind: "stage"; stage: GenerationStageKey; waiting: boolean }
  | { kind: "error"; recoverable: boolean; message: string | null };

/** current_stage(백엔드) → 화면 7단계. 없거나 모르는 값은 "preparing_pet"으로 — 아직 아무 것도 못 봤을 때의 안전한 기본값이다. */
const STAGE_TO_UI: Record<string, GenerationStageKey> = {
  QUEUED: "preparing_pet",
  IDENTITY: "preparing_pet",
  REFERENCE_SET: "preparing_pet",
  CANONICAL: "creating_appearance",
  KEYFRAMES: "creating_appearance",
  MOTION_SPEC: "generating_motion",
  MOTION_GENERATION: "generating_motion",
  QA: "checking_result",
  DELIVERY: "rendering",
  PUBLICATION: "rendering",
  PUBLISHED: "complete",
};

/** 그 단계에서 실제로 도는 provider job 의 operation 키 (durable_provider_jobs.py 의 OP_*). */
const STAGE_PROVIDER_OP: Partial<Record<string, string>> = {
  CANONICAL: "CANONICAL_IMAGE",
  KEYFRAMES: "KEYFRAME_IMAGE",
  MOTION_GENERATION: "MOTION_VIDEO",
};

const TERMINAL_ERROR_STATUS = new Set(["FAILED", "CANCELLED"]);

/** 같은 operation 의 job 이 여럿이면 가장 최근에 갱신된 것을 대표로 본다. */
export function latestProviderStatus(
  providerState: Record<string, ProviderJobSummary> | null | undefined,
  operation: string | undefined
): string | null {
  if (!providerState || !operation) return null;
  let best: ProviderJobSummary | null = null;
  let bestKey = "";
  for (const job of Object.values(providerState)) {
    if (!job || job.provider_operation !== operation) continue;
    const key = String(job.last_polled_at || job.submitted_at || "");
    if (!best || key >= bestKey) {
      best = job;
      bestKey = key;
    }
  }
  const status = best?.provider_status;
  return status ? String(status).toUpperCase() : null;
}

export function isRecoverableErrorCode(code: string | null | undefined): boolean {
  return String(code || "").toUpperCase() === "RECOVERY_REQUIRED";
}

/**
 * generation-run 상태(또는 아직 하나도 못 받은 null)를 화면 뷰로 옮긴다.
 * 순수 함수 — 어떤 값이 들어와도 던지지 않는다(모르는 stage/status 는 안전한
 * 기본값으로 접는다).
 */
export function deriveGenerationProgress(
  run: GenerationProgressRunLike | null | undefined
): GenerationProgressView {
  if (!run) return { kind: "stage", stage: "preparing_pet", waiting: false };

  const status = String(run.status || "").toUpperCase();

  if (TERMINAL_ERROR_STATUS.has(status)) {
    return {
      kind: "error",
      recoverable: false,
      message: run.last_error?.message ?? null,
    };
  }
  if (status === "RECOVERY_REQUIRED") {
    return {
      kind: "error",
      recoverable: true,
      message: run.last_error?.message ?? null,
    };
  }

  const rawStage = String(run.current_stage || "").toUpperCase();
  const stage = STAGE_TO_UI[rawStage] ?? "preparing_pet";
  if (stage === "complete") {
    return { kind: "stage", stage: "complete", waiting: false };
  }

  const operation = STAGE_PROVIDER_OP[rawStage];
  const providerStatus = latestProviderStatus(run.provider_state, operation);
  const waiting =
    status === "WAITING_PROVIDER" ||
    providerStatus === "IN_QUEUE" ||
    providerStatus === "PENDING";

  // "생성 자리 대기" 는 모션 생성 단계에서만 별도 스텝으로 승격한다 — 다른
  // 단계(사진 준비)의 대기는 그 단계 안에서 배지로만 알려 주고, stepper 를
  // 앞뒤로 튀게 하지 않는다.
  if (waiting && stage === "generating_motion") {
    return { kind: "stage", stage: "waiting_capacity", waiting: true };
  }

  return { kind: "stage", stage, waiting };
}
