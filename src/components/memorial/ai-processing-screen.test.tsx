/**
 * 처리 화면이 **활성 펫의 사진 전부**를 제출하는가 (멀티 포토 제출).
 *
 * 한 펫은 사진 1–3장의 배열을 가진다. Start 를 누르면 그 배열 전체가
 *   beginIntakePass(photos) → 장별 업로드 루프 → 동기화
 * 로 흘러야 한다. 화면에 크게 보이는 대표 사진(uploadedImage)은 표시용일 뿐이고,
 * 무엇이 제출되는지를 정하지 않는다.
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const beginIntakePass = vi.fn();
const persistPhase1Intake = vi.fn();
const finish = vi.fn(async () => {});

vi.mock("@/lib/reference-sync", () => ({
  beginIntakePass: (...args: unknown[]) => beginIntakePass(...args),
}));
vi.mock("@/lib/original-reference", async () => {
  const actual = await vi.importActual<typeof import("@/lib/original-reference")>(
    "@/lib/original-reference",
  );
  return { ...actual, persistPhase1Intake: (...args: unknown[]) => persistPhase1Intake(...args) };
});
vi.mock("@/lib/test-app-flags", () => ({ MOCK_CUTOUT_ENABLED: true, TEST_APP_MODE: true }));
vi.mock("@/lib/mock-cutout", () => ({
  mockCutoutFromFile: vi.fn(async () => "data:image/png;base64,Y3V0b3V0"),
}));
vi.mock("@/lib/normalize-image", () => ({
  normalizeImageForCutout: vi.fn(async () => new File([new Uint8Array([1])], "n.jpg", { type: "image/jpeg" })),
  friendlyCutoutError: (message: string) => message,
  cutoutRejectionMessage: () => null,
}));
vi.mock("@/lib/client-cutout", () => ({
  clientCutoutFromFile: vi.fn(async () => "data:image/png;base64,Y3V0b3V0"),
  dataUrlToFile: (_url: string, name: string) => new File([new Uint8Array([2])], name, { type: "image/png" }),
}));
vi.mock("@/lib/display-image", () => ({
  createDisplayImageUrl: vi.fn(async (url: string) => url),
  createDisplayCutoutUrl: vi.fn(async (url: string) => url),
}));
vi.mock("@/app/services/videoProcessingApi", () => ({
  assertUsableCutout: vi.fn(),
  cutoutImage: vi.fn(),
  isCutoutApiUnreachableError: () => false,
  isCutoutRejectedError: () => false,
}));
vi.mock("@/lib/premium-auth-token", () => ({
  getPremiumAccessToken: vi.fn(async () => ({ token: "token", source: "test" })),
}));
vi.mock("@/lib/eternal-beam-user", () => ({ getEternalBeamUserId: () => "alice@test" }));
vi.mock("@/lib/supabase-auth", () => ({ syncEternalBeamIdentity: vi.fn(async () => "alice@test") }));
vi.mock("@/lib/image-trace", () => ({ traceImage: vi.fn(async () => {}), dumpImageTrace: vi.fn() }));
vi.mock("@/lib/video-api-warmup", () => ({ warmupVideoApi: vi.fn(async () => true) }));
vi.mock("@/lib/pending-generation", () => ({ setPendingCutout: vi.fn() }));
vi.mock("@/lib/use-processing-clock", () => ({ useProcessingClock: () => ({ seconds: 0 }) }));
vi.mock("@/components/memorial/pet-idle-display", () => ({ PetIdleDisplay: () => null }));

import { AIProcessingScreen } from "./ai-processing-screen";
import { beginPhase1Intake } from "@/lib/phase1-intake-session";

const photo = (label: string) => `data:image/jpeg;base64,${btoa(`photo-${label}`)}`;
const A = photo("A");
const B = photo("B");
const C = photo("C");

type Uploaded = { contentId: string; dataUrl: string; withCutout: boolean };
let uploads: Uploaded[];

beforeEach(() => {
  uploads = [];
  beginIntakePass.mockReset();
  persistPhase1Intake.mockReset();
  finish.mockClear();
  beginIntakePass.mockImplementation(async () => ({ hashes: [], locked: false, preSyncOk: true, finish }));
  persistPhase1Intake.mockImplementation(
    async (params: { contentId: string; dataUrl: string; userId: string; cutoutFile?: File }) => {
      uploads.push({ contentId: params.contentId, dataUrl: params.dataUrl, withCutout: Boolean(params.cutoutFile) });
      const referenceId = `ref:${params.contentId}:${params.dataUrl.slice(-6)}`;
      return {
        userId: params.userId,
        contentId: params.contentId,
        petId: `pet_${params.contentId}`,
        referenceId,
        objectPath: "p",
        version: 1,
        recorded: true,
        deduplicated: false,
        cutoutReferenceId: params.cutoutFile ? `cut:${referenceId}` : null,
        cutoutObjectPath: params.cutoutFile ? "c" : null,
        cutoutRecorded: Boolean(params.cutoutFile),
        intakeReady: Boolean(params.cutoutFile),
      };
    },
  );
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
});

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  localStorage.clear();
  vi.unstubAllGlobals();
});

/** Start 직후의 처리 화면 — 부모(EternalBeamApp)가 활성 펫의 값을 내려 주는 모양 그대로. */
function start(photos: string[], slot = "pet_slot_1", contentId = "content-1", highlighted = photos[0]) {
  const identity = beginPhase1Intake(slot, () => contentId);
  const onComplete = vi.fn();
  const view = render(
    <AIProcessingScreen
      uploadedImage={highlighted}
      uploadedImages={photos}
      petSlotId={slot}
      intakeIdentity={identity}
      language="en"
      onComplete={onComplete}
    />,
  );
  return { identity, onComplete, view };
}

async function finished(onComplete: ReturnType<typeof vi.fn>) {
  await waitFor(() => expect(onComplete).toHaveBeenCalled(), { timeout: 8000 });
}

/** 한 번의 Start 가 업로드한 **서로 다른** 원본 (장마다 원본 → 원본+누끼 두 번 올린다). */
const submitted = () => [...new Set(uploads.map((u) => u.dataUrl))];

describe("Start submits every photo of the active pet", () => {
  it.each([
    ["1 photo", [A]],
    ["2 photos", [A, B]],
    ["3 photos", [A, B, C]],
  ])("%s → exactly those photos reach beginIntakePass and the upload loop", async (_label, photos) => {
    const { onComplete, identity } = start(photos);
    await finished(onComplete);

    expect(beginIntakePass).toHaveBeenCalledTimes(1);
    const pass = beginIntakePass.mock.calls[0][0] as { petId: string; photos: string[] };
    expect(pass.petId).toBe(identity.petId);
    expect(pass.photos).toEqual(photos);

    expect(submitted()).toEqual(photos);
    // 장마다 원본 1회 + 원본·누끼 1회.
    expect(uploads).toHaveLength(photos.length * 2);
    expect(uploads.filter((u) => u.withCutout).map((u) => u.dataUrl)).toEqual(photos);
    expect(new Set(uploads.map((u) => u.contentId))).toEqual(new Set([identity.contentId]));
    expect(finish).toHaveBeenCalledWith({ syncAfter: true });
  }, 12000);

  it("the highlighted (displayed) photo does not change what is submitted", async () => {
    // 같은 펫, 같은 세 장 — 화면에 크게 보이는 사진만 C 로 다르다.
    const { onComplete } = start([A, B, C], "pet_slot_1", "content-1", C);
    await finished(onComplete);

    expect((beginIntakePass.mock.calls[0][0] as { photos: string[] }).photos).toEqual([A, B, C]);
    expect(submitted()).toEqual([A, B, C]);
  }, 12000);

  it("receipt handed to generation lists every ready pair, not just the first", async () => {
    const { onComplete } = start([A, B, C]);
    await finished(onComplete);
    const stored = JSON.parse(sessionStorage.getItem("eternal_beam_pipeline_v1") || "{}");
    expect(stored.phase1_intake.original_reference_ids).toHaveLength(3);
    expect(stored.phase1_intake.cutout_reference_ids).toHaveLength(3);
  }, 12000);

  it("Pet 2's Start submits only Pet 2's photos under Pet 2's identity", async () => {
    const pet1 = start([A, B, C], "pet_slot_1", "content-1");
    await finished(pet1.onComplete);
    const pet1Uploads = [...uploads];
    cleanup();
    uploads = [];
    beginIntakePass.mockClear();

    const D = photo("D");
    const pet2 = start([D], "pet_slot_2", "content-2");
    await finished(pet2.onComplete);

    const pass = beginIntakePass.mock.calls[0][0] as { petId: string; photos: string[] };
    expect(pass.petId).toBe("pet_content-2");
    expect(pass.photos).toEqual([D]);
    expect(submitted()).toEqual([D]);
    expect(new Set(uploads.map((u) => u.contentId))).toEqual(new Set(["content-2"]));
    // 펫 1의 사진은 펫 1의 신원으로만 올라갔다.
    expect(new Set(pet1Uploads.map((u) => u.contentId))).toEqual(new Set(["content-1"]));
    expect(pet1Uploads.some((u) => u.dataUrl === D)).toBe(false);
  }, 20000);
});
