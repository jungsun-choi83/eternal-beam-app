/**
 * Phase 4 — 진행 화면이 실제로 generation-run 상태를 받아 그리는지 소스로
 * 확인한다 (phase7-cutover-wiring.test.ts 와 같은 철학: 순수 함수가 옳아도
 * 화면이 부르지 않으면 배선이 아니다).
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (p: string) => readFileSync(p, "utf8");

const PREVIEW = "src/components/memorial/preview-screen.tsx";
const SCREEN = "src/components/memorial/generation-progress-screen.tsx";
const APP = "src/app/EternalBeamApp.tsx";
const I18N = "src/components/memorial/memorial-i18n.ts";

test("확인(runPhase7Generation)과 재개(resumePhase7Generation) 둘 다 onProgress 로 runState 를 채운다", () => {
  const src = read(PREVIEW);
  assert.match(src, /runPhase7Generation\(\{[\s\S]{0,200}?poll:\s*\{\s*onProgress:\s*\(run\)\s*=>\s*setRunState\(run\)/);
  assert.match(
    src,
    /resumePhase7Generation\(\{[\s\S]{0,300}?poll:\s*\{[\s\S]{0,80}?onProgress:[\s\S]{0,80}?setRunState\(run\)/
  );
});

test("finalizeOutcome 은 outcome.run 을 runState 에 곧장 반영한다 — 마지막 폴 tick 경합에 기대지 않는다", () => {
  const src = read(PREVIEW);
  const start = src.indexOf("const finalizeOutcome");
  const end = src.indexOf("\n  );", start);
  const body = src.slice(start, end > 0 ? end : undefined);
  assert.match(body, /setRunState\(outcome\.run\)/);
});

test("실제 제출 시도에만 runAttemptedRef 를 세운다 — 클라이언트 검증 오류는 전면 오류 화면을 띄우지 않는다", () => {
  const src = read(PREVIEW);
  assert.match(src, /const runAttemptedRef = useRef\(false\)/);
  // 확인 경로: petId 검증 통과 후, runPhase7Generation 호출 직전에 세운다.
  const confirmPhase7 = src.indexOf("if (phase7GenerationEnabled())");
  const confirmRun = src.indexOf("runPhase7Generation(", confirmPhase7);
  const confirmFlag = src.lastIndexOf("runAttemptedRef.current = true", confirmRun);
  assert.ok(confirmFlag > confirmPhase7 && confirmFlag < confirmRun);
  // 재개 경로도 마찬가지.
  const resumeEffectStart = src.indexOf("useEffect(() => {\n    if (!phase7GenerationEnabled() || isLibraryFlow) return;");
  const resumeRun = src.indexOf("resumePhase7Generation(", resumeEffectStart);
  const resumeFlag = src.lastIndexOf("runAttemptedRef.current = true", resumeRun);
  assert.ok(resumeFlag > resumeEffectStart && resumeFlag < resumeRun);
});

test("진행/오류 화면은 GenerationProgressScreen 하나로 그려진다 — 조정 UI 를 대체한다", () => {
  const src = read(PREVIEW);
  assert.match(src, /import \{ GenerationProgressScreen \} from "@\/components\/memorial\/generation-progress-screen"/);
  assert.match(src, /const showGenerationProgress = generating && phase7GenerationEnabled\(\)/);
  assert.match(src, /if \(showGenerationProgress \|\| showGenerationError\)/);
  assert.match(src, /<GenerationProgressScreen/);
  assert.match(src, /view=\{view\}/);
  assert.match(src, /onRetry=\{showGenerationError \? handleConfirm : undefined\}/);
});

test("새로고침 재개 중에도(마운트 시 이미 활성 실행이 있으면) 첫 페인트부터 진행 화면이다", () => {
  const src = read(PREVIEW);
  const stateInit = src.indexOf("const [generating, setGenerating] = useState(() => {");
  assert.ok(stateInit > 0);
  const stateEnd = src.indexOf("});", stateInit);
  const body = src.slice(stateInit, stateEnd);
  assert.match(body, /readActiveGeneration\(meta\.contentId\)/);
});

test("Phase 6 — preview 화면은 조정 단계부터 데스크톱 wide 스테이지를 쓴다(Composer 가 메인 작업 공간)", () => {
  const app = read(APP);
  assert.match(
    app,
    /wide=\{screen === 'home' \|\| screen === 'photoUpload' \|\| screen === 'themeSelection' \|\| screen === 'preview' \|\| screen === 'device' \|\| screen === 'library' \|\| screen === 'settings'\}/
  );
});

test("진행 화면은 백엔드 7단계를 바꾸지 않고 5개 사용자 단계로만 시각적으로 묶는다", () => {
  const src = read(SCREEN);
  const displayPhases = src.slice(
    src.indexOf("const DISPLAY_PHASES"),
    src.indexOf("const DISPLAY_PHASE_INDEX"),
  );
  assert.equal([...displayPhases.matchAll(/\{ key:/g)].length, 5);
  assert.match(src, /waiting_capacity:\s*2/);
  assert.match(src, /generating_motion:\s*2/);
  assert.match(src, /complete:\s*DISPLAY_PHASES\.length/);
});

test("진행 UI는 추정 퍼센트를 만들지 않고 실제 상태와 경과 시간만 표시한다", () => {
  const src = read(SCREEN);
  assert.doesNotMatch(src, /aria-valuenow|role="progressbar"/);
  assert.doesNotMatch(src, /style=\{\{\s*width:\s*`\$\{/);
  assert.match(src, /formatElapsed\(elapsedSec\)/);
  assert.match(src, /view\.waiting \? t\.waitingBadge : t\.activeBadge/);
});

test("진행 화면 7단계 카피가 ko/en 모두 있다", () => {
  const i18n = read(I18N);
  const stages = [
    "preparing_pet",
    "creating_appearance",
    "waiting_capacity",
    "generating_motion",
    "checking_result",
    "rendering",
    "complete",
  ];
  const occurrences = stages.map((s) => [...i18n.matchAll(new RegExp(`${s}:\\s*\\{`, "g"))].length);
  for (const [i, count] of occurrences.entries()) {
    assert.equal(count, 2, `${stages[i]} 는 ko/en 두 번 정의돼야 한다 (실제 ${count}회)`);
  }
});

test("진행 화면 컴포넌트는 프로바이더 이름을 절대 문자열로 드러내지 않는다", () => {
  const src = read(SCREEN);
  for (const banned of ["fal", "Wan", "GPT Image", "Luma"]) {
    const pattern = new RegExp(`\\b${banned}\\b`);
    assert.ok(!pattern.test(src), `${banned} 가 진행 화면 소스에 노출돼 있다`);
  }
});
