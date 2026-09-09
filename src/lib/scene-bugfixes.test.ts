/**
 * Phase 19 에서 함께 고친 두 개의 독립 버그.
 *
 *   1. Beach / 커스텀 배경 테마 id 충돌
 *   2. Goya 데모 폴백이 **진짜 생성 자산을 가린다**
 *
 * 둘 다 "조용히 틀리는" 종류다 — 예외도 로그도 없이 다른 그림이 나온다.
 * 배경이 구워진 뒤로는 증상이 더 나쁘다: 데모 클립에는 승인된 배경도 없다.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  CUSTOM_PHOTO_BG_THEME_ID,
  CUSTOM_PHOTO_BG_THEME_KEY,
  getMemorialTheme,
  memorialThemes,
} from "../components/memorial/themes.ts";
import { ensureIdleMp4Url, isGoyaDemoIdleUrl } from "./device-host-flags.ts";

// ── 1. 테마 id 충돌 ──────────────────────────────────────────────────────────

test("테마 id 는 유일하다", () => {
  const ids = memorialThemes.map((t) => t.id);
  assert.equal(new Set(ids).size, ids.length, `id 충돌: ${ids.join(",")}`);
});

test("커스텀 배경 테마가 id 로 조회된다 — 예전에는 Beach 가 돌아왔다", () => {
  const t = getMemorialTheme(CUSTOM_PHOTO_BG_THEME_ID);
  assert.ok(t, "커스텀 배경 테마를 id 로 찾지 못한다");
  assert.equal(t.themeKey, CUSTOM_PHOTO_BG_THEME_KEY);
});

test("id 9 는 예전처럼 Beach 로 남는다 — 저장된 값이 옮겨가지 않는다", () => {
  // 이미 9 를 저장해 둔 사용자의 테마가 바뀌면 안 된다. 그래서 Beach 가 아니라
  // 커스텀 쪽 상수를 옮겼다.
  assert.equal(getMemorialTheme(9)?.themeKey, "beach");
});

test("테마 키도 유일하다", () => {
  const keys = memorialThemes.map((t) => t.themeKey);
  assert.equal(new Set(keys).size, keys.length, `key 충돌: ${keys.join(",")}`);
});

// ── 2. 데모 폴백이 진짜 자산을 가리지 않는다 ────────────────────────────────

test("확장자가 없어도 진짜 생성 자산이 이긴다 — 이것이 가리던 경로였다", () => {
  // 서명 URL·CDN 리라이트·확장자 없는 오브젝트 키는 흔하다. 예전에는 이런
  // 주소가 isVideo=false 로 떨어져 Goya 데모가 대신 나왔다.
  const signed =
    "https://xyz.supabase.co/storage/v1/object/sign/user-assets/u/c/idle_loop?token=abc";
  assert.equal(ensureIdleMp4Url(signed, { allowDemoFallback: true }), signed);

  const cdn = "https://cdn.example.com/v1/render/9f2c1a";
  assert.equal(ensureIdleMp4Url(cdn, { allowDemoFallback: true }), cdn);
});

test("확장자가 있는 평범한 주소도 그대로 통과", () => {
  const u = "https://s/idle_loop.mp4";
  assert.equal(ensureIdleMp4Url(u, { allowDemoFallback: true }), u);
});

test("자산이 아예 없을 때만 데모가 나온다", () => {
  const out = ensureIdleMp4Url("", { allowDemoFallback: true });
  assert.ok(out.length > 0, "폴백이 꺼져 버렸다");
  assert.equal(ensureIdleMp4Url("", { allowDemoFallback: false }), "");
});

test("Goya 목업 + 사용자 누끼일 때만 폴백으로 내려간다 (기존 동작 유지)", () => {
  const goya = "https://device.eternalbeam.com/demo/goya_idle_packed.mp4";
  assert.equal(isGoyaDemoIdleUrl(goya), true);
  const withCutout = ensureIdleMp4Url(goya, {
    allowDemoFallback: true,
    cutoutUrl: "blob:cutout",
  });
  assert.notEqual(withCutout, goya);
  // 누끼가 없으면 예전처럼 목업을 그대로 쓴다.
  assert.equal(ensureIdleMp4Url(goya, { allowDemoFallback: true }), goya);
});

test("상대 경로 자산도 진짜 자산으로 본다", () => {
  assert.equal(
    ensureIdleMp4Url("/assets/idle_loop.mp4", { allowDemoFallback: true }),
    "/assets/idle_loop.mp4"
  );
});

test("쓰레기 값은 자산으로 치지 않는다", () => {
  // 스킴 없는 문자열은 자산이 아니다 — 폴백이 맞다.
  const out = ensureIdleMp4Url("not a url", { allowDemoFallback: true });
  assert.notEqual(out, "not a url");
});
