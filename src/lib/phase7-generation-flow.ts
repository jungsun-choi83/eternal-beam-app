/**
 * Phase 7G — 미리보기 확인 → 새 생성 시스템(Phase 1–7) 오케스트레이션.
 *
 * ── 대체 대상 ────────────────────────────────────────────────────────────────
 * 레거시: 확인 → 장면 굽기 → 레거시 생성 엔드포인트 (Luma, 25분 동기 대기).
 * 신규:   확인 → POST generation-runs → 워커가 Phase 2–6 + QA + 포장(7F)
 *         → 폴링 → 재생 해석(발행 or REVIEW 개발 경로) → 기존 재생기.
 * (레거시 엔드포인트 이름을 여기 적지 않는다 — 배선 테스트가 이 모듈에 그
 *  문자열이 없음을 정적으로 증명한다.)
 *
 * 테마는 생성에 **절대** 들어가지 않는다 — Phase 6 모션은 테마 독립이고,
 * 선택된 테마는 재생 시점에 packed-alpha 합성으로만 얹힌다.
 *
 * ── QA 정직성 ────────────────────────────────────────────────────────────────
 * PASS  → Phase 7A 발행 → pets 포인터 → 발행 재생 (published: true)
 * REVIEW→ 발행 없음. 데이터베이스 상태는 REVIEW 그대로. 포장된 후보를
 *         실행 재생 리졸버로만 본다 (published: false, qa_decision: "REVIEW").
 * FAIL  → 재생 없음. 레거시 생성기로 **절대 폴백하지 않는다.**
 */

import {
  GenerationRunError,
  getGenerationRun,
  getRunPlayback,
  isTerminalRunStatus,
  pollGenerationRun,
  startGenerationRun,
  type GenerationRun,
  type PollOptions,
  type RunApiDeps,
  type RunPlayback,
} from "./generation-run-api.ts";
import { markGenerationStarted } from "./generation-resume.ts";

/** 명시적 레거시 회귀 스위치 — 조용한 폴백이 아니라 개발자가 켜는 값이다. */
export function phase7GenerationEnabled(): boolean {
  try {
    const v = (import.meta as { env?: Record<string, string> }).env?.VITE_LEGACY_GENERATION;
    return String(v ?? "").trim() !== "1";
  } catch {
    return true;
  }
}

/** 같은 콘텐츠의 재확인이 같은 실행을 재사용하게 하는 결정론 키. */
export function freeHomeIdempotencyKey(contentId: string): string {
  return `free-home:${contentId}`;
}

export interface Phase7Outcome {
  run: GenerationRun;
  playback: RunPlayback;
}

/**
 * 실행 생성 → 종료까지 폴링 → 재생 해석.
 *
 * 던지는 경우: 실행 실패(REVIEW 제외), 폴링 타임아웃, 재생 해석 불가.
 * REVIEW 는 실패가 아니라 **발행 없는 재생**으로 돌아온다.
 */
export async function runPhase7Generation(
  params: { petId: string; contentId: string; poll?: PollOptions },
  deps: RunApiDeps = {}
): Promise<Phase7Outcome> {
  const started = await startGenerationRun(
    { petId: params.petId, idempotencyKey: freeHomeIdempotencyKey(params.contentId) },
    deps
  );
  // durable 마커 — 새로고침 재개(resumePhase7Generation)가 "확인이 실제로
  // 눌렸다"를 알 수 있는 유일한 근거다. 여기서부터는 새로고침해도 온보딩으로
  // 되돌아가지 않고 이 실행을 다시 찾는다.
  markGenerationStarted({
    contentId: params.contentId,
    petId: params.petId,
    runId: started.run_id,
  });
  // 첫 폴링 tick(최대 intervalMs)을 기다리지 않고 방금 만든/찾은 실행 상태를
  // 곧바로 보여준다 — 진행 화면이 잠깐이라도 빈 채로 있지 않게 한다.
  params.poll?.onProgress?.(started);
  const run = await pollGenerationRun(started.run_id, params.poll ?? {}, deps);
  return finalizeRunOutcome(run, deps);
}

/**
 * 이미 시작된 실행을 **새로 만들지 않고** 재발견한다 — 앱 새로고침/재방문
 * 재개 전용. run_id 를 알면 곧장 GET 하나로 끝나고, 모르거나 그 조회가
 * 실패하면(예: 오래된/손상된 로컬 기록) 결정론적 idempotency_key 조인으로
 * 안전하게 떨어진다 — 이 경로도 새 실행을 만들지 않는다(같은 키는 같은
 * 실행을 돌려준다, generation-run-api.ts 참고).
 */
async function resolveExistingGenerationRun(
  params: { petId: string; contentId: string; runId?: string },
  deps: RunApiDeps = {}
): Promise<GenerationRun> {
  if (params.runId) {
    try {
      return await getGenerationRun(params.runId, deps);
    } catch {
      // run_id 조회 실패 — 아래 조인 경로로 안전하게 폴백한다.
    }
  }
  return startGenerationRun(
    { petId: params.petId, idempotencyKey: freeHomeIdempotencyKey(params.contentId) },
    deps
  );
}

/**
 * 새로고침/재방문 재개: 이미 시작된 실행을 재발견해, 끝나지 않았으면 종료
 * 상태까지 이어서 폴링하고, 끝나 있으면(PUBLISHED/REVIEW/FAILED/RECOVERY_
 * REQUIRED) 그 상태를 그대로 해석한다. runPhase7Generation 과 달리 **새
 * 실행을 제출하지 않는다** — 호출자가 마커(readActiveGeneration)로 "확인이
 * 눌힌 적 있음"을 먼저 확인한 뒤에만 불러야 한다.
 */
export async function resumePhase7Generation(
  params: { petId: string; contentId: string; runId?: string; poll?: PollOptions },
  deps: RunApiDeps = {}
): Promise<Phase7Outcome> {
  const current = await resolveExistingGenerationRun(
    { petId: params.petId, contentId: params.contentId, runId: params.runId },
    deps
  );
  markGenerationStarted({
    contentId: params.contentId,
    petId: params.petId,
    runId: current.run_id,
  });
  params.poll?.onProgress?.(current);
  const run = isTerminalRunStatus(current.status)
    ? current
    : await pollGenerationRun(current.run_id, params.poll ?? {}, deps);
  return finalizeRunOutcome(run, deps);
}

/** PUBLISHED/REVIEW 는 재생으로, 그 밖의 종료 상태는 명시적 에러로. */
async function finalizeRunOutcome(
  run: GenerationRun,
  deps: RunApiDeps
): Promise<Phase7Outcome> {
  if (run.status === "PUBLISHED") {
    const playback = await getRunPlayback(run.run_id, deps);
    return { run, playback };
  }

  const errorCode = (run.last_error?.code || "").toUpperCase();
  if (run.status === "FAILED" && errorCode === "MOTION_QA_REVIEW") {
    // QA 는 REVIEW 를 REVIEW 로 남겼다. 발행 없이, 포장된 후보만 본다.
    const playback = await getRunPlayback(run.run_id, deps);
    if (playback.published || playback.qa_decision !== "REVIEW") {
      // 리졸버 계약 위반 — 가짜 발행/가짜 PASS 를 재생으로 받지 않는다.
      throw new GenerationRunError(
        "REVIEW 재생 해석이 계약과 다릅니다.",
        "REVIEW_PLAYBACK_INVALID",
        409
      );
    }
    return { run, playback };
  }

  throw new GenerationRunError(
    run.last_error?.message || `생성 실행이 실패했습니다 (${run.status}).`,
    errorCode || run.status,
    409
  );
}

/** 하이드레이션/파이프라인에 들어가는 최소 재생 필드 — 순수 함수. */
export interface Phase7PipelinePatch {
  idle_video_url: string;
  background_baked: boolean;
  delivery_format: string | null;
  /** 새 시스템 산출물 표시 — 레거시 registry/register 쓰기를 우회하는 근거. */
  generation_source: "phase7-run";
  /** 데이터베이스의 실제 QA 결정. REVIEW 면 발행되지 않은 개발 재생이다. */
  qa_decision: string;
  published: boolean;
}

export function phase7PipelinePatch(outcome: Phase7Outcome): Phase7PipelinePatch {
  const { playback } = outcome;
  return {
    idle_video_url: playback.url,
    background_baked: playback.background_baked === true,
    delivery_format: playback.delivery_format ?? null,
    generation_source: "phase7-run",
    qa_decision: playback.qa_decision,
    published: playback.published === true,
  };
}
