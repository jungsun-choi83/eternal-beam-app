/**
 * Phase 7H 구매 계약 — 프론트가 pet_image_url 을 **더 이상 보내지 않는다**.
 *
 * 소스 텍스트를 본다 (phase7-cutover-wiring 과 같은 철학): 서버가 필드를 무시해도
 * 프론트가 브라우저 data: URL(수 MB base64)을 계속 실어 보내면 계약 정리가 아니다.
 * 구매 본문은 kind + pet_id 뿐이어야 한다.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (p: string) => readFileSync(p, "utf8");

const API = "src/lib/premium-assets.ts";
const LIBRARY_HOOK = "src/components/memorial/use-behavior-library.ts";
const UNLOCK_HOOK = "src/components/memorial/use-premium-unlock.ts";
const LIBRARY_UI = "src/components/memorial/behavior-library.tsx";
const PLAY_SCREEN = "src/components/memorial/memorial-device-play-screen.tsx";

test("purchasePremium 본문은 kind + pet_id 뿐이다 — pet_image_url 없음", () => {
  const src = read(API);
  const fn = src.slice(src.indexOf("export async function purchasePremium"));
  const body = fn.slice(fn.indexOf("JSON.stringify"), fn.indexOf("signal:"));
  assert.match(body, /kind: params\.kind/);
  assert.match(body, /pet_id: params\.petId/);
  assert.doesNotMatch(body, /pet_image_url/, "구매 본문에 이미지가 실린다");
  assert.doesNotMatch(fn, /petImageUrl/, "구매 API 시그니처에 이미지가 남아 있다");
});

test("구매 경로 어디에도 petImageUrl 이 흐르지 않는다", () => {
  for (const p of [LIBRARY_HOOK, UNLOCK_HOOK, LIBRARY_UI]) {
    assert.doesNotMatch(read(p), /petImageUrl/, `${p} 가 여전히 이미지를 넘긴다`);
  }
  // 화면이 BehaviorLibrary 에 누끼 URL 을 내려 주지 않는다.
  const screen = read(PLAY_SCREEN);
  const mount = screen.slice(screen.indexOf("<BehaviorLibrary"));
  const tagEnd = mount.indexOf("/>");
  assert.doesNotMatch(mount.slice(0, tagEnd), /petImageUrl/);
});

test("data: URL 을 원격 URL 로 변환해 검증을 우회하는 코드가 없다", () => {
  // 구매 API 파일이 업로드/변환 유틸을 끌어오지 않는다 — 새 계약에서는
  // 생성 입력이 서버의 Phase 1 intake 기록이므로 변환 자체가 무의미하다.
  const src = read(API);
  assert.doesNotMatch(src, /ensureCutoutPublicUrl|uploadAsset|data:image/);
});
