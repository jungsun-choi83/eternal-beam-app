/**
 * Phase 7G — 컷오버 배선: 새 경로가 기본이고, 레거시는 남아 있되 불리지 않는다.
 *
 * 소스 텍스트를 본다 — 순수 함수가 옳아도 화면이 부르지 않으면 컷오버가 아니다.
 * (baked-playback-wiring / packed-delivery-wiring 과 같은 철학.)
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (p: string) => readFileSync(p, "utf8");

const PREVIEW = "src/components/memorial/preview-screen.tsx";
const REGISTRY = "src/lib/pet-registry-api.ts";
const FLOW = "src/lib/phase7-generation-flow.ts";
const RUN_API = "src/lib/generation-run-api.ts";
const AI_PROC = "src/components/memorial/ai-processing-screen.tsx";

test("미리보기 확인: Phase 7 분기가 레거시 제출보다 먼저 온다", () => {
  const src = read(PREVIEW);
  const confirm = src.indexOf("const handleConfirm");
  assert.ok(confirm > 0);
  const phase7 = src.indexOf("if (phase7GenerationEnabled())", confirm);
  const legacySubmit = src.indexOf("requestIdleGeneration(", confirm);
  assert.ok(phase7 > 0, "Phase 7 분기가 없다");
  assert.ok(legacySubmit > 0, "레거시 코드는 보존돼야 한다 (명시 회귀 스위치용)");
  assert.ok(phase7 < legacySubmit, "레거시 제출이 새 경로보다 먼저다");
  // 새 분기는 자기 안에서 끝난다 — 레거시로 흘러내리지 않는다.
  const legacyLabel = src.indexOf("레거시 경로 (VITE_LEGACY_GENERATION=1 전용)", phase7);
  assert.ok(legacyLabel > phase7, "레거시 경로 라벨이 없다");
  const branch = src.slice(phase7, legacyLabel);
  assert.match(branch, /return;/);
  assert.match(branch, /runPhase7Generation\(/);
  assert.match(branch, /phase7PipelinePatch\(/);
  // 새 분기에서 기기 push/장면 굽기를 하지 않는다.
  assert.doesNotMatch(branch, /schedulePetReadyToDevice/);
  assert.doesNotMatch(branch, /buildCanonicalScene/);
  // 실패 시 레거시 폴백이 없다고 소스가 스스로 말한다.
  assert.match(branch, /레거시 폴백 없음/);
});

test("registry/register 우회: 새 실행 산출물은 클라이언트 등록 금지", () => {
  const preview = read(PREVIEW);
  assert.match(
    preview,
    /pipeline\.generation_source === "phase7-run"\) return/,
    "미리보기 등록 효과에 우회가 없다"
  );
  const registry = read(REGISTRY);
  assert.match(
    registry,
    /generation_source === "phase7-run"\) return null/,
    "복원 경로(ensureStoredReadyPetRegistered)에 우회가 없다"
  );
});

test("새 경로 어디에도 레거시 생성기 참조가 없다", () => {
  for (const p of [FLOW, RUN_API]) {
    const src = read(p);
    assert.ok(!src.includes("generate-pet-video"), `${p} 가 레거시 엔드포인트를 참조한다`);
    assert.ok(!src.includes("generatePetVideo"), `${p} 가 레거시 클라이언트를 참조한다`);
    assert.ok(!src.includes("requestIdleGeneration"), `${p} 가 레거시 오케스트레이터를 참조한다`);
  }
});

test("StoredPipeline: 새 실행 산출물 표시 필드가 계약에 있다", () => {
  const src = read(AI_PROC);
  assert.match(src, /generation_source\?: string \| null/);
  assert.match(src, /qa_decision\?: string \| null/);
});

test("REVIEW 는 REVIEW 로 흐른다 — 흐름 코드가 결정을 가공하지 않는다", () => {
  const src = read(FLOW);
  // REVIEW 재생은 published=false + qa_decision=REVIEW 계약 위반 시 거절한다.
  assert.match(src, /playback\.published \|\| playback\.qa_decision !== "REVIEW"/);
  assert.match(src, /REVIEW_PLAYBACK_INVALID/);
  // "PASS 로 승격" 같은 가공이 코드에 없다.
  assert.doesNotMatch(src, /qa_decision\s*[:=]\s*"PASS"/);
});
