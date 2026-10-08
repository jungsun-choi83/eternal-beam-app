/**
 * Phase 10 — global visual polish + final brand system.
 *
 * Presentation-only phase, so (like the other redesign suites) it is pinned
 * by reading the stylesheet / component source rather than mounting a DOM.
 * These tests guard the contract every screen now depends on:
 *
 *   1. brand.css is the single token source (palette, spacing, radius, motion,
 *      focus) and loads before every other stylesheet.
 *   2. The shared primitives expose one button system, one status system and
 *      one form-control system.
 *   3. prefers-reduced-motion is honoured globally (CSS + framer-motion).
 *   4. The old saturated / dark-theme values are gone from the shared layer.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (p: string) => readFileSync(p, "utf8");

const BRAND = "src/styles/brand.css";
const INDEX = "src/styles/index.css";
const FOUNDATION = "src/styles/foundation.css";
const THEME = "src/styles/theme.css";
const PREMIUM = "src/styles/memorial-premium.css";
const TAILWIND = "src/styles/tailwind.css";
const UI_IMPROVEMENTS = "src/styles/ui-improvements.css";
const BUTTONS = "src/components/ui/buttons.tsx";
const STATUS = "src/components/ui/status-badge.tsx";
const APP_SHELL = "src/components/layout/app-shell.tsx";
const MAIN = "src/main.tsx";
const HTML = "index.html";

const STYLE_FILES = [
  BRAND,
  FOUNDATION,
  THEME,
  PREMIUM,
  TAILWIND,
  UI_IMPROVEMENTS,
  "src/styles/home-dashboard.css",
  "src/styles/pet-intake.css",
  "src/styles/generation-progress.css",
  "src/styles/theme-selection.css",
  "src/styles/preview-composer.css",
  "src/styles/my-library.css",
  "src/styles/beam-device.css",
  "src/styles/hologram-effects.css",
  "src/styles/onboarding-effects.css",
];

/** Declarations of one `:root { … }` block (the first one in the file). */
function rootBlock(css: string): string {
  const i = css.indexOf(":root {");
  assert.ok(i >= 0, ":root 블록이 없다");
  return css.slice(i, css.indexOf("\n}", i));
}

// ── 1. Token source ──────────────────────────────────────────────────────────

test("brand.css 가 모든 스타일시트보다 먼저 로드된다 (fonts 다음, theme 이전)", () => {
  const css = read(INDEX);
  const brandAt = css.indexOf("brand.css");
  assert.ok(brandAt > 0, "brand.css 가 import 되지 않았다");
  assert.ok(brandAt < css.indexOf("theme.css"));
  assert.ok(brandAt < css.indexOf("memorial-premium.css"));
  assert.ok(brandAt < css.indexOf("foundation.css"));
});

test("최종 팔레트 토큰 — 아이보리 페이지 · 오프화이트 카드 · 차콜 텍스트 · 샴페인 골드 · 더스티 블루 · 세이지 · 테라코타", () => {
  const root = rootBlock(read(BRAND));
  for (const token of [
    "--eb-bg:",
    "--eb-bg-deep:",
    "--eb-surface:",
    "--eb-surface-2:",
    "--eb-surface-3:",
    "--eb-surface-inverse:",
    "--eb-text:",
    "--eb-text-2:",
    "--eb-text-3:",
    "--eb-text-on-gold:",
    "--eb-hairline:",
    "--eb-hairline-strong:",
    "--eb-gold:",
    "--eb-gold-strong:",
    "--eb-gold-soft:",
    "--eb-gold-text:",
    "--eb-gold-wash:",
    "--eb-gold-line:",
    "--eb-blue:",
    "--eb-blue-text:",
    "--eb-blue-wash:",
    "--eb-sage:",
    "--eb-sage-text:",
    "--eb-sage-wash:",
    "--eb-terracotta:",
    "--eb-terracotta-text:",
    "--eb-terracotta-wash:",
    "--eb-warn:",
    "--eb-warn-text:",
    "--eb-warn-wash:",
  ]) {
    assert.ok(root.includes(token), `토큰이 없다: ${token}`);
  }
  // Semantic aliases point at the muted palette, not saturated iOS colours.
  assert.match(root, /--eb-success:\s*var\(--eb-sage\)/);
  assert.match(root, /--eb-error:\s*var\(--eb-terracotta\)/);
  assert.match(root, /--eb-info:\s*var\(--eb-blue\)/);
  // Page is light: ivory background, charcoal text.
  assert.match(root, /--eb-bg:\s*#f7f2e9/i);
  assert.match(root, /--eb-text:\s*#2a2622/i);
});

test("간격 스케일은 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 하나뿐이다 — 옛 5·10 단계는 스케일 값으로 스냅된다", () => {
  const root = rootBlock(read(BRAND));
  assert.match(root, /--eb-space-1:\s*0\.25rem/);
  assert.match(root, /--eb-space-2:\s*0\.5rem/);
  assert.match(root, /--eb-space-3:\s*0\.75rem/);
  assert.match(root, /--eb-space-4:\s*1rem/);
  assert.match(root, /--eb-space-6:\s*1\.5rem/);
  assert.match(root, /--eb-space-8:\s*2rem/);
  assert.match(root, /--eb-space-12:\s*3rem/);
  assert.match(root, /--eb-space-16:\s*4rem/);
  assert.match(root, /--eb-space-5:\s*var\(--eb-space-6\)/);
  assert.match(root, /--eb-space-10:\s*var\(--eb-space-12\)/);
  // No other stylesheet redefines the scale.
  for (const file of STYLE_FILES.filter((f) => f !== BRAND)) {
    assert.doesNotMatch(read(file), /--eb-space-\d+:\s*\d/, `${file} 가 간격 토큰을 다시 정의한다`);
  }
});

test("라운드 · 그림자 · 모션 · 포커스 · 터치 토큰이 한 곳에 있다", () => {
  const root = rootBlock(read(BRAND));
  for (const token of [
    "--eb-radius-xs:",
    "--eb-radius-sm:",
    "--eb-radius-md:",
    "--eb-radius-lg:",
    "--eb-radius-xl:",
    "--eb-radius-pill:",
    "--eb-shadow-1:",
    "--eb-shadow-2:",
    "--eb-shadow-3:",
    "--eb-shadow-gold:",
    "--eb-dur-fast:",
    "--eb-dur:",
    "--eb-dur-slow:",
    "--eb-ease:",
    "--eb-focus-ring:",
    "--eb-touch: 44px",
  ]) {
    assert.ok(root.includes(token), `토큰이 없다: ${token}`);
  }
  // Transitions stay restrained: nothing slower than 320ms.
  assert.match(root, /--eb-dur-slow:\s*320ms/);
});

test("타이포그래피 역할 — 페이지 제목 · 섹션 제목 · 본문 · 캡션 · 버튼 라벨, 데스크톱에서 제목만 커진다", () => {
  const css = read(BRAND);
  for (const cls of [".eb-title {", ".eb-section-title {", ".eb-body {", ".eb-caption {", ".eb-btn-label {"]) {
    assert.ok(css.includes(cls), `타이포 클래스가 없다: ${cls}`);
  }
  const root = rootBlock(css);
  assert.match(root, /--eb-type-title:\s*1\.375rem/);
  assert.match(root, /--eb-type-button:\s*1rem/);
  const desktopAt = css.indexOf("@media (min-width: 1024px)");
  assert.ok(desktopAt > 0);
  assert.match(css.slice(desktopAt, css.indexOf("}", css.indexOf("}", desktopAt) + 1)), /--eb-type-title:\s*1\.625rem/);
});

// ── 2. Shared primitives ─────────────────────────────────────────────────────

test("버튼 시스템 — Primary · Secondary · Ghost · Destructive, Loading · Disabled · Success · Error 상태가 한 곳에 정의된다", () => {
  const css = read(BRAND);
  for (const sel of [
    ".eb-btn--primary",
    ".eb-btn--secondary",
    ".eb-btn--ghost",
    ".eb-btn--destructive",
    ".eb-btn--success",
    ".eb-btn--error",
    '.eb-btn[aria-busy="true"]',
    ".eb-btn__spinner",
    ".eb-btn--primary:disabled",
    ".eb-btn--secondary:disabled",
  ]) {
    assert.ok(css.includes(sel), `버튼 규칙이 없다: ${sel}`);
  }
  // Legacy primary/secondary classes ride on the same rules.
  assert.match(css, /\.eb-btn--primary,\s*\.mem-btn-primary,\s*\.cta-gold\s*\{/);
  assert.match(css, /\.eb-btn--secondary,\s*\.mem-btn-secondary\s*\{/);
  // Primary is a solid champagne fill with charcoal text — no gradient texture.
  const primaryAt = css.indexOf(".eb-btn--primary,");
  const primaryRule = css.slice(primaryAt, css.indexOf("}", primaryAt));
  assert.match(primaryRule, /background:\s*var\(--eb-gold\)/);
  assert.match(primaryRule, /color:\s*var\(--eb-text-on-gold\)/);
  assert.doesNotMatch(primaryRule, /gradient/);

  const tsx = read(BUTTONS);
  for (const name of ["PrimaryButton", "SecondaryButton", "GhostButton", "DestructiveButton", "EbButton"]) {
    assert.match(tsx, new RegExp(`export function ${name}`), `${name} 가 export 되지 않는다`);
  }
  assert.match(tsx, /"default" \| "loading" \| "success" \| "error"/);
  assert.match(tsx, /aria-busy=\{state === "loading" \|\| undefined\}/);
});

test("상태 시스템 — loading · waiting · generating · connected · offline · success · warning · error · locked · owned · premium 이 모두 배지/도트로 존재한다", () => {
  const css = read(BRAND);
  const tones = [
    "loading",
    "waiting",
    "generating",
    "connected",
    "offline",
    "success",
    "warning",
    "error",
    "locked",
    "owned",
    "premium",
    "info",
    "neutral",
  ];
  for (const tone of tones) {
    assert.ok(css.includes(`.eb-status-badge--${tone}`), `배지 톤이 없다: ${tone}`);
    assert.ok(css.includes(`.eb-status-dot--${tone}`), `도트 톤이 없다: ${tone}`);
  }
  const tsx = read(STATUS);
  for (const tone of tones) {
    assert.ok(tsx.includes(`"${tone}"`), `StatusTone 에 ${tone} 이 없다`);
  }
  assert.match(tsx, /export function StatusDot/);
});

test("폼 컨트롤 — 입력 · 필 / 탭 · 세그먼트 · 선택 카드 · 드롭존 · 구매 컨트롤이 공유 규칙을 갖는다", () => {
  const css = read(BRAND);
  for (const sel of [
    ".eb-input {",
    ".eb-input:focus,",
    '.eb-input[aria-invalid="true"]',
    ".eb-pill {",
    ".eb-pill--active,",
    ".eb-segmented {",
    ".eb-segmented__item {",
    ".eb-selector {",
    ".eb-selector--selected,",
    ".eb-dropzone {",
    ".eb-balance-pill {",
    ".eb-price {",
    ".eb-check-mark {",
  ]) {
    assert.ok(css.includes(sel), `폼 규칙이 없다: ${sel}`);
  }
  // Pills / selectors / inputs all meet the 44px touch target.
  for (const sel of [".eb-btn {", ".eb-pill {", ".eb-selector {", ".eb-input {", ".eb-icon-btn,"]) {
    const i = css.indexOf(sel);
    const rule = css.slice(i, css.indexOf("}", i));
    assert.match(rule, /var\(--eb-touch\)/, `${sel} 가 44px 터치 타깃을 쓰지 않는다`);
  }
});

test("카드 — 일관된 라운드 · 헤어라인 · 절제된 그림자 · 선택/호버/포커스 처리", () => {
  const css = read(BRAND);
  const i = css.indexOf(".eb-card {");
  const rule = css.slice(i, css.indexOf("}", i));
  assert.match(rule, /border-radius:\s*var\(--eb-radius-lg\)/);
  assert.match(rule, /border:\s*1px solid var\(--eb-hairline\)/);
  assert.match(rule, /box-shadow:\s*var\(--eb-shadow-1\)/);
  assert.ok(css.includes(".eb-card--interactive:hover"));
  assert.ok(css.includes(".eb-card--selected,"));
  assert.ok(css.includes(".eb-card--interactive:focus-visible"));
});

// ── 3. Motion & accessibility ────────────────────────────────────────────────

test("prefers-reduced-motion 을 전역으로 존중한다 — 스피너는 계속 돈다", () => {
  const css = read(BRAND);
  const at = css.indexOf("@media (prefers-reduced-motion: reduce)");
  assert.ok(at > 0, "전역 reduced-motion 규칙이 없다");
  const block = css.slice(at);
  assert.match(block, /\*,\s*\*::before,\s*\*::after\s*\{[^}]*animation-duration:\s*0\.01ms !important/);
  assert.match(block, /transition-duration:\s*0\.01ms !important/);
  assert.match(block, /\.eb-spinner,\s*\.eb-btn__spinner,\s*\.animate-spin\s*\{[^}]*animation-iteration-count:\s*infinite !important/);

  // framer-motion follows the OS preference too.
  const main = read(MAIN);
  assert.match(main, /<MotionConfig reducedMotion="user">/);
});

test("키보드 포커스가 항상 보인다 — 골드 포커스 링", () => {
  const css = read(BRAND);
  assert.match(css, /:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--eb-gold\)/);
  assert.match(css, /\.eb-btn:focus-visible[\s\S]*?box-shadow:\s*var\(--eb-focus-ring\)/);
});

// ── 4. Old theme is gone from the shared layer ───────────────────────────────

test("포화된 SaaS 색 · iOS 시스템 색 · 잉크 블랙이 스타일시트에 남아 있지 않다", () => {
  const banned = [
    /#667eea/i,
    /#764ba2/i,
    /#00d4ff/i,
    /gradient-eternal/,
    /#34c759/i,
    /#ff453a/i,
    /#ff3b30/i,
    /#64b5f6/i,
    /#050506/i,
    /#0a0a0a/i,
    /#121214/i,
    /background:\s*#000\b/,
  ];
  for (const file of STYLE_FILES) {
    const css = read(file);
    for (const re of banned) {
      assert.doesNotMatch(css, re, `${file} 에 옛 색이 남아 있다: ${re}`);
    }
  }
});

test("앱 셸과 로딩 페이지가 아이보리다 — 잉크 블랙 배경이 없다", () => {
  const shell = read(APP_SHELL);
  assert.doesNotMatch(shell, /bg-\[#0a0a0a\]/);
  assert.match(shell, /app-shell__surface/);
  const foundation = read(FOUNDATION);
  const surfaceAt = foundation.indexOf(".app-shell__surface {");
  assert.ok(surfaceAt > 0);
  assert.match(foundation.slice(surfaceAt, foundation.indexOf("}", surfaceAt)), /background:\s*var\(--eb-bg\)/);
  const html = read(HTML);
  assert.doesNotMatch(html, /background:\s*#000/);
  assert.match(html, /#f7f2e9/i);
});

test("safe-area 는 한 번만 더해진다 — 표준 헤더/푸터 오프셋 토큰", () => {
  const foundation = read(FOUNDATION);
  assert.match(foundation, /--eb-header-top:\s*max\(2\.75rem, var\(--eb-safe-top\)\)/);
  assert.match(foundation, /--eb-footer-bottom:\s*max\(1\.25rem, var\(--eb-safe-bottom\)\)/);
  const headerAt = foundation.indexOf(".eb-screen-header {");
  assert.match(foundation.slice(headerAt, foundation.indexOf("}", headerAt)), /padding-top:\s*var\(--eb-header-top\)/);
});
