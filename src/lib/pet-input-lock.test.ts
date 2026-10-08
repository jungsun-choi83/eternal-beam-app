/**
 * 펫 입력 잠금 (Stage 1c) — 클라이언트 계약.
 *
 * - 서버의 409 PHASE1_LOCKED 는 오류 알림이 아니라 "잠겼다"는 예상된 답이다
 * - 잠금 여부는 서버 답이 정본, 모를 때만 로컬 마커
 * - 생성 실행을 만들기 전에 확인을 받는다
 * - 잠긴 뒤에는 추가·삭제·교체가 꺼진다
 */

import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, test } from "node:test";

import { Phase1IntakeError } from "./original-reference.ts";
import {
  fetchPetInputsLocked,
  intakeContinueTarget,
  isPhase1LockedError,
  resolvePetInputsLocked,
} from "./pet-input-lock.ts";
import {
  __resetIntakePassesForTests,
  beginIntakePass,
  ReferenceSyncError,
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

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetch(impl: (url: string, init?: RequestInit) => Response): string[] {
  const urls: string[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    urls.push(String(input));
    return impl(String(input), init);
  }) as typeof fetch;
  return urls;
}

const PHOTO = `data:image/jpeg;base64,${Buffer.from("photo-bytes").toString("base64")}`;
const LOCKED = { detail: { code: "PHASE1_LOCKED", message: "생성이 시작된 뒤에는 바꿀 수 없습니다." } };

test("isPhase1LockedError recognises the server code on both error types", () => {
  assert.equal(isPhase1LockedError(new Phase1IntakeError("x", 409, "PHASE1_LOCKED")), true);
  assert.equal(isPhase1LockedError(new ReferenceSyncError("x", 409, "PHASE1_LOCKED")), true);
  assert.equal(isPhase1LockedError(new Phase1IntakeError("x", 409, "PHASE1_ORIGINAL_LIMIT")), false);
  assert.equal(isPhase1LockedError(new Error("PHASE1_LOCKED")), false);
  assert.equal(isPhase1LockedError(null), false);
});

test("fetchPetInputsLocked reads inputs_locked and never throws", async () => {
  const urls = mockFetch(() => json({ pet_id: "pet_x", inputs_locked: true }));
  assert.equal(await fetchPetInputsLocked({ petId: "pet_x", accessToken: "t" }), true);
  assert.ok(urls[0].endsWith("/api/v1/pet/references/pet_x"));

  mockFetch(() => json({ pet_id: "pet_x", inputs_locked: false }));
  assert.equal(await fetchPetInputsLocked({ petId: "pet_x", accessToken: "t" }), false);

  // 서버가 판정하지 못했거나(null), 요청이 실패하면 "모름"이다.
  mockFetch(() => json({ pet_id: "pet_x", inputs_locked: null }));
  assert.equal(await fetchPetInputsLocked({ petId: "pet_x", accessToken: "t" }), null);
  mockFetch(() => json({ detail: "nope" }, 403));
  assert.equal(await fetchPetInputsLocked({ petId: "pet_x", accessToken: "t" }), null);
  mockFetch(() => {
    throw new TypeError("fetch failed");
  });
  assert.equal(await fetchPetInputsLocked({ petId: "pet_x", accessToken: "t" }), null);
  assert.equal(await fetchPetInputsLocked({ petId: "pet_x", accessToken: "" }), null);
});

test("the server answer wins; the local marker is only a fallback", () => {
  const hasPhotos = true;
  assert.equal(resolvePetInputsLocked({ serverLocked: true, localMarker: false, hasPhotos }), true);
  // 실패한 실행 뒤: 로컬에는 "확인을 눌렀다"가 남아 있어도 서버가 풀렸다고 하면 풀린다.
  assert.equal(resolvePetInputsLocked({ serverLocked: false, localMarker: true, hasPhotos }), false);
  // 서버 답을 모를 때는 확인 직후의 로컬 기록으로 잠근다.
  assert.equal(resolvePetInputsLocked({ serverLocked: null, localMarker: true, hasPhotos }), true);
  assert.equal(
    resolvePetInputsLocked({ serverLocked: undefined, localMarker: false, hasPhotos }),
    false,
  );
});

test("an empty slot is never locked — the user can pick photos for a new pet", () => {
  // 새로고침 뒤: 자리는 비었는데 잠긴 아이의 신원(서버 답·로컬 마커)이 남아 있다.
  assert.equal(
    resolvePetInputsLocked({ serverLocked: true, localMarker: true, hasPhotos: false }),
    false,
  );
  assert.equal(
    resolvePetInputsLocked({ serverLocked: null, localMarker: true, hasPhotos: false }),
    false,
  );
});

test("Start on a locked pet goes to preview, never to the processing screen", () => {
  assert.equal(intakeContinueTarget({ canStart: true, photosLocked: true }), "preview");
  assert.equal(intakeContinueTarget({ canStart: true, photosLocked: false }), "aiProcessing");
  assert.equal(intakeContinueTarget({ canStart: false, photosLocked: false }), null);
  assert.equal(intakeContinueTarget({ canStart: false, photosLocked: true }), null);
});

test("PHASE1_LOCKED from the pre-loop sync is expected: no notice, pass marked locked", async () => {
  const urls = mockFetch(() => json(LOCKED, 409));
  const notices: IntakeSyncNotice[] = [];

  const pass = await beginIntakePass({
    petId: "pet_x",
    photos: [PHOTO],
    accessToken: "t",
    onNotice: (n) => notices.push(n),
  });

  assert.ok(pass);
  assert.equal(pass.locked, true);
  assert.equal(pass.preSyncOk, false);
  assert.deepEqual(notices, []); // 오류로 보이지 않는다
  await pass.finish({ syncAfter: true });
  assert.deepEqual(notices, []);
  assert.equal(urls.filter((u) => u.endsWith("/sync")).length, 2);
});

test("an unlocked sync leaves the pass unlocked", async () => {
  mockFetch(() => json({ pet_id: "pet_x", active: [], rejected_reference_ids: [], missing_hashes: [] }));
  const pass = await beginIntakePass({ petId: "pet_x", photos: [PHOTO], accessToken: "t" });
  assert.equal(pass!.locked, false);
  await pass!.finish({ syncAfter: false });
});

// ── 화면 배선 ────────────────────────────────────────────────────────────────

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const processing = read("../components/memorial/ai-processing-screen.tsx");
const preview = read("../components/memorial/preview-screen.tsx");
const upload = read("../components/memorial/photo-upload-screen.tsx");
const app = read("../app/EternalBeamApp.tsx");
const flow = read("./phase7-generation-flow.ts");

test("processing screen does not upload to a locked pet and says why", () => {
  const locked = processing.indexOf("if (intakePass.locked) throw new Error(t.photosLocked);");
  const loop = processing.indexOf("for (let index = 0; index < total; index += 1)");
  assert.ok(locked > 0 && locked < loop);
  // 다른 탭/예전 앱이 사이에 생성을 시작한 경우의 장별 메시지.
  assert.match(processing, /\} else if \(isPhase1LockedError\(imageError\)\) \{[\s\S]{0,120}msg = t\.photosLocked;/);
});

test("preview starts the run directly from its single CTA — no lock-confirmation step", () => {
  // 확인 패널·두 번째 버튼은 없다: CTA → handleConfirm → runPhase7Generation.
  assert.doesNotMatch(preview, /lockConfirm|lockAcknowledged|handleLockConfirmed/);
  assert.doesNotMatch(preview, /role="alertdialog"/);
  assert.equal(preview.split("onClick={handleConfirm}").length - 1, 1);
  const confirm = preview.indexOf("const handleConfirm = useCallback(async () => {");
  const run = preview.indexOf("const outcome = await runPhase7Generation({");
  assert.ok(confirm > 0 && run > confirm);
  // 잠금은 여전히 실행 생성에서 시작한다: startGenerationRun → 로컬 마커.
  const start = flow.indexOf("const started = await startGenerationRun(");
  const mark = flow.indexOf("markGenerationStarted({");
  assert.ok(start > 0 && start < mark);
});

test("upload screen turns off add, replace and remove when photos are locked", () => {
  assert.match(upload, /photosLocked\?: boolean;/);
  assert.match(upload, /disabled=\{photosLocked \|\| \(!isVideo && selectedImages\.length >= maxImages\)\}/);
  assert.match(upload, /\{onReplaceImage && !photosLocked \? \(/);
  assert.match(upload, /\{onRemoveImage && !photosLocked \? \(/);
  assert.match(upload, /\{u\.photosLockedNotice\}/);
});

test("app derives the lock from the server answer or the confirm marker and guards its handlers", () => {
  assert.match(app, /fetchPetInputsLocked\(\{ petId: lockPetId, accessToken: auth\.token \}\)/);
  assert.match(app, /localMarker: intakeIdentity \? hasActiveGeneration\(intakeIdentity\.contentId\) : false,/);
  assert.match(app, /photosLocked=\{photosLocked\}/);
  // 빈 자리는 잠기지 않고, 거기서 고른 사진은 새 신원으로 간다.
  assert.match(app, /hasPhotos: uploadedImages\.length > 0,/);
  assert.match(
    app,
    /const identity = identityForAddedPhotos\(slotId, existing\.length, slot\.intakeIdentity\)/,
  );
  // Start 는 잠긴 아이를 처리 화면으로 보내지 않는다.
  const lockedRoute = app.indexOf(
    "intakeContinueTarget({ canStart: canStartIntake, photosLocked }) === 'preview'",
  );
  const processingRoute = app.indexOf("navigateTo('aiProcessing')");
  assert.ok(lockedRoute > 0 && processingRoute > 0);
  // 잠긴 아이는 처리 화면으로 가는 줄에 닿기 전에 미리보기로 빠진다.
  assert.ok(lockedRoute < processingRoute);
  assert.match(
    app.slice(lockedRoute, processingRoute),
    /\) \{\s*navigateTo\('preview'\)\s*return\s*\}/,
  );
  // 처리 화면으로 가는 길은 이 한 곳뿐이다.
  assert.equal(app.split("navigateTo('aiProcessing')").length - 1, 1);
  for (const handler of [
    "const handleImagesUpload = async (files: File[]) => {",
    "const handleRemoveUploadedImage = (index: number) => {",
    "const handleReplaceUploadedImage = async (index: number, file: File) => {",
  ]) {
    const at = app.indexOf(handler);
    assert.ok(at > 0, handler);
    assert.match(app.slice(at, at + handler.length + 40), /\n    if \(photosLocked\) return/);
  }
});
