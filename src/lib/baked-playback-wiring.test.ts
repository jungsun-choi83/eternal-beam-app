/**
 * 구운 자산 재생 배선 — **판정이 화면에 실제로 닿는가.** (Phase 25)
 *
 * ── 왜 이 파일이 따로 있는가 ────────────────────────────────────────────────
 * baked-playback.ts 의 순수 판정은 이미 canonical-scene.test.ts 가 검증한다.
 * 문제는 거기가 아니었다. 판정 함수 넷 중 **하나만** 호출되고 있었고
 * (shouldTransparentComposite), 나머지 셋은 테스트만 부르는 죽은 코드였다.
 * 그래서 다음이 전부 참이었다:
 *
 *   * 배경 레이어가 조건 없이 깔렸다 (배경 이중 적용)
 *   * 1280×720 장면이 세로 62% 슬롯에 밀려 들어갔다
 *   * 승인된 배치가 재생 시점에 한 번 더 적용됐다
 *   * background_baked 는 저장까지 되고 **아무도 읽지 않았다**
 *
 * 순수 함수 테스트는 그중 무엇도 잡지 못한다 — 함수는 옳은 답을 내고 있었고,
 * 아무도 묻지 않았을 뿐이다. 그래서 여기서는 **호출부**를 본다.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  isBackgroundBaked,
  playbackFrameClass,
  playbackLayout,
  shouldApplySubjectTransform,
  shouldRenderThemeBackdrop,
  shouldTransparentComposite,
} from "./baked-playback.ts";

const BAKED = { backgroundBaked: true };
const LEGACY = { backgroundBaked: false };

const read = (p: string) => readFileSync(p, "utf8");

const PREVIEW = "src/components/memorial/preview-screen.tsx";
const DEVICE = "src/components/memorial/memorial-device-play-screen.tsx";
const THEME_SEL = "src/components/memorial/theme-selection-screen.tsx";
const AI_PROC = "src/components/memorial/ai-processing-screen.tsx";
const IDLE_VIDEO = "src/components/memorial/idle-loop-video.tsx";
const CSS = "src/styles/memorial-premium.css";

// ── 판정 (A1) ───────────────────────────────────────────────────────────────

test("구운 자산: 키잉·배경 레이어·배치 변환을 모두 하지 않는다", () => {
  assert.equal(shouldTransparentComposite(BAKED), false, "키를 뽑으면 그림자가 뚫린다");
  assert.equal(shouldRenderThemeBackdrop(BAKED), false, "배경이 두 번 적용된다");
  assert.equal(shouldApplySubjectTransform(BAKED), false, "배치가 두 번 적용된다");
  assert.equal(playbackLayout(BAKED), "scene");
});

test("레거시 자산: 세 처리를 그대로 받는다", () => {
  assert.equal(shouldTransparentComposite(LEGACY), true);
  assert.equal(shouldRenderThemeBackdrop(LEGACY), true);
  assert.equal(shouldApplySubjectTransform(LEGACY), true);
  assert.equal(playbackLayout(LEGACY), "subject");
});

test("표시가 없으면 레거시다 — 기존 자산이 최우선", () => {
  for (const absent of [null, undefined, {}, { backgroundBaked: undefined }]) {
    assert.equal(isBackgroundBaked(absent), false, JSON.stringify(absent));
    assert.equal(shouldRenderThemeBackdrop(absent), true);
    assert.equal(shouldApplySubjectTransform(absent), true);
    assert.equal(playbackLayout(absent), "subject");
  }
});

test("구운 자산은 16:9 전면 상자, 62% 클래스가 붙지 않는다", () => {
  const cls = playbackFrameClass(BAKED);
  assert.equal(cls, "theme-preview-frame__scene");
  assert.ok(!cls.includes("__pet"), cls);
  assert.ok(!cls.includes("62%"), cls);
});

test("레거시 상자 문자열은 기존 마크업 그대로다", () => {
  // 한 글자라도 달라지면 지금 잘 나오고 있는 화면이 바뀐다.
  assert.equal(
    playbackFrameClass(LEGACY),
    "theme-preview-frame__pet max-h-[62%] max-w-[92%]"
  );
});

// ── CSS (A3) ────────────────────────────────────────────────────────────────

test("62% 규칙은 한 글자도 바뀌지 않았다", () => {
  assert.ok(
    read(CSS).includes(
      `.theme-preview-frame__pet {
  width: auto;
  height: 62%;
  max-width: 92%;
  max-height: 62%;
  aspect-ratio: var(--idle-aspect, 4 / 5);
}`
    ),
    "레거시 규칙을 건드렸다"
  );
});

test("구운 자산 상자는 프레임을 **가득 채운다** — 작게 박히지 않는다", () => {
  // 이 규칙이 실제 결함의 자리였다. width:100% + aspect-ratio:16/9 는
  // 3:4 프레임 안에서 세로 42% 짜리 가로 띠가 된다 — 62% 슬롯을 걷어내고
  // 더 작은 띠로 바꾼 셈이었다.
  const css = read(CSS);
  const i = css.indexOf(".theme-preview-frame__scene {");
  assert.ok(i > 0, "__scene 규칙이 없다");
  const rule = css.slice(i, css.indexOf("}", i));

  assert.match(rule, /width:\s*100%/);
  assert.match(rule, /height:\s*100%/, "높이를 채우지 않으면 띠가 된다");
  assert.match(rule, /aspect-ratio:\s*auto/, "비율을 못 박으면 프레임을 못 채운다");
  assert.match(rule, /max-height:\s*none/, "상속된 max-height 가 높이를 깎는다");
  assert.match(rule, /max-width:\s*none/);
  assert.ok(!/62%/.test(rule), "구운 상자에 62% 가 새어 들어갔다");
  assert.ok(!/16\s*\/\s*9/.test(rule), "16:9 를 못 박아 다시 띠가 된다");
});

test("구운 실제 영상은 contain, 여백은 흐린 동일 영상으로 채운다", () => {
  const css = read(CSS);
  const i = css.indexOf(".theme-preview-frame__scene .idle-loop-video__raw");
  assert.ok(i > 0, "구운 상자 안 <video> 규칙이 없다");
  const rule = css.slice(i, css.indexOf("}", i));
  assert.match(rule, /object-fit:\s*contain/);
  assert.match(rule, /object-position:\s*center/);
  assert.match(rule, /height:\s*100%/);

  const backdropAt = css.indexOf(".idle-loop-video__backdrop {");
  assert.ok(backdropAt > 0, "흐린 배경 레이어가 없다");
  const backdrop = css.slice(backdropAt, css.indexOf("}", backdropAt));
  assert.match(backdrop, /object-fit:\s*cover/);
  assert.match(backdrop, /filter:\s*blur\(/);
});

test("합성하지 않는 분기의 <video> 규칙이 있다 — 래퍼가 생겼기 때문이다", () => {
  const css = read(CSS);
  const i = css.indexOf(".idle-loop-video__raw {");
  assert.ok(i > 0, "__raw 규칙이 없다");
  const rule = css.slice(i, css.indexOf("}", i));
  assert.match(rule, /width:\s*100%/);
  assert.match(rule, /height:\s*100%/);
});

// ── --idle-aspect 함정 (A2) ─────────────────────────────────────────────────

test("비율 계산이 transparentComposite 조기 반환 **앞**에서 일어난다", () => {
  // 이것이 이번 수정의 핵심이다. 예전에는 --idle-aspect 설정이
  // `if (!transparentComposite) return;` **뒤**에 있어서, 정확히 구운 자산만
  // 비율을 못 받고 4/5 폴백에 걸렸다.
  const src = read(IDLE_VIDEO);
  const applyAt = src.indexOf("const applyAspect = ()");
  const detectAt = src.indexOf("const detectMode = ()");
  assert.ok(applyAt > 0, "applyAspect 가 분리되지 않았다");
  assert.ok(applyAt < detectAt, "applyAspect 가 detectMode 안에 남아 있다");

  const detect = src.slice(detectAt, src.indexOf("const onVideoError", detectAt));
  const earlyReturn = detect.indexOf("return;");
  const callInEarlyBranch = detect.indexOf("applyAspect()");
  assert.ok(
    callInEarlyBranch > 0 && callInEarlyBranch < earlyReturn,
    "키잉하지 않는 분기가 비율을 설정하지 않고 빠져나간다"
  );
});

test("비율은 packed 소스만 절반으로 센다", () => {
  const src = read(IDLE_VIDEO);
  const i = src.indexOf("const applyAspect = ()");
  const fn = src.slice(i, src.indexOf("const detectMode", i));
  assert.match(fn, /modeRef\.current === "packed" \? Math\.floor\(vh \/ 2\) : vh/);
  assert.match(fn, /setProperty\("--idle-aspect"/);
});

test("합성하지 않는 분기가 래퍼를 갖는다 — 없으면 걸 곳이 없다", () => {
  // 예전에는 이 분기가 <video> 를 맨몸으로 돌려줬고, wrapRef 가 null 이라
  // setProperty 가 조용히 아무 일도 하지 않았다.
  const src = read(IDLE_VIDEO);
  const i = src.indexOf("if (!useCanvasComposite) {");
  assert.ok(i > 0);
  // 이 분기 전체 — 자기 자신의 `return (` 에서 자르면 본문을 통째로 놓친다.
  const branch = src.slice(i, src.indexOf("\n  }\n", i));
  assert.match(branch, /ref=\{wrapRef\}/, "래퍼에 wrapRef 가 없다");
  assert.match(branch, /className=\{`idle-loop-video [^`]*\$\{className\}`\}/, "className 이 상자로 가지 않는다");
  assert.match(branch, /className="idle-loop-video__raw"/);
  assert.match(branch, /className="idle-loop-video__backdrop"/);
  assert.match(branch, /\{blurredBackdrop && \(/);
});

test("구운 자산만 흐린 배경을 요청한다 — 레거시 키잉 경로는 그대로다", () => {
  const src = read("src/components/memorial/pet-idle-display.tsx");
  assert.match(src, /blurredBackdrop=\{backgroundBaked\}/);
});

// ── 화면 배선 (A1 · A4) ─────────────────────────────────────────────────────

for (const [name, path] of [
  ["미리보기", PREVIEW],
  ["기기 재생", DEVICE],
] as const) {
  test(`${name}: 배경 레이어가 판정 뒤에 있다`, () => {
    const src = read(path);
    assert.match(src, /shouldRenderThemeBackdrop\(bakedAsset\)/);
    // ThemeBackgroundVideo 가 게이트 **안**에 있어야 한다.
    const gate = src.indexOf("shouldRenderThemeBackdrop(bakedAsset)");
    const bg = src.indexOf("<ThemeBackgroundVideo");
    assert.ok(gate < bg, "배경 영상이 게이트 밖에 있다");
  });

  test(`${name}: 구운 자산과 레거시가 **구조로** 갈린다`, () => {
    const src = read(path);
    // 한 컨테이너에 조건부 클래스를 거는 대신 분기 자체를 나눈다 —
    // 그래야 구운 쪽에 레거시 속성이 섞여 들어갈 자리가 없다.
    assert.match(src, /\{!shouldApplySubjectTransform\(bakedAsset\) \? \(/);
  });

  test(`${name}: 구운 분기에 레거시 레이아웃·효과가 하나도 없다`, () => {
    const src = read(path);
    const i = src.indexOf("{!shouldApplySubjectTransform(bakedAsset) ? (");
    const j = src.indexOf(") : ", i);
    const baked = src.slice(i, j);

    assert.match(baked, /className="absolute inset-0"/, "구운 분기가 프레임을 채우지 않는다");
    for (const forbidden of [
      "subjectTransform",
      "theme-preview-frame__pet",
      "62%",
      "preview-subject-layer",
      "items-end",
      "drop-shadow",
      "contact-shadow",
      "paddingLeft",
      "onFeetMarginChange",
    ]) {
      assert.ok(!baked.includes(forbidden), `구운 분기에 ${forbidden} 이 남아 있다`);
    }
  });

  test(`${name}: 레거시 분기는 예전 레이아웃 그대로다`, () => {
    const src = read(path);
    assert.match(src, /transform: subjectTransform\(/, "접지 변환이 사라졌다");
    assert.match(src, /absolute inset-0 flex items-end justify-center preview-subject-layer/);
  });

  test(`${name}: 상자 클래스를 직접 적지 않고 판정에서 받는다`, () => {
    const src = read(path);
    assert.match(src, /className=\{playbackFrameClass\(bakedAsset\)\}/);
    assert.ok(
      !src.includes('"theme-preview-frame__pet max-h-[62%] max-w-[92%]"'),
      "화면이 상자 클래스를 하드코딩하고 있다"
    );
  });

  test(`${name}: 두 분기가 각각 플래그를 명시한다`, () => {
    const src = read(path);
    // 구운 분기는 축약형(=true), 레거시 분기는 명시적 false.
    assert.match(src, /\n\s+backgroundBaked\n/, "구운 분기가 플래그를 켜지 않는다");
    assert.match(src, /backgroundBaked=\{false\}/, "레거시 분기가 기본값에 기댄다");
  });

  test(`${name}: 영상이 없으면 구운 자산으로 치지 않는다`, () => {
    // 정적 누끼가 나가는 동안 배경을 지우면 화면이 검게 빈다.
    assert.match(
      read(path),
      /backgroundBaked:\s*hasIdle && pipeline\?\.background_baked === true/
    );
  });
}

test("테마 선택: 이미 파싱하던 객체에서 플래그를 꺼내 넘긴다", () => {
  const src = read(THEME_SEL);
  assert.match(src, /pipeline\.background_baked === true/);
  assert.match(src, /backgroundBaked=\{idleBaked\}/);
  // 영상이 없으면 정적 누끼다 — 그때 구운 것으로 치면 안 된다.
  assert.match(src, /Boolean\(pipeline\.idle_video_url\) && pipeline\.background_baked/);
});

test("AI 처리: 생성 이전 화면이라 명시적으로 false 다", () => {
  // 기본값에 기대면 "빠뜨린 것"과 "그렇게 정한 것"이 구분되지 않는다.
  assert.match(read(AI_PROC), /backgroundBaked=\{false\}/);
});

test("모든 PetIdleDisplay 호출부가 플래그를 명시한다 — 빠진 곳이 없다", () => {
  for (const p of [PREVIEW, DEVICE, THEME_SEL, AI_PROC]) {
    const src = read(p);
    let i = src.indexOf("<PetIdleDisplay");
    assert.ok(i > 0, p);
    while (i > 0) {
      const el = src.slice(i, src.indexOf("/>", i));
      assert.match(el, /backgroundBaked/, `${p} 의 한 호출부가 플래그를 넘기지 않는다`);
      i = src.indexOf("<PetIdleDisplay", i + 1);
    }
  }
});

test("판정 함수가 더는 죽어 있지 않다", () => {
  // 이번 수정 이전에는 넷 중 셋이 테스트에서만 불렸다.
  const wired = [PREVIEW, DEVICE].map(read).join("\n");
  assert.match(wired, /shouldRenderThemeBackdrop/);
  assert.match(wired, /shouldApplySubjectTransform/);
  assert.match(wired, /playbackFrameClass/);
  assert.match(read("src/components/memorial/pet-idle-display.tsx"), /shouldTransparentComposite/);
});
