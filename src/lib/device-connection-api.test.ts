import assert from "node:assert/strict";
import test from "node:test";

import { getDeviceConnectionState } from "./device-connection-api.ts";

const token = async () => ({ token: "jwt-device", source: "supabase" as const });

function fakeResponse(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

test("getDeviceConnectionState returns the real online/offline snapshot", async () => {
  const result = await getDeviceConnectionState("beam-001", {
    apiBase: "https://api.test",
    getToken: token,
    fetchFn: async (url, init) => {
      assert.equal(url, "https://api.test/api/v1/device/devices/beam-001");
      assert.equal((init?.headers as Record<string, string>).Authorization, "Bearer jwt-device");
      return fakeResponse({
        device_id: "beam-001",
        online: true,
        connected_at: "2026-09-26T00:00:00Z",
        last_seen: "2026-09-26T00:05:00Z",
        last_ack: "cmd-1",
      });
    },
  });
  assert.deepEqual(result, {
    ok: true,
    state: {
      device_id: "beam-001",
      online: true,
      connected_at: "2026-09-26T00:00:00Z",
      last_seen: "2026-09-26T00:05:00Z",
      last_ack: "cmd-1",
    },
  });
});

test("getDeviceConnectionState reports offline honestly (not connected)", async () => {
  const result = await getDeviceConnectionState("beam-001", {
    getToken: token,
    fetchFn: async () =>
      fakeResponse({ device_id: "beam-001", online: false, connected_at: null, last_seen: null, last_ack: null }),
  });
  assert.equal(result.ok, true);
  assert.equal(result.ok && result.state.online, false);
});

test("getDeviceConnectionState maps 404 to not_provisioned, not offline", async () => {
  const result = await getDeviceConnectionState("beam-001", {
    getToken: token,
    fetchFn: async () => fakeResponse({ detail: { code: "DEVICE_UNKNOWN" } }, 404),
  });
  assert.deepEqual(result, { ok: false, reason: "not_provisioned", message: "device_not_provisioned" });
});

test("getDeviceConnectionState maps missing auth token to reason=auth without a network call", async () => {
  let called = false;
  const result = await getDeviceConnectionState("beam-001", {
    getToken: async () => ({ token: null, source: "none", reason: "no-session" }),
    fetchFn: async () => {
      called = true;
      return fakeResponse({});
    },
  });
  assert.equal(called, false);
  assert.equal(result.ok, false);
  assert.equal(!result.ok && result.reason, "auth");
});

test("getDeviceConnectionState maps a thrown fetch error to reason=network", async () => {
  const result = await getDeviceConnectionState("beam-001", {
    getToken: token,
    fetchFn: async () => {
      throw new Error("offline");
    },
  });
  assert.equal(result.ok, false);
  assert.equal(!result.ok && result.reason, "network");
});

test("getDeviceConnectionState maps 401 to reason=auth", async () => {
  const result = await getDeviceConnectionState("beam-001", {
    getToken: token,
    fetchFn: async () => fakeResponse({}, 401),
  });
  assert.equal(result.ok, false);
  assert.equal(!result.ok && result.reason, "auth");
});
