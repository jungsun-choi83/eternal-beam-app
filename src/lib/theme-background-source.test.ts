/**
 * 배경 영상이 없는 테마도 **생성될 수 있어야 한다.** (Phase 28)
 *
 * ── 무엇이 막혀 있었나 ──────────────────────────────────────────────────────
 * 테마 12개 중 `bgVideo` 를 가진 것은 셋뿐이다(fresh_forest·beach·snow_forest).
 * 나머지 여섯(celestial·golden_meadow·starlight·aurora·sunset·ocean_deep)은
 * resolveBackgroundSource 가 null 을 돌려줬고, requireBackground=true 인
 * 확인 화면에서는 그것이 곧 BACKGROUND_LOAD_FAILED — **생성 거절**이었다.
 *
 * 그런데 그 여섯 테마도 미리보기에는 배경이 보인다. 화면이 `theme.thumb` 을
 * bg-cover 로 깔기 때문이다. 즉 고객은 배경이 있는 그림을 승인한 뒤
 * "배경을 불러오지 못했습니다"를 받았고, 무료 테마 셋(celestial·
 * golden_meadow·starlight)이 거기 포함돼 있었다.
 *
 * 그래서 **미리보기가 깐 바로 그 이미지**로 합성한다. 승인한 그림과 만들어지는
 * 그림이 같아야 한다는 규칙이 이 폴백의 유일한 근거다.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { resolveBackgroundSource } from "./build-canonical-scene.ts";
import {
  CUSTOM_PHOTO_BG_THEME_KEY,
  ORIGINAL_PHOTO_THEME_KEY,
  memorialThemes,
  type MemorialTheme,
} from "../components/memorial/themes.ts";

/** 배경 갈래를 타는 실제 테마들 — 원본/커스텀은 다른 분기다. */
const PLAIN_THEMES = memorialThemes.filter(
  (t) =>
    t.themeKey !== ORIGINAL_PHOTO_THEME_KEY &&
    t.themeKey !== CUSTOM_PHOTO_BG_THEME_KEY
);

const byKey = (k: string): MemorialTheme =>
  memorialThemes.find((t) => t.themeKey === k)!;

// ── 폴백 ────────────────────────────────────────────────────────────────────

test("배경 영상이 없는 테마는 썸네일로 합성된다 — 더는 거절되지 않는다", () => {
  for (const key of [
    "celestial",
    "golden_meadow",
    "starlight",
    "aurora",
    "sunset",
    "ocean_deep",
  ]) {
    const theme = byKey(key);
    assert.ok(!theme.bgVideo, `${key} 에 bgVideo 가 생겼다 — 이 테스트를 다시 보라`);

    const { url, backgroundId } = resolveBackgroundSource({ type: "theme", theme });
    assert.equal(url, theme.thumb, key);
    assert.ok(url, `${key} 가 여전히 null 이다 — 생성이 거절된다`);
    assert.equal(backgroundId, key, "배경 id 가 바뀌면 sceneId 도 바뀐다");
  }
});

test("배경 영상이 있는 테마는 **아무것도 바뀌지 않는다**", () => {
  // 이미 동작하던 셋이 폴백 때문에 다른 그림으로 만들어지면 안 된다.
  for (const key of ["fresh_forest", "beach", "snow_forest"]) {
    const theme = byKey(key);
    assert.ok(theme.bgVideo, key);
    assert.equal(resolveBackgroundSource({ type: "theme", theme }).url, theme.bgVideo, key);
  }
});

test("모든 일반 테마가 이제 배경 주소를 갖는다", () => {
  const missing = PLAIN_THEMES.filter(
    (t) => !resolveBackgroundSource({ type: "theme", theme: t }).url
  );
  assert.deepEqual(
    missing.map((t) => t.themeKey),
    [],
    "이 테마들은 선택해도 생성이 거절된다"
  );
});

test("배경 id 는 폴백과 무관하다 — sceneId 가 흔들리지 않는다", () => {
  // sceneId 는 (콘텐츠, 배경 종류, 배경 id, 배치)에서 결정적으로 파생되고
  // 그것이 유료 생성의 멱등 키다. 여기서 id 가 바뀌면 같은 승인이 두 번
  // 제출된다.
  for (const t of PLAIN_THEMES) {
    assert.equal(
      resolveBackgroundSource({ type: "theme", theme: t }).backgroundId,
      t.themeKey,
      t.themeKey
    );
  }
});

// ── 다른 갈래를 건드리지 않는다 ─────────────────────────────────────────────

test("원본·커스텀 갈래는 그대로다", () => {
  assert.equal(
    resolveBackgroundSource({ type: "custom", customUrl: "https://x/bg.mp4" }).url,
    "https://x/bg.mp4"
  );
  assert.equal(resolveBackgroundSource({ type: "custom", customUrl: "" }).url, null);
  // 원본은 localStorage 에서 읽는다 — 테스트 환경에는 없으므로 null 이고,
  // 그것은 실패가 아니다(원본 경로는 합성을 건너뛴다).
  assert.equal(resolveBackgroundSource({ type: "original" }).backgroundId, "original");
});

test("테마가 없으면 여전히 null 이다 — 없는 것을 지어내지 않는다", () => {
  assert.equal(resolveBackgroundSource({ type: "theme", theme: null }).url, null);
  assert.equal(resolveBackgroundSource({ type: "theme" }).url, null);
});

// ── 미리보기와 같은 이미지인가 ──────────────────────────────────────────────

test("합성이 쓰는 이미지가 미리보기가 까는 이미지와 같다", () => {
  // 이 폴백의 근거 전체가 이 한 줄이다. 화면이 다른 이미지를 깔기 시작하면
  // 고객이 승인한 그림과 만들어지는 그림이 갈라진다.
  for (const path of [
    "src/components/memorial/preview-screen.tsx",
    "src/components/memorial/memorial-device-play-screen.tsx",
  ]) {
    const src = readFileSync(path, "utf8");
    assert.match(
      src,
      /backgroundImage: `url\(\$\{(?:originalPhoto \|\| )?(?:currentTheme|theme)\.thumb\}\)`/,
      `${path} 가 thumb 이 아닌 배경을 깔고 있다`
    );
  }
});

test("썸네일 자산이 실제로 존재한다 — 없으면 폴백이 곧 실패다", () => {
  for (const t of PLAIN_THEMES) {
    assert.ok(t.thumb?.trim(), `${t.themeKey} 에 썸네일이 없다`);
    assert.doesNotThrow(
      () => readFileSync(`public${t.thumb}`),
      `${t.themeKey}: ${t.thumb} 파일이 없다`
    );
  }
});

test("썸네일은 이미지다 — loadBackgroundSource 가 영상으로 오해하지 않는다", async () => {
  // .mp4/.webm/.mov 로 끝나면 첫 프레임 추출 경로를 탄다. 정지 이미지에 그
  // 경로를 태우면 조용히 실패한다.
  for (const t of PLAIN_THEMES) {
    if (t.bgVideo) continue;
    assert.ok(
      /\.(jpg|jpeg|png|webp)$/i.test(t.thumb),
      `${t.themeKey}: ${t.thumb} 가 이미지 확장자가 아니다`
    );
  }
});

test("커스텀 배경 테마에는 폴백이 걸리지 않는다 — 플레이스홀더로 생성하지 않는다", async () => {
  // custom_photo_bg 도 thumb(플레이스홀더)을 갖고 있다. 그것으로 합성하면
  // 고객이 아직 만들지도 않은 배경 대신 안내용 이미지가 인쇄된다.
  const { resolveSceneBackground } = await import("./build-canonical-scene.ts");
  const custom = byKey(CUSTOM_PHOTO_BG_THEME_KEY);
  const choice = resolveSceneBackground(custom);
  assert.equal(choice.type, "custom", "커스텀 테마가 theme 갈래로 샜다");
  // 저장된 커스텀 배경이 없으면 여전히 null — 거절되는 것이 맞다.
  assert.equal(resolveBackgroundSource(choice).url, null);
});
