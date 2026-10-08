import assert from "node:assert/strict";
import test from "node:test";

import {
  recordBeamCommand,
  getRecentBeamCommands,
  reconcileAckedCommand,
  clearBeamActivity,
} from "./beam-activity-log.ts";

class MemoryStorage {
  private data = new Map<string, string>();
  getItem(key: string) {
    return this.data.get(key) ?? null;
  }
  setItem(key: string, value: string) {
    this.data.set(key, String(value));
  }
  removeItem(key: string) {
    this.data.delete(key);
  }
  clear() {
    this.data.clear();
  }
}

const previousLocal = globalThis.localStorage;

test.before(() => {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: new MemoryStorage(),
  });
});

test.after(() => {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: previousLocal,
  });
});

test.beforeEach(() => {
  clearBeamActivity();
});

test("recordBeamCommand keeps most-recent-first order and caps at 5", () => {
  for (let i = 0; i < 7; i += 1) {
    recordBeamCommand({ commandId: `cmd-${i}`, event: "theme_play", label: `theme-${i}`, delivery: "sent" });
  }
  const entries = getRecentBeamCommands();
  assert.equal(entries.length, 5);
  assert.equal(entries[0].commandId, "cmd-6");
  assert.equal(entries[4].commandId, "cmd-2");
});

test("a failed send is recorded as failed, never as a fake success", () => {
  recordBeamCommand({ event: "pet_asset", label: "pet-1/BREATHING", delivery: "failed", reason: "network" });
  const [entry] = getRecentBeamCommands();
  assert.equal(entry.delivery, "failed");
  assert.equal(entry.reason, "network");
});

test("reconcileAckedCommand upgrades only the matching, still-live entry", () => {
  recordBeamCommand({ commandId: "cmd-1", event: "theme_play", label: "fresh_forest", delivery: "sent" });
  recordBeamCommand({ commandId: "cmd-2", event: "pet_asset", label: "pet-1/BREATHING", delivery: "failed" });

  const next = reconcileAckedCommand("cmd-2");
  const failedEntry = next.find((e) => e.commandId === "cmd-2");
  assert.equal(failedEntry?.delivery, "failed", "a failed command must never flip to acked");

  const acked = reconcileAckedCommand("cmd-1");
  const sentEntry = acked.find((e) => e.commandId === "cmd-1");
  assert.equal(sentEntry?.delivery, "acked");
});

test("reconcileAckedCommand with no match is a no-op read", () => {
  recordBeamCommand({ commandId: "cmd-1", event: "theme_play", label: "fresh_forest", delivery: "sent" });
  const result = reconcileAckedCommand("cmd-unknown");
  assert.equal(result[0].delivery, "sent");
});

test("clearBeamActivity empties the log", () => {
  recordBeamCommand({ event: "theme_play", label: "fresh_forest", delivery: "sent" });
  clearBeamActivity();
  assert.deepEqual(getRecentBeamCommands(), []);
});
