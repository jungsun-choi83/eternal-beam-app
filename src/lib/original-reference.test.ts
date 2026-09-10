/**
 * 원본 레퍼런스 인테이크 계약 (Durable Pet Identity Intake, Phase 1).
 *
 * - 멀티파트 필드가 서버(assets.py post_persist_original) 계약과 1:1 이다
 * - 어떤 실패에서도 throw 하지 않는다 — 온보딩 플로우를 막을 수 없다
 * - data: URL 이 아닌 원본은 추측해 올리지 않는다
 */

import { strict as assert } from "node:assert";
import { afterEach, test } from "node:test";

import {
  ORIGINAL_REFERENCE_MAX_BYTES,
  decodeDataUrl,
  Phase1IntakeError,
  persistPhase1Intake,
  persistOriginalReference,
} from "./original-reference.ts";

const realFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = realFetch;
});

const JPEG_DATA_URL = `data:image/jpeg;base64,${Buffer.from("fake-jpeg-bytes").toString("base64")}`;

function mockFetch(
  impl: (url: string, init?: RequestInit) => Promise<Response> | Response,
): { calls: Array<{ url: string; init?: RequestInit }> } {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    return impl(url, init);
  }) as typeof fetch;
  return { calls };
}

function okResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

test("decodeDataUrl: base64 data URL → 바이트 + MIME", () => {
  const decoded = decodeDataUrl(JPEG_DATA_URL);
  assert.ok(decoded);
  assert.equal(decoded.mime, "image/jpeg");
  assert.equal(Buffer.from(decoded.bytes).toString(), "fake-jpeg-bytes");
});

test("decodeDataUrl: data: 가 아니면 null (blob:/http: 를 원본으로 추측하지 않는다)", () => {
  assert.equal(decodeDataUrl("blob:https://app/xyz"), null);
  assert.equal(decodeDataUrl("https://cdn/photo.jpg"), null);
  assert.equal(decodeDataUrl(""), null);
});

test("persistOriginalReference: 서버 계약대로 멀티파트를 보낸다", async () => {
  const { calls } = mockFetch(() =>
    okResponse({
      reference_id: "ref-1",
      version: 1,
      reference_recorded: true,
      deduplicated: false,
    }),
  );

  const result = await persistOriginalReference({
    userId: "alice@test",
    contentId: "cid1",
    dataUrl: JPEG_DATA_URL,
    diagnostics: { quality_score: 0.8 },
  });

  assert.ok(result);
  assert.equal(result.referenceId, "ref-1");
  assert.equal(result.version, 1);
  assert.equal(result.recorded, true);
  assert.equal(result.deduplicated, false);

  assert.equal(calls.length, 1);
  assert.ok(calls[0].url.endsWith("/api/assets/original"));
  const form = calls[0].init?.body as FormData;
  assert.ok(form instanceof FormData);
  assert.equal(form.get("user_id"), "alice@test");
  assert.equal(form.get("content_id"), "cid1");
  assert.equal(form.get("diagnostics_json"), '{"quality_score":0.8}');
  const file = form.get("file") as File;
  assert.ok(file instanceof File);
  assert.equal(file.type, "image/jpeg");
});

test("persistOriginalReference: 네트워크 실패에서도 throw 하지 않는다", async () => {
  mockFetch(() => Promise.reject(new Error("network down")));
  const result = await persistOriginalReference({
    userId: "alice@test",
    contentId: "cid1",
    dataUrl: JPEG_DATA_URL,
  });
  assert.equal(result, null);
});

test("persistOriginalReference: 서버 오류 응답 → null (throw 없음)", async () => {
  mockFetch(() => new Response("nope", { status: 502 }));
  const result = await persistOriginalReference({
    userId: "alice@test",
    contentId: "cid1",
    dataUrl: JPEG_DATA_URL,
  });
  assert.equal(result, null);
});

test("persistOriginalReference: 식별자가 없으면 업로드하지 않는다", async () => {
  const { calls } = mockFetch(() => okResponse({}));
  assert.equal(
    await persistOriginalReference({ userId: " ", contentId: "cid1", dataUrl: JPEG_DATA_URL }),
    null,
  );
  assert.equal(
    await persistOriginalReference({ userId: "alice", contentId: "", dataUrl: JPEG_DATA_URL }),
    null,
  );
  assert.equal(calls.length, 0);
});

test("persistOriginalReference: data: 가 아닌 소스는 건너뛴다", async () => {
  const { calls } = mockFetch(() => okResponse({}));
  const result = await persistOriginalReference({
    userId: "alice@test",
    contentId: "cid1",
    dataUrl: "blob:https://app/preview",
  });
  assert.equal(result, null);
  assert.equal(calls.length, 0);
});

test("persistOriginalReference: 상한 초과 원본은 올리지 않는다", async () => {
  const { calls } = mockFetch(() => okResponse({}));
  const big = Buffer.alloc(ORIGINAL_REFERENCE_MAX_BYTES + 1, 65).toString("base64");
  const result = await persistOriginalReference({
    userId: "alice@test",
    contentId: "cid1",
    dataUrl: `data:image/jpeg;base64,${big}`,
  });
  assert.equal(result, null);
  assert.equal(calls.length, 0);
});

test("persistOriginalReference: recorded=false 를 정직하게 전달한다", async () => {
  mockFetch(() =>
    okResponse({ reference_id: null, version: 1, reference_recorded: false, deduplicated: false }),
  );
  const result = await persistOriginalReference({
    userId: "alice@test",
    contentId: "cid1",
    dataUrl: JPEG_DATA_URL,
  });
  assert.ok(result);
  assert.equal(result.recorded, false);
});

test("persistPhase1Intake: auth, stable ids, and optional cutout use one contract", async () => {
  const { calls } = mockFetch(() =>
    okResponse({
      user_id: "alice@test",
      content_id: "stable",
      pet_id: "pet_stable",
      reference_id: "original-1",
      object_path: "alice/stable/references/original.jpg",
      version: 1,
      reference_recorded: true,
      deduplicated: true,
      cutout_recorded: true,
      cutout_reference_id: "cutout-1",
      cutout_object_path: "alice/stable/references/cutout.png",
      intake_ready: true,
    }),
  );
  const cutout = new File([new Uint8Array([1, 2, 3])], "cutout.png", {
    type: "image/png",
  });

  const result = await persistPhase1Intake({
    userId: "alice@test",
    contentId: "stable",
    dataUrl: JPEG_DATA_URL,
    accessToken: "jwt",
    cutoutFile: cutout,
  });

  assert.equal(result.intakeReady, true);
  assert.equal(result.petId, "pet_stable");
  assert.equal(result.cutoutReferenceId, "cutout-1");
  assert.equal((calls[0].init?.headers as Record<string, string>).Authorization, "Bearer jwt");
  const form = calls[0].init?.body as FormData;
  assert.equal(form.get("phase1_intake"), "true");
  assert.equal(form.get("content_id"), "stable");
  assert.equal(form.get("cutout_file"), cutout);
});

test("persistPhase1Intake: failures are observable and retryable", async () => {
  mockFetch(() =>
    new Response(
      JSON.stringify({
        detail: { code: "PHASE1_CUTOUT_PERSIST_FAILED", message: "retry me" },
      }),
      { status: 502, headers: { "Content-Type": "application/json" } },
    ),
  );
  await assert.rejects(
    persistPhase1Intake({
      userId: "alice@test",
      contentId: "stable",
      dataUrl: JPEG_DATA_URL,
      accessToken: "jwt",
    }),
    (error: unknown) =>
      error instanceof Phase1IntakeError &&
      error.status === 502 &&
      error.code === "PHASE1_CUTOUT_PERSIST_FAILED",
  );
});

test("persistPhase1Intake: unauthenticated writes never start", async () => {
  const { calls } = mockFetch(() => okResponse({}));
  await assert.rejects(
    persistPhase1Intake({
      userId: "alice@test",
      contentId: "stable",
      dataUrl: JPEG_DATA_URL,
      accessToken: "",
    }),
    (error: unknown) =>
      error instanceof Phase1IntakeError && error.code === "UNAUTHENTICATED",
  );
  assert.equal(calls.length, 0);
});
