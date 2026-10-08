/**
 * Phase 11B-4 follow-up — 테마 선택 그리드의 **카드 썸네일** 회귀 방지.
 *
 * 그리드 재설계 직후 카드가 "이름만 있는 빈 상자"로 보였다. 원인은 두 가지였다:
 *   1. theme-thumbs 의 원본은 수 MB 풀사이즈 사진인데(캐러셀은 ±1 카드만
 *      로드해 지연이 가려졌다) 그리드는 11장을 한 번에 요청하고, 사진이 오기
 *      전에 보여 줄 테마 바탕이 없었다.
 *   2. 커스텀 카드가 카탈로그의 자리표시자 에셋 대신 아이콘만 그렸다.
 *
 * 여기서는 (a) 카탈로그 헬퍼 getThemeCardThumb 가 모든 테마에 **실제 존재하는**
 * 에셋을 돌려주는지, (b) 화면이 그 헬퍼만 쓰고 경로를 따로 들고 있지 않은지,
 * (c) 선택 표시·커스텀 카드·"기기 명령 없음" 보장이 그대로인지 지킨다.
 * JSX 는 node --test 로 렌더할 수 없으므로 이 스위트의 관례대로 순수 함수는
 * 직접 실행하고 배선은 소스를 읽어 확인한다.
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

import { groupPremiumThemes } from "./theme-groups.ts";
import { indexOffers, themeRow, type ThemeOffer } from "./theme-ownership.ts";
import {
  CUSTOM_PHOTO_BG_THEME_KEY,
  ORIGINAL_PHOTO_THEME_KEY,
  getThemeCardThumb,
  memorialThemes,
  premiumMemorialThemes,
} from "../components/memorial/themes.ts";

const read = (p: string) => readFileSync(p, "utf8");

const THEME_SEL = "src/components/memorial/theme-selection-screen.tsx";
const CSS = "src/styles/theme-selection.css";

const screen = read(THEME_SEL);
const css = read(CSS);

/** 카드 컴포넌트 본문만 잘라 낸다. */
function themeGridCardBody(): string {
  const start = screen.indexOf("function ThemeGridCard(");
  const end = screen.indexOf("export function ThemeSelectionScreen(", start);
  assert.ok(start > 0 && end > start, "ThemeGridCard 를 찾을 수 없다");
  return screen.slice(start, end);
}

function themeThumbBody(): string {
  const start = screen.indexOf("const ThemeThumb = (");
  const end = screen.indexOf("function ThemeStateLabel(", start);
  assert.ok(start > 0 && end > start, "ThemeThumb 를 찾을 수 없다");
  return screen.slice(start, end);
}

function cssRule(selector: string): string {
  const i = css.indexOf(`${selector} {`);
  assert.ok(i >= 0, `${selector} 규칙이 없다`);
  return css.slice(i, css.indexOf("}", i));
}

function offer(over: Partial<ThemeOffer>): ThemeOffer {
  return {
    themeKey: "x",
    free: false,
    owned: false,
    priceKrw: null,
    creditPrice: null,
    purchasable: false,
    ...over,
  };
}

const ORIGINAL = memorialThemes.find((t) => t.themeKey === ORIGINAL_PHOTO_THEME_KEY)!;
const CUSTOM = memorialThemes.find((t) => t.themeKey === CUSTOM_PHOTO_BG_THEME_KEY)!;
const FOREST = memorialThemes.find((t) => t.themeKey === "fresh_forest")!;
const BEACH = memorialThemes.find((t) => t.themeKey === "beach")!;
const AURORA = memorialThemes.find((t) => t.themeKey === "aurora")!;
/** 원본·커스텀을 뺀 "보통" 테마 — 무료와 유료 모두. */
const NORMAL = memorialThemes.filter((t) => t !== ORIGINAL && t !== CUSTOM);
const PAID = NORMAL.filter((t) => t.premium);

// ── 1. 보통 테마는 카탈로그의 실제 에셋을 그린다 ──────────────────────────────

test("보통 테마 카드는 카탈로그 thumb(실제 존재하는 파일)를 그대로 쓴다", () => {
  assert.ok(NORMAL.length >= 8, "테마 목록이 줄었다");
  for (const theme of NORMAL) {
    const src = getThemeCardThumb(theme);
    assert.equal(src, theme.thumb, `${theme.themeKey}: 카탈로그 thumb 이 아니다`);
    assert.match(src, /^\/theme-thumbs\/[\w-]+\.(jpe?g|png|webp)$/i, `${theme.themeKey}: ${src}`);
    assert.ok(existsSync(`public${src}`), `${theme.themeKey}: ${src} 파일이 public 에 없다`);
  }
});

test("Forest 와 Beach 카드는 각각의 기존 실제 프리뷰 에셋을 그린다", () => {
  assert.equal(getThemeCardThumb(FOREST), "/theme-thumbs/fresh_forest.jpg");
  assert.equal(getThemeCardThumb(BEACH), "/theme-thumbs/beach.jpg");
  assert.ok(existsSync(`public${getThemeCardThumb(FOREST)}`));
  assert.ok(existsSync(`public${getThemeCardThumb(BEACH)}`));
});

test("화면은 헬퍼로 주소를 풀고 <img> 에 그대로 넣는다 — 경로를 따로 들지 않는다", () => {
  assert.match(screen, /getThemeCardThumb,?\s/, "getThemeCardThumb 를 import 하지 않는다");
  const thumb = themeThumbBody();
  assert.match(thumb, /const src = getThemeCardThumb\(theme, originalPhoto\)/);
  assert.match(thumb, /<img src=\{src\}/);
  // 사진은 4:3 상자를 object-fit: cover 로 채운다 — 늘어나지 않는다.
  assert.match(thumb, /className="theme-grid__img"/);
  const img = cssRule(".theme-grid__img");
  assert.match(img, /object-fit:\s*cover/);
  assert.match(img, /width:\s*100%/);
  assert.match(img, /height:\s*100%/);
  assert.match(cssRule(".theme-grid__thumb"), /aspect-ratio:\s*4 \/ 3/);
  // 화면 어디에도 하드코딩된 썸네일 경로가 없다.
  assert.doesNotMatch(screen, /\/theme-thumbs\//);
});

test("사진이 오기 전·에셋이 정말 없을 때만 보이는 바탕은 테마 gradient 다 — 새 이미지를 만들지 않는다", () => {
  const thumb = themeThumbBody();
  // 바탕(gradient)이 사진 **아래**에 깔린다: 소스 순서가 곧 z-order 다.
  const base = thumb.indexOf("theme-grid__thumb-base");
  const img = thumb.indexOf("<img");
  assert.ok(base > 0 && img > base, "gradient 바탕이 사진 아래에 있지 않다");
  assert.match(thumb, /bg-gradient-to-b \$\{theme\.gradient\}/);
  // 사진이 있을 때는 무조건 그린다 — 바탕만 남는 경우는 src 가 빈 경우뿐이다.
  assert.match(thumb, /\{src \? \(\s*<img/);
  // 언레이어드 규칙이 background 를 건드리면 유틸리티 gradient 를 덮어 버린다.
  assert.doesNotMatch(cssRule(".theme-grid__thumb-base"), /background/);
  for (const theme of memorialThemes) {
    assert.match(theme.gradient, /^from-\S+ via-\S+ to-\S+$/, `${theme.themeKey}: gradient 가 없다`);
  }
});

// ── 2. 유료 테마도 같은 에셋을 그린다 ───────────────────────────────────────

test("잠긴 유료 테마 카드도 실제 썸네일을 그린다 — 잠금은 본문 상태 문구가 말한다", () => {
  assert.ok(PAID.length >= 3, "유료 테마가 줄었다");
  const offers = indexOffers(
    PAID.map((t) => offer({ themeKey: t.themeKey, owned: false, creditPrice: 5, purchasable: true }))
  );
  const { lockedThemes } = groupPremiumThemes(premiumMemorialThemes, offers);
  for (const theme of PAID) {
    assert.ok(lockedThemes.includes(theme), `${theme.themeKey} 가 잠긴 그룹에 없다`);
    assert.equal(themeRow(theme, offers).action, "buy");
    const src = getThemeCardThumb(theme);
    assert.equal(src, theme.thumb);
    assert.ok(existsSync(`public${src}`), `${theme.themeKey}: ${src} 파일이 없다`);
  }

  // 카드는 그룹/소유 상태와 무관하게 **항상** 같은 ThemeThumb 를 그린다.
  const card = themeGridCardBody();
  assert.equal((card.match(/<ThemeThumb /g) ?? []).length, 1);
  const thumbAt = card.indexOf("<ThemeThumb ");
  const before = card.slice(card.indexOf('<div className="theme-grid__thumb">'), thumbAt);
  assert.doesNotMatch(before, /\?\s*\(?\s*$/, "ThemeThumb 가 조건부로 그려진다");
  assert.doesNotMatch(card, /group === "premium"|row\.state === "not-owned"[^\n]*ThemeThumb/);
  // 소유 상태 배지는 사진 위가 아니라 본문에 있어 항상 읽힌다.
  const body = card.slice(card.indexOf('<div className="theme-grid__body">'));
  assert.match(body, /<ThemeStateLabel theme=\{theme\} offers=\{offers\} tc=\{tc\} \/>/);
  assert.match(screen, /<Lock className="w-3 h-3"/);
});

test("유료 Aurora 카드도 잠금 상태와 무관하게 기존 프리뷰 에셋을 그린다", () => {
  assert.equal(AURORA.premium, true);
  assert.equal(getThemeCardThumb(AURORA), "/theme-thumbs/aurora.jpg");
  assert.ok(existsSync(`public${getThemeCardThumb(AURORA)}`));
});

// ── 3. 선택 상태가 사진 위에서 살아남는다 ───────────────────────────────────

test("선택된 카드는 금색 외곽선 + 사진 위 체크 표시를 유지한다", () => {
  const card = themeGridCardBody();
  assert.match(card, /aria-pressed=\{selected\}/);
  assert.match(card, /selected \? " theme-grid__card--selected" : ""/);
  // 체크는 썸네일 안, 사진 **뒤(위)** 에 온다.
  const thumbStart = card.indexOf('<div className="theme-grid__thumb">');
  const thumbEnd = card.indexOf('<div className="theme-grid__body">');
  const thumb = card.slice(thumbStart, thumbEnd);
  assert.ok(thumb.indexOf("<ThemeThumb ") < thumb.indexOf("eb-check-mark theme-grid__check"));
  assert.match(thumb, /\{selected \? \(\s*<div className="eb-check-mark theme-grid__check">/);

  assert.match(cssRule(".theme-grid__card--selected"), /border-color:\s*var\(--eb-gold\)/);
  assert.match(cssRule(".theme-grid__card--selected"), /box-shadow:[^;]*var\(--eb-gold\)/);
  assert.match(cssRule(".theme-grid__check"), /box-shadow:[^;]*var\(--eb-surface\)/);
  // 체크·배지는 사진(z-index auto)보다 위에 쌓인다.
  assert.match(css, /\.theme-grid__check,\s*\.theme-grid__badge \{[^}]*z-index:\s*2/);
});

test("썸네일은 카드의 둥근 모서리에 잘리고, 모바일 2열·데스크톱 3/4열을 유지한다", () => {
  const card = cssRule(".theme-grid__card");
  assert.match(card, /border-radius:\s*var\(--eb-radius-lg\)/);
  assert.match(card, /overflow:\s*hidden/);
  assert.match(cssRule(".theme-grid__thumb"), /overflow:\s*hidden/);
  assert.match(cssRule(".theme-select__grid"), /grid-template-columns:\s*repeat\(2,\s*1fr\)/);
  assert.match(css, /@container \(min-width:\s*640px\)[\s\S]*?\.theme-select__grid \{[\s\S]*?repeat\(3,\s*1fr\)/);
  assert.match(css, /@container \(min-width:\s*960px\)[\s\S]*?\.theme-select__grid \{[\s\S]*?repeat\(4,\s*1fr\)/);
});

// ── 4. 커스텀 카드 ──────────────────────────────────────────────────────────

test("커스텀 카드는 카탈로그의 자리표시자 에셋을 그리고, 꽃 배지와 '나만의 배경' 안내를 단다", () => {
  assert.equal(CUSTOM.requiresGeneration, true);
  const src = getThemeCardThumb(CUSTOM);
  assert.equal(src, CUSTOM.thumb);
  assert.ok(existsSync(`public${src}`), `${src} 파일이 없다`);

  const card = themeGridCardBody();
  assert.match(card, /const isCustom = Boolean\(theme\.requiresGeneration\)/);
  assert.match(card, /const isCustomPlaceholder = isCustom && !hasCustomAsset/);
  assert.match(card, /\{isCustom \? \(\s*<div className="theme-grid__badge" aria-hidden>\s*<Flower2/);
  assert.match(card, /isCustomPlaceholder \? " theme-grid__card--custom" : ""/);
  assert.match(card, /isCustomPlaceholder \? tc\.customCardTitle : themeLabel\(theme\)/);
  assert.match(card, /\{tc\.customCardHint\}/);
  assert.match(cssRule(".theme-grid__card--custom"), /border-style:\s*dashed/);
  // 아이콘만 있는 예전 자리표시자 상자는 사라졌다.
  assert.doesNotMatch(screen, /theme-grid__custom-icon/);
  assert.doesNotMatch(css, /theme-grid__custom-icon/);

  // 커스텀 배경 흐름 자체는 그대로: 생성 전이면 primary CTA 가 생성 화면으로 보낸다.
  assert.match(screen, /hasCustomAsset=\{theme\.requiresGeneration \? Boolean\(getEffectiveBgVideo\(theme\)\) : true\}/);
  assert.match(screen, /if \(needsCustomBackground\) \{\s*onSelectCustomBackground\?\.\(previewTheme\);/);
});

test("원본 사진 카드는 주입된 사진을, 없으면 빈 바탕을 쓴다 — 고정 에셋으로 속이지 않는다", () => {
  assert.equal(getThemeCardThumb(ORIGINAL, "data:image/png;base64,AAAA"), "data:image/png;base64,AAAA");
  assert.equal(getThemeCardThumb(ORIGINAL, null), "");
  assert.equal(getThemeCardThumb(ORIGINAL), "");
});

// ── 5. 썸네일을 그리거나 카드를 눌러도 기기로는 아무것도 가지 않는다 ────────

test("썸네일·카드는 기기 명령을 모른다 — 이미지 로드/탭에 부작용이 없다", () => {
  assert.doesNotMatch(screen, /sendDeviceCommand|device-command-api|triggerThemeOnDevice|scheduleThemeBackgroundSync/);
  const thumb = themeThumbBody();
  assert.doesNotMatch(thumb, /onLoad|onError|onClick|fetch\(|useEffect/);
  const card = themeGridCardBody();
  assert.match(card, /onClick=\{onSelect\}/);
  assert.doesNotMatch(card, /fetch\(|useEffect|ownership\.buy|onContinue/);
  // 카드 탭 → selectTheme 는 강조만 바꾼다.
  const i = screen.indexOf("const selectTheme");
  const j = screen.indexOf("const activeTheme =", i);
  assert.match(screen.slice(i, j), /setHighlightTheme\(theme\.id\)/);
  assert.doesNotMatch(screen.slice(i, j), /sendDeviceCommand|onSelectTheme|onContinue/);
});
