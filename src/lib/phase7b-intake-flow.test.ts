import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const app = readFileSync(new URL("../app/EternalBeamApp.tsx", import.meta.url), "utf8");
const processing = readFileSync(
  new URL("../components/memorial/ai-processing-screen.tsx", import.meta.url),
  "utf8",
);

test("upload commit allocates the stable intake identity before processing — per pet slot", () => {
  const commit = app.slice(app.indexOf("const commitUpload"), app.indexOf("// 영상은 원본 사진이 아니다"));
  assert.match(commit, /kind === 'image'/);
  // 신원은 **활성 슬롯 앞으로** 발급된다. 인자 없는 호출은 전역 한 칸이던 시절의
  // 결함(펫 2가 펫 1의 content_id 를 덮어씀)으로 돌아가는 길이다.
  assert.match(commit, /const nextIdentity = beginPhase1Intake\(slotId\)/);
  assert.match(commit, /clearPhase1Intake\(slotId\)/);
  assert.ok(!/beginPhase1Intake\(\)/.test(app), "intake identity must never be allocated slot-less");
  assert.ok(!/clearPhase1Intake\(\)(?!\s*\/\/ all)/.test(commit));
  assert.match(app, /intakeIdentity=\{intakeIdentity\}/);
  assert.match(app, /petSlotId=\{activePetSlotId\}/);
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

test("Phase 7B intake can iterate 1–3 selected images before identity build", () => {
  assert.match(processing, /for \(let index = 0; index < total; index \+= 1\)/);
  assert.match(processing, /const total = intakeImages\.length/);
  assert.match(processing, /await buildIdentityProfile\(firstReady\.petId, auth\.token\)/);
});

