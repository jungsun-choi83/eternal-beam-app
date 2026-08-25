/**
 * 장면 준비 안전장치 (Phase 20).
 *
 * 지키는 계약:
 *   * 배경을 **골랐는데** 그 배경으로 만들지 못하면 → 던진다 (제출하지 않는다).
 *   * 배경 선택이 없는 **레거시 흐름**에서만 단색 폴백이 허용된다.
 *   * 장면 실패 문구는 "생성 실패"가 아니다 — 다시 생성을 유도하면 유료 제출이 반복된다.
 *   * "원본 사진 그대로"가 배경 목록에 실제로 있다.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  SceneError,
  isPreSubmissionServerCode,
  isSceneError,
  looksLikeCanvasTaint,
  sceneErrorMessage,
  serverGenerationMessage,
} from "./scene-errors.ts";
import {
  ORIGINAL_PHOTO_THEME_ID,
  ORIGINAL_PHOTO_THEME_KEY,
  getMemorialTheme,
  memorialThemes,
} from "../components/memorial/themes.ts";

// ── 오류 타입 ────────────────────────────────────────────────────────────────

test("장면 오류는 생성 오류와 구분된다", () => {
  const e = new SceneError("BACKGROUND_LOAD_FAILED", "x");
  assert.equal(isSceneError(e), true);
  assert.equal(isSceneError(new Error("x")), false);
  assert.equal(e.recoverable, true, "장면 실패는 제출 전이므로 복구 가능해야 한다");
});

test("모든 장면 오류 문구가 '다시 생성'이 아니라 '배경을 다시'로 유도한다", () => {
  const codes = [
    "SCENE_PREPARATION_FAILED",
    "BACKGROUND_LOAD_FAILED",
    "CUSTOM_BACKGROUND_CORS_FAILED",
    "SCENE_UPLOAD_FAILED",
    "ORIGINAL_PHOTO_MISSING",
  ] as const;
  for (const c of codes) {
    const ko = sceneErrorMessage(c, "ko");
    const en = sceneErrorMessage(c, "en");
    assert.ok(ko.length > 0, c);
    assert.ok(en.length > 0, c);
    // "생성에 실패" 라고 말하면 고객이 재생성을 눌러 유료 제출을 반복한다.
    assert.doesNotMatch(ko, /생성에 실패|생성 실패/, c);
    assert.doesNotMatch(en, /generation failed/i, c);
  }
});

test("CORS 오염을 알아본다 — 커스텀 배경의 대표 실패 모드", () => {
  assert.equal(looksLikeCanvasTaint({ name: "SecurityError" }), true);
  assert.equal(looksLikeCanvasTaint(new Error("Tainted canvases may not be exported")), true);
  assert.equal(looksLikeCanvasTaint(new Error("network")), false);
});

// ── 서버의 제출 전 거절 ──────────────────────────────────────────────────────

test("제출 전 서버 코드는 과금되지 않았음을 말한다", () => {
  assert.equal(isPreSubmissionServerCode("GENERATION_IDEMPOTENCY_UNAVAILABLE"), true);
  assert.equal(isPreSubmissionServerCode("GENERATION_IN_PROGRESS"), true);
  assert.equal(isPreSubmissionServerCode("LUMA_FAILED"), false);

  const ko = serverGenerationMessage("GENERATION_IDEMPOTENCY_UNAVAILABLE", "ko")!;
  assert.match(ko, /과금되지 않/, "과금되지 않았다는 사실을 말해야 재결제 시도를 막는다");
  const en = serverGenerationMessage("GENERATION_IDEMPOTENCY_UNAVAILABLE", "en")!;
  assert.match(en, /Nothing was charged/i);
});

test("진행 중은 기다리라고 말한다 — 다시 누르라고 하지 않는다", () => {
  const ko = serverGenerationMessage("GENERATION_IN_PROGRESS", "ko")!;
  assert.match(ko, /기다려/);
});

test("모르는 코드는 null — 기존 문구가 그대로 쓰인다", () => {
  assert.equal(serverGenerationMessage("SOMETHING_ELSE"), null);
});

// ── 구조 고정: 선택한 배경으로 못 만들면 제출하지 않는다 ────────────────────

test("장면 합성이 배경 실패 시 던진다 — 검정 폴백으로 조용히 넘어가지 않는다", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync("src/lib/scene-export.ts", "utf8");

  assert.match(src, /requireBackground/, "배경 필수 여부를 구분하지 않는다");
  assert.match(src, /throw new SceneError\(\s*\n?\s*"BACKGROUND_LOAD_FAILED"/);
  assert.match(src, /CUSTOM_BACKGROUND_CORS_FAILED/);
  // 예전의 "단색 폴백 유지" 는 레거시 분기 안에만 남아 있어야 한다.
  assert.match(src, /레거시 흐름에서만 단색 폴백/);
});

test("미리보기가 장면 실패 시 생성을 호출하지 않는다", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync("src/components/memorial/preview-screen.tsx", "utf8");

  const i = src.indexOf("backgroundWasChosen");
  assert.ok(i > 0, "배경 선택 여부를 판단하지 않는다");

  const block = src.slice(i, i + 2200);
  assert.match(block, /if \(backgroundWasChosen\)/, "선택 여부와 무관하게 진행한다");
  assert.match(block, /return;/, "장면 실패 후에도 생성으로 내려간다");
  // 예전의 "레거시 경로로 생성" 무조건 폴백이 남아 있으면 안 된다.
  assert.doesNotMatch(
    block,
    /장면 굽기 실패 — 레거시 경로로 생성/,
    "배경을 골랐는데도 레거시로 떨어진다"
  );
});

test("API 클라이언트가 제출 전 코드를 보존한다", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync("src/app/services/videoProcessingApi.ts", "utf8");
  assert.match(src, /GENERATION_IDEMPOTENCY_UNAVAILABLE/);
  // 422 누끼 거절(CutoutRejectedError)을 삼키면 안 된다 — 좁게 잡았는지 확인.
  assert.match(src, /PRE_SUBMISSION\.includes\(code\)/);
});

// ── 원본 사진 옵션 ───────────────────────────────────────────────────────────

test("'원본 사진 그대로'가 배경 목록에 있다", () => {
  const t = getMemorialTheme(ORIGINAL_PHOTO_THEME_ID);
  assert.ok(t, "원본 사진 테마가 목록에 없다");
  assert.equal(t.themeKey, ORIGINAL_PHOTO_THEME_KEY);
  assert.equal(t.premium, false, "원본 배경은 무료여야 한다");
});

test("원본 사진은 목록 맨 앞 — 누끼 직후 첫 선택지", () => {
  assert.equal(memorialThemes[0].themeKey, ORIGINAL_PHOTO_THEME_KEY);
});

test("원본 사진은 생성 화면을 거치지 않는다", () => {
  const t = getMemorialTheme(ORIGINAL_PHOTO_THEME_ID)!;
  assert.notEqual(t.requiresGeneration, true, "원본인데 배경 생성을 요구한다");
  assert.equal(t.bgVideo, undefined, "원본에는 고정 배경 영상이 없다");
});

test("테마 id/키는 여전히 유일하다 — 새 항목이 충돌을 만들지 않았다", () => {
  const ids = memorialThemes.map((t) => t.id);
  const keys = memorialThemes.map((t) => t.themeKey);
  assert.equal(new Set(ids).size, ids.length, `id 충돌: ${ids.join(",")}`);
  assert.equal(new Set(keys).size, keys.length, `key 충돌: ${keys.join(",")}`);
});

test("원본 갈래는 미리보기에서 펫을 두 번 그리지 않는다", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync("src/components/memorial/preview-screen.tsx", "utf8");
  assert.match(src, /isOriginalPhotoTheme/, "원본 갈래를 구분하지 않는다");
  assert.match(
    src,
    /cutoutDisplay && !isOriginalPhotoTheme/,
    "원본 사진 위에 누끼를 또 얹는다 — 아이가 두 번 보인다"
  );
});

// ── CORS 실패는 **제출 전에** 일어난다 (런타임 증명) ────────────────────────

/**
 * 최소 DOM 대역. 캔버스가 오염됐을 때 브라우저는 toBlob 에서 **동기로**
 * SecurityError 를 던진다 — 그 순간을 그대로 재현한다.
 */
function stubDomWithTaintedCanvas() {
  const g = globalThis as Record<string, unknown>;
  const prevDoc = g.document;
  const prevImage = g.Image;

  g.document = {
    createElement: (tag: string) => {
      if (tag !== "canvas") return {};
      return {
        width: 0,
        height: 0,
        getContext: () => ({
          fillRect() {},
          drawImage() {},
          save() {},
          restore() {},
          translate() {},
          scale() {},
          set fillStyle(_v: string) {},
        }),
        toBlob() {
          const e = new Error("Tainted canvases may not be exported.");
          e.name = "SecurityError";
          throw e;
        },
      };
    },
  };
  class FakeImage {
    crossOrigin = "";
    naturalWidth = 400;
    naturalHeight = 400;
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    set src(_v: string) {
      queueMicrotask(() => this.onload?.());
    }
  }
  g.Image = FakeImage;
  return () => {
    g.document = prevDoc;
    g.Image = prevImage;
  };
}

test("커스텀 배경 CORS 오염 → 제출 전에 던진다 (돈이 나가지 않는다)", async () => {
  const restore = stubDomWithTaintedCanvas();
  try {
    const { composeSceneImage } = await import("./scene-export.ts");
    await assert.rejects(
      () =>
        composeSceneImage({
          backgroundUrl: "https://other-origin.example/bg.png",
          petCutoutUrl: "blob:cutout",
          placement: { scale: 1, posX: 0, posY: 0, shiftPct: 0 },
          requireBackground: true,
        }),
      (e: unknown) => {
        assert.ok(isSceneError(e), `SceneError 가 아니다: ${String(e)}`);
        assert.equal((e as SceneError).code, "CUSTOM_BACKGROUND_CORS_FAILED");
        return true;
      }
    );
  } finally {
    restore();
  }
});

test("장면이 던지면 생성 요청이 만들어지지 않는다", async () => {
  const restore = stubDomWithTaintedCanvas();
  try {
    const { buildCanonicalScene } = await import("./build-canonical-scene.ts");
    let uploaded = 0;
    await assert.rejects(() =>
      buildCanonicalScene({
        userId: "u",
        contentId: "c",
        petCutoutUrl: "blob:cutout",
        placement: { scale: 1, posX: 0, posY: 0, shiftPct: 0 },
        floorY: 0.88,
        background: { type: "custom", customUrl: "https://other/bg.png" },
        requireBackground: true,
        upload: async () => {
          uploaded += 1;
          return "https://s/should-not-happen.png";
        },
      })
    );
    // 업로드조차 하지 않는다 — 생성은 그보다도 뒤다.
    assert.equal(uploaded, 0, "장면이 실패했는데 업로드가 일어났다");
  } finally {
    restore();
  }
});

test("레거시 흐름에서만 검정 폴백 — 배경을 고르지 않았을 때", async () => {
  const restore = stubDomWithTaintedCanvas();
  try {
    const { composeSceneImage } = await import("./scene-export.ts");
    // requireBackground=false 여도 toBlob 자체가 오염으로 던지므로 SceneError 다.
    // 여기서 확인하는 것은 **배경 로드 실패로는** 던지지 않는다는 것.
    await assert.rejects(
      () =>
        composeSceneImage({
          backgroundUrl: null,
          petCutoutUrl: "blob:cutout",
          placement: { scale: 1, posX: 0, posY: 0, shiftPct: 0 },
          requireBackground: false,
        }),
      (e: unknown) => {
        // 배경 없음으로는 던지지 않았고(BACKGROUND_LOAD_FAILED 아님),
        // 캔버스 오염 단계까지 진행했다는 뜻이다.
        assert.notEqual((e as SceneError).code, "BACKGROUND_LOAD_FAILED");
        return true;
      }
    );
  } finally {
    restore();
  }
});
