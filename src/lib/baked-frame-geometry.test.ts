/**
 * 구운 장면이 **프레임을 실제로 채우는가** — 크기를 계산해서 확인한다.
 *
 * ── 왜 헬퍼 테스트로는 못 잡았나 ────────────────────────────────────────────
 * 판정 함수(shouldTransparentComposite 등)는 내내 옳은 답을 내고 있었다.
 * 화면도 그 답을 쓰고 있었다. 그런데도 영상은 작게 박혀 나왔다 — 결함이
 * **CSS 상자의 크기**에 있었기 때문이다.
 *
 *   예전 1: .theme-preview-frame__pet   height 62%          (누끼용 세로 슬롯)
 *   예전 2: .theme-preview-frame__scene width 100% + 16/9   3:4 프레임에서 42%
 *
 * 둘 다 "구운 자산이냐"는 질문에는 옳게 답하고 있었고, 둘 다 화면에서는 작았다.
 * 그래서 여기서는 문자열이 아니라 **렌더 높이를 계산**한다. 규칙이 다시
 * 비율이나 상한을 물고 오면 이 숫자가 1.0 에서 떨어진다.
 *
 * ── DOM 없이 하는 이유 ──────────────────────────────────────────────────────
 * 이 저장소에는 jsdom/happy-dom 이 없다. 그래서 CSS 파일에서 규칙을 읽어
 * 브라우저가 할 계산(aspect-ratio · max-height · height)을 그대로 따라 한다.
 * 완전한 레이아웃 엔진은 아니지만, 이 결함이 살던 정확히 그 계산이다.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const CSS = "src/styles/memorial-premium.css";

/** 프레임 비율 — 두 화면 모두 aspect-[3/4] 다. */
const FRAME_W = 3;
const FRAME_H = 4;

type Decls = Record<string, string>;

/** 선택자 하나의 선언을 읽는다. 주석은 버린다 — 설명이 값으로 새면 안 된다. */
function ruleFor(css: string, selector: string): Decls {
  const i = css.indexOf(selector);
  assert.ok(i >= 0, `${selector} 규칙이 없다`);
  // 셀렉터 그룹(`a, b { … }`)도 받는다 — 다음 `{` 까지가 셀렉터다.
  const open = css.indexOf("{", i);
  assert.ok(open > i, `${selector} 뒤에 블록이 없다`);
  const body = css
    .slice(open + 1, css.indexOf("}", open))
    .replace(/\/\*[\s\S]*?\*\//g, "");
  const out: Decls = {};
  for (const line of body.split(";")) {
    const [k, ...rest] = line.split(":");
    if (!k?.trim() || rest.length === 0) continue;
    out[k.trim()] = rest.join(":").trim();
  }
  return out;
}

function parseRatio(value: string | undefined): number | null {
  if (!value) return null;
  // var(--idle-aspect, 4 / 5) → 폴백값을 쓴다(측정값이 오기 전 상태).
  const fallback = value.match(/var\([^,]+,\s*([\d.]+)\s*\/\s*([\d.]+)\s*\)/);
  if (fallback) return Number(fallback[1]) / Number(fallback[2]);
  const plain = value.match(/^([\d.]+)\s*\/\s*([\d.]+)$/);
  if (plain) return Number(plain[1]) / Number(plain[2]);
  return null; // auto 등
}

function pct(value: string | undefined): number | null {
  const m = (value || "").match(/^([\d.]+)%$/);
  return m ? Number(m[1]) / 100 : null;
}

/**
 * 프레임 높이 대비 실제 렌더 높이(0~1).
 *
 * 브라우저 순서를 따른다: height → aspect-ratio(너비에서 유도) → max-height 상한.
 */
function renderedHeightFraction(d: Decls): number {
  const frameW = FRAME_W;
  const frameH = FRAME_H;

  let h: number | null = null;
  const hPct = pct(d.height);
  if (hPct != null) h = frameH * hPct;

  if (h == null) {
    const ratio = parseRatio(d["aspect-ratio"]);
    const wPct = pct(d.width);
    if (ratio != null && wPct != null) h = (frameW * wPct) / ratio;
  }
  if (h == null) h = frameH; // height:auto + aspect auto → 부모가 정한다

  const maxH = pct(d["max-height"]);
  if (maxH != null) h = Math.min(h, frameH * maxH);

  return h / frameH;
}

const css = readFileSync(CSS, "utf8");

// ── 구운 장면 ───────────────────────────────────────────────────────────────

test("구운 장면은 프레임 높이를 100% 쓴다", () => {
  const scene = ruleFor(css, ".theme-preview-frame__scene");
  const f = renderedHeightFraction(scene);
  assert.equal(
    f,
    1,
    `구운 영상이 프레임의 ${(f * 100).toFixed(0)}% 만 차지한다 — 작게 박혀 보인다`
  );
});

test("예전 두 상자는 이 검사에서 떨어진다 — 이 테스트가 헛돌지 않는다", () => {
  // 실제로 화면에 나갔던 두 값을 그대로 넣어 본다.
  const legacySlot = renderedHeightFraction({
    width: "auto",
    height: "62%",
    "max-height": "62%",
    "aspect-ratio": "var(--idle-aspect, 4 / 5)",
  });
  assert.ok(Math.abs(legacySlot - 0.62) < 0.001, String(legacySlot));

  const oldSceneBand = renderedHeightFraction({
    width: "100%",
    height: "auto",
    "max-height": "100%",
    "aspect-ratio": "var(--idle-aspect, 16 / 9)",
  });
  // (3 × 1) ÷ (16/9) ÷ 4 = 0.4219…
  assert.ok(Math.abs(oldSceneBand - 0.4219) < 0.001, String(oldSceneBand));
  assert.ok(oldSceneBand < 0.62, "예전 '수정'이 슬롯보다 더 작았다");
});

test("구운 상자는 비율도 상한도 물지 않는다", () => {
  const scene = ruleFor(css, ".theme-preview-frame__scene");
  assert.equal(parseRatio(scene["aspect-ratio"]), null, "비율을 물면 띠가 된다");
  assert.equal(pct(scene["max-height"]), null, "max-height 가 높이를 깎는다");
  assert.equal(pct(scene["max-width"]), null);
  assert.equal(scene.width, "100%");
  assert.equal(scene.height, "100%");
});

test("구운 영상은 상자를 잘라서 채운다 — 검은 띠를 만들지 않는다", () => {
  const v = ruleFor(css, ".theme-preview-frame__scene .idle-loop-video__raw");
  assert.equal(v["object-fit"], "cover");
  assert.equal(v["object-position"], "center");
  assert.equal(v.width, "100%");
  assert.equal(v.height, "100%");
});

// ── 레거시는 그대로 ─────────────────────────────────────────────────────────

test("레거시 누끼 상자는 62% 그대로다 — 지금 잘 나오는 화면을 건드리지 않았다", () => {
  const pet = ruleFor(css, ".theme-preview-frame__pet");
  assert.equal(pet.height, "62%");
  assert.equal(pet["max-height"], "62%");
  assert.equal(pet["max-width"], "92%");
  assert.equal(pet["aspect-ratio"], "var(--idle-aspect, 4 / 5)");
  assert.ok(Math.abs(renderedHeightFraction(pet) - 0.62) < 0.001);
});

test("두 상자는 서로 다른 규칙이다 — 하나를 고쳐 다른 하나가 바뀌지 않는다", () => {
  const petAt = css.indexOf(".theme-preview-frame__pet {");
  const sceneAt = css.indexOf(".theme-preview-frame__scene {");
  assert.ok(petAt > 0 && sceneAt > 0);
  assert.notEqual(petAt, sceneAt);
  // __scene 이 뒤에 와야 .idle-loop-video 의 비율/상한을 덮는다.
  assert.ok(sceneAt > css.indexOf(".idle-loop-video {"), "구운 규칙이 너무 앞에 있다");
});

// ── 지금은 남겨 두는 문제: 3:4 프레임 vs 16:9 장면 ──────────────────────────

test("cover 로 채우면 좌우가 잘린다 — 비율 차이는 아직 남아 있다", () => {
  // 이 테스트는 결함을 막는 것이 아니라 **알려진 손실을 기록**한다.
  // 1280×720 장면을 3:4 프레임에 cover 로 넣으면 가로가 크게 잘린다.
  const sceneRatio = 16 / 9;
  const frameRatio = FRAME_W / FRAME_H;
  const visibleWidthFraction = frameRatio / sceneRatio;
  assert.ok(visibleWidthFraction < 0.5, String(visibleWidthFraction));
  // 약 42% 만 보인다. 세로로 잘리는 것(contain=검은 띠)보다는 낫지만,
  // 근본 해결은 생성 비율이나 프레임 비율을 맞추는 별개 과제다.
  assert.ok(Math.abs(visibleWidthFraction - 0.4219) < 0.001);
});
