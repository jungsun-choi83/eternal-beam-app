/**
 * Phase 7G — 확인 → 새 생성 시스템 오케스트레이션.
 *
 * 핵심 계약:
 *   * 레거시 /api/generate-pet-video 는 **한 번도** 호출되지 않는다.
 *   * PASS → 발행 재생. REVIEW → 발행 없는 재생(qa_decision 그대로).
 *   * 안전한 fallback → 고객 재생. 인프라/무자산 실패만 던진다.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  freeHomeIdempotencyKey,
  phase7PipelinePatch,
  resumePhase7Generation,
  retryPhase7Generation,
  runPhase7Generation,
} from "./phase7-generation-flow.ts";
import {
  GenerationRunError,
  pollGenerationRun,
  submitBusinessQAFeedback,
} from "./generation-run-api.ts";

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

test("Phase 12 feedback records acceptance without starting another generation", async () => {
  const { fetchFn, urls } = makeFetch((url, init) => {
    assert.equal(url, `/api/v1/pet/generation-runs/${RUN_ID}/feedback`);
    assert.equal(init?.method, "POST");
    assert.deepEqual(JSON.parse(String(init?.body)), {
      accepted: false,
      complaints: ["IDENTITY"],
      comment: "face changed",
    });
    return {
      body: {
        run_id: RUN_ID,
        accepted: false,
        complaints: ["IDENTITY"],
        recorded: true,
      },
    };
  });

  const result = await submitBusinessQAFeedback(
    RUN_ID,
    { accepted: false, complaints: ["IDENTITY"], comment: "face changed" },
    deps(fetchFn)
  );
  assert.equal(result.recorded, true);
  assert.deepEqual(urls, [`/api/v1/pet/generation-runs/${RUN_ID}/feedback`]);
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

test("DELIVERED_FALLBACK: legacy FAILED status still returns customer playback", async () => {
  const { fetchFn, urls } = makeFetch((url, init) => {
    if (init?.method === "POST") return { status: 202, body: runBody("QUEUED") };
    if (url.endsWith("/playback")) {
      return {
        body: {
          run_id: RUN_ID,
          status: "FAILED",
          terminal_state: "DELIVERED_FALLBACK",
          published: false,
          qa_decision: "FALLBACK",
          url: "https://storage.test/u/canonical_cutout.png?token=fresh",
          delivery_format: "canonical_local_idle",
          background_baked: false,
          fallback_tier: "CANONICAL_LOCAL_IDLE",
        },
      };
    }
    return {
      body: runBody("FAILED", {
        terminal_state: "DELIVERED_FALLBACK",
        current_stage: "DELIVERY",
        last_error: null,
      }),
    };
  });

  const outcome = await runPhase7Generation(
    { petId: "pet_abc", contentId: "cid-fallback", poll: { sleep: async () => {} } },
    deps(fetchFn)
  );
  assert.equal(outcome.run.terminal_state, "DELIVERED_FALLBACK");
  assert.equal(outcome.playback.qa_decision, "FALLBACK");
  assert.equal(outcome.playback.delivery_format, "canonical_local_idle");
  const patch = phase7PipelinePatch(outcome);
  assert.equal(patch.idle_video_url, "", "Canonical image must not be passed to video playback");
  assert.equal(
    patch.cutout_display_url,
    "https://storage.test/u/canonical_cutout.png?token=fresh"
  );
  assert.equal(patch.dog_only_nobg_url, patch.cutout_display_url);
  assert.ok(urls.every((u) => !u.includes("generate-pet-video")));
});

test("DELIVERED_FALLBACK with completed status renders the returned canonical URL", async () => {
  const { fetchFn } = makeFetch((url, init) => {
    if (init?.method === "POST") return { status: 202, body: runBody("QUEUED") };
    if (url.endsWith("/playback")) {
      return {
        body: {
          run_id: RUN_ID,
          status: "PUBLISHED",
          terminal_state: "DELIVERED_FALLBACK",
          published: false,
          qa_decision: "FALLBACK",
          url: "https://storage.test/u/canonical_cutout.png?token=fresh",
          delivery_format: "canonical_local_idle",
          background_baked: false,
        },
      };
    }
    return {
      body: runBody("PUBLISHED", {
        terminal_state: "DELIVERED_FALLBACK",
        current_stage: "DELIVERY",
        last_error: null,
      }),
    };
  });

  const outcome = await runPhase7Generation(
    { petId: "pet_abc", contentId: "cid-fallback-ok", poll: { sleep: async () => {} } },
    deps(fetchFn)
  );
  const patch = phase7PipelinePatch(outcome);
  assert.equal(patch.cutout_display_url, "https://storage.test/u/canonical_cutout.png?token=fresh");
  assert.equal(patch.idle_video_url, "");
  assert.equal(patch.published, false);
});

test("no generated asset and no fallback: FAILED is an error and playback is never requested", async () => {
  const { fetchFn, urls } = makeFetch((_url, init) => {
    if (init?.method === "POST") return { status: 202, body: runBody("QUEUED") };
    return {
      body: runBody("FAILED", {
        terminal_state: null,
        last_error: { code: "NO_SAFE_FALLBACK", message: "no asset" },
      }),
    };
  });

  await assert.rejects(
    runPhase7Generation(
      { petId: "pet_abc", contentId: "cid-no-fallback", poll: { sleep: async () => {} } },
      deps(fetchFn)
    ),
    (e: unknown) => (e as { code?: string }).code === "NO_SAFE_FALLBACK"
  );
  assert.ok(urls.every((u) => !u.endsWith("/playback")));
});

test("canonical still fallback is explicitly marked for static cutout rendering", () => {
  const patch = phase7PipelinePatch({
    run: runBody("FAILED", { terminal_state: "DELIVERED_FALLBACK" }),
    playback: {
      run_id: RUN_ID,
      status: "FAILED",
      published: false,
      qa_decision: "FALLBACK",
      url: "https://storage.test/u/canonical_raw.jpg?token=fresh",
      delivery_format: "canonical_still",
      background_baked: false,
      asset_kind: "canonical_image",
    },
  });
  assert.equal(patch.idle_video_url, "");
  assert.equal(patch.cutout_display_url, "https://storage.test/u/canonical_raw.jpg?token=fresh");
  assert.equal(patch.delivery_format, "canonical_still");
});

test("canonical fallback display uses image paths, with still mode wired on both screens", () => {
  const display = readFileSync("src/components/memorial/pet-idle-display.tsx", "utf8");
  const preview = readFileSync("src/components/memorial/preview-screen.tsx", "utf8");
  const device = readFileSync(
    "src/components/memorial/memorial-device-play-screen.tsx",
    "utf8"
  );
  assert.match(display, /if \(staticCutout\)[\s\S]*?<img/);
  assert.match(preview, /staticCutout=\{pipeline\?\.delivery_format === "canonical_still"\}/);
  assert.match(device, /staticCutout=\{pipeline\?\.delivery_format === "canonical_still"\}/);
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

// ── 새로고침 안전 재개 (resumePhase7Generation) ───────────────────────────
// 확인된 문제의 회귀 가드: 새로고침 뒤 재개는 새 실행을 만들지 않는다.

test("재개: run_id 를 알면 GET 하나로 끝난다 — POST(startGenerationRun) 없음", async () => {
  let posts = 0;
  let gets = 0;
  const { fetchFn } = makeFetch((url, init) => {
    if (init?.method === "POST") {
      posts += 1;
      return { status: 202, body: runBody("QUEUED") };
    }
    if (url.endsWith("/playback")) {
      return {
        body: {
          run_id: RUN_ID,
          status: "PUBLISHED",
          published: true,
          qa_decision: "PASS",
          url: "https://storage.test/u/resumed.mp4",
          delivery_format: "packed_alpha",
          background_baked: false,
        },
      };
    }
    gets += 1;
    return { body: runBody("PUBLISHED") };
  });

  const outcome = await resumePhase7Generation(
    { petId: "pet_abc", contentId: "cid-resume-1", runId: RUN_ID },
    deps(fetchFn)
  );

  assert.equal(posts, 0, "이미 run_id 를 아는 재개는 실행을 새로 만들지 않는다");
  assert.equal(gets, 1);
  assert.equal(outcome.playback.published, true);
});

test("재개: WAITING_PROVIDER/RUNNING 이면 종료 상태까지 이어서 폴링한다", async () => {
  let polls = 0;
  const { fetchFn } = makeFetch((url, init) => {
    if (init?.method === "POST") return { status: 202, body: runBody("QUEUED") };
    if (url.endsWith("/playback")) {
      return {
        body: {
          run_id: RUN_ID,
          status: "PUBLISHED",
          published: true,
          qa_decision: "PASS",
          url: "https://storage.test/u/resumed2.mp4",
          background_baked: false,
        },
      };
    }
    polls += 1;
    // 첫 조회는 아직 WAITING_PROVIDER(=canonical/motion 진행 중) — 이어서
    // 폴링해야 한다.
    return { body: runBody(polls < 3 ? "WAITING_PROVIDER" : "PUBLISHED") };
  });

  const outcome = await resumePhase7Generation(
    { petId: "pet_abc", contentId: "cid-resume-2", runId: RUN_ID, poll: { intervalMs: 1000, sleep: async () => {} } },
    deps(fetchFn)
  );

  assert.ok(polls >= 3, "WAITING_PROVIDER 에서 곧장 끝난 것으로 처리하면 안 된다");
  assert.equal(outcome.playback.published, true);
});

test("재개: 이미 PUBLISHED 면 다시 폴링하지 않고 곧장 재생을 해석한다", async () => {
  let getRunCalls = 0;
  const { fetchFn } = makeFetch((url, init) => {
    if (init?.method === "POST") return { status: 202, body: runBody("QUEUED") };
    if (url.endsWith("/playback")) {
      return {
        body: {
          run_id: RUN_ID,
          status: "PUBLISHED",
          published: true,
          qa_decision: "PASS",
          url: "https://storage.test/u/already-done.mp4",
          background_baked: false,
        },
      };
    }
    getRunCalls += 1;
    return { body: runBody("PUBLISHED") };
  });

  const outcome = await resumePhase7Generation(
    { petId: "pet_abc", contentId: "cid-resume-3", runId: RUN_ID },
    deps(fetchFn)
  );

  assert.equal(getRunCalls, 1, "PUBLISHED 를 확인한 GET 딱 한 번 — 폴링 루프에 다시 들어가지 않는다");
  assert.equal(outcome.playback.published, true);
});

test("재개: run_id 조회가 실패하면 idempotency_key 조인으로 안전하게 떨어진다 (새 실행 아님)", async () => {
  let posts = 0;
  const { fetchFn } = makeFetch((url, init) => {
    if (url.endsWith(`/generation-runs/stale-run-id`)) {
      return { status: 404, body: { detail: { code: "GENERATION_RUN_NOT_FOUND" } } };
    }
    if (init?.method === "POST") {
      posts += 1;
      const body = JSON.parse(String(init.body));
      assert.equal(body.idempotency_key, "free-home:cid-resume-4");
      return { status: 202, body: runBody("PUBLISHED") };
    }
    if (url.endsWith("/playback")) {
      return {
        body: {
          run_id: RUN_ID,
          status: "PUBLISHED",
          published: true,
          qa_decision: "PASS",
          url: "https://storage.test/u/joined.mp4",
          background_baked: false,
        },
      };
    }
    return { body: runBody("PUBLISHED") };
  });

  const outcome = await resumePhase7Generation(
    { petId: "pet_abc", contentId: "cid-resume-4", runId: "stale-run-id" },
    deps(fetchFn)
  );

  // 여기서의 POST 는 "새 실행 생성"이 아니라 결정론적 idempotency_key 로
  // 같은 실행을 재발견/조인하는 것이다(startGenerationRun 자체의 계약).
  assert.equal(posts, 1);
  assert.equal(outcome.playback.published, true);
});

test("재개: FAILED(REVIEW 아님)는 그대로 오류로 복원된다", async () => {
  const { fetchFn } = makeFetch((url, init) => {
    if (init?.method === "POST") return { status: 202, body: runBody("QUEUED") };
    return {
      body: runBody("FAILED", { last_error: { code: "MOTION_QA_FAILED", message: "QA 실패" } }),
    };
  });

  await assert.rejects(
    resumePhase7Generation(
      { petId: "pet_abc", contentId: "cid-resume-5", runId: RUN_ID },
      deps(fetchFn)
    ),
    (e: GenerationRunError) => e.code === "MOTION_QA_FAILED"
  );
});

test("재개: RECOVERY_REQUIRED 도 오류로 복원된다(레거시 폴백 없음)", async () => {
  const { fetchFn } = makeFetch((url, init) => {
    if (init?.method === "POST") return { status: 202, body: runBody("QUEUED") };
    return { body: runBody("RECOVERY_REQUIRED") };
  });

  await assert.rejects(
    resumePhase7Generation(
      { petId: "pet_abc", contentId: "cid-resume-6", runId: RUN_ID },
      deps(fetchFn)
    ),
    (e: GenerationRunError) => e.code === "RECOVERY_REQUIRED"
  );
});

// ── 정지/재시도 생명주기 (stale-run resurrection fix) ─────────────────────────

test("폴링 타임아웃: 서버 실행을 poll_timeout 사유로 취소한 뒤 원래 오류를 던진다", async () => {
  const cancels: unknown[] = [];
  const { fetchFn, urls } = makeFetch((url, init) => {
    if (url.endsWith("/api/v1/pet/generation-runs") && init?.method === "POST") {
      return { status: 202, body: runBody("QUEUED") };
    }
    if (url.endsWith(`/generation-runs/${RUN_ID}/cancel`)) {
      cancels.push(JSON.parse(String(init?.body)));
      return { body: runBody("CANCELLED") };
    }
    return { body: runBody("RUNNING") };
  });
  await assert.rejects(
    runPhase7Generation(
      {
        petId: "pet_abc",
        contentId: "cid-timeout",
        poll: { intervalMs: 1000, timeoutMs: 2500, sleep: async () => {} },
      },
      deps(fetchFn)
    ),
    (e: GenerationRunError) => e.code === "RUN_POLL_TIMEOUT"
  );
  assert.deepEqual(cancels, [{ reason: "poll_timeout" }]);
  assert.equal(urls.filter((u) => u.endsWith("/cancel")).length, 1);
});

test("폴링 타임아웃: 취소 호출이 실패해도 원래 타임아웃 오류가 그대로 난다", async () => {
  const { fetchFn } = makeFetch((url, init) => {
    if (url.endsWith("/api/v1/pet/generation-runs") && init?.method === "POST") {
      return { status: 202, body: runBody("QUEUED") };
    }
    if (url.endsWith("/cancel")) return { status: 503, body: { detail: { code: "DOWN" } } };
    return { body: runBody("RUNNING") };
  });
  await assert.rejects(
    resumePhase7Generation(
      {
        petId: "pet_abc",
        contentId: "cid-timeout-2",
        runId: RUN_ID,
        poll: { intervalMs: 1000, timeoutMs: 2000, sleep: async () => {} },
      },
      deps(fetchFn)
    ),
    (e: GenerationRunError) => e.code === "RUN_POLL_TIMEOUT"
  );
});

test("Retry: 확인(POST 생성)이 아니라 /retry 로 같은 실행을 되살리고 끝까지 폴링한다", async () => {
  let polls = 0;
  const { fetchFn, urls } = makeFetch((url, init) => {
    if (url.endsWith(`/generation-runs/${RUN_ID}/retry`)) {
      assert.equal(init?.method, "POST");
      return { body: runBody("QUEUED", { lease_recoveries: 0, retry_count: 1 }) };
    }
    if (url.endsWith(`/generation-runs/${RUN_ID}/playback`)) {
      return {
        body: {
          run_id: RUN_ID,
          status: "PUBLISHED",
          published: true,
          qa_decision: "PASS",
          url: "https://storage.test/u/packed.mp4",
          delivery_format: "packed_alpha",
          background_baked: false,
        },
      };
    }
    polls += 1;
    return { body: runBody(polls < 2 ? "RUNNING" : "PUBLISHED") };
  });
  const outcome = await retryPhase7Generation(
    {
      runId: RUN_ID,
      petId: "pet_abc",
      contentId: "cid-retry",
      poll: { intervalMs: 1000, sleep: async () => {} },
    },
    deps(fetchFn)
  );
  assert.equal(outcome.run.status, "PUBLISHED");
  assert.equal(outcome.playback.published, true);
  // 새 실행을 만들지 않는다 — 생성 POST 는 한 번도 나가지 않는다.
  const createPosts = urls.filter((u) => u.endsWith("/api/v1/pet/generation-runs"));
  assert.deepEqual(createPosts, []);
  assert.equal(urls.filter((u) => u.endsWith("/retry")).length, 1);
});

test("Retry: 서버가 여전히 FAILED 를 돌려주면(재시도 불가) 폴링 없이 오류로 끝난다", async () => {
  let gets = 0;
  const { fetchFn } = makeFetch((url) => {
    if (url.endsWith("/retry")) {
      return { body: runBody("FAILED", { last_error: { code: "MOTION_QA_FAIL", message: "no" } }) };
    }
    gets += 1;
    return { body: runBody("FAILED") };
  });
  await assert.rejects(
    retryPhase7Generation(
      { runId: RUN_ID, petId: "pet_abc", contentId: "cid-retry-2" },
      deps(fetchFn)
    ),
    (e: GenerationRunError) => e.code === "MOTION_QA_FAIL"
  );
  assert.equal(gets, 0);
});

test("Phase 12 cohort preview exposes the existing feedback API without generation side effects", () => {
  const source = readFileSync(
    new URL("../components/memorial/preview-screen.tsx", import.meta.url),
    "utf8"
  );
  const qaSource = readFileSync(
    new URL("../components/memorial/motion-qa-feedback.tsx", import.meta.url),
    "utf8"
  );
  assert.match(source, /<MotionQAFeedback/);
  assert.match(qaSource, /data-business-qa-feedback/);
  assert.match(source, /submitBusinessQAFeedback\(runState\.run_id/);
  assert.match(qaSource, /\["IDENTITY", "ANATOMY", "MOTION"\]/);
  // Internal QA controls never render in a production build, and only by
  // explicit opt-in (VITE_INTERNAL_MOTION_QA=1) in a dev build.
  assert.match(qaSource, /if \(!enrolled \|\| !isInternalMotionQAEnabled\(\)\) return null;/);
  assert.match(qaSource, /if \(!import\.meta\.env\.DEV \|\| import\.meta\.env\.PROD\) return false;/);
  assert.match(qaSource, /VITE_INTERNAL_MOTION_QA/);
  assert.doesNotMatch(source, /handleUserTestFeedback[\s\S]{0,800}startGenerationRun/);
});
