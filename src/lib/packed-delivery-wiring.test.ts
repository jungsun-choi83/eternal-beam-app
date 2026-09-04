/**
 * Phase 7F — packed-alpha 전달 포맷: 판정 + **배선**.
 *
 * baked-playback-wiring.test.ts 와 같은 철학이다: 순수 판정이 옳아도 화면이
 * 부르지 않으면 죽은 코드다. 여기서는 (1) resolveDeliveryFormat 판정과
 * (2) 그 값이 재생기까지 실제로 흐르는가(소스 배선)를 함께 본다.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { resolveDeliveryFormat } from "./baked-playback.ts";

const read = (p: string) => readFileSync(p, "utf8");

const IDLE_LOOP = "src/components/memorial/idle-loop-video.tsx";
const PET_DISPLAY = "src/components/memorial/pet-idle-display.tsx";
const DEVICE = "src/components/memorial/memorial-device-play-screen.tsx";
const PREVIEW = "src/components/memorial/preview-screen.tsx";
const AI_PROC = "src/components/memorial/ai-processing-screen.tsx";

// ── 판정 ────────────────────────────────────────────────────────────────────

test("resolveDeliveryFormat: 명시 packed_alpha → packed 렌더러", () => {
  assert.equal(
    resolveDeliveryFormat({ backgroundBaked: false, delivery_format: "packed_alpha" }),
    "packed_alpha"
  );
  // camelCase / 공백 / 대문자 허용 — 서버·파이프라인 양쪽 표기를 모두 받는다.
  assert.equal(
    resolveDeliveryFormat({ deliveryFormat: "  PACKED_ALPHA " }),
    "packed_alpha"
  );
});

test("resolveDeliveryFormat: 레거시(선언 없음) → blackkey 그대로", () => {
  assert.equal(resolveDeliveryFormat({ backgroundBaked: false }), "blackkey");
  assert.equal(resolveDeliveryFormat(null), "blackkey");
  assert.equal(resolveDeliveryFormat({ delivery_format: "unknown_future" }), "blackkey");
});

test("resolveDeliveryFormat: baked 가 항상 이긴다 (배경 이중 적용 금지)", () => {
  assert.equal(
    resolveDeliveryFormat({ backgroundBaked: true, delivery_format: "packed_alpha" }),
    "baked"
  );
  assert.equal(resolveDeliveryFormat({ background_baked: true }), "baked");
});

// ── 배선: 재생기 ────────────────────────────────────────────────────────────

test("IdleLoopVideo: deliveryFormat prop 이 휴리스틱보다 먼저 packed 를 확정한다", () => {
  const src = read(IDLE_LOOP);
  assert.match(src, /deliveryFormat\?:/, "prop 선언이 없다");
  assert.match(
    src,
    /const explicitPacked = deliveryFormat === "packed_alpha"/,
    "명시 판정이 없다"
  );
  // 마운트 시점: 명시 || 파일명 휴리스틱.
  assert.match(src, /explicitPacked \|\| isLikelyPackedAlphaSource\(src\)/);
  // detectMode: 명시면 크로마 측정 없이 packed 확정.
  assert.match(src, /if \(explicitPacked\) \{[\s\S]*?modeRef\.current = "packed"/);
  // 레거시 호환: 명시가 없을 때의 크로마 자동 감지는 그대로 남아 있다.
  assert.match(src, /const packed = isPackedAlphaVideo\(el, src\)/);
  assert.match(src, /modeRef\.current = packed \? "packed" : "blackkey"/);
});

test("PetIdleDisplay: deliveryFormat 을 전달하되 데모 폴백 소스에는 물려주지 않는다", () => {
  const src = read(PET_DISPLAY);
  assert.match(src, /deliveryFormat\?:/);
  assert.match(
    src,
    /deliveryFormat=\{display\.src === idleVideoUrl \? deliveryFormat : null\}/
  );
});

// ── 배선: 화면 ──────────────────────────────────────────────────────────────

test("devicePlay 화면: 판정 → prop, 그리고 하이드레이션 호출", () => {
  const src = read(DEVICE);
  assert.match(src, /resolveDeliveryFormat\(pipeline\)/, "판정을 부르지 않는다");
  assert.match(src, /deliveryFormat=\{breathingDeliveryFormat\}/, "prop 이 흐르지 않는다");
  // 데모 폴백 게이트 — bakedAsset 과 같은 이유로 hasIdle 이 필요하다.
  assert.match(src, /hasIdle && resolveDeliveryFormat\(pipeline\) === "packed_alpha"/);
  // 발행 포인터 하이드레이션(새 서명 URL + 명시 포맷)이 실제로 불린다.
  assert.match(src, /hydrateStoredPipeline\(\)/);
});

test("preview 화면: 같은 판정·같은 prop (두 화면이 갈라지면 한쪽만 회색이 된다)", () => {
  const src = read(PREVIEW);
  assert.match(src, /hasIdle && resolveDeliveryFormat\(pipeline\) === "packed_alpha"/);
  assert.match(src, /deliveryFormat=\{breathingDeliveryFormat\}/);
});

test("StoredPipeline: delivery_format 필드가 계약에 있다", () => {
  const src = read(AI_PROC);
  assert.match(src, /delivery_format\?: string \| null/);
});

// ── 이벤트별 렌더 모드 (Phase 7I.1) ─────────────────────────────────────────

test("IdleLoopVideo: 이벤트 레이어는 자기 포맷으로 그린다 — BREATH 모드 파생 금지", () => {
  const src = read(IDLE_LOOP);
  assert.match(src, /eventDeliveryFormats\?:/, "이벤트 포맷 prop 이 없다");
  assert.match(src, /const eventModeFor = useCallback/, "이벤트별 모드 판정이 없다");
  assert.match(src, /eventRenderMode\(/, "공용 판정 함수를 쓰지 않는다");
  // drawLayer 가 모드를 매개변수로 받고, 액션 레이어 호출이 eventModeFor 를 넘긴다.
  assert.match(src, /mode: RenderMode = modeRef\.current/);
  assert.match(src, /eventModeFor\(activeEvent\(\)\?\.id\)/);
  // frameH 판정이 레이어 자신의 모드를 본다 (BREATH 모드가 아니라).
  assert.match(src, /const frameH = mode === "packed"/);
});

test("PetIdleDisplay/화면: 이벤트 포맷이 발견에서 재생기까지 흐른다", () => {
  assert.match(read(PET_DISPLAY), /eventDeliveryFormats=\{eventDeliveryFormats\}/);
  for (const screen of [DEVICE, PREVIEW]) {
    const src = read(screen);
    assert.match(src, /formats: idleEventFormats/, `${screen}: 훅의 포맷을 받지 않는다`);
    assert.match(src, /eventDeliveryFormats=\{eventDeliveryFormats\}/, `${screen}: prop 이 흐르지 않는다`);
    // COME_CLOSER 포맷도 같은 발견 계약에서 온다.
    assert.match(src, /readyAssets\?\.COME_CLOSER\?\.deliveryFormat/, `${screen}: COME_CLOSER 포맷이 없다`);
  }
});

// ── 레거시 호환: 기존 두 세대의 재생 경로는 그대로다 ────────────────────────

test("레거시 배선 불변: baked 는 raw, 구세대 누끼는 blackkey 경로를 유지", () => {
  const idle = read(IDLE_LOOP);
  // transparentComposite=false → raw 는 그대로.
  assert.match(idle, /modeRef\.current = "raw"/);
  const device = read(DEVICE);
  // 두 분기(구움/레거시)와 배경 레이어 판정이 남아 있다.
  assert.match(device, /shouldRenderThemeBackdrop\(bakedAsset\)/);
  assert.match(device, /backgroundBaked=\{false\}/);
});
