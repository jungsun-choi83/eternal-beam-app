/**
 * 운영 QR 미리보기 → **Shaker 화면**. 절대 고객 앱 업로드로 가지 않는다.
 *
 * ── 고치는 결함 ─────────────────────────────────────────────────────────────
 * 운영이 만든 미리보기 링크를 열었을 때 고객 앱이 부팅되면, 그 앱은 이미 만들어진
 * 펫을 모른 채 업로드 → 누끼 → BREATHING 생성 흐름을 다시 시작한다. 즉 **이미 있는
 * 강아지를 다시 만들라고 한다.** canonical petId 가 갈라지는 입구다.
 *
 * 서버 쪽 원인(레지스트리 미조회 · API 오리진 폴백)은 backend/tests/
 * test_ops_shaker_preview_no_regeneration.py 가 고정한다. 여기서는 **링크가
 * 올바르게 왔을 때 프론트가 반드시 Shaker 로 간다**는 나머지 절반을 고정한다.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { isShakerPath, readShakerParams, resolveShakerEntry } from "./shaker-entry.ts";
import { isOpsShakerPath } from "./shaker-ops-entry.ts";

const strip = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");

const APP = readFileSync("src/app/App.tsx", "utf8");
const SHAKER_SCREEN = readFileSync("src/components/memorial/shaker-screen.tsx", "utf8");

/** 서버(shaker_ops_v1._share_url)가 만드는 것과 같은 모양의 링크. */
const PET = "pet_5da0d31f-33d8-4735-8e60-0c2a532ed358";
const TOKEN = "l1-8tm21QXauDqmoulVE2OvC3JPH0_tqttQaCRO45bU";
const SHARE_URL = `https://app.eternalbeam.test/shaker?petId=${PET}&share=${TOKEN}`;

// ── 링크 → 화면 ──────────────────────────────────────────────────────────────

test("운영이 만든 미리보기 링크는 Shaker 경로다", () => {
  const url = new URL(SHARE_URL);
  assert.equal(isShakerPath(url.pathname), true);
  // 고객 앱도 운영 콘솔도 아니다 — 그쪽으로 갈라지면 생성 흐름이 열린다.
  assert.equal(isOpsShakerPath(url.pathname), false);
  assert.notEqual(url.pathname, "/");
});

test("링크의 펫·토큰이 그대로 해석된다 — 새 펫을 만들 이유가 없다", () => {
  const url = new URL(SHARE_URL);
  const entry = resolveShakerEntry(readShakerParams(url.search));
  assert.equal(entry.kind, "ready");
  assert.equal(entry.kind === "ready" && entry.petId, PET);
  assert.equal(entry.kind === "ready" && entry.token, TOKEN);
});

test("토큰이 없거나 깨져도 업로드로 떨어지지 않는다", () => {
  // Shaker 화면이 자기 자리에서 이유를 보여 준다. 고객 앱으로 넘기면 QR 을 스캔한
  // 사람이 남의 펫 업로드 화면을 보게 된다.
  assert.equal(resolveShakerEntry(readShakerParams("")).kind, "missing-token");
  assert.equal(resolveShakerEntry(readShakerParams("?share=abc")).kind, "malformed-token");
  assert.equal(isShakerPath("/shaker"), true);
});

// ── 라우팅 배선 ──────────────────────────────────────────────────────────────

test("App 이 고객 앱보다 Shaker 를 먼저 분기한다", () => {
  const code = strip(APP);
  const shakerAt = code.indexOf("isShakerEntry()");
  const appAt = code.indexOf("<EternalBeamApp");
  assert.ok(shakerAt > -1, "Shaker 분기가 없다");
  assert.ok(appAt > -1, "고객 앱 분기가 없다");
  assert.ok(
    shakerAt < appAt,
    "고객 앱이 먼저 걸리면 QR 방문자가 업로드 화면으로 부팅된다"
  );
  assert.match(code, /<ShakerScreen\s*\/>/);
});

// ── Shaker 화면은 생성하지 않는다 ────────────────────────────────────────────

/** Shaker 화면이 닿아서는 안 되는 것들 — 전부 생성·업로드로 가는 경로다. */
const FORBIDDEN = [
  "generate-pet-video",
  "requestIdleGeneration",
  "purchasePremium",
  "pending-generation",
  "idle-generation-request",
  "come-closer-autogen",
  "registerPet",
  "UploadScreen",
];

test("Shaker 화면에 생성·업로드 경로가 없다", () => {
  const code = strip(SHAKER_SCREEN);
  for (const bad of FORBIDDEN) {
    assert.ok(!code.includes(bad), `shaker-screen 이 ${bad} 를 참조한다`);
  }
});

test("Shaker 화면은 조회 하나만 한다 — 이미 있는 자산을 읽을 뿐이다", () => {
  const code = strip(SHAKER_SCREEN);
  assert.match(code, /fetchShakerPet\(/);
  // 쓰기는 없다. 있으면 공개 화면이 서버 상태를 바꾼다는 뜻이다.
  assert.ok(!/method:\s*["']POST["']/.test(code));
});
