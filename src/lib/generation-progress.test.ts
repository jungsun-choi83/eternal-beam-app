import { test } from "node:test";
import assert from "node:assert/strict";

import {
  deriveGenerationProgress,
  isRecoverableErrorCode,
  latestProviderStatus,
} from "./generation-progress.ts";

test("QUEUED/IDENTITY/REFERENCE_SET 은 '펫 준비'로 접힌다", () => {
  for (const stage of ["QUEUED", "IDENTITY", "REFERENCE_SET"]) {
    assert.deepEqual(
      deriveGenerationProgress({ status: "RUNNING", current_stage: stage }),
      { kind: "stage", stage: "preparing_pet", waiting: false }
    );
  }
});

test("CANONICAL/KEYFRAMES 는 '모습 만들기'로 접힌다", () => {
  for (const stage of ["CANONICAL", "KEYFRAMES"]) {
    assert.deepEqual(
      deriveGenerationProgress({ status: "RUNNING", current_stage: stage }),
      { kind: "stage", stage: "creating_appearance", waiting: false }
    );
  }
});

test("provider_status IN_QUEUE — 모션 생성 중 대기열은 '생성 자리 대기'로 승격된다", () => {
  const run = {
    status: "RUNNING",
    current_stage: "MOTION_GENERATION",
    provider_state: {
      job1: { provider_operation: "MOTION_VIDEO", provider_status: "IN_QUEUE" },
    },
  };
  assert.deepEqual(deriveGenerationProgress(run), {
    kind: "stage",
    stage: "waiting_capacity",
    waiting: true,
  });
});

test("provider_status IN_PROGRESS — 모션 생성 중이면 '움직임 생성'을 유지하고 대기로 보지 않는다", () => {
  const run = {
    status: "RUNNING",
    current_stage: "MOTION_GENERATION",
    provider_state: {
      job1: { provider_operation: "MOTION_VIDEO", provider_status: "IN_PROGRESS" },
    },
  };
  assert.deepEqual(deriveGenerationProgress(run), {
    kind: "stage",
    stage: "generating_motion",
    waiting: false,
  });
});

test("여러 job 중 가장 최근에 갱신된 것을 대표 상태로 쓴다", () => {
  const run = {
    status: "RUNNING",
    current_stage: "MOTION_GENERATION",
    provider_state: {
      old: {
        provider_operation: "MOTION_VIDEO",
        provider_status: "IN_QUEUE",
        last_polled_at: "2026-01-01T00:00:00Z",
      },
      newer: {
        provider_operation: "MOTION_VIDEO",
        provider_status: "IN_PROGRESS",
        last_polled_at: "2026-01-01T00:05:00Z",
      },
    },
  };
  assert.equal(latestProviderStatus(run.provider_state, "MOTION_VIDEO"), "IN_PROGRESS");
  assert.deepEqual(deriveGenerationProgress(run), {
    kind: "stage",
    stage: "generating_motion",
    waiting: false,
  });
});

test("status WAITING_PROVIDER 만으로도 모션 단계는 대기로 본다 (provider_state 없이도)", () => {
  const run = { status: "WAITING_PROVIDER", current_stage: "MOTION_GENERATION" };
  assert.deepEqual(deriveGenerationProgress(run), {
    kind: "stage",
    stage: "waiting_capacity",
    waiting: true,
  });
});

test("QA 단계는 '결과 확인'", () => {
  assert.deepEqual(deriveGenerationProgress({ status: "RUNNING", current_stage: "QA" }), {
    kind: "stage",
    stage: "checking_result",
    waiting: false,
  });
});

test("DELIVERY/PUBLICATION 단계는 '빔으로 전송 준비'", () => {
  for (const stage of ["DELIVERY", "PUBLICATION"]) {
    assert.deepEqual(deriveGenerationProgress({ status: "RUNNING", current_stage: stage }), {
      kind: "stage",
      stage: "rendering",
      waiting: false,
    });
  }
});

test("PUBLISHED 은 완료 — waiting 여부와 무관하게 항상 완료", () => {
  assert.deepEqual(
    deriveGenerationProgress({ status: "PUBLISHED", current_stage: "PUBLISHED" }),
    { kind: "stage", stage: "complete", waiting: false }
  );
});

test("FAILED/CANCELLED 은 복구 불가 에러 뷰", () => {
  assert.deepEqual(
    deriveGenerationProgress({
      status: "FAILED",
      current_stage: "MOTION_GENERATION",
      last_error: { code: "MOTION_PROVIDER_ERROR", message: "생성 실패" },
    }),
    { kind: "error", recoverable: false, message: "생성 실패" }
  );
  assert.deepEqual(
    deriveGenerationProgress({ status: "CANCELLED", current_stage: "QA" }),
    { kind: "error", recoverable: false, message: null }
  );
});

test("RECOVERY_REQUIRED 은 복구 가능 에러 뷰", () => {
  const view = deriveGenerationProgress({
    status: "RECOVERY_REQUIRED",
    current_stage: "MOTION_GENERATION",
    last_error: { code: "PROVIDER_TIMEOUT", message: "다시 시도 중" },
  });
  assert.deepEqual(view, { kind: "error", recoverable: true, message: "다시 시도 중" });
});

test("isRecoverableErrorCode — RECOVERY_REQUIRED 코드만 복구 가능으로 본다", () => {
  assert.equal(isRecoverableErrorCode("RECOVERY_REQUIRED"), true);
  assert.equal(isRecoverableErrorCode("recovery_required"), true);
  assert.equal(isRecoverableErrorCode("FAILED"), false);
  assert.equal(isRecoverableErrorCode(undefined), false);
});

test("run 이 아직 없을 때(null) — 안전한 첫 단계 기본값, 에러 아님", () => {
  assert.deepEqual(deriveGenerationProgress(null), {
    kind: "stage",
    stage: "preparing_pet",
    waiting: false,
  });
  assert.deepEqual(deriveGenerationProgress(undefined), {
    kind: "stage",
    stage: "preparing_pet",
    waiting: false,
  });
});

test("모르는/누락된 current_stage 와 provider_state 는 안전한 기본값으로 접힌다 (fallback)", () => {
  assert.deepEqual(
    deriveGenerationProgress({ status: "RUNNING", current_stage: "SOMETHING_NEW" }),
    { kind: "stage", stage: "preparing_pet", waiting: false }
  );
  assert.deepEqual(
    deriveGenerationProgress({ status: "RUNNING", current_stage: undefined }),
    { kind: "stage", stage: "preparing_pet", waiting: false }
  );
  // provider_state 가 비어 있거나 다른 operation 뿐이면 대기로 오판하지 않는다.
  assert.deepEqual(
    deriveGenerationProgress({
      status: "RUNNING",
      current_stage: "MOTION_GENERATION",
      provider_state: { job1: { provider_operation: "CANONICAL_IMAGE", provider_status: "IN_QUEUE" } },
    }),
    { kind: "stage", stage: "generating_motion", waiting: false }
  );
  assert.equal(latestProviderStatus(null, "MOTION_VIDEO"), null);
  assert.equal(latestProviderStatus({}, "MOTION_VIDEO"), null);
  assert.equal(latestProviderStatus({ a: { provider_operation: "MOTION_VIDEO" } }, "MOTION_VIDEO"), null);
});
