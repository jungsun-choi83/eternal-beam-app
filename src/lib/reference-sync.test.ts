/**
 * 현재 사진 집합 동기화 계약 (stale-reference hotfix, Stage 1b).
 *
 * - 클라이언트 sha256 == 서버 content_hash (업로드되는 바이트 그대로의 해시)
 * - 동기화는 업로드 루프 전에 한 번, 전체 해시로 / 루프 뒤에 같은 목록으로 한 번
 * - 한 장이라도 해시를 못 구하면 동기화하지 않는다 (부분 목록 금지)
 * - 동기화 실패는 패스를 막지 않는다
 * - 이번 패스에서 업로드가 실패한 사진도 해시 목록에 남는다
 * - 같은 펫의 두 패스는 겹치지 않는다
 */

import { strict as assert } from "node:assert";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, test } from "node:test";

import {
  decodeDataUrl,
  Phase1IntakeError,
  PHASE1_INTAKE_TIMEOUT_MS,
  PHASE1_UPLOAD_TIMEOUT_CODE,
  persistPhase1Intake,
} from "./original-reference.ts";
import {
  __resetIntakePassesForTests,
  beginIntakePass,
  hashIntakePhotos,
  ReferenceSyncError,
  sha256Hex,
  syncPetReferences,
  type IntakeSyncNotice,
} from "./reference-sync.ts";

const realFetch = globalThis.fetch;
const realWarn = console.warn;

beforeEach(() => {
  __resetIntakePassesForTests();
  console.warn = () => {};
});

afterEach(() => {
  globalThis.fetch = realFetch;
  console.warn = realWarn;
});

/** 0–255 전 범위 + EXIF 처럼 보이는 머리 — 재인코딩되면 반드시 달라지는 바이트. */
function photoBytes(seed: number): Buffer {
  const header = Buffer.from([0xff, 0xd8, 0xff, 0xe1, 0x00, 0x10, 0x45, 0x78, 0x69, 0x66, 0x00, 0x00]);
  const body = Buffer.alloc(1024);
  for (let i = 0; i < body.length; i++) body[i] = (i * 31 + seed * 7) & 0xff;
  return Buffer.concat([header, body]);
}

function dataUrl(bytes: Buffer, mime = "image/jpeg"): string {
  return `data:${mime};base64,${bytes.toString("base64")}`;
}

function nodeSha(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

const BYTES = [photoBytes(1), photoBytes(2), photoBytes(3)];
const PHOTOS = BYTES.map((b) => dataUrl(b));
const HASHES = BYTES.map(nodeSha);

type Call = { url: string; init?: RequestInit };

function mockFetch(impl: (url: string, init?: RequestInit) => Promise<Response> | Response): Call[] {
  const calls: Call[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    return impl(url, init);
  }) as typeof fetch;
  return calls;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SYNC_OK = { pet_id: "pet_stable", active: [], rejected_reference_ids: [], missing_hashes: [] };

function syncBody(call: Call): string[] {
  return (JSON.parse(String(call.init?.body)) as { content_hashes: string[] }).content_hashes;
}

// ── 해시 동등성 ──────────────────────────────────────────────────────────────

test("client sha256 equals the server's content_hash input: the exact uploaded bytes", async () => {
  // 서버는 받은 파일 바이트의 sha256 을 content_hash 로 쓴다
  // (pet_reference_service.record_original: hashlib.sha256(data).hexdigest()).
  let uploaded: Uint8Array | null = null;
  mockFetch(async (_url, init) => {
    const file = (init?.body as FormData).get("file") as File;
    uploaded = new Uint8Array(await file.arrayBuffer());
    return json({
      user_id: "alice",
      content_id: "stable",
      pet_id: "pet_stable",
      reference_id: "r1",
      object_path: "p",
      version: 1,
      reference_recorded: true,
    });
  });

  await persistPhase1Intake({
    userId: "alice",
    contentId: "stable",
    dataUrl: PHOTOS[0],
    accessToken: "token",
  });

  assert.ok(uploaded);
  // 업로드된 바이트는 사용자가 고른 바이트 그대로다 — 재압축·리사이즈·EXIF 제거 없음.
  assert.deepEqual(Buffer.from(uploaded), BYTES[0]);
  const serverHash = nodeSha(uploaded);
  assert.deepEqual(await hashIntakePhotos([PHOTOS[0]]), [serverHash]);
  assert.equal(await sha256Hex(decodeDataUrl(PHOTOS[0])!.bytes), serverHash);
});

test("sha256Hex matches a known vector and is lowercase hex", async () => {
  assert.equal(
    await sha256Hex(new TextEncoder().encode("abc")),
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  );
});

test("hashIntakePhotos is all-or-nothing", async () => {
  assert.deepEqual(await hashIntakePhotos(PHOTOS), HASHES);
  assert.equal(await hashIntakePhotos([]), null);
  assert.equal(await hashIntakePhotos([PHOTOS[0], "https://cdn.test/remote.jpg"]), null);
  assert.equal(await hashIntakePhotos([PHOTOS[0], "data:image/jpeg;base64,"]), null);
  assert.equal(await hashIntakePhotos([PHOTOS[0], "data:image/jpeg;base64,@@not-base64@@"]), null);
});

// ── 동기화 호출 ──────────────────────────────────────────────────────────────

test("syncPetReferences posts the hash list with auth and parses the active set", async () => {
  const calls = mockFetch(() =>
    json({
      pet_id: "pet_stable",
      active: [{ reference_id: "r1", content_hash: HASHES[0] }],
      rejected_reference_ids: ["r3"],
      missing_hashes: [HASHES[1]],
    }),
  );
  const result = await syncPetReferences({
    petId: "pet_stable",
    contentHashes: HASHES.slice(0, 2),
    accessToken: "token",
  });

  assert.equal(calls.length, 1);
  assert.ok(calls[0].url.endsWith("/api/v1/pet/references/pet_stable/sync"));
  assert.equal(calls[0].init?.method, "POST");
  assert.equal((calls[0].init?.headers as Record<string, string>).Authorization, "Bearer token");
  assert.deepEqual(syncBody(calls[0]), HASHES.slice(0, 2));
  assert.deepEqual(result.active, [{ referenceId: "r1", contentHash: HASHES[0] }]);
  assert.deepEqual(result.rejectedReferenceIds, ["r3"]);
  assert.deepEqual(result.missingHashes, [HASHES[1]]);
});

test("syncPetReferences surfaces the server error code", async () => {
  mockFetch(() =>
    json({ detail: { code: "PET_REFERENCE_UPDATE_NOT_APPLIED", message: "저장되지 않았습니다." } }, 503),
  );
  await assert.rejects(
    syncPetReferences({ petId: "pet_stable", contentHashes: HASHES, accessToken: "token" }),
    (error: unknown) =>
      error instanceof ReferenceSyncError &&
      error.status === 503 &&
      error.code === "PET_REFERENCE_UPDATE_NOT_APPLIED",
  );
});

// ── 패스 순서 ────────────────────────────────────────────────────────────────

test("sync runs once before the loop with every current hash, then once after", async () => {
  const events: string[] = [];
  const calls = mockFetch((url) => {
    events.push(url.endsWith("/sync") ? "sync" : "upload");
    return json(SYNC_OK);
  });

  const pass = await beginIntakePass({ petId: "pet_stable", photos: PHOTOS, accessToken: "token" });
  assert.ok(pass);
  assert.equal(pass.preSyncOk, true);
  assert.deepEqual(pass.hashes, HASHES);
  // 루프가 시작되기 전에 정확히 한 번, 전체 목록으로.
  assert.deepEqual(events, ["sync"]);
  assert.deepEqual(syncBody(calls[0]), HASHES);

  for (const _photo of PHOTOS) {
    await fetch("/api/assets/original", { method: "POST" });
  }
  await pass.finish({ syncAfter: true });

  assert.deepEqual(events, ["sync", "upload", "upload", "upload", "sync"]);
  assert.deepEqual(syncBody(calls[4]), HASHES);

  // finish 는 한 번만 동작한다.
  await pass.finish({ syncAfter: true });
  assert.equal(calls.length, 5);
});

test("a photo whose upload failed this pass stays in the hash list", async () => {
  const calls = mockFetch((url) => (url.endsWith("/sync") ? json(SYNC_OK) : json({}, 502)));

  const pass = await beginIntakePass({ petId: "pet_stable", photos: PHOTOS, accessToken: "token" });
  assert.ok(pass);
  const failed: number[] = [];
  for (let index = 0; index < PHOTOS.length; index += 1) {
    const res = await fetch("/api/assets/original", { method: "POST" });
    if (!res.ok) failed.push(index);
  }
  await pass.finish({ syncAfter: true });

  assert.deepEqual(failed, [0, 1, 2]);
  const syncs = calls.filter((c) => c.url.endsWith("/sync"));
  assert.equal(syncs.length, 2);
  // 업로드 성공 여부와 무관하게 UI 의 전체 목록이 간다.
  assert.deepEqual(syncBody(syncs[0]), HASHES);
  assert.deepEqual(syncBody(syncs[1]), HASHES);
});

test("no sync at all when any current photo cannot be hashed", async () => {
  const calls = mockFetch(() => json(SYNC_OK));
  const notices: IntakeSyncNotice[] = [];

  const pass = await beginIntakePass({
    petId: "pet_stable",
    photos: [PHOTOS[0], "https://cdn.test/remote.jpg", PHOTOS[2]],
    accessToken: "token",
    onNotice: (n) => notices.push(n),
  });

  // 패스는 열린다(업로드는 계속) — 동기화만 건너뛴다. 부분 목록은 보내지 않는다.
  assert.ok(pass);
  assert.equal(pass.hashes, null);
  assert.equal(pass.preSyncOk, false);
  await pass.finish({ syncAfter: true });
  assert.equal(calls.length, 0);
  assert.deepEqual(notices, [{ kind: "hash_failed" }]);
});

test("a failed sync does not stop the pass and is reported as a notice", async () => {
  let syncCalls = 0;
  const calls = mockFetch((url) => {
    if (!url.endsWith("/sync")) return json({ ok: true });
    syncCalls += 1;
    return json({ detail: { code: "PET_REFERENCES_UNAVAILABLE", message: "down" } }, 503);
  });
  const notices: IntakeSyncNotice[] = [];

  const pass = await beginIntakePass({
    petId: "pet_stable",
    photos: PHOTOS,
    accessToken: "token",
    onNotice: (n) => notices.push(n),
  });
  assert.ok(pass);
  assert.equal(pass.preSyncOk, false);

  const upload = await fetch("/api/assets/original", { method: "POST" });
  assert.equal(upload.ok, true); // 루프는 그대로 돈다
  await pass.finish({ syncAfter: true });

  // 루프 뒤 안전망도 시도한다.
  assert.equal(syncCalls, 2);
  assert.equal(calls.length, 3);
  assert.deepEqual(
    notices.map((n) => (n.kind === "sync_failed" ? `${n.kind}:${n.phase}:${n.error.code}` : n.kind)),
    ["sync_failed:before:PET_REFERENCES_UNAVAILABLE", "sync_failed:after:PET_REFERENCES_UNAVAILABLE"],
  );
});

test("a network failure during sync is also non-blocking", async () => {
  mockFetch(() => {
    throw new TypeError("fetch failed");
  });
  const notices: IntakeSyncNotice[] = [];
  const pass = await beginIntakePass({
    petId: "pet_stable",
    photos: PHOTOS,
    accessToken: "token",
    onNotice: (n) => notices.push(n),
  });
  assert.ok(pass);
  assert.equal(pass.preSyncOk, false);
  await pass.finish({ syncAfter: false });
  assert.equal(notices.length, 1);
});

test("finish({ syncAfter: false }) closes the pass without a second sync", async () => {
  const calls = mockFetch(() => json(SYNC_OK));
  const pass = await beginIntakePass({ petId: "pet_stable", photos: PHOTOS, accessToken: "token" });
  await pass!.finish({ syncAfter: false });
  assert.equal(calls.length, 1);
});

// ── 펫당 한 번에 한 패스 ──────────────────────────────────────────────────────

const tick = () => new Promise((resolve) => setTimeout(resolve, 5));

test("two passes for the same pet cannot overlap", async () => {
  const events: string[] = [];
  mockFetch((_url, init) => {
    const hashes = (JSON.parse(String(init?.body)) as { content_hashes: string[] }).content_hashes;
    events.push(`sync:${hashes.length}`);
    return json(SYNC_OK);
  });

  const first = await beginIntakePass({ petId: "pet_stable", photos: PHOTOS, accessToken: "token" });
  assert.ok(first);

  let secondOpened = false;
  const secondPromise = beginIntakePass({
    petId: "pet_stable",
    photos: PHOTOS.slice(0, 2),
    accessToken: "token",
  }).then((pass) => {
    secondOpened = true;
    events.push("second-open");
    return pass;
  });

  await tick();
  // 첫 패스가 열려 있는 동안 두 번째는 동기화조차 하지 않는다.
  assert.equal(secondOpened, false);
  assert.deepEqual(events, ["sync:3"]);

  events.push("first-upload");
  await first.finish({ syncAfter: true });
  const second = await secondPromise;
  assert.ok(second);
  await second.finish({ syncAfter: true });

  assert.deepEqual(events, [
    "sync:3",
    "first-upload",
    "sync:3",
    "sync:2",
    "second-open",
    "sync:2",
  ]);
});

test("passes for different pets do not wait on each other", async () => {
  mockFetch(() => json(SYNC_OK));
  const a = await beginIntakePass({ petId: "pet_a", photos: PHOTOS, accessToken: "token" });
  const b = await beginIntakePass({ petId: "pet_b", photos: PHOTOS, accessToken: "token" });
  assert.ok(a && b);
  await a.finish({ syncAfter: false });
  await b.finish({ syncAfter: false });
});

test("a pass that went stale while waiting neither syncs nor opens", async () => {
  const calls = mockFetch(() => json(SYNC_OK));
  const first = await beginIntakePass({ petId: "pet_stable", photos: PHOTOS, accessToken: "token" });

  let current = true;
  const stalePromise = beginIntakePass({
    petId: "pet_stable",
    photos: PHOTOS.slice(0, 1),
    accessToken: "token",
    isCurrent: () => current,
  });
  current = false; // 기다리는 사이 사진이 또 바뀌었다
  await first!.finish({ syncAfter: false });

  assert.equal(await stalePromise, null);
  assert.equal(calls.length, 1); // 첫 패스의 사전 동기화뿐

  // 낡은 패스는 자리를 쥐고 있지 않다 — 다음 패스가 바로 열린다.
  const next = await beginIntakePass({ petId: "pet_stable", photos: PHOTOS, accessToken: "token" });
  assert.ok(next);
  await next.finish({ syncAfter: false });
});

test("a cancelled pass skips the after-loop sync", async () => {
  const calls = mockFetch(() => json(SYNC_OK));
  let current = true;
  const pass = await beginIntakePass({
    petId: "pet_stable",
    photos: PHOTOS,
    accessToken: "token",
    isCurrent: () => current,
  });
  current = false;
  await pass!.finish({ syncAfter: true });
  assert.equal(calls.length, 1);
});

// ── 화면 배선 ────────────────────────────────────────────────────────────────

const processing = readFileSync(
  new URL("../components/memorial/ai-processing-screen.tsx", import.meta.url),
  "utf8",
);

test("processing screen syncs before the upload loop and again after it", () => {
  const begin = processing.indexOf("intakePass = await beginIntakePass({");
  const loop = processing.indexOf("for (let index = 0; index < total; index += 1)");
  const after = processing.indexOf("await intakePass.finish({ syncAfter: true });");
  const identity = processing.indexOf("await buildIdentityProfile(firstReady.petId, auth.token)");
  assert.ok(begin > 0 && loop > 0 && after > 0 && identity > 0);
  // 전 → 루프 → 후 → 신원 빌드. 루프 안에서는 부르지 않는다.
  assert.ok(begin < loop && loop < after && after < identity);
  assert.equal(processing.split("beginIntakePass({").length - 1, 1);
  assert.equal(processing.split("finish({ syncAfter: true })").length - 1, 1);

  // UI 의 전체 사진 목록을 보낸다 — 업로드 대상만 추린 intakeImages 가 아니다.
  assert.match(processing, /photos: currentPhotosRef\.current,/);
  assert.match(processing, /petId: stableIdentity\.petId,/);
  // 낡은 패스는 업로드하지 않는다.
  assert.match(processing, /if \(!intakePass\) return;/);
  // 취소·예외 경로에서도 패스를 닫는다.
  assert.match(
    processing,
    /\} finally \{\s*\/\/[^\n]*\n\s*await intakePass\?\.finish\(\{ syncAfter: false \}\);/,
  );
});

test("processing screen shows sync problems as a non-blocking notice", () => {
  assert.match(processing, /setSyncNotice\(notice\.kind === "hash_failed" \? t\.syncSkippedNotice : t\.syncFailedNotice\)/);
  assert.match(processing, /\{syncNotice && !error \? \(\s*<div className="eb-notice eb-notice--warning" role="status">/);
  // 동기화 알림은 오류 상태(setError/fail)를 건드리지 않는다.
  const onNotice = processing.slice(
    processing.indexOf("onNotice: (notice) => {"),
    processing.indexOf("if (!intakePass) return;"),
  );
  assert.ok(!/setError|fail\(/.test(onNotice));
  // 409 PHASE1_ORIGINAL_LIMIT 는 무엇을 하면 되는지 말해 준다.
  assert.match(processing, /imageError\.code === "PHASE1_ORIGINAL_LIMIT"/);
  assert.match(processing, /preSyncOk \? t\.originalLimitReached : t\.originalLimitAfterSyncFailure/);
});

// ── 업로드 시간 초과 ─────────────────────────────────────────────────────────

/** 응답하지 않는 서버 — abort 신호가 올 때만 끝난다. */
function hangUntilAborted(init?: RequestInit): Promise<Response> {
  return new Promise((_resolve, reject) => {
    init?.signal?.addEventListener("abort", () => {
      reject(new DOMException("The operation was aborted.", "AbortError"));
    });
  });
}

test("a hung upload times out with a clear error instead of waiting forever", async () => {
  assert.equal(PHASE1_INTAKE_TIMEOUT_MS, 120_000);
  let sawSignal = false;
  mockFetch((_url, init) => {
    sawSignal = init?.signal instanceof AbortSignal;
    return hangUntilAborted(init);
  });

  await assert.rejects(
    persistPhase1Intake({
      userId: "alice",
      contentId: "stable",
      dataUrl: PHOTOS[0],
      accessToken: "token",
      timeoutMs: 20,
    }),
    (error: unknown) =>
      error instanceof Phase1IntakeError && error.code === PHASE1_UPLOAD_TIMEOUT_CODE,
  );
  assert.equal(sawSignal, true);
});

test("a response body that never finishes also times out", async () => {
  mockFetch((_url, init) => {
    const body = new ReadableStream({
      start(controller) {
        init?.signal?.addEventListener("abort", () =>
          controller.error(new DOMException("aborted", "AbortError")),
        );
      },
    });
    return new Response(body, { status: 200, headers: { "Content-Type": "application/json" } });
  });
  await assert.rejects(
    persistPhase1Intake({
      userId: "alice",
      contentId: "stable",
      dataUrl: PHOTOS[0],
      accessToken: "token",
      timeoutMs: 20,
    }),
    (error: unknown) =>
      error instanceof Phase1IntakeError && error.code === PHASE1_UPLOAD_TIMEOUT_CODE,
  );
});

test("a timed-out upload releases the per-pet queue so a later pass proceeds", async () => {
  const events: string[] = [];
  mockFetch((url, init) => {
    if (url.endsWith("/sync")) {
      events.push("sync");
      return json(SYNC_OK);
    }
    events.push("upload:hung");
    return hangUntilAborted(init);
  });

  // 첫 패스 — 처리 화면과 같은 모양: 장별 try/catch, 그리고 finally 로 패스를 닫는다.
  const first = await beginIntakePass({ petId: "pet_stable", photos: PHOTOS, accessToken: "token" });
  assert.ok(first);

  let secondOpened = false;
  const secondPromise = beginIntakePass({
    petId: "pet_stable",
    photos: PHOTOS,
    accessToken: "token",
  }).then((pass) => {
    secondOpened = true;
    return pass;
  });

  let failed = 0;
  try {
    try {
      await persistPhase1Intake({
        userId: "alice",
        contentId: "stable",
        dataUrl: PHOTOS[0],
        accessToken: "token",
        timeoutMs: 20,
      });
    } catch (imageError) {
      assert.ok(imageError instanceof Phase1IntakeError);
      assert.equal(imageError.code, PHASE1_UPLOAD_TIMEOUT_CODE);
      failed += 1;
      // 멈춘 업로드가 끝나기 전에는 다음 패스가 열리지 않았다.
      assert.equal(secondOpened, false);
    }
  } finally {
    await first.finish({ syncAfter: true });
  }
  assert.equal(failed, 1);

  // 시간 초과 뒤 첫 패스가 닫히자 다음 패스가 열린다 — 새로고침이 필요 없다.
  const second = await secondPromise;
  assert.ok(second);
  assert.equal(second.preSyncOk, true);
  await second.finish({ syncAfter: false });
  assert.deepEqual(events, ["sync", "upload:hung", "sync", "sync"]);
});

test("processing screen closes the pass on every exit path", () => {
  // 장별 catch 가 업로드 실패(시간 초과 포함)를 삼키고 루프는 계속된다.
  assert.match(processing, /\} catch \(imageError\) \{/);
  // 루프 뒤, 그리고 바깥 finally 에서 패스를 닫는다.
  assert.match(processing, /await intakePass\.finish\(\{ syncAfter: true \}\);/);
  assert.match(processing, /await intakePass\?\.finish\(\{ syncAfter: false \}\);/);
});
