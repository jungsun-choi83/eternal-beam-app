import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const app = readFileSync(new URL("../app/EternalBeamApp.tsx", import.meta.url), "utf8");
const processing = readFileSync(
  new URL("../components/memorial/ai-processing-screen.tsx", import.meta.url),
  "utf8",
);

test("upload commit allocates the stable intake identity before processing", () => {
  const commit = app.slice(app.indexOf("const commitUpload"), app.indexOf("const originalPhoto"));
  assert.match(commit, /kind === 'image'/);
  assert.match(commit, /beginPhase1Intake\(\)/);
  assert.match(app, /intakeIdentity=\{intakeIdentity\}/);
});

test("original is awaited before cutout and derived attachment is awaited after", () => {
  const firstPersist = processing.indexOf("const original = await persistPhase1Intake");
  const cutout = processing.indexOf("const cutout = await runCutoutWithFallback");
  const attach = processing.indexOf("const ready = await persistPhase1Intake");
  assert.ok(firstPersist >= 0 && firstPersist < cutout);
  assert.ok(cutout < attach);
  assert.ok(!processing.includes("void persistOriginalReference"));
});

test("theme data is absent from the Phase 1 intake calls", () => {
  for (const marker of [
    "const original = await persistPhase1Intake",
    "const ready = await persistPhase1Intake",
  ]) {
    const call = processing.slice(processing.indexOf(marker), processing.indexOf("});", processing.indexOf(marker)));
    assert.ok(!/theme|scene|background/i.test(call));
  }
});

