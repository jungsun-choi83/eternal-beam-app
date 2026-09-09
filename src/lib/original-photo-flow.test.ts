/**
 * 원본 사진 흐름 — **업로드 → 미리보기 → 생성이 같은 한 장을 본다.**
 *
 * ── 실제 결함 ───────────────────────────────────────────────────────────────
 * 업로드 경로가 둘인데 하는 일이 달랐다.
 *
 *   홈 화면 선택기   상태 + 종류 + main_photo 저장 + 낡은 영상 URL 제거
 *   업로드 화면      상태 + 종류. **그게 전부였다.**
 *
 * 업로드 화면으로 고른 사진은 화면에만 있었고 localStorage 에는 없었다. 그런데
 * 원본 배경을 읽는 세 곳(테마 카드·미리보기 배경·장면 합성)은 전부 localStorage
 * 를 각자 읽었다. 그래서 조용히 셋 중 하나가 일어났다: 카드가 검게 비거나,
 * 지난번 사진이 배경으로 들어가거나, ORIGINAL_PHOTO_MISSING 으로 거절됐다.
 *
 * 이제 저장은 commitMainMedia 한 곳, 해석은 resolveOriginalPhoto 한 곳,
 * 화면들은 부모가 내려 준 **같은 값**을 쓴다.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test, { beforeEach } from "node:test";

import {
  MAIN_PHOTO_KEY,
  MAIN_VIDEO_KEY,
  MEDIA_TYPE_KEY,
  commitMainMedia,
  inferKindFromUrl,
  readMainPhoto,
  readMainVideoUrl,
  readMediaKind,
  resolveOriginalPhoto,
} from "./main-media-store.ts";
import { resolveBackgroundSource, resolveSceneBackground } from "./build-canonical-scene.ts";
import {
  ORIGINAL_PHOTO_THEME_KEY,
  getMemorialTheme,
  memorialThemes,
} from "../components/memorial/themes.ts";

const PHOTO_A = "data:image/png;base64,AAAA";
const PHOTO_B = "data:image/png;base64,BBBB";
const VIDEO = "blob:http://localhost/9f2c";

const read = (p: string) => readFileSync(p, "utf8");

const APP = "src/app/EternalBeamApp.tsx";
const UPLOAD = "src/components/memorial/photo-upload-screen.tsx";
const THEMES = "src/components/memorial/theme-selection-screen.tsx";
const PREVIEW = "src/components/memorial/preview-screen.tsx";

/** node 에는 localStorage 가 없다. 실제 동작에 필요한 만큼만 세운다. */
function installLocalStorage(): void {
  const box = new Map<string, string>();
  (globalThis as Record<string, unknown>).localStorage = {
    getItem: (k: string) => (box.has(k) ? box.get(k)! : null),
    setItem: (k: string, v: string) => void box.set(k, String(v)),
    removeItem: (k: string) => void box.delete(k),
    clear: () => box.clear(),
  };
}

beforeEach(installLocalStorage);

const ORIGINAL_THEME = memorialThemes.find(
  (t) => t.themeKey === ORIGINAL_PHOTO_THEME_KEY
)!;

// ── 두 업로드 경로가 같은 일을 한다 ─────────────────────────────────────────

test("업로드 화면 경로: 사진이 저장되고 낡은 영상 URL 이 사라진다", () => {
  // 이전 세션에서 영상을 올린 상태.
  commitMainMedia("video", VIDEO);
  assert.equal(readMainVideoUrl(), VIDEO);

  // 이제 업로드 화면에서 사진을 고른다.
  commitMainMedia("image", PHOTO_A);

  assert.equal(readMainPhoto(), PHOTO_A);
  assert.equal(readMediaKind(), "image");
  assert.equal(readMainVideoUrl(), null, "폐기된 blob: URL 이 남아 있다");
});

test("홈 선택기 경로: 같은 결과가 나온다", () => {
  commitMainMedia("image", PHOTO_B);
  assert.deepEqual(
    {
      photo: readMainPhoto(),
      video: readMainVideoUrl(),
      kind: readMediaKind(),
    },
    { photo: PHOTO_B, video: null, kind: "image" }
  );
});

test("영상을 올리면 원본 사진이 사라진다 — 두 키가 동시에 살지 않는다", () => {
  commitMainMedia("image", PHOTO_A);
  commitMainMedia("video", VIDEO);
  assert.equal(readMainVideoUrl(), VIDEO);
  assert.equal(readMainPhoto(), null);
  assert.equal(readMediaKind(), "video");
});

test("빈 값은 저장하지 않는다 — 멀쩡한 사진을 지우지 않는다", () => {
  commitMainMedia("image", PHOTO_A);
  commitMainMedia("image", "   ");
  assert.equal(readMainPhoto(), PHOTO_A);
});

test("두 경로가 **같은 함수**를 부른다 — 코드가 갈라져 있지 않다", () => {
  const app = read(APP);
  // 저장 로직이 앱에 다시 적혀 있으면 안 된다.
  assert.ok(
    !app.includes(`localStorage.setItem('${MAIN_PHOTO_KEY}'`),
    "홈 선택기가 저장 로직을 따로 들고 있다"
  );
  assert.match(app, /const commitUpload = \(url: string, kind: MediaKind\)/);
  const calls = app.match(/commitUpload\(/g) ?? [];
  assert.ok(calls.length >= 3, `commitUpload 호출이 ${calls.length}개뿐이다`);

  // 업로드 화면은 종류를 **알려 주고**, 저장은 부모가 한다.
  const upload = read(UPLOAD);
  assert.match(upload, /onImageUpload\(result, "image"\)/);
  assert.match(upload, /onImageUpload\(URL\.createObjectURL\(file\), "video"\)/);
  assert.ok(
    !upload.includes(MAIN_PHOTO_KEY),
    "업로드 화면이 저장 키를 직접 만지고 있다"
  );
});

// ── 새로고침 복원 ───────────────────────────────────────────────────────────

test("새로고침 후에도 저장된 사진이 복원된다", () => {
  commitMainMedia("image", PHOTO_A);
  // 새로고침 = React 상태 소실. current 가 없다.
  assert.equal(resolveOriginalPhoto(null), PHOTO_A);
  assert.equal(resolveOriginalPhoto(undefined), PHOTO_A);
});

test("지금 들고 있는 사진이 저장된 값을 이긴다", () => {
  commitMainMedia("image", PHOTO_A);
  // 저장이 아직 안 됐거나 실패해도 화면 값이 정본이다.
  assert.equal(resolveOriginalPhoto(PHOTO_B), PHOTO_B);
});

test("아무것도 없으면 null — 지어내지 않는다", () => {
  assert.equal(resolveOriginalPhoto(null), null);
  assert.equal(resolveOriginalPhoto(""), null);
});

// ── 영상을 원본 사진으로 오해하지 않는다 ────────────────────────────────────

test("영상 blob 은 원본 사진이 아니다", () => {
  assert.equal(resolveOriginalPhoto(VIDEO), null, "영상이 정지 배경으로 넘어갔다");
  assert.equal(inferKindFromUrl(VIDEO), "video");
  assert.equal(inferKindFromUrl("data:video/mp4;base64,AA"), "video");
  assert.equal(inferKindFromUrl("https://cdn/x.mp4?sig=1"), "video");
  assert.equal(inferKindFromUrl(PHOTO_A), "image");
  assert.equal(inferKindFromUrl("https://cdn/x.jpg"), "image");
});

test("영상을 올린 상태에서 원본을 고르면 사진이 없다고 나온다", () => {
  commitMainMedia("video", VIDEO);
  assert.equal(resolveOriginalPhoto(VIDEO), null);
});

test("영상이 화면에 있어도 저장된 사진이 있으면 그것을 쓴다", () => {
  // 사진을 올린 뒤 화면 상태만 영상으로 바뀐 경우 — 저장 값이 답이다.
  localStorage.setItem(MAIN_PHOTO_KEY, PHOTO_A);
  localStorage.setItem(MEDIA_TYPE_KEY, "image");
  assert.equal(resolveOriginalPhoto(VIDEO), PHOTO_A);
});

// ── 카드·미리보기·생성이 같은 한 장을 본다 ──────────────────────────────────

test("생성이 화면과 같은 사진을 받는다", () => {
  commitMainMedia("image", PHOTO_A);
  // 화면이 보여 준 것은 PHOTO_B(방금 올린 것, 저장은 아직).
  const choice = resolveSceneBackground(ORIGINAL_THEME, PHOTO_B);
  assert.equal(choice.type, "original");
  assert.equal(resolveBackgroundSource(choice).url, PHOTO_B, "생성이 지난번 사진을 쓴다");
});

test("주입값이 없으면 저장된 값으로 떨어진다", () => {
  commitMainMedia("image", PHOTO_A);
  assert.equal(
    resolveBackgroundSource(resolveSceneBackground(ORIGINAL_THEME)).url,
    PHOTO_A
  );
});

test("원본 사진이 없으면 배경 주소가 없다 — 검은 판을 만들지 않는다", () => {
  const choice = resolveSceneBackground(ORIGINAL_THEME, null);
  assert.equal(resolveBackgroundSource(choice).url, null);
});

test("카드·큰 미리보기·생성이 모두 같은 prop 에서 나온다", () => {
  const app = read(APP);
  assert.match(app, /const originalPhoto = resolveOriginalPhoto\(uploadedImage\)/);
  // 두 화면 모두 같은 값을 받는다.
  assert.equal((app.match(/originalPhoto=\{originalPhoto\}/g) ?? []).length, 2);

  const themes = read(THEMES);
  // 화면이 저장소를 직접 읽지 않는다.
  assert.ok(!themes.includes("readOriginalPhoto"), "테마 화면이 저장소를 직접 읽는다");
  assert.match(themes, /originalPhoto \|\| theme\.thumb/, "카드가 주입값을 안 쓴다");
  assert.match(themes, /originalPhoto=\{originalPhoto\}/);

  const preview = read(PREVIEW);
  assert.match(preview, /originalPhotoProp \|\| readOriginalPhoto\(\)/);
  assert.match(preview, /resolveSceneBackground\(currentTheme, originalPhoto\)/);
});

// ── 큰 미리보기가 고른 배경을 보여 준다 ─────────────────────────────────────

test("테마를 고르면 큰 미리보기가 그 배경을 그린다", () => {
  const src = read(THEMES);
  // 배경 영상 · 썸네일 · 원본 세 갈래가 모두 있어야 한다.
  assert.match(src, /<ThemeBackgroundVideo/, "테마 영상 배경이 없다");
  assert.match(src, /backgroundImage: `url\(\$\{previewTheme\.thumb\}\)`/, "썸네일 배경이 없다");
  assert.match(src, /previewIsOriginal \?/, "원본 갈래 분기가 없다");
  // 배경 판정이 미리보기 화면과 같은 함수를 쓴다.
  assert.match(src, /getEffectiveBgVideo\(previewTheme\)/);
});

test("원본 갈래에는 펫을 덧그리지 않는다", () => {
  // 사진에 이미 아이가 있다. 누끼를 한 번 더 얹으면 둘로 보인다.
  const src = read(THEMES);
  const i = src.indexOf("{previewIsOriginal ? (");
  const j = src.indexOf(") : (", i);
  const originalBranch = src.slice(i, j);
  assert.ok(!originalBranch.includes("PetIdleDisplay"), "원본 위에 펫을 얹는다");
  assert.match(originalBranch, /<img/);
});

test("원본이 없으면 검은 판이 아니라 오류가 보이고 계속 진행이 막힌다", () => {
  const src = read(THEMES);
  assert.match(src, /const originalMissing = Boolean\(previewIsOriginal && !originalPhoto\)/);
  assert.match(src, /role="alert"/);
  assert.match(src, /disabled=\{!activeTheme \|\| originalMissing\}/);
  assert.match(src, /activeTheme && !originalMissing && onContinue\(activeTheme\)/);
});

test("미리보기 화면도 같은 규칙을 쓴다 — 검은 배경으로 생성하지 않는다", () => {
  const src = read(PREVIEW);
  assert.match(src, /const originalMissing = isOriginalPhotoTheme && !originalPhoto/);
  // 제출 전에 막는다.
  const i = src.indexOf("const handleConfirm");
  const guard = src.indexOf("if (originalMissing) {", i);
  const submit = src.indexOf("requestIdleGeneration(", i);
  assert.ok(guard > 0 && guard < submit, "생성 제출 뒤에 검사한다");
  assert.match(src, /disabled=\{generating \|\| originalMissing\}/);
});

// ── 멀리 있는(지연 로딩) 테마 ───────────────────────────────────────────────

test("선택된 카드는 멀리 있어도 검게 남지 않는다", () => {
  const src = read(THEMES);
  assert.match(
    src,
    /const loadImage = Math\.abs\(index - focusIndex\) <= 1 \|\| selected/,
    "선택된 카드가 ±1 가상화에 걸려 검은 채로 남는다"
  );
});

test("가상화 자체는 남아 있다 — 전부 로드하지 않는다", () => {
  const src = read(THEMES);
  assert.match(src, /Math\.abs\(index - focusIndex\) <= 1/);
});

// ── 테마 목록의 원본 카드 ───────────────────────────────────────────────────

test("원본 테마는 고정 썸네일이 없다 — 그래서 주입이 필요하다", () => {
  assert.equal(getMemorialTheme(ORIGINAL_THEME.id)?.thumb, "");
});
