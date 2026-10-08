/**
 * Phase 5 — Theme Selection redesign, extended by Phase 11B-4 (structural
 * grid redesign). Focused regression coverage for the behaviors the redesign
 * must not break: three ownership states, the credit purchase flow,
 * checkout/return restoration, the "browsing never sends a device command"
 * guarantee, the mobile/desktop layout contract (stepper + grid instead of
 * the old live-preview stage + carousel), and the My Library embedding.
 *
 * Style follows the rest of this suite: pure functions (theme-groups.ts,
 * theme-ownership.ts, create-flow-steps.ts) are exercised directly; wiring
 * that only exists as JSX is verified by reading the component/CSS source,
 * the same approach theme-purchase-flow.test.ts and
 * library-preview-flow-wiring.test.ts use.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { groupPremiumThemes } from "./theme-groups.ts";
import { indexOffers, themeRow, type ThemeOffer } from "./theme-ownership.ts";
import { premiumMemorialThemes } from "../components/memorial/themes.ts";

const read = (p: string) => readFileSync(p, "utf8");

const THEME_SEL = "src/components/memorial/theme-selection-screen.tsx";
const STEPPER = "src/components/memorial/create-flow-stepper.tsx";
const APP = "src/app/EternalBeamApp.tsx";
const USE_OWNERSHIP = "src/components/memorial/use-theme-ownership.ts";
const STORE_API = "src/lib/theme-store-api.ts";
const RETURN_SCREEN = "src/components/memorial/theme-purchase-return-screen.tsx";
const MY_LIBRARY = "src/components/memorial/my-library-screen.tsx";
const CSS = "src/styles/theme-selection.css";

const AURORA = premiumMemorialThemes.find((t) => t.themeKey === "aurora")!;
const SUNSET = premiumMemorialThemes.find((t) => t.themeKey === "sunset")!;
const CUSTOM = premiumMemorialThemes.find((t) => t.themeKey === "custom_photo_bg")!;

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

// ── 1. 무료 테마 선택 ────────────────────────────────────────────────────────

test("무료 테마: 서버 카탈로그가 없어도 usable 이고 바로 미리보기로 이어진다", () => {
  const row = themeRow({ themeKey: "fresh_forest", premium: false }, new Map());
  assert.equal(row.state, "free");
  assert.equal(row.action, "use");
  assert.equal(row.usable, true);

  // 화면의 primary action 은 usable 한 테마를 ownership.buy 없이 바로 확정한다.
  const src = read(THEME_SEL);
  const start = src.indexOf("const handlePrimaryAction");
  const end = src.indexOf("// 커스텀 배경", start);
  const body = src.slice(start, end);
  assert.match(body, /if \(!activeRow\.usable\) return;/);
  const useBranch = body.slice(body.indexOf("if (!activeRow.usable) return;"));
  assert.match(useBranch, /commitTheme\(previewTheme\)/);
  assert.match(useBranch, /onContinue\(previewTheme\.id\)/);
});

// ── 2. 보유한 프리미엄 테마 ──────────────────────────────────────────────────

test("보유한 프리미엄 테마: Owned 그룹에 들어가고 잠긴 목록엔 없다", () => {
  const offers = indexOffers([
    offer({ themeKey: AURORA.themeKey, owned: true, creditPrice: 5, purchasable: false }),
  ]);
  const { ownedThemes, lockedThemes, customThemes } = groupPremiumThemes(
    premiumMemorialThemes,
    offers
  );
  assert.ok(ownedThemes.some((t) => t.themeKey === AURORA.themeKey));
  assert.ok(!lockedThemes.some((t) => t.themeKey === AURORA.themeKey));
  assert.ok(!customThemes.some((t) => t.themeKey === AURORA.themeKey));

  const row = themeRow(AURORA, offers);
  assert.equal(row.state, "owned");
  assert.equal(row.action, "use");
  assert.equal(row.usable, true);
});

// ── 3. 잠긴 유료 테마 ───────────────────────────────────────────────────────

test("잠긴 유료 테마: Premium(잠김) 그룹에 들어가고 구매 CTA 로만 풀린다", () => {
  const offers = indexOffers([
    offer({ themeKey: SUNSET.themeKey, owned: false, creditPrice: 5, purchasable: true }),
  ]);
  const { ownedThemes, lockedThemes } = groupPremiumThemes(premiumMemorialThemes, offers);
  assert.ok(lockedThemes.some((t) => t.themeKey === SUNSET.themeKey));
  assert.ok(!ownedThemes.some((t) => t.themeKey === SUNSET.themeKey));

  const row = themeRow(SUNSET, offers);
  assert.equal(row.action, "buy");
  assert.equal(row.usable, false);

  const src = read(THEME_SEL);
  const start = src.indexOf("const handlePrimaryAction");
  const end = src.indexOf("// 커스텀 배경", start);
  const body = src.slice(start, end);
  assert.match(body, /if \(activeRow\.action === "buy"\)/);
  assert.match(body, /ownership\.buy\(previewTheme\.themeKey\)/);
});

test("requiresGeneration 테마는 보유 여부와 무관하게 항상 Custom 그룹이다", () => {
  const offers = indexOffers([
    offer({ themeKey: CUSTOM.themeKey, owned: true, creditPrice: 5, purchasable: false }),
  ]);
  const { customThemes, ownedThemes } = groupPremiumThemes(premiumMemorialThemes, offers);
  assert.ok(customThemes.some((t) => t.themeKey === CUSTOM.themeKey));
  assert.ok(!ownedThemes.some((t) => t.themeKey === CUSTOM.themeKey));
});

// ── 4. Beam Credit 구매 흐름은 그대로다 ─────────────────────────────────────

test("구매는 여전히 크레딧 하나뿐이다 — KRW 결제창을 다시 열지 않는다", () => {
  const hook = read(USE_OWNERSHIP);
  assert.match(hook, /purchaseThemeWithCredits\(\{ themeKey, accessToken: token \}\)/);
  assert.doesNotMatch(hook, /startThemeCheckout|openThemePaymentWindow/);

  const api = read(STORE_API);
  assert.match(api, /themes\/purchase-with-credits/);
  assert.doesNotMatch(api, /themes\/checkout/);
});

// ── 5. 결제 복귀가 구매한 테마를 복원한다 ───────────────────────────────────

test("레거시 결제 복귀는 확정 후 테마 선택 화면으로 돌아가고, 그 카탈로그가 OWNED 를 반영한다", () => {
  const returned = read(RETURN_SCREEN);
  assert.match(returned, /confirmThemePurchaseReturn\(r\.themeKey\)/);

  const app = read(APP);
  assert.match(app, /if \(readThemePurchaseReturnState\(\)\) return 'themeSelection'/);

  // 화면 자체는 복귀를 특별 취급하지 않는다 — useThemeOwnership 이 다시
  // 카탈로그를 읽으면 서버가 이미 owned:true 를 돌려준다. 특별 분기가 새로
  // 생기면 이 화면과 일반 진입 경로가 갈라진다.
  const screen = read(THEME_SEL);
  assert.doesNotMatch(screen, /theme-purchase-return|ThemePurchaseReturn/);
});

// ── 6. 테마를 보거나 고르는 것만으로는 기기에 아무것도 보내지 않는다 ────────

test("테마 선택 화면은 sendDeviceCommand 를 모른다 — 탐색/선택이 기기로 새지 않는다", () => {
  const src = read(THEME_SEL);
  assert.doesNotMatch(src, /sendDeviceCommand/);
  assert.doesNotMatch(src, /device-command-api/);
  assert.doesNotMatch(src, /triggerThemeOnDevice|scheduleThemeBackgroundSync/);
});

test("selectTheme(카드 탭)은 기기·서버 부작용이 없다 — 강조만 한다", () => {
  const src = read(THEME_SEL);
  const start = src.indexOf("const selectTheme");
  const end = src.indexOf("const activeTheme =", start);
  const body = src.slice(start, end);
  assert.match(body, /setHighlightTheme\(theme\.id\)/);
  assert.doesNotMatch(body, /sendDeviceCommand|ownership\.buy|onContinue|onSelectTheme/);
});

// ── 7. 그리드 레이아웃 — 캐러셀/큰 미리보기를 대체한다 ──────────────────────

test("큰 라이브 미리보기와 캐러셀이 사라졌다 — 준비 상태의 작은 누끼 표시는 허용한다", () => {
  const src = read(THEME_SEL);
  assert.doesNotMatch(src, /theme-select__stage/);
  assert.doesNotMatch(src, /theme-selection-screen__preview/);
  assert.doesNotMatch(src, /theme-selection-screen__carousel-card/);
  assert.doesNotMatch(src, /snap-x snap-mandatory/);
  assert.doesNotMatch(src, /<PetIdleDisplay/);
  assert.match(src, /theme-select__prepared-pet-stage/);
  assert.doesNotMatch(src, /<ThemeBackgroundVideo/);
  assert.match(src, /theme-select__grid/);
  assert.match(src, /<ThemeGridCard/);
});

test("본문이 stepper 칸/메인 칸으로 나뉜다 — 컨트롤이 반응형 그리드로 그려진다", () => {
  const src = read(THEME_SEL);
  assert.match(src, /theme-select__body/);
  assert.match(src, /theme-select__stepper/);
  assert.match(src, /theme-select__main/);
  assert.match(src, /theme-select__grid/);
  assert.match(src, /theme-select__filters/);
});

test("두 칸 전환(스테퍼 세로 목록 ↔ 압축 바)은 뷰포트가 아니라 화면 자신의 폭(컨테이너 쿼리)을 기준으로 한다", () => {
  // My Library 는 이 화면을 wide 하지 않은 520px 스테이지 안에서도 그대로
  // 재사용한다(아래 9번) — 뷰포트 media query 만 쓰면 그 임베딩에서 2단
  // 레이아웃이 좁은 상자 안에 욱여넣어진다.
  const css = read(CSS);
  assert.match(css, /\.theme-selection-screen\s*\{[^}]*container-type:\s*inline-size;/);
  assert.match(css, /@container[^{]*\{\s*\.theme-select__body\s*\{[\s\S]*flex-direction:\s*row;/);
  assert.doesNotMatch(css, /@media \(min-width: 1024px\)/, "뷰포트 브레이크포인트로 되돌아갔다");
});

test("테마 그리드는 모바일 2열에서 시작해 컨테이너 폭에 따라 3열·4열로 넓어진다", () => {
  const css = read(CSS);
  const baseAt = css.indexOf(".theme-select__grid {");
  const baseRule = css.slice(baseAt, css.indexOf("}", baseAt));
  assert.match(baseRule, /grid-template-columns:\s*repeat\(2,\s*1fr\)/);
  assert.match(css, /@container \(min-width: 640px\)[\s\S]*grid-template-columns:\s*repeat\(3,\s*1fr\)/);
  assert.match(css, /@container \(min-width: 960px\)[\s\S]*grid-template-columns:\s*repeat\(4,\s*1fr\)/);
});

test("카드 본문은 44px 터치 타깃을 지킨다", () => {
  const css = read(CSS);
  const i = css.indexOf(".theme-grid__body {");
  const rule = css.slice(i, css.indexOf("}", i));
  assert.match(rule, /min-height:\s*44px/);
});

test("고정 CTA 높이를 실제로 재서 스크롤 하단에 예약한다 — 마지막 카드가 footer 아래에 숨지 않는다", () => {
  const src = read(THEME_SEL);
  assert.match(src, /const scrollRef = useRef<HTMLDivElement>\(null\)/);
  assert.match(src, /const footerRef = useRef<HTMLDivElement>\(null\)/);
  assert.match(src, /new ResizeObserver\(syncFooterReserve\)/);
  assert.match(src, /--theme-select-footer-reserve/);
  assert.match(src, /ref=\{scrollRef\}/);
  assert.match(src, /ref=\{footerRef\}/);

  const css = read(CSS);
  const i = css.indexOf(".theme-select__scroll {");
  const rule = css.slice(i, css.indexOf("}", i));
  assert.match(rule, /padding-bottom:\s*var\(--theme-select-footer-reserve\)/);
  assert.match(rule, /scroll-padding-bottom:\s*var\(--theme-select-footer-reserve\)/);
});

// ── 8. Create-flow stepper ──────────────────────────────────────────────────

test("Create 흐름 스테퍼는 computeCreateFlowSteps 로 실제 상태에서 계산한다 — 진행률을 지어내지 않는다", () => {
  const src = read(THEME_SEL);
  assert.match(src, /import \{ computeCreateFlowSteps \} from "@\/lib\/create-flow-steps";/);
  assert.match(src, /computeCreateFlowSteps\(hasPreparedPet\)/);
  assert.match(src, /const hasPreparedPet = cutoutReadiness === "ready"/);
});

test("Create 흐름 스테퍼는 My Library(테마 변경) 흐름에서는 렌더되지 않는다", () => {
  const src = read(THEME_SEL);
  const i = src.indexOf("{!isLibraryFlow ? (");
  assert.ok(i > 0, "isLibraryFlow 게이트를 찾을 수 없다");
  const j = src.indexOf(") : null}", i);
  const gated = src.slice(i, j);
  assert.match(gated, /<CreateFlowStepper/);
});

test("스테퍼 컴포넌트는 세로 목록과 압축 바를 모두 그리고, CSS가 어느 쪽을 보일지 정한다", () => {
  const src = read(STEPPER);
  assert.match(src, /create-flow-stepper__full/);
  assert.match(src, /create-flow-stepper__compact/);
});

// ── 9. My Library 테마 선택 흐름은 그대로다 ─────────────────────────────────

test("My Library 는 여전히 같은 props 계약으로 ThemeSelectionScreen 을 그린다", () => {
  const lib = read(MY_LIBRARY);
  const i = lib.indexOf("<ThemeSelectionScreen");
  const call = lib.slice(i, lib.indexOf("onBack", i));
  assert.match(call, /\bisLibraryFlow\b/);
  assert.match(call, /libraryPublication=\{libraryPublication\}/);
  assert.match(call, /cutoutImage=\{null\}/);
  assert.match(call, /cutoutReadiness="ready"/);
  assert.match(call, /onSelectTheme=\{setSelectedTheme\}/);
  assert.match(call, /onSelectCustomBackground=/);

  // 화면의 props 계약 자체가 좁아지거나 이름이 바뀌지 않았다.
  const screen = read(THEME_SEL);
  assert.match(screen, /isLibraryFlow\?: boolean;/);
  assert.match(screen, /libraryPublication\?: LibraryPublication \| null;/);
  assert.match(screen, /onSelectCustomBackground\?: \(theme: MemorialTheme\) => void;/);
});

test("My Library 흐름은 'Change Theme' 맥락 제목을 쓴다 — 제작 스테퍼 대신이다", () => {
  const src = read(THEME_SEL);
  assert.match(src, /isLibraryFlow \? tc\.changeThemeContext : tc\.title/);
});
