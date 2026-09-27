import { strict as assert } from "node:assert";
import { test } from "node:test";
import { homeBeamCardState } from "./home-beam-state.ts";

test("no device id → Not set up", () => {
  assert.equal(homeBeamCardState({ deviceId: null, status: "unavailable", unavailableReason: "not_provisioned" }), "setup");
});

test("gateway says not provisioned → Not set up", () => {
  assert.equal(homeBeamCardState({ deviceId: "beam-001", status: "unavailable", unavailableReason: "not_provisioned" }), "setup");
});

test("first poll in flight → checking (never a guessed offline/online)", () => {
  assert.equal(homeBeamCardState({ deviceId: "beam-001", status: "checking", unavailableReason: null }), "checking");
});

test("gateway online → Connected", () => {
  assert.equal(homeBeamCardState({ deviceId: "beam-001", status: "connected", unavailableReason: null }), "connected");
});

test("gateway offline → Offline", () => {
  assert.equal(homeBeamCardState({ deviceId: "beam-001", status: "offline", unavailableReason: null }), "offline");
});

test("cannot verify (auth / network / unknown) → Offline, not a fabricated state", () => {
  for (const reason of ["auth", "network", "unknown"] as const) {
    assert.equal(homeBeamCardState({ deviceId: "beam-001", status: "unavailable", unavailableReason: reason }), "offline");
  }
});
