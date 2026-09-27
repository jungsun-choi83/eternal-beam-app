/**
 * My Library 카드 UI — (1) packed_alpha 정지 미리보기, (2) 세 점 메뉴 포털.
 *
 * 순수 규칙(배치·모드·크기)은 직접 실행하고, 렌더 구조는 my-library-redesign.test.ts
 * 와 같은 컨벤션으로 소스를 읽어 검증한다.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { placeCardMenu, MENU_GAP_PX, MENU_VIEWPORT_MARGIN_PX } from "./library-card-menu.ts";
import { fitPosterFrame, posterCanvasSize, posterRenderMode, posterSeekTime } from "./library-card-preview.ts";

const read = (p: string) => readFileSync(p, "utf8");
const SCREEN = "src/components/memorial/my-library-screen.tsx";
const POSTER = "src/components/memorial/library-motion-poster.tsx";
const CSS = "src/styles/my-library.css";

const PACKED = "https://x.supabase.co/storage/v1/object/sign/user-assets/u/p/motions/tail_wagging/v1/wan_3_standard_a1_packed.mp4?token=t";

// ── 1. packed_alpha 카드는 합성된 미리보기를 낸다 ─────────────────────────────

test("카드 미디어는 <video> 가 아니라 LibraryMotionPoster(캔버스 합성)를 그린다", () => {
  const src = read(SCREEN);
  assert.match(src, /<LibraryMotionPoster\s[\s\S]*?src=\{motion\.url\}[\s\S]*?deliveryFormat=\{motion\.deliveryFormat\}/);
  assert.doesNotMatch(src, /className="my-library__card-video"/, "원시 <video> 카드가 남아 있다");
});

test("포스터는 Preview/Composer 와 같은 drawPackedAlphaVideo 로 RGB·알파를 합성한다", () => {
  const src = read(POSTER);
  assert.match(src, /import \{[\s\S]*?drawPackedAlphaVideo[\s\S]*?\} from "@\/lib\/packed-alpha-canvas"/);
  assert.match(src, /drawPackedAlphaVideo\(ctx, video, dx, dy, drawW, drawH, scratch\)/);
  assert.match(src, /<canvas[\s\S]*?className=\{`my-library__card-poster/);
});

test("delivery_format=packed_alpha → packed 모드; 파일명 규칙은 포맷이 없을 때만 보조한다", () => {
  assert.equal(posterRenderMode("packed_alpha", "https://cdn/x.mp4"), "packed");
  assert.equal(posterRenderMode(null, PACKED), "packed");
  assert.equal(posterRenderMode(null, "https://cdn/legacy/COME_CLOSER_abc.mp4"), "plain");
  assert.equal(posterRenderMode("blackkey", PACKED), "plain");
});

// ── 2. 위/아래 vstack 원본을 그대로 노출하지 않는다 ───────────────────────────

test("packed 모드는 프레임 높이를 절반으로 잡고, 실패해도 RGB 절반만 그린다", () => {
  const src = read(POSTER);
  assert.match(src, /mode === "packed" \? Math\.floor\(video\.videoHeight \/ 2\) : video\.videoHeight/);
  // taint 폴백도 매트 절반을 노출하지 않는 drawPackedRgbHalfOnly 다.
  assert.match(src, /catch \{[\s\S]*?drawPackedRgbHalfOnly\(ctx, video, dx, dy, drawW, drawH, scratch\)/);
  // 전체 프레임 drawImage 는 plain(레거시) 분기에만 있다.
  const plain = src.slice(src.indexOf("} else {", src.indexOf('mode === "packed") {')));
  assert.match(plain, /ctx\.drawImage\(video, dx, dy, drawW, drawH\)/);
  assert.equal((src.match(/ctx\.drawImage\(video/g) ?? []).length, 1);
});

test("미리보기 프레임은 잘라내지 않고(contain) 가운데 놓는다", () => {
  // 9:16 카드에 854x480 RGB 절반 → 가로를 꽉 채우고 세로 가운데.
  const r = fitPosterFrame(270, 480, 854, 480);
  assert.equal(Math.round(r.drawW), 270);
  assert.ok(r.drawH < 480 && r.dy > 0 && Math.abs(r.dy * 2 + r.drawH - 480) < 0.01);
});

// ── 3. 재생하지 않을 때 카드는 가볍다 ─────────────────────────────────────────

test("정지 포스터는 자동 재생이 없고, 프레임을 그린 뒤 비디오를 놓아준다 (루프는 재생 이펙트에서만)", () => {
  const src = read(POSTER);
  assert.doesNotMatch(src, /autoPlay/);
  assert.match(src, /video\.removeAttribute\("src"\);\s*video\.load\(\);/);
  assert.match(src, /if \(paintFrame\(canvas, video, mode, scratch\)\) \{\s*update\("ready"\);\s*releaseSource\(video\);/);
  assert.match(src, /muted\s+playsInline/);
  const playingEffect = src.slice(src.indexOf("// ── 인라인 재생"));
  assert.match(playingEffect, /video\.loop = true;/);
  assert.doesNotMatch(src.slice(0, src.indexOf("// ── 인라인 재생")), /video\.loop = true/);
});

// ── Play 버튼: 탐색하지 않고 인라인 재생 ─────────────────────────────────────

test("Play 클릭은 카드 탐색으로 버블링되지 않는다 — 형제 버튼 + preventDefault + stopPropagation", () => {
  const src = read(SCREEN);
  // 재생 버튼은 탐색 버튼(.my-library__card-hit) 안이 아니라 미디어 컨테이너의 형제다.
  const mediaStart = src.indexOf('<div className="my-library__card-media">');
  const media = src.slice(mediaStart, src.indexOf("</div>", src.indexOf("className={`my-library__card-play", mediaStart)));
  const hitClose = media.indexOf("</button>");
  const playOpen = media.indexOf('className={`my-library__card-play');
  assert.ok(hitClose > 0 && playOpen > hitClose, "Play 버튼이 탐색 버튼 안에 중첩돼 있다");
  assert.match(media, /onClick=\{\(e\) => \{\s*e\.preventDefault\(\);\s*e\.stopPropagation\(\);\s*onTogglePlay\(\);\s*\}\}/);
  // Play 는 onSelect(테마 선택)를 절대 부르지 않는다.
  const playBlock = media.slice(playOpen);
  assert.doesNotMatch(playBlock, /onSelect|setStep|selectMotion/);
  assert.doesNotMatch(src, /<span className="my-library__card-play" aria-hidden>/, "예전 aria-hidden span 재생 아이콘이 남아 있다");
});

test("Play 클릭은 인라인 미리보기를 켠다 — playing 이 포스터로 전달돼 같은 합성기로 매 프레임 그린다", () => {
  const src = read(SCREEN);
  assert.match(src, /<LibraryMotionPoster[\s\S]*?playing=\{playing\}/);
  assert.match(src, /playing=\{activePlaybackId === motion\.id\}/);
  assert.match(src, /onTogglePlay=\{\(\) => onTogglePlayback\(motion\.id\)\}/);
  const poster = read(POSTER);
  const playingEffect = poster.slice(poster.indexOf("// ── 인라인 재생"));
  assert.match(playingEffect, /video\.play\(\)\.catch\(/);
  assert.match(playingEffect, /paintFrame\(canvas, video, mode, scratch\);\s*raf = requestAnimationFrame\(draw\);/);
  // 재생 중에도 같은 paintFrame → drawPackedAlphaVideo 경로다 (원본 vstack 노출 없음).
  assert.match(poster, /function paintFrame\([\s\S]*?drawPackedAlphaVideo\(ctx, video, dx, dy, drawW, drawH, scratch\)/);
  // Play/Pause 토글 + 접근성.
  assert.match(src, /aria-pressed=\{playing\}/);
  assert.match(src, /aria-label=\{playing \? t\.previewPause\(name\) : t\.previewPlay\(name\)\}/);
  assert.match(src, /\{playing \? <Pause[\s\S]*?: <Play/);
});

test("카드 본체 클릭은 여전히 탐색한다 — onSelect → selectMotion → 테마 선택", () => {
  const src = read(SCREEN);
  assert.match(src, /className="my-library__card-hit"[\s\S]*?onClick=\{clickable \? onSelect : undefined\}/);
  assert.match(src, /const selectMotion = useCallback\(\(pet: LibraryPetId, motion: LibraryMotion\) => \{[\s\S]*?storePreviewPipeline\(pet, motion\);[\s\S]*?setStep\("themes"\);/);
});

test("동시에 한 카드만 재생한다 — 화면 단일 activePlaybackId, 다른 카드 Play 는 이전 카드를 멈춘다", () => {
  const src = read(SCREEN);
  assert.match(src, /const \[activePlaybackId, setActivePlaybackId\] = useState<string \| null>\(null\);/);
  assert.match(src, /setActivePlaybackId\(\(current\) => \(current === motionId \? null : motionId\)\);/);
  // 라이브러리를 떠나면 멈춘다.
  assert.match(src, /if \(step !== "library"\) setActivePlaybackId\(null\);/);
  // 재생 상태는 프롭으로 내려간다 — 카드마다 로컬 state 로 재생을 갖지 않는다.
  assert.doesNotMatch(src.slice(src.indexOf("function MotionCard("), src.indexOf("function GeneratingMotionCard(")), /useState<boolean>|setPlaying/);
});

test("인라인 재생도 카드의 packed_alpha 자산·포맷을 그대로 쓴다", () => {
  const src = read(SCREEN);
  assert.match(src, /<LibraryMotionPoster\s+src=\{motion\.url\}\s+deliveryFormat=\{motion\.deliveryFormat\}\s+playing=\{playing\}/);
  const poster = read(POSTER);
  // 재생 이펙트는 같은 src 에 attachSource 하고, 모드는 posterRenderMode(deliveryFormat) 하나뿐이다.
  const playingEffect = poster.slice(poster.indexOf("// ── 인라인 재생"));
  assert.match(playingEffect, /attachSource\(video, src\);/);
  assert.equal((poster.match(/posterRenderMode\(/g) ?? []).length, 1);
  assert.equal(posterRenderMode("packed_alpha", PACKED), "packed");
});

test("Play 버튼은 44px 터치 타깃이고 터치 기기·재생 중에는 항상 보인다", () => {
  const css = read(CSS);
  const i = css.indexOf(".my-library__card-play {");
  const rule = css.slice(i, css.indexOf("}", i));
  assert.match(rule, /min-height:\s*44px/);
  assert.match(rule, /min-width:\s*44px/);
  assert.match(css, /@media \(hover: none\) \{\s*\.my-library__card-play \{\s*opacity: 1;/);
  assert.match(css, /\.my-library__card-play--active \{\s*opacity: 1;/);
  assert.match(css, /\.my-library__card-media:hover \.my-library__card-play/);
});

test("렌더 해상도는 dpr 2·640px 로 제한된다", () => {
  assert.deepEqual(posterCanvasSize(200, 356, 3), { w: 400, h: 712 });
  assert.deepEqual(posterCanvasSize(640, 1138, 2), { w: 640, h: 1138 });
  assert.deepEqual(posterCanvasSize(100, 178, 1), { w: 100, h: 178 });
  assert.equal(posterSeekTime(4), 0.1);
  assert.equal(posterSeekTime(0.1), 0.05);
  assert.equal(posterSeekTime(NaN), 0);
});

test("실패한 자산만 폴백 상태를 보이고, 상태 배지는 서버 값 그대로다", () => {
  const src = read(POSTER);
  assert.match(src, /video\.addEventListener\("error", onError\)/);
  assert.match(src, /state === "error" \? \([\s\S]*?my-library__card-media-fallback/);
  const screen = read(SCREEN);
  assert.match(screen, /if \(motion\.accessState === "unavailable"\) return "unavailable";/);
});

// ── 4. 메뉴는 카드 바깥에서 보인다 ────────────────────────────────────────────

test("세 점 메뉴는 document.body 포털 + fixed 로 뜬다 — 카드 overflow:hidden 에 잘리지 않는다", () => {
  const src = read(SCREEN);
  assert.match(src, /import \{ createPortal \} from "react-dom";/);
  assert.match(src, /createPortal\([\s\S]*?className=\{`my-library__card-menu-list[\s\S]*?document\.body,?\s*\)/);
  const css = read(CSS);
  const i = css.indexOf(".my-library__card-menu-list {");
  const rule = css.slice(i, css.indexOf("}", i));
  assert.match(rule, /position:\s*fixed/);
  assert.match(rule, /z-index:\s*1200/);
  // 카드 자체는 그대로 overflow:hidden — 리디자인이 아니다.
  const card = css.slice(css.indexOf(".my-library__card {"), css.indexOf("}", css.indexOf(".my-library__card {")));
  assert.match(card, /overflow:\s*hidden/);
});

test("메뉴 위치는 클릭한 버튼의 getBoundingClientRect 기준이다", () => {
  const src = read(SCREEN);
  assert.match(src, /button\.getBoundingClientRect\(\)/);
  assert.match(src, /placeCardMenu\(\{[\s\S]*?anchor: \{ top: rect\.top, bottom: rect\.bottom, left: rect\.left, right: rect\.right \}/);
  assert.match(src, /style=\{placed \? \{ top: placed\.top, left: placed\.left \} : \{ visibility: "hidden" \}\}/);
});

// ── 5. 뷰포트 아래가 모자라면 위로 뒤집는다 ──────────────────────────────────

test("placeCardMenu: 아래 공간이 넉넉하면 below, 모자라면 above 로 뒤집는다", () => {
  const menu = { menuWidth: 160, menuHeight: 60, viewportWidth: 390, viewportHeight: 800 };
  const below = placeCardMenu({ ...menu, anchor: { top: 100, bottom: 144, left: 300, right: 344 } });
  assert.equal(below.placement, "below");
  assert.equal(below.top, 144 + MENU_GAP_PX);
  assert.equal(below.left, 344 - 160);

  const above = placeCardMenu({ ...menu, anchor: { top: 740, bottom: 784, left: 300, right: 344 } });
  assert.equal(above.placement, "above");
  assert.equal(above.top, 740 - MENU_GAP_PX - 60);
});

test("placeCardMenu: 뷰포트 밖으로 나가지 않도록 여백 안에 가둔다", () => {
  const r = placeCardMenu({
    anchor: { top: 10, bottom: 54, left: 0, right: 40 },
    menuWidth: 160,
    menuHeight: 60,
    viewportWidth: 200,
    viewportHeight: 120,
  });
  assert.equal(r.left, MENU_VIEWPORT_MARGIN_PX);
  assert.ok(r.top >= MENU_VIEWPORT_MARGIN_PX && r.top + 60 <= 120 - MENU_VIEWPORT_MARGIN_PX);
});

// ── 6. 바깥 클릭·Escape 로 닫힌다 ─────────────────────────────────────────────

test("메뉴는 바깥 pointerdown·Escape·스크롤·리사이즈로 닫힌다", () => {
  const src = read(SCREEN);
  assert.match(src, /document\.addEventListener\("pointerdown", onPointerDown, true\)/);
  assert.match(src, /if \(menuRef\.current\?\.contains\(target\) \|\| buttonRef\.current\?\.contains\(target\)\) return;/);
  assert.match(src, /if \(e\.key === "Escape"\) \{[\s\S]*?close\(true\);/);
  assert.match(src, /window\.addEventListener\("scroll", onViewportChange, true\)/);
  assert.match(src, /window\.addEventListener\("resize", onViewportChange\)/);
});

// ── 7. 모바일 접근성 ───────────────────────────────────────────────────────────

test("메뉴 버튼과 항목은 44px 터치 타깃을 유지하고 role/aria 를 갖는다", () => {
  const css = read(CSS);
  for (const selector of [".my-library__card-overflow {", ".my-library__card-menu-item {"]) {
    const i = css.indexOf(selector);
    assert.match(css.slice(i, css.indexOf("}", i)), /min-height:\s*44px/);
  }
  const src = read(SCREEN);
  assert.match(src, /aria-haspopup="menu"/);
  assert.match(src, /aria-expanded=\{open\}/);
  assert.match(src, /role="menu"[\s\S]*?role="menuitem"/);
});

// ── 8. Theme → Preview → Play 흐름은 그대로다 ─────────────────────────────────

test("카드 클릭은 여전히 onSelectMotion(pet, motion) → storePreviewPipeline 이고 delivery_format 을 그대로 넘긴다", () => {
  const src = read(SCREEN);
  assert.match(src, /onSelect=\{\(\) => onSelectMotion\(pet, motion\)\}/);
  assert.match(src, /delivery_format: motion\.deliveryFormat,/);
  assert.match(src, /idle_video_url: motion\.url \?\? "",/);
  assert.match(src, /onAction=\{onSelect\}/);
});
