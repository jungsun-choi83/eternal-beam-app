/**
 * 레거시 Device Play(직접 Pi 송출 화면) 의 프로덕션 흐름 퇴역.
 *
 * MemorialDevicePlayScreen 은 발행 뒤·멤버십 결제 복귀 뒤의 **정상 목적지**였다.
 * 그 화면은 마운트만으로 pi-sensor-bridge → LAN 탐색 → POST /demo/play 를
 * 태운다(?pi=RASPBERRY_IP 문구 포함). 지원되는 기기 경로는 하나뿐이다:
 *
 *   프론트 → /api/v1/device/commands → 백엔드 게이트웨이 → WebSocket → Pi/APK
 *
 * 여기서 고정하는 계약:
 *   1) PUBLISHED 생성은 Preview 에 머무른다
 *   2) 발행은 기기 명령을 하나도 자동으로 보내지 않는다
 *   3) 명시적 Play on Beam 은 theme_play → pet_asset (현대 게이트웨이만)
 *   4) 정상 멤버십 복귀는 devicePlay 로 가지 않는다
 *   5) 멤버십 자격은 복귀 화면 마운트 시 다시 조회된다
 *   6) 정상 내비게이션은 직접 Pi 송출 화면에 닿을 수 없다
 *   7) 명시적 데모(?demo=device)만 그 화면에 닿는다
 *   8) Library/Preview 흐름은 그대로다
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { playPublishedOnBeam } from "./play-on-beam.ts";
import { classifyDeviceCommandFailure, type DeviceCommandPayload } from "./device-command-api.ts";

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
const read = (p: string) => readFileSync(p, "utf8");

const APP_RAW = read("src/app/EternalBeamApp.tsx");
const APP = strip(APP_RAW);
const PREVIEW = strip(read("src/components/memorial/preview-screen.tsx"));
const MY_LIBRARY = strip(read("src/components/memorial/my-library-screen.tsx"));
const SETTINGS = strip(read("src/components/memorial/settings-screen.tsx"));
const PET_PROFILE = strip(read("src/lib/pet-profile.ts"));
const PLAY_ON_BEAM = strip(read("src/lib/play-on-beam.ts"));

const LEGACY_DEVICE_SYMBOLS = [
  "pi-sensor-bridge",
  "schedulePetReadyToDevice",
  "scheduleThemeBackgroundSync",
  "triggerThemeOnDevice",
  "broadcastFreeThemeToDevice",
  "preloadDeviceMotions",
  "/demo/play",
  "/demo/pet-ready",
  "RASPBERRY_IP",
];

// ── 1) PUBLISHED 생성은 Preview 에 머무른다 ─────────────────────────────────

test("finalizeOutcome: 기기 전달(device)은 onComplete 을 부르지 않는다 — 실물(shipping)만 다음 화면이 있다", () => {
  const start = PREVIEW.indexOf("const finalizeOutcome");
  const end = PREVIEW.indexOf("[pipeline, onComplete, deliveryMode]", start);
  assert.ok(start > 0 && end > start, "finalizeOutcome 을 찾을 수 없다");
  const body = PREVIEW.slice(start, end);
  assert.match(body, /if \(deliveryMode === "shipping"\) onComplete\(\);/);
  assert.equal((body.match(/onComplete\(\)/g) ?? []).length, 1, "onComplete 이 무조건 호출된다");
});

test("App: Preview 의 onComplete 은 실물(shipping)만 배송지로 — devicePlay 로 가지 않는다", () => {
  const start = APP.indexOf("<PreviewScreen");
  const end = APP.indexOf("/>", start);
  const block = APP.slice(start, end);
  assert.match(block, /navigateTo\('shippingAddress'\)/);
  assert.doesNotMatch(block, /navigateTo\('devicePlay'\)/, "발행 뒤 직접 Pi 송출 화면으로 넘어간다");
  assert.doesNotMatch(block, /canEnterDevicePlay/, "devicePlay 진입 가드가 아직 배선돼 있다");
});

test("Preview: 발행 뒤 Create 갈래도 Play on Web / Play on Beam 레일을 연다", () => {
  assert.match(
    PREVIEW,
    /const showPublishedRail = isLibraryFlow \|\| \(hasIdle && deliveryMode === "device"\);/,
  );
  assert.match(PREVIEW, /\{showPublishedRail \? \(/);
});

test("Preview: 발행 BREATHING 포인터를 마운트 시 하이드레이션한다 (GET 만 — 생성·송출 없음)", () => {
  assert.match(PREVIEW, /import \{ hydrateStoredPipeline \} from "@\/lib\/breathing-hydration";/);
  const start = PREVIEW.indexOf("void hydrateStoredPipeline()");
  assert.ok(start > 0, "하이드레이션 호출이 없다");
  const around = PREVIEW.slice(start - 200, start + 300);
  assert.match(around, /if \(isLibraryFlow \|\| !hasIdle\) return;/, "라이브러리/미발행에서도 하이드레이션한다");
});

// ── 2) 발행은 기기 명령을 자동으로 보내지 않는다 ────────────────────────────

test("finalizeOutcome 은 기기 명령을 하나도 보내지 않는다 — 레거시 심볼도 sendDeviceCommand 도 없다", () => {
  const start = PREVIEW.indexOf("const finalizeOutcome");
  const end = PREVIEW.indexOf("[pipeline, onComplete, deliveryMode]", start);
  const body = PREVIEW.slice(start, end);
  for (const sym of [...LEGACY_DEVICE_SYMBOLS, "sendDeviceCommand", "playPublishedOnBeam", "onPlayOnBeam"]) {
    assert.ok(!body.includes(sym), `발행 마무리가 ${sym} 를 부른다 — 동의 없는 기기 송출`);
  }
});

test("Preview 화면의 프로덕션(Phase 7) 경로에는 레거시 직접 Pi 심볼이 없다", () => {
  // 레거시 생성 경로(VITE_LEGACY_GENERATION=1)만 schedulePetReadyToDevice 를
  // 남겨 두고 있고, 그것은 phase7GenerationEnabled() 뒤에 있다. 여기서는 그
  // 호출이 레거시 분기 안에만 있음을 확인한다.
  const phase7 = PREVIEW.indexOf("if (phase7GenerationEnabled()) {");
  const legacyStart = PREVIEW.indexOf("try {", PREVIEW.indexOf("return;", phase7));
  const before = PREVIEW.slice(0, legacyStart);
  for (const sym of ["schedulePetReadyToDevice(", "triggerThemeOnDevice", "/demo/play", "RASPBERRY_IP"]) {
    assert.ok(
      !before.replace(/import \{ schedulePetReadyToDevice \}[^\n]*\n/, "").includes(sym),
      `${sym} 가 프로덕션 경로에 있다`,
    );
  }
});

// ── 3) 명시적 Play on Beam — theme_play → pet_asset, 현대 게이트웨이만 ──────

test("playPublishedOnBeam: theme_play 성공 뒤에만 pet_asset 이 나간다", async () => {
  const sent: DeviceCommandPayload[] = [];
  const result = await playPublishedOnBeam(
    { themeKey: "fresh_forest", petId: "pet_abc", motionId: "BREATHING" },
    {
      resolveDeviceId: () => "beam-001",
      send: async (payload) => {
        sent.push(payload);
        return { ok: true, delivery: "sent", command_id: `cmd-${sent.length}` };
      },
    },
  );
  assert.deepEqual(result, { ok: true, commandId: "cmd-2" });
  assert.equal(sent.length, 2);
  assert.equal(sent[0].event, "theme_play");
  assert.equal(sent[1].event, "pet_asset");
  assert.deepEqual(sent[0], { device_id: "beam-001", event: "theme_play", theme_id: "fresh_forest" });
  assert.deepEqual(sent[1], { device_id: "beam-001", event: "pet_asset", pet_id: "pet_abc", motion_id: "BREATHING" });
});

test("playPublishedOnBeam: theme_play 실패면 pet_asset 을 보내지 않고 진짜 실패를 돌려준다", async () => {
  const sent: DeviceCommandPayload[] = [];
  const result = await playPublishedOnBeam(
    { themeKey: "fresh_forest", petId: "pet_abc", motionId: "BREATHING" },
    {
      resolveDeviceId: () => "beam-001",
      send: async (payload) => {
        sent.push(payload);
        return { ok: false, reason: "DEVICE_UNKNOWN", status: 404 };
      },
    },
  );
  assert.deepEqual(result, { ok: false, reason: "DEVICE_UNKNOWN", status: 404 });
  assert.equal(sent.length, 1);
  assert.equal(classifyDeviceCommandFailure(result as { reason: string; status?: number }), "device_unavailable");
});

test("playPublishedOnBeam: 페어링된 기기가 없으면 명령을 하나도 보내지 않는다 (beam-001 폴백 없음)", async () => {
  let calls = 0;
  const result = await playPublishedOnBeam(
    { themeKey: "fresh_forest", petId: "pet_abc", motionId: "BREATHING" },
    {
      resolveDeviceId: () => null,
      send: async () => {
        calls += 1;
        return { ok: true, delivery: "sent" };
      },
    },
  );
  assert.deepEqual(result, { ok: false, reason: "no_paired_device" });
  assert.equal(calls, 0);
  assert.equal(classifyDeviceCommandFailure({ reason: "no_paired_device" }), "device_unavailable");
});

test("playPublishedOnBeam: 발행된 펫이 없으면 보내지 않고 '준비 안 됨(rejected)' 으로 분류된다", async () => {
  let calls = 0;
  const result = await playPublishedOnBeam(
    { themeKey: "fresh_forest", petId: null, motionId: "BREATHING" },
    { resolveDeviceId: () => "beam-001", send: async () => { calls += 1; return { ok: true, delivery: "sent" }; } },
  );
  assert.deepEqual(result, { ok: false, reason: "no_published_pet" });
  assert.equal(calls, 0);
  assert.equal(classifyDeviceCommandFailure({ reason: "no_published_pet" }), "rejected");
});

test("play-on-beam 은 device-command-api 만 쓴다 — 레거시 직접 Pi 심볼이 없다", () => {
  assert.match(PLAY_ON_BEAM, /from "\.\/device-command-api\.ts"/);
  for (const sym of LEGACY_DEVICE_SYMBOLS) {
    assert.ok(!PLAY_ON_BEAM.includes(sym), `${sym} 가 play-on-beam 에 있다`);
  }
  assert.ok(!PLAY_ON_BEAM.includes("fetch("), "게이트웨이를 우회하는 fetch 가 있다");
});

test("App: Create 갈래의 Play on Beam 은 playPublishedOnBeam(BREATHING) 으로 나간다", () => {
  assert.match(APP, /import \{ playPublishedOnBeam \} from '@\/lib\/play-on-beam'/);
  const start = APP.indexOf("<PreviewScreen");
  const end = APP.indexOf("/>", start);
  const block = APP.slice(start, end);
  assert.match(block, /onPlayOnBeam=\{\(\) => \{/);
  assert.match(block, /playPublishedOnBeam\(\{/);
  assert.match(block, /motionId: 'BREATHING'/);
  assert.match(block, /themeKey: theme\.themeKey/);
});

// ── 4) 정상 멤버십 복귀는 devicePlay 로 가지 않는다 ──────────────────────────

test("handleBillingReturn: 복원 화면 또는 설정(멤버십 강조) — devicePlay 문자열이 없다", () => {
  const i = APP.indexOf("const handleBillingReturn");
  const body = APP.slice(i, i + 900);
  assert.match(body, /navigateTo\(restored\.screen\)/);
  assert.match(body, /openSettings\('home', \{ focusMembership: true \}\)/);
  assert.doesNotMatch(body, /devicePlay/);
});

test("멤버십 진입(onOpenMembership)은 발행된 Preview 에서 설정으로 — 돌아갈 곳은 preview 다", () => {
  assert.match(APP, /onOpenMembership=\{\(\) => openSettings\('preview', \{ focusMembership: true \}\)\}/);
  assert.match(PREVIEW, /onOpenMembership\?: \(\) => void;/);
  assert.match(PREVIEW, /onClick=\{onOpenMembership\}/);
});

// ── 5) 멤버십 자격 갱신 ───────────────────────────────────────────────────────

test("복귀 화면들은 마운트 시 자격을 다시 조회한다 — Provider(preview/library)·MembershipSection(settings)", () => {
  const provider = strip(read("src/components/memorial/premium-assets-context.tsx"));
  assert.match(provider, /useEffect\(\(\) => \{\s*cancelledRef\.current = false;\s*void refresh\(\);/);
  const section = strip(read("src/components/memorial/membership-section.tsx"));
  assert.match(section, /void refresh\(\)/);
  assert.match(SETTINGS, /<MembershipSection language=\{currentLanguage\} focusOnMount=\{focusMembership\} \/>/);
  // Preview 는 Provider 로 감싸여 있다 — 마운트마다 GET.
  assert.match(PREVIEW, /<PremiumAssetsProvider petId=\{petId\} enabled=\{petId != null\}>/);
  assert.match(MY_LIBRARY, /<PremiumAssetsProvider petId=\{props\.pet\.petId\} enabled>/);
});

// ── 6) 정상 내비게이션은 직접 Pi 송출 화면에 닿을 수 없다 ────────────────────

test("App: navigateTo('devicePlay') 는 전부 deviceDemo 가드 안에만 있다", () => {
  const needle = "navigateTo('devicePlay')";
  let idx = APP.indexOf(needle);
  assert.ok(idx > 0, "데모 경로 자체가 사라졐다면 이 가드도 갱신해야 한다");
  let count = 0;
  while (idx !== -1) {
    count += 1;
    const before = APP.slice(Math.max(0, idx - 220), idx);
    assert.match(before, /deviceDemo/, `가드 없는 devicePlay 진입:\n${before}`);
    idx = APP.indexOf(needle, idx + 1);
  }
  assert.equal(count, 1, "devicePlay 진입점이 늘었다 — 정상 흐름에 새는지 확인하라");
});

test("App: devicePlay 화면은 deviceDemo 일 때만 렌더된다", () => {
  assert.match(APP, /\{screen === 'devicePlay' && deviceDemo && \(/);
});

test("App: 직접 Pi LAN 탐색(schedulePiDiscovery)은 deviceDemo 뒤에만 있다", () => {
  const needle = "schedulePiDiscovery(";
  let idx = APP.indexOf(needle);
  assert.ok(idx > 0);
  while (idx !== -1) {
    const before = APP.slice(Math.max(0, idx - 260), idx);
    assert.match(before, /deviceDemo/, `가드 없는 LAN 탐색:\n${before}`);
    idx = APP.indexOf(needle, idx + 1);
  }
});

test("pet-profile: Pi 이름 동기화는 명시적 데모에서만 — 가입 화면이 LAN 을 두드리지 않는다", () => {
  const i = PET_PROFILE.indexOf("export async function syncPetProfileToDevice");
  const body = PET_PROFILE.slice(i, i + 300);
  assert.match(body, /if \(!isDeviceKickstarterDemo\(\)\) return false;/);
});

test("App: physicalOrder 의 뒤로는 home 이다 — devicePlay 가 아니다", () => {
  const start = APP.indexOf("<PhysicalOrderScreen");
  const end = APP.indexOf("/>", start);
  assert.match(APP.slice(start, end), /onBack=\{\(\) => navigateTo\('home', 'back'\)\}/);
});

test("App: MemorialDevicePlayScreen 은 여전히 import 되고 기존 props 로 그려진다 (데모 보존, 광범위 삭제 없음)", () => {
  assert.match(APP_RAW, /import \{ MemorialDevicePlayScreen \} from '@\/components\/memorial\/memorial-device-play-screen'/);
  assert.match(APP, /<MemorialDevicePlayScreen/);
});

// ── 8) Library/Preview 흐름은 그대로다 ───────────────────────────────────────

test("Preview: handleConfirm 은 그대로 hasIdle 이면 onComplete 을 부른다 (실물 CTA 경로)", () => {
  assert.match(PREVIEW, /if \(hasIdle\) \{\s*onComplete\(\);\s*return;\s*\}/);
});

test("My Library: PreviewScreen 배선이 그대로다 — onPlayOnBeam 은 라이브러리가 소유한다", () => {
  assert.match(MY_LIBRARY, /onPlayOnBeam=\{async \(\) => \{/);
  assert.match(MY_LIBRARY, /event: "theme_play"/);
  assert.match(MY_LIBRARY, /event: "pet_asset"/);
  assert.match(MY_LIBRARY, /onOpenMembership/, "잠긴 제안에서 멤버십으로 갈 수 없다");
});

test("Behavior Library 는 My Library 의 Available to Create 로 이동했고 devicePlay 와 분리됐다", () => {
  assert.match(MY_LIBRARY, /<BehaviorLibrary/);
  assert.doesNotMatch(PREVIEW, /<BehaviorLibrary/);
  assert.doesNotMatch(SETTINGS, /<BehaviorLibrary/);
  const play = strip(read("src/components/memorial/memorial-device-play-screen.tsx"));
  assert.doesNotMatch(play, /<BehaviorLibrary/, "레거시 기기 화면이 아직 생성 UI 를 소유한다");
  assert.doesNotMatch(play, /<MembershipCard/, "레거시 기기 화면이 아직 멤버십 진입을 소유한다");
});
