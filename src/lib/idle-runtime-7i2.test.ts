/**
 * Phase 7I.2 — 웹 아이들 런타임: 발견(7I.1) → 적격성 → 스케줄러 → 재생 → 복귀.
 *
 * 새 정책을 만들지 않는다 — 기존 스케줄러(타이밍·가중치·쿨다운·큐잉 금지·연속
 * 반복 회피)와 기존 적격성(READY ∩ 선호 ∩ [구독 게이트 시] entitled)이 새 READY
 * 자산 위에서 그대로 성립하는지를 **체인으로** 고정한다.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { eligibleBehaviorIds, eligibleSources } from "./behavior-library.ts";
import { eventRenderMode } from "./baked-playback.ts";
import {
  IDLE_COOLDOWN_MAX_MS,
  IDLE_COOLDOWN_MIN_MS,
  IDLE_QUIET_MIN_MS,
  eligibleIdleEvents,
  nextCooldownDelayMs,
  selectIdleEvent,
} from "./idle-event-scheduler.ts";
import {
  IDLE_EVENT_IDS,
  RUNTIME_EVENTS,
  registeredIdleEvents,
  type IdleEvent,
  type RuntimeEventId,
} from "./pet-runtime-events.ts";
import type { PremiumAssets } from "./premium-assets.ts";

const IDLES: readonly IdleEvent[] = IDLE_EVENT_IDS;
const url = (id: string, packed = false) =>
  `https://storage.test/u/p/${id.toLowerCase()}${packed ? "_packed" : ""}.mp4?token=fresh`;

/** 7I.1 발견 응답 모양 — 크레딧 모드 기본 (현 로컬/개발 구성). */
function discovered(over: Partial<PremiumAssets> = {}): PremiumAssets {
  const ready = Object.fromEntries(IDLES.map((i) => [i, url(i, true)]));
  return {
    petId: "pet1",
    ready,
    readyAssets: Object.fromEntries(
      IDLES.map((i) => [i, { url: url(i, true), deliveryFormat: "packed_alpha" }])
    ),
    generating: [],
    missing: [],
    idleEvents: [...IDLES],
    actionEvents: ["COME_CLOSER"],
    prices: {},
    entitled: false,
    preferences: {},
    subscriptionStatus: null,
    subscriptionRequired: false,
    ...over,
  };
}

// ── 1. 네 아이들 모두 스케줄러 후보가 된다 ──────────────────────────────────

test("READY 4종 전부가 스케줄러 후보로 들어간다", () => {
  const assets = discovered();
  const eligible = eligibleBehaviorIds(IDLES, assets);
  assert.deepEqual([...eligible].sort(), [...IDLES].sort());
  const candidates = eligibleIdleEvents(eligible);
  assert.equal(candidates.length, 4);
  for (const def of candidates) {
    assert.equal(def.kind, "IDLE_EVENT");
    assert.equal(def.returnToIdle, true, `${def.id} 가 BREATHING 으로 돌아가지 않는다`);
    assert.equal(def.returnPolicy, "seam-aligned", `${def.id} 복귀 정책이 다르다`);
    assert.equal(def.themeIndependent, true, `${def.id} 가 테마 의존이다`);
  }
});

// ── 2. 미소유/미생성은 후보에서 빠진다 ──────────────────────────────────────

test("미소유 모션은 조용히 빠지고 나머지는 그대로 돈다", () => {
  const partial = discovered({
    ready: { BLINKING: url("BLINKING", true), TAIL_WAGGING: url("TAIL_WAGGING") },
    readyAssets: {
      BLINKING: { url: url("BLINKING", true), deliveryFormat: "packed_alpha" },
      TAIL_WAGGING: { url: url("TAIL_WAGGING"), deliveryFormat: null },
    },
    generating: ["EAR_TWITCHING"],
  });
  const eligible = eligibleBehaviorIds(IDLES, partial);
  assert.deepEqual([...eligible].sort(), ["BLINKING", "TAIL_WAGGING"]);
  assert.equal(eligibleIdleEvents(eligible).length, 2);
});

// ── 3. 선호 OFF 존중 — 후보와 소스가 함께 빈다 ──────────────────────────────

test("OFF 는 후보에서도 소스에서도 빠진다 — 수동 트리거로도 재생 불가", () => {
  const assets = discovered({ preferences: { HEAD_TILTING: false } });
  const eligible = eligibleBehaviorIds(IDLES, assets);
  assert.ok(!eligible.includes("HEAD_TILTING"));
  const sources = eligibleSources(
    Object.fromEntries(IDLES.map((i) => [i, url(i, true)])),
    assets
  );
  assert.equal(sources.HEAD_TILTING, undefined, "OFF 인데 소스가 남아 있다");
  assert.equal(Object.keys(sources).length, 3);
});

// ── 4. 연속 반복 회피 / 쿨다운은 예전 그대로다 ──────────────────────────────

test("연속 반복 회피: 대안이 있으면 직전 이벤트를 다시 고르지 않는다", () => {
  const candidates = eligibleIdleEvents(IDLES);
  for (let roll = 0; roll < 1; roll += 0.07) {
    const picked = selectIdleEvent(candidates, "BLINKING", () => roll);
    assert.notEqual(picked, "BLINKING", `roll=${roll} 에서 즉시 반복됐다`);
  }
  // 후보가 하나뿐이면 그대로 재사용한다 — 아니면 아무것도 재생되지 않는다.
  const only = eligibleIdleEvents(["TAIL_WAGGING"]);
  assert.equal(selectIdleEvent(only, "TAIL_WAGGING", () => 0.5), "TAIL_WAGGING");
});

test("가중치: BLINKING(3) 이 다른 이벤트보다 자주 나온다", () => {
  const candidates = eligibleIdleEvents(IDLES);
  const counts: Record<string, number> = {};
  for (let i = 0; i < 600; i++) {
    const picked = selectIdleEvent(candidates, null, () => (i + 0.5) / 600);
    counts[picked as string] = (counts[picked as string] ?? 0) + 1;
  }
  // 총 가중치 6 중 BLINKING=3 → 결정적 균등 roll 에서 정확히 절반.
  assert.equal(counts.BLINKING, 300);
});

test("쿨다운 범위는 바뀌지 않았다 — 재생 직후 즉시 재발화 없음", () => {
  assert.equal(IDLE_COOLDOWN_MIN_MS, 3_500);
  assert.equal(IDLE_COOLDOWN_MAX_MS, 9_000);
  assert.ok(IDLE_QUIET_MIN_MS >= 5_000);
  assert.equal(nextCooldownDelayMs(() => 0), IDLE_COOLDOWN_MIN_MS);
  assert.equal(nextCooldownDelayMs(() => 1), IDLE_COOLDOWN_MAX_MS);
});

// ── 5. COME_CLOSER 는 자동 회전에 절대 들어가지 않는다 ──────────────────────

test("COME_CLOSER 는 availableIds 에 섞여 들어와도 스케줄러 후보가 아니다", () => {
  const mixed: RuntimeEventId[] = [...IDLES, "COME_CLOSER"];
  const candidates = eligibleIdleEvents(mixed);
  assert.ok(candidates.every((d) => d.id !== "COME_CLOSER"));
  assert.ok(registeredIdleEvents().every((d) => d.kind === "IDLE_EVENT"));
});

// ── 6. 혼합 전달 포맷 — 포맷은 자격에 영향이 없고 렌더 모드만 정한다 ────────

test("혼합 포맷: 자격은 동일, 렌더 모드는 이벤트마다 자기 포맷을 따른다", () => {
  const formats: Record<string, string | null> = {
    BLINKING: "packed_alpha",
    EAR_TWITCHING: null, // 레거시
    HEAD_TILTING: "blackkey",
    TAIL_WAGGING: "baked",
  };
  const assets = discovered({
    readyAssets: Object.fromEntries(
      IDLES.map((i) => [i, { url: url(i), deliveryFormat: formats[i] }])
    ),
    ready: Object.fromEntries(IDLES.map((i) => [i, url(i)])),
  });
  // 자격: 포맷과 무관하게 4종 전부.
  assert.equal(eligibleBehaviorIds(IDLES, assets).length, 4);
  // 렌더 모드: 각자 자기 포맷 — BREATH 모드에서 파생되지 않는다.
  assert.equal(eventRenderMode(formats.BLINKING, url("BLINKING")), "packed");
  assert.equal(eventRenderMode(formats.EAR_TWITCHING, url("EAR_TWITCHING")), "blackkey");
  assert.equal(eventRenderMode(formats.HEAD_TILTING, url("HEAD_TILTING")), "blackkey");
  assert.equal(eventRenderMode(formats.TAIL_WAGGING, url("TAIL_WAGGING")), "raw");
});

// ── 7. 복귀·정리 배선 — 이벤트가 끝나면 BREATHING 으로, 상태는 깨끗하게 ──────

const IDLE_LOOP = "src/components/memorial/idle-loop-video.tsx";

test("복귀 경로가 상태를 리셋한다 — 이벤트 <video> 정지·되감기 + IDLE 복귀", () => {
  const src = readFileSync(IDLE_LOOP, "utf8");
  // finishReturn / startIdle 둘 다 IDLE 로 되돌리고 액션 소스를 멈춘다.
  const resets = [...src.matchAll(/playbackRef\.current = \{ phase: "IDLE" \}/g)];
  assert.ok(resets.length >= 2, "IDLE 복귀 리셋이 부족하다");
  assert.match(src, /action\.pause\(\);\s*\n\s*action\.currentTime = 0;/);
  // BREATH 는 결정적 착지 — 휴지 자세(t=0)에서 재개한다.
  assert.match(src, /idle\.currentTime = 0;/);
});

test("스케줄러는 재생 종료 신호로만 재무장한다 — 큐잉 없음", () => {
  const src = readFileSync("src/components/memorial/use-idle-event-scheduler.ts", "utf8");
  assert.match(src, /playing=true 면 무조건 타이머를 버린다/);
  assert.match(src, /미뤄 둔 이벤트를 재생하지 않는다/);
  // 행동 게이트에 DEV 의존이 없다 — DEV 는 로그에만 쓰인다 (한 줄/블록 두 형태).
  const devUses = [...src.matchAll(/import\.meta\.env\.DEV/g)];
  const devLogs = [...src.matchAll(/if \(import\.meta\.env\.DEV\) \{?\s*console\./g)];
  assert.equal(devUses.length, devLogs.length, "DEV 게이트가 로그 밖 동작에 쓰였다");
});

// ── 8. 테마 불변 — 런타임 어디에도 테마가 들어가지 않는다 ───────────────────

test("적격성·스케줄러·레지스트리에 테마 개념이 없다", () => {
  for (const p of [
    "src/lib/behavior-library.ts",
    "src/lib/idle-event-scheduler.ts",
    "src/components/memorial/use-idle-event-scheduler.ts",
    "src/components/memorial/use-idle-event-assets.ts",
  ]) {
    const src = readFileSync(p, "utf8");
    assert.doesNotMatch(src, /themeKey|theme_id|selectedTheme|backgroundImage/, `${p} 에 테마가 스며들었다`);
  }
  // 이벤트 정의는 전부 테마 독립 선언이다.
  for (const id of IDLES) {
    assert.equal(RUNTIME_EVENTS[id]?.themeIndependent, true);
  }
});
