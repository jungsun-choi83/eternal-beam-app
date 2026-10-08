import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  demoDeviceId,
  resolvePairedDeviceId,
  sendDeviceCommand,
  type DeviceCommandPayload,
} from "./device-command-api.ts";

const token = async () => ({ token: "jwt-device", source: "supabase" as const });

function fakeResponse(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as Response;
}

test("sendDeviceCommand sends authenticated JSON and accepts sent", async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const payload: DeviceCommandPayload = {
    device_id: demoDeviceId(),
    event: "pet_asset",
    pet_id: "pet_abc",
    motion_id: "BREATHING",
    spawn_vfx: "heart",
  };
  const result = await sendDeviceCommand(payload, {
    apiBase: "https://api.test",
    getToken: token,
    fetchFn: async (url, init) => {
      calls.push({ url, init });
      return fakeResponse({ command_id: "cmd-1", delivery: "sent" });
    },
  });

  assert.deepEqual(result, { ok: true, delivery: "sent", command_id: "cmd-1" });
  assert.equal(calls[0].url, "https://api.test/api/v1/device/commands");
  assert.equal(
    (calls[0].init?.headers as Record<string, string>).Authorization,
    "Bearer jwt-device",
  );
  assert.deepEqual(JSON.parse(String(calls[0].init?.body)), payload);
});

test("sendDeviceCommand keeps pet_asset backward compatible without spawn_vfx", async () => {
  let wireBody: unknown;
  const payload: DeviceCommandPayload = {
    device_id: demoDeviceId(),
    event: "pet_asset",
    pet_id: "pet_abc",
    motion_id: "BREATHING",
  };

  const result = await sendDeviceCommand(payload, {
    getToken: token,
    fetchFn: async (_url, init) => {
      wireBody = JSON.parse(String(init?.body));
      return fakeResponse({ command_id: "cmd-legacy", delivery: "pending" });
    },
  });

  assert.equal(result.ok, true);
  assert.deepEqual(wireBody, payload);
  assert.equal("spawn_vfx" in (wireBody as Record<string, unknown>), false);
});

test("sendDeviceCommand accepts offline pending delivery", async () => {
  const result = await sendDeviceCommand(
    { device_id: demoDeviceId(), event: "theme_play", theme_id: "fresh_forest" },
    { getToken: token, fetchFn: async () => fakeResponse({ delivery: "pending" }) },
  );
  assert.deepEqual(result, { ok: true, delivery: "pending", command_id: undefined });
});

test("sendDeviceCommand reports failures instead of throwing", async () => {
  const warnings: unknown[][] = [];
  const logger = { warn: (...args: unknown[]) => warnings.push(args) };
  const result = await sendDeviceCommand(
    { device_id: demoDeviceId(), event: "theme_play", theme_id: "fresh_forest" },
    {
      getToken: token,
      logger,
      fetchFn: async () => {
        throw new Error("offline");
      },
    },
  );
  assert.equal(result.ok, false);
  assert.equal(warnings.length, 1);
});

test("Theme Selection sends zero device commands — browsing and Continue are both web-only (P0 #1)", () => {
  const source = readFileSync("src/app/EternalBeamApp.tsx", "utf8");
  const selectStart = source.indexOf("const handleThemeSelect");
  const continueStart = source.indexOf("const handleThemeContinue");
  const selectBlock = source.slice(selectStart, continueStart);
  const continueBlock = source.slice(continueStart, source.indexOf("const handlePreviewSettingsChange"));
  assert.equal(selectBlock.includes("sendDeviceCommand"), false);
  assert.equal(
    continueBlock.includes("sendDeviceCommand"),
    false,
    "Continue must never send theme_play — only an explicit Play on Beam press may",
  );
  assert.equal(
    source.includes("sendDeviceCommand"),
    false,
    "Theme Selection screen no longer talks to the device gateway at all",
  );
});

test("published Phase 7 flow never auto-sends a device command — only explicit Play on Beam may (P0 #2)", () => {
  const source = readFileSync("src/components/memorial/preview-screen.tsx", "utf8");
  // 결과 반영은 finalizeOutcome 으로 공유된다 — 방금 끝난 확인과 새로고침
  // 재개(resumePhase7Generation)가 같은 코드로 마무리되게 하기 위해서다.
  // 이제 이 함수는 기기로 아무것도 보내지 않는다 — Play on Beam 이 명시적
  // 으로 눌릴 때만(My Library 의 onPlayOnBeam) 명령이 나간다.
  const finalizeStart = source.indexOf("const finalizeOutcome");
  const finalizeEnd = source.indexOf("\n  );", finalizeStart);
  const block = source.slice(finalizeStart, finalizeEnd);
  assert.equal(
    (block.match(/sendDeviceCommand\(/g) || []).length,
    0,
    "finalizeOutcome must never fire a device command automatically on publish",
  );
  assert.match(block, /outcome\.run\.status === "PUBLISHED"/);
  assert.match(block, /clearActiveGeneration\(next\.content_id\)/);
  const deviceCommandImport = source.match(/import \{[^}]*\} from "@\/lib\/device-command-api";/);
  assert.ok(deviceCommandImport, "preview-screen.tsx no longer imports device-command-api at all");
  assert.equal(
    deviceCommandImport![0].includes("sendDeviceCommand"),
    false,
    "Create/upload flow no longer imports sendDeviceCommand — it never sends a device command",
  );

  const phase7Start = source.indexOf("if (phase7GenerationEnabled())");
  const phase7End = source.indexOf("// ═══ 레거시 경로", phase7Start);
  const phase7Block = source.slice(phase7Start, phase7End);
  assert.match(phase7Block, /finalizeOutcome\(/);
  assert.equal(phase7Block.includes("sendDeviceCommand("), false);
});

test("resolvePairedDeviceId has no real per-user pairing source yet — only the explicit demo flag unlocks a device (P0 #3)", () => {
  // No `window` exists in this Node test environment, which also pins the
  // safe default outside a browser/demo context: no device, never beam-001
  // by accident.
  assert.equal(resolvePairedDeviceId(), null);
});

test("single hardware test mode: VITE_DEVICE_TEST_ID is an explicit test/staging opt-in, never a production default", () => {
  const source = readFileSync("src/lib/device-command-api.ts", "utf8");
  // The override must read a real env var (not a hardcoded literal) and must
  // never be reachable from resolvePairedDeviceId() without going through
  // that env read — i.e. no second unconditional `return DEMO_DEVICE_ID`.
  assert.match(source, /VITE_DEVICE_TEST_ID/);
  const resolveStart = source.indexOf("export function resolvePairedDeviceId");
  const resolveEnd = source.indexOf("\n}", resolveStart);
  const resolveBody = source.slice(resolveStart, resolveEnd);
  assert.equal(
    (resolveBody.match(/DEMO_DEVICE_ID/g) || []).length,
    1,
    "resolvePairedDeviceId must only ever return the demo id via the explicit kickstarter-demo branch, not as a bare fallback",
  );
  assert.match(resolveBody, /testDeviceIdFromEnv\(\)/);
});
