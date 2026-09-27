/**
 * Phase 11 — My Library structural redesign (grouped-by-pet grid, matching
 * the approved reference layout: header + Beam Credits/Membership summary +
 * filters, pet sections, media-first motion cards, bottom CTAs).
 *
 * Style follows theme-selection-redesign.test.ts: the layout only exists as
 * JSX/CSS, so it is verified by reading the component/CSS source rather than
 * mounting a DOM.
 *
 * This supersedes the previous drill-down redesign's pinned "pets → motions"
 * step contract (browsing is now a single grouped grid — see
 * library-preview-flow-wiring.test.ts for what's still pinned in the
 * themes/preview tail, which this redesign does not touch).
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (p: string) => readFileSync(p, "utf8");

const MY_LIBRARY = "src/components/memorial/my-library-screen.tsx";
const MY_LIBRARY_API = "src/lib/my-library-api.ts";
const CSS = "src/styles/my-library.css";
const I18N = "src/components/memorial/memorial-i18n.ts";

// ── 화면 자신의 렌더 폭 기준 반응형 (theme-selection.css 와 같은 컨벤션) ─────

test("my-library.css: 컨테이너 쿼리로 반응형을 낸다 — 뷰포트 media query 가 아니다", () => {
  const css = read(CSS);
  assert.match(css, /\.my-library\s*\{[^}]*container-type:\s*inline-size;/);
  assert.match(css, /@container[^{]*\{\s*\.my-library__hero\s*\{[\s\S]*flex-direction:\s*row;/);
  assert.doesNotMatch(css, /@media \(min-width: 1024px\)/, "뷰포트 브레이크포인트로 되돌아갔다");
});

test("my-library.css: 데스크톱에서 모션 카드가 여러 열 그리드로 넓어진다", () => {
  const css = read(CSS);
  const i = css.indexOf("@container (min-width: 860px)");
  assert.ok(i > 0, "데스크톱 분기를 찾을 수 없다");
  const block = css.slice(i);
  assert.match(block, /\.my-library__cards-grid\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fill/);
});

test("my-library.css: 펫 재시도·카드 메뉴·추가 반려 버튼이 44px 터치 타깃을 지킨다", () => {
  const css = read(CSS);
  for (const selector of [
    ".my-library__pet-retry {",
    ".my-library__chip {",
    ".my-library__filter-chip {",
    ".my-library__select {",
    ".my-library__card-overflow {",
    ".my-library__card-menu-item {",
    ".my-library__add-pet {",
    ".my-library__create-btn {",
  ]) {
    const i = css.indexOf(selector);
    assert.ok(i > 0, `규칙을 찾을 수 없다: ${selector}`);
    const rule = css.slice(i, css.indexOf("}", i));
    assert.match(rule, /min-height:\s*44px/, `${selector} 가 44px 미달이다`);
  }
});

// ── 구조: 헤더(제목/부제/크레딧/멤버십/CTA) → 필터 → 펫 그룹 → 하단 CTA ──────

test("My Library: 헤더에 Beam Credits 요약·멤버십 요약·Create New CTA 가 있다", () => {
  const src = read(MY_LIBRARY);
  assert.match(src, /my-library__chip--credits/);
  assert.match(src, /my-library__chip--membership/);
  assert.match(src, /onClick=\{onCreateNew\}/);
});

test("My Library: 필터 행에 All Pets·개별 펫·모션 필터·정렬이 있다", () => {
  const src = read(MY_LIBRARY);
  assert.match(src, /\{t\.allPets\}/);
  assert.match(src, /setPetFilter\(id\.petId\)/);
  assert.match(src, /\{t\.allMotions\}/);
  assert.match(src, /setSortOrder\(e\.target\.value as SortOrder\)/);
});

test("My Library: 펫별로 섹션을 묶어 그린다 — pet → motion 드릴다운이 없는 단일 그리드다", () => {
  const src = read(MY_LIBRARY);
  assert.match(src, /my-library__pet-section/);
  assert.match(src, /my-library__cards-grid/);
  // 예전 2단계(펫 목록 → 모션 목록) 드릴다운의 흔적이 없다.
  assert.doesNotMatch(src, /"pets"\s*\|\s*"motions"/, "예전 pets/motions 드릴다운 단계가 남아 있다");
});

test("My Library: 하단에 '다른 반려 추가'와 '새 추억 만들기' 배너가 있다", () => {
  const src = read(MY_LIBRARY);
  assert.match(src, /my-library__add-pet\b/);
  assert.match(src, /my-library__banner\b/);
  assert.match(src, /\{t\.addAnotherPet\}/);
  assert.match(src, /\{t\.bannerCta\}/);
});

// ── 카드 상태: Included / Owned / Generating / Unavailable ──────────────────

test("My Library: 카드 상태가 소유/접근 데이터에서 정직하게 파생된다 — 지어내지 않는다", () => {
  const src = read(MY_LIBRARY);
  const i = src.indexOf("function statusForMotion(");
  assert.ok(i > 0, "statusForMotion 을 찾을 수 없다");
  const body = src.slice(i, src.indexOf("\n}", i));
  assert.match(body, /accessState === "unavailable"/);
  assert.match(body, /ownershipType === "included"/);
});

test("My Library: 아직 생성되지 않은 멤버십 모션을 카드로 지어내지 않는다 — /api/v1/library 는 커밋된 것만 돌려준다", () => {
  const src = read(MY_LIBRARY);
  assert.match(src, /fetchLibraryForPet/);
  assert.doesNotMatch(src, /status:\s*"locked"/, "아직 안 만든 모션을 locked 카드로 지어내고 있다");
});

test("My Library: Generating 카드는 실제 재개 마커/폴링 데이터에서만 만들어진다", () => {
  const src = read(MY_LIBRARY);
  assert.match(src, /readActiveGeneration/, "BREATHING 재개 마커를 읽지 않는다");
  assert.match(src, /<BehaviorLibrary/, "프리미엄 생성 중 상태를 공유 폴링 UI 에서 읽지 않는다");
  const behavior = read("src/components/memorial/behavior-library.tsx");
  assert.match(behavior, /item\.status === "generating"/);
});

// ── 부분 로딩 회복력: 한 펫의 실패가 나머지를 가리지 않는다 (그대로 유지) ────

test("My Library: 펫별로 독립된 로딩 상태를 추적한다 — 전체를 하나의 loading/error 로 묶지 않는다", () => {
  const src = read(MY_LIBRARY);
  assert.match(src, /PetLibraryState/, "펫별 상태 타입이 없다");
  assert.match(src, /"loading"/);
  assert.match(src, /"ready"/);
  assert.match(src, /"error"/);
  assert.match(src, /fetchLibraryPetIds/);
  assert.match(src, /fetchLibraryForPet/);
});

test("My Library: 펫 하나의 재시도가 그 펫만 다시 부른다 — 전체를 다시 불러오지 않는다", () => {
  const src = read(MY_LIBRARY);
  const i = src.indexOf("const loadPetMotions = useCallback((petId: string) => {");
  assert.ok(i > 0, "loadPetMotions 를 찾을 수 없다");
  const j = src.indexOf("}, []);", i);
  const body = src.slice(i, j);
  assert.match(body, /fetchLibraryForPet\(petId\)/);
  assert.doesNotMatch(body, /fetchLibraryPetIds/, "펫 하나 재시도가 전체 목록도 다시 부른다");

  const retryIdx = src.indexOf("onRetry={() => loadPetMotions(id.petId)}");
  assert.ok(retryIdx > 0, "카드의 재시도 버튼이 그 함수를 쓰지 않는다");
});

test("My Library: 소유 모션이 없으면 소유 카드 섹션은 숨기되 실제 생성 제안은 유지한다", () => {
  const src = read(MY_LIBRARY);
  assert.match(src, /\{sorted\.length > 0 \? \(/);
  assert.match(src, /<BehaviorLibrary/);
});

test("My Library: 등록 목록 실패에는 재시도 버튼이 있다", () => {
  const src = read(MY_LIBRARY);
  const i = src.indexOf(": registryError ? (");
  assert.ok(i > 0);
  const block = src.slice(i, src.indexOf("resolvedPetIds.length === 0", i));
  assert.match(block, /<ErrorState/);
  assert.match(block, /onRetry=\{loadRegistry\}/);
});

// ── i18n — 하드코딩된 영문 문자열을 남기지 않는다 ───────────────────────────

test("memorial-i18n: library 섹션이 ko/en 모두에 있다", () => {
  const src = read(I18N);
  const koIdx = src.indexOf("ko: {");
  const enIdx = src.indexOf("en: {");
  assert.ok(koIdx > 0 && enIdx > 0 && koIdx < enIdx);
  const koBlock = src.slice(koIdx, enIdx);
  const enBlock = src.slice(enIdx);
  for (const block of [koBlock, enBlock]) {
    assert.match(block, /library:\s*\{/);
  }
});

test("My Library: memorialT(language).library 를 통해 문구를 가져온다 — 하드코딩된 'My Library' 문자열이 없다", () => {
  const src = read(MY_LIBRARY);
  assert.match(src, /const t = memorialT\(language\)\.library;/);
  assert.doesNotMatch(src, />My Library</, "화면 문구가 여전히 하드코딩돼 있다");
});

// ── 기존 계약은 이 리덕자인으로도 그대로다 (하위 호환) ──────────────────────

test("my-library-api: 기존 함수들이 여전히 존재하고 같은 계약을 지킨다 (하위 호환)", () => {
  const src = read(MY_LIBRARY_API);
  assert.match(src, /export async function fetchMyLibraryPets/);
  assert.match(src, /export async function fetchLibraryPetIds/);
  assert.match(src, /export async function fetchPublishedMotionsForPet/);
});

test("my-library-api: 새 집계 엔드포인트(GET /api/v1/library)를 호출한다", () => {
  const src = read(MY_LIBRARY_API);
  assert.match(src, /export async function fetchLibraryForPet/);
  assert.match(src, /\/api\/v1\/library\?pet_id=/);
});
