import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { authenticatedOpsFetch } from "./shaker-ops-api.ts";

test("expired Ops token refreshes and retries exactly once", async () => {
  const authorizations: string[] = [];
  let refreshes = 0;
  const response = await authenticatedOpsFetch("/api/v1/shaker/ops/pets", {}, {
    getToken: async () => "expired",
    refreshToken: async () => {
      refreshes += 1;
      return "fresh";
    },
    fetch: async (_input, init) => {
      authorizations.push((init?.headers as Record<string, string>).Authorization);
      return new Response(null, { status: authorizations.length === 1 ? 401 : 200 });
    },
  });
  assert.equal(response.status, 200);
  assert.equal(refreshes, 1);
  assert.deepEqual(authorizations, ["Bearer expired", "Bearer fresh"]);
});

test("unrecoverable authentication stops after the first 401", async () => {
  let requests = 0;
  await assert.rejects(
    authenticatedOpsFetch("/api/v1/shaker/ops/pets", {}, {
      getToken: async () => "expired",
      refreshToken: async () => null,
      fetch: async () => {
        requests += 1;
        return new Response(null, { status: 401 });
      },
    }),
    (error: unknown) => (error as { code?: string }).code === "UNAUTHENTICATED"
  );
  assert.equal(requests, 1);
});

test("Ops input remains controlled and query is not cleared during search", () => {
  const screen = readFileSync("src/components/memorial/shaker-ops-screen.tsx", "utf8");
  assert.match(screen, /value=\{query\}/);
  assert.match(screen, /onChange=\{\(e\) => setQuery\(e\.target\.value\)\}/);
  assert.match(screen, /onKeyDown=\{\(e\) => e\.key === "Enter" && void doSearch\(\)\}/);
  assert.ok(!screen.includes("setQuery(\"\")"));
  assert.ok(!screen.includes("key={query}"));
});

test("backend contract is registry-first with explicit legacy opt-in and failure", () => {
  const router = readFileSync("backend/routers/shaker_ops_v1.py", "utf8");
  const service = readFileSync("backend/services/shaker_ops.py", "utf8");
  assert.match(router, /includeLegacy: bool = False/);
  assert.match(router, /limit: int = 200/);
  assert.match(service, /source="REGISTRY"/);
  assert.match(service, /source="LEGACY"/);
  assert.match(service, /PET_REGISTRY_UNAVAILABLE/);
  assert.match(service, /registry_rows\.sort\(key=_created, reverse=True\)/);
});
