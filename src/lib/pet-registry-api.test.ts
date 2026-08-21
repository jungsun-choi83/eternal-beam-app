import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { ensurePetRegistered } from "./pet-registry-api.ts";

const PET = "pet_5da0d31f-33d8-4735-8e60-0c2a532ed358";
const CONTENT = PET.slice(4);
const BREATHING = "https://project.supabase.co/storage/v1/object/sign/pets/u/c/idle_loop.mp4?token=x";

test("no token returns PENDING_AUTH and never makes an insecure POST", async () => {
  let requests = 0;
  const result = await ensurePetRegistered(
    { petId: PET, contentId: CONTENT, breathingUrl: BREATHING },
    {
      getToken: async () => ({ token: null, source: "none", reason: "no-session" }),
      fetch: async () => {
        requests += 1;
        return new Response(null, { status: 500 });
      },
    }
  );
  assert.deepEqual(result, { state: "PENDING_AUTH", reason: "no-session" });
  assert.equal(requests, 0);
});

test("authenticated ensure posts the unchanged canonical petId to the existing endpoint", async () => {
  let url = "";
  let init: RequestInit | undefined;
  const result = await ensurePetRegistered(
    { petId: PET, contentId: CONTENT, breathingUrl: BREATHING },
    {
      getToken: async () => ({ token: "jwt", source: "supabase" }),
      fetch: async (input, requestInit) => {
        url = String(input);
        init = requestInit;
        return new Response(JSON.stringify({ pet_id: PET, content_id: CONTENT }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      },
    }
  );
  assert.deepEqual(result, { state: "REGISTERED" });
  assert.equal(url, "/api/v1/pet/registry/register");
  assert.equal(init?.method, "POST");
  assert.equal((init?.headers as Record<string, string>).Authorization, "Bearer jwt");
  assert.deepEqual(JSON.parse(String(init?.body)), {
    pet_id: PET,
    content_id: CONTENT,
    breathing_url: BREATHING,
  });
});

test("Preview ensures both fresh and restored READY pets and retries on auth restoration", () => {
  const source = readFileSync("src/components/memorial/preview-screen.tsx", "utf8");
  assert.match(source, /if \(!hasIdle \|\| !pipeline\?\.content_id \|\| !pipeline\.idle_video_url\) return/);
  assert.match(source, /ensurePetRegistered\(registration\)/);
  assert.match(source, /onAuthStateChange\(\(signedIn\) => \{\s*if \(signedIn\) void ensure\(\)/);

  const generationStart = source.indexOf("const handleConfirm");
  const generationBody = source.slice(generationStart);
  assert.ok(!generationBody.includes("ensurePetRegistered("), "generation must not own registration");
});

test("app-wide session restoration repairs a stored READY pet even after Preview unmounts", () => {
  const source = readFileSync("src/app/EternalBeamApp.tsx", "utf8");
  const registry = readFileSync("src/lib/pet-registry-api.ts", "utf8");
  assert.match(source, /onAuthStateChange\(\(signedIn\) =>/);
  assert.match(source, /if \(!signedIn\) return/);
  assert.match(source, /ensureStoredReadyPetRegistered\(\)/);
  assert.match(registry, /sessionStorage\.getItem\("eternal_beam_pipeline_v1"\)/);
});
