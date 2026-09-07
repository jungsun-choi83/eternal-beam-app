/**
 * Phase 7G — 확인 → 새 생성 시스템 오케스트레이션.
 *
 * 핵심 계약:
 *   * 레거시 /api/generate-pet-video 는 **한 번도** 호출되지 않는다.
 *   * PASS → 발행 재생. REVIEW → 발행 없는 재생(qa_decision 그대로).
 *   * FAIL → 던진다. 레거시 생성기로 폴백하지 않는다.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  freeHomeIdempotencyKey,
  phase7PipelinePatch,
  runPhase7Generation,
} from "./phase7-generation-flow.ts";
import { GenerationRunError, pollGenerationRun } from "./generation-run-api.ts";

type Handler = (url: string, init?: RequestInit) => { status?: number; body: unknown };

function makeFetch(handler: Handler) {
  const urls: string[] = [];
  const fetchFn = (async (url: string, init?: RequestInit) => {
    urls.push(url);
    const { status = 200, body } = handler(url, init);
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    } as Response;
  }) as unknown as typeof fetch;
  return { fetchFn, urls };
}

const token = async () => ({ token: "jwt-7g", source: "supabase" as const });
const deps = (fetchFn: typeof fetch) => ({ fetchFn, getToken: token, apiBase: "" });

const RUN_ID = "run-77";

function runBody(status: string, extra: Record<string, unknown> = {}) {
  return {
    run_id: RUN_ID,
    status,
    current_stage: status === "PUBLISHED" ? "PUBLISHED" : "QA",
    pet_id: "pet_abc",
    ...extra,
  };
}

test("PASS: 실행 생성 → 폴링 → 발행 재생", async () => {
  let polls = 0;
  const { fetchFn, urls } = makeFetch((url, init) => {
    if (url.endsWith("/api/v1/pet/generation-runs") && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      assert.equal(body.pet_id, "pet_abc");
      assert.equal(body.motion_id, "BREATHING");
      assert.equal(body.request_kind, "FREE_HOME");
      assert.equal(body.idempotency_key, "free-home:cid-1");
      return { status: 202, body: runBody("QUEUED") };
    }
    if (url.endsWith(`/generation-runs/${RUN_ID}/playback`)) {
      return {
        body: {
          run_id: RUN_ID,
          status: "PUBLISHED",
          published: true,
          qa_decision: "PASS",
          url: "https://storage.test/u/packed.mp4?token=fresh",
          delivery_format: "packed_alpha",
          background_baked: false,
        },
      };
    }
    polls += 1;
    return { body: runBody(polls < 2 ? "RUNNING" : "PUBLISHED") };
  });

  const outcome = await runPhase7Generation(
    { petId: "pet_abc", contentId: "cid-1", poll: { intervalMs: 1000, sleep: async () => {} } },
    deps(fetchFn)
  );
  assert.equal(outcome.playback.published, true);
  assert.equal(outcome.playback.qa_decision, "PASS");

  const patch = phase7PipelinePatch(outcome);
  assert.equal(patch.idle_video_url, "https://storage.test/u/packed.mp4?token=fresh");
  assert.equal(patch.delivery_format, "packed_alpha");
  assert.equal(patch.background_baked, false);
  assert.equal(patch.generation_source, "phase7-run");
  assert.equal(patch.published, true);

  // 레거시 생성기는 단 한 번도 불리지 않았다.
  assert.ok(urls.every((u) => !u.includes("generate-pet-video")), urls.join("\n"));
});

test("REVIEW: 발행 없이 개발 재생 — QA 상태 그대로", async () => {
  const { fetchFn, urls } = makeFetch((url, init) => {
    if (init?.method === "POST") return { status: 202, body: runBody("QUEUED") };
    if (url.endsWith("/playback")) {
      return {
        body: {
          run_id: RUN_ID,
          status: "FAILED",
          published: false,
          qa_decision: "REVIEW",
          url: "https://storage.test/u/review_packed.mp4?token=fresh",
          delivery_format: "packed_alpha",
          background_baked: false,
        },
      };
    }
    return {
      body: runBody("FAILED", { last_error: { code: "MOTION_QA_REVIEW", message: "검토 필요" } }),
    };
  });

  const outcome = await runPhase7Generation(
    { petId: "pet_abc", contentId: "cid-2", poll: { sleep: async () => {} } },
    deps(fetchFn)
  );
  assert.equal(outcome.playback.published, false);
  assert.equal(outcome.playback.qa_decision, "REVIEW");
  const patch = phase7PipelinePatch(outcome);
  assert.equal(patch.qa_decision, "REVIEW");
  assert.equal(patch.published, false);
  assert.ok(urls.every((u) => !u.includes("generate-pet-video")));
});

test("REVIEW 인데 리졸버가 발행/PASS 를 주장하면 계약 위반으로 거절", async () => {
  const { fetchFn } = makeFetch((url, init) => {
    if (init?.method === "POST") return { status: 202, body: runBody("QUEUED") };
    if (url.endsWith("/playback")) {
      return {
        body: {
          run_id: RUN_ID,
          status: "FAILED",
          published: true, // 가짜 발행 — 받으면 안 된다
          qa_decision: "PASS",
          url: "https://x/y.mp4",
          background_baked: false,
        },
      };
    }
    return { body: runBody("FAILED", { last_error: { code: "MOTION_QA_REVIEW" } }) };
  });

  await assert.rejects(
    runPhase7Generation(
      { petId: "pet_abc", contentId: "cid-3", poll: { sleep: async () => {} } },
      deps(fetchFn)
    ),
    (e: GenerationRunError) => e.code === "REVIEW_PLAYBACK_INVALID"
  );
});

test("FAIL: 던진다 — 레거시 생성기 폴백 없음", async () => {
  const { fetchFn, urls } = makeFetch((url, init) => {
    if (init?.method === "POST") return { status: 202, body: runBody("QUEUED") };
    return {
      body: runBody("FAILED", { last_error: { code: "MOTION_QA_FAILED", message: "QA 실패" } }),
    };
  });

  await assert.rejects(
    runPhase7Generation(
      { petId: "pet_abc", contentId: "cid-4", poll: { sleep: async () => {} } },
      deps(fetchFn)
    ),
    (e: GenerationRunError) => e.code === "MOTION_QA_FAILED"
  );
  assert.ok(urls.every((u) => !u.includes("generate-pet-video")));
});

test("폴링: 종료 상태까지 반복, 타임아웃이면 명시 오류", async () => {
  let calls = 0;
  const { fetchFn } = makeFetch(() => {
    calls += 1;
    return { body: runBody("RUNNING") };
  });
  await assert.rejects(
    pollGenerationRun(
      RUN_ID,
      { intervalMs: 1000, timeoutMs: 2500, sleep: async () => {} },
      deps(fetchFn)
    ),
    (e: GenerationRunError) => e.code === "RUN_POLL_TIMEOUT"
  );
  assert.ok(calls >= 3);
});

test("멱등 키는 콘텐츠에 결정론적", () => {
  assert.equal(freeHomeIdempotencyKey("cid-9"), "free-home:cid-9");
  assert.equal(freeHomeIdempotencyKey("cid-9"), freeHomeIdempotencyKey("cid-9"));
});
