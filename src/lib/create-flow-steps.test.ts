import assert from "node:assert/strict";
import test from "node:test";

import { computeCreateFlowSteps } from "./create-flow-steps.ts";

test("누끼가 있으면: Upload/Prepare 완료, Choose a Theme 현재, 나머지 예정", () => {
  const steps = computeCreateFlowSteps(true);
  assert.deepEqual(
    steps.map((s) => s.status),
    ["complete", "complete", "current", "upcoming", "upcoming"]
  );
  assert.deepEqual(
    steps.map((s) => s.id),
    ["uploadPhotos", "preparePet", "chooseTheme", "previewCreate", "playOnBeam"]
  );
});

test("누끼가 없으면: Prepare Pet 이 아직 현재이고 Choose a Theme 는 예정이다", () => {
  const steps = computeCreateFlowSteps(false);
  assert.deepEqual(
    steps.map((s) => s.status),
    ["complete", "current", "upcoming", "upcoming", "upcoming"]
  );
});

test("Preview & Create / Play on Beam 은 어느 경우에도 예정이다 — 이 화면 뒤에 온다", () => {
  for (const hasPreparedPet of [true, false]) {
    const steps = computeCreateFlowSteps(hasPreparedPet);
    assert.equal(steps.find((s) => s.id === "previewCreate")?.status, "upcoming");
    assert.equal(steps.find((s) => s.id === "playOnBeam")?.status, "upcoming");
  }
});

test("Upload Photos 는 어느 경우에도 완료다 — 이 화면 앞에 온다", () => {
  for (const hasPreparedPet of [true, false]) {
    assert.equal(
      computeCreateFlowSteps(hasPreparedPet).find((s) => s.id === "uploadPhotos")?.status,
      "complete"
    );
  }
});
