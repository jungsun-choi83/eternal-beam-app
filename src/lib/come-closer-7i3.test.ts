/**
 * Phase 7I.3 — COME_CLOSER 웹 상호작용: 인증 READY 계약 → 더블탭 → 재생 → 복귀.
 *
 * 트리거/우선순위/선점/복귀 정책 자체는 pet-runtime-events.test.ts (4a–4f,
 * 1A-5/6)가 이미 고정한다. 여기서는 **새 자산 계약과의 접점**을 고정한다:
 * 발견원, 자격→소스 게이트, 포맷 독립, dev-autogen 의존 제거, 테마 불변.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { eventRenderMode } from "./baked-playback.ts";
import { isBehaviorEligible, eligibleSources } from "./behavior-library.ts";
import {
  RUNTIME_EVENTS,
  decideTrigger,
  registeredIdleEvents,
} from "./pet-runtime-events.ts";
import type { PremiumAssets } from "./premium-assets.ts";

const PREVIEW = "src/components/memorial/preview-screen.tsx";
const MEMORIAL = "src/components/memorial/memorial-device-play-screen.tsx";
const read = (p: string) => readFileSync(p, "utf8");
/** 주석 제거 — "예전에는 X 를 썼다" 류 역사 설명이 의존성 검사에 걸리지 않게. */
const strip = (code: string) =>
  code.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

const CC_URL = "https://storage.test/u/p/come_closer_packed.mp4?token=fresh";

function assets(over: Partial<PremiumAssets> = {}): PremiumAssets {
  return {
    petId: "pet1",
    ready: { COME_CLOSER: CC_URL },
    readyAssets: { COME_CLOSER: { url: CC_URL, deliveryFormat: "packed_alpha" } },
    generating: [],
    missing: [],
    idleEvents: ["BLINKING", "EAR_TWITCHING", "HEAD_TILTING", "TAIL_WAGGING"],
    actionEvents: ["COME_CLOSER"],
    prices: {},
    entitled: false,
    preferences: {},
    subscriptionStatus: null,
    subscriptionRequired: false, // 크레딧 모드 — 현 로컬/개발 구성
    ...over,
  };
}

// ── 1. READY 자산 → 자격 → 소스 → 트리거 수락 (체인) ────────────────────────

test("READY COME_CLOSER: 자격 통과 → 소스 유지 → IDLE 트리거 수락", () => {
  const a = assets();
  assert.equal(isBehaviorEligible("COME_CLOSER", a), true);
  const sources = eligibleSources({ COME_CLOSER: a.readyAssets.COME_CLOSER.url }, a);
  assert.equal(sources.COME_CLOSER, CC_URL);
  const decision = decideTrigger({
    requestedEventId: "COME_CLOSER",
    phase: "IDLE",
    currentEventId: null,
    hasSource: true,
  });
  assert.equal(decision.accepted, true);
});

// ── 2. 미소유/미생성 → 재생 불가 (소스가 비어 no-source 거절) ────────────────

test("미소유 COME_CLOSER 는 재생되지 않는다 — 자격도 소스도 트리거도 막힌다", () => {
  for (const broken of [
    assets({ ready: {}, readyAssets: {} }), // MISSING
    assets({ ready: {}, readyAssets: {}, generating: ["COME_CLOSER"] }), // GENERATING
    assets({ preferences: { COME_CLOSER: false } }), // OFF
    assets({ subscriptionRequired: true, entitled: false }), // 구독 모드 만료
  ]) {
    assert.equal(isBehaviorEligible("COME_CLOSER", broken), false);
    const sources = eligibleSources({ COME_CLOSER: CC_URL }, broken);
    assert.equal(sources.COME_CLOSER, undefined);
  }
  const rejected = decideTrigger({
    requestedEventId: "COME_CLOSER",
    phase: "IDLE",
    currentEventId: null,
    hasSource: false,
  });
  assert.equal(rejected.accepted, false);
  assert.equal((rejected as { reason?: string }).reason, "no-source");
});

// ── 3. 혼합 포맷 — 자격과 무관, 렌더 모드만 정한다 (BREATH 파생 금지) ────────

test("COME_CLOSER 포맷: packed 신자산 / 레거시 null / 명시 blackkey 전부 재생 모드가 있다", () => {
  assert.equal(eventRenderMode("packed_alpha", CC_URL), "packed");
  assert.equal(eventRenderMode(null, "https://s/library/COME_CLOSER_abc.mp4"), "blackkey");
  assert.equal(eventRenderMode("blackkey", CC_URL), "blackkey");
  // 자격은 포맷을 아예 보지 않는다.
  const legacy = assets({
    readyAssets: { COME_CLOSER: { url: CC_URL, deliveryFormat: null } },
  });
  assert.equal(isBehaviorEligible("COME_CLOSER", legacy), true);
});

// ── 4. 상호작용은 COME_CLOSER 만 쏜다 + 자동 회전 불포함 ─────────────────────

test("더블탭 경로는 COME_CLOSER 리터럴만 트리거한다 (두 화면 동일)", () => {
  for (const [name, path] of [["preview", PREVIEW], ["memorial", MEMORIAL]] as const) {
    const src = read(path);
    const fires = [...src.matchAll(/(?:fire\?\.|comeCloserTriggerRef\.current\?\.)\("([A-Z_]+)"\)/g)];
    assert.ok(fires.length > 0, `${name}: 더블탭 트리거가 없다`);
    for (const [, id] of fires) {
      assert.equal(id, "COME_CLOSER", `${name}: 더블탭이 ${id} 를 쏜다`);
    }
    // 자격 게이트를 거친다 — 만료/OFF/미소유면 쏘지 않는다.
    assert.match(src, /comeCloserAllowedRef\.current/, `${name}: 자격 게이트가 없다`);
  }
  // 자동 아이들 회전에는 절대 없다.
  assert.ok(registeredIdleEvents().every((d) => d.id !== "COME_CLOSER"));
});

// ── 5. dev-autogen / 레거시 조회 의존 제거 (활성 웹 경로) ────────────────────

test("활성 화면 어디에도 dev-autogen/레거시 조회 의존이 없다", () => {
  for (const [name, path] of [["preview", PREVIEW], ["memorial", MEMORIAL]] as const) {
    const src = strip(read(path));
    assert.ok(!src.includes("come-closer-autogen"), `${name} 이 dev-autogen 을 import 한다`);
    assert.ok(!src.includes("lookupComeCloserAsset"), `${name} 에 dev 조회가 있다`);
    assert.ok(!src.includes("pollComeCloserUntilReady"), `${name} 에 dev 폴링이 있다`);
    assert.ok(!src.includes("ensureComeCloser"), `${name} 이 상호작용에서 생성한다`);
    assert.ok(!src.includes("/dev/come-closer"), `${name} 이 dev 엔드포인트를 부른다`);
    // 발견원은 인증 READY 계약 하나다.
    assert.match(src, /readyAssets\?\.COME_CLOSER/, `${name} 발견원이 다르다`);
  }
});

// ── 6. 복귀·테마 — 정의 계약이 그대로다 ─────────────────────────────────────

test("COME_CLOSER 정의 불변 — 최우선·중단 불가·복귀·테마 독립·preload auto", () => {
  const def = RUNTIME_EVENTS.COME_CLOSER!;
  assert.equal(def.priority, 100);
  assert.equal(def.interruptible, false);
  assert.equal(def.returnToIdle, true);
  assert.equal(def.returnPolicy, "hold-and-dissolve");
  assert.equal(def.entryPolicy, "immediate");
  assert.equal(def.themeIndependent, true);
  assert.equal(def.preload, "auto");
});

test("발견 effect 의존성에 테마가 없다 — 테마 변경이 조회/캐시를 흔들지 않는다", () => {
  const memorial = read(MEMORIAL);
  assert.match(memorial, /\}, \[pipeline, premiumAssets\]\);/, "memorial 발견 effect 의존성이 다르다");
  const preview = read(PREVIEW);
  assert.match(
    preview,
    /\}, \[pipeline, hasIdle, premiumAssetsForDiscovery\]\);/,
    "preview 발견 effect 의존성이 다르다"
  );
});
