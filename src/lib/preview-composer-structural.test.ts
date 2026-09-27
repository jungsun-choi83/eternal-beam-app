/**
 * Phase 11B-5 — Preview / Composer structural alignment.
 *
 * Same testing convention as library-preview-flow-wiring.test.ts: this repo
 * has no DOM-rendering harness (no jsdom/@testing-library dependency), so
 * these are source-pattern assertions against the real files, not rendered
 * output. They pin the specific structural/behavioral requirements from the
 * Phase 11B-5 spec that the existing test files don't already cover.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (p: string) => readFileSync(p, "utf8");

const PREVIEW = "src/components/memorial/preview-screen.tsx";
const PREVIEW_CSS = "src/styles/preview-composer.css";
const MY_LIBRARY = "src/components/memorial/my-library-screen.tsx";

// ── Desktop landscape preview structure ─────────────────────────────────────

test("Desktop: the stage frame is landscape, not the old portrait 3:4 box", () => {
  const css = read(PREVIEW_CSS);
  assert.match(
    css,
    /@container \(min-width: 860px\) \{[\s\S]*aspect-ratio: 16 \/ 10;/,
    "desktop stage no longer requests a landscape aspect-ratio"
  );
  assert.ok(
    !/@container \(min-width: 860px\) \{[\s\S]*aspect-ratio: 3 \/ 4;/.test(css),
    "desktop stage still pins the old portrait 3:4 aspect-ratio"
  );
});

test("Desktop: stage/panel split leans toward the stage (~65/35), not a near-even split", () => {
  const css = read(PREVIEW_CSS);
  const stageFlexMatch = css.match(/@container \(min-width: 860px\) \{[\s\S]*?\.preview-composer__stage \{\s*flex: ([\d.]+) 1 0%;/);
  assert.ok(stageFlexMatch, "could not find the desktop stage flex-grow value");
  assert.ok(
    Number(stageFlexMatch![1]) > 1.5,
    "stage flex-grow isn't weighted toward ~65% of the split"
  );
});

test("Mobile (default, no container match): the compact preview is landscape too, not an oversized portrait", () => {
  const src = read(PREVIEW);
  assert.match(
    src,
    /preview-gesture-surface theme-preview-frame relative w-full aspect-\[4\/3\]/,
    "base (mobile) gesture surface isn't landscape-framed"
  );
  assert.ok(
    !src.includes("aspect-[3/4]"),
    "the old portrait 3:4 frame class is still present"
  );
});

// ── Library vs Create behavior ──────────────────────────────────────────────

test("Published rail (Library, or Create after a device-mode publish) renders Play on Web + Play on Beam; pre-publish Create keeps its confirm/progress CTA untouched", () => {
  const src = read(PREVIEW);
  assert.match(
    src,
    /const showPublishedRail = isLibraryFlow \|\| \(hasIdle && deliveryMode === "device"\);/,
    "published rail is not derived from Library-or-published-device state"
  );
  assert.match(src, /\{showPublishedRail \? \(/, "no showPublishedRail branch in the action area");
  const branchStart = src.indexOf("{showPublishedRail ? (");
  const branchEnd = src.indexOf("      </div>\n      </div>\n    </div>", branchStart);
  const branch = src.slice(branchStart, branchEnd);
  assert.match(branch, /onClick=\{handlePlayOnWeb\}/, "Published branch missing Play on Web");
  assert.match(branch, /onClick=\{handlePlayOnBeam\}/, "Published branch missing Play on Beam");
  // Pre-publish Create flow (the else branch) still owns the original confirm/progress
  // CTA — untouched handleConfirm wiring, not replaced by Beam-style stay-on-screen actions.
  assert.match(branch, /onClick=\{handleConfirm\}/, "Create-flow branch lost its confirm CTA");
  assert.match(
    branch,
    /disabled=\{generating \|\| originalMissing\}/,
    "Create-flow CTA disabled condition changed"
  );
});

test("Active generation still shows the real progress screen — not the composer panel", () => {
  const src = read(PREVIEW);
  assert.match(src, /const showGenerationProgress = generating && phase7GenerationEnabled\(\);/);
  assert.match(src, /if \(showGenerationProgress \|\| showGenerationError\) \{/);
  assert.match(src, /<GenerationProgressScreen/);
});

// ── Change Theme preserves context ──────────────────────────────────────────

test("Change Theme (header button and mobile theme row) reuses the same onBack — no new navigation/state-reset path", () => {
  const src = read(PREVIEW);
  const headerBlock = src.slice(src.indexOf("<header"), src.indexOf("</header>"));
  assert.match(
    headerBlock,
    /className="mem-btn-secondary preview-composer__change-theme-btn"[\s\S]*?onClick=\{onBack\}|onClick=\{onBack\}[\s\S]*?className="mem-btn-secondary preview-composer__change-theme-btn"/,
  );
  const panelStart = src.indexOf('className="preview-composer__panel shrink-0"');
  const themeRow = src.slice(panelStart, src.indexOf("</button>", panelStart));
  assert.match(themeRow, /onClick=\{onBack\}/, "mobile theme row doesn't call the same onBack");
  // onBack itself (My Library: setStep("themes"); Create flow: navigateTo('themeSelection'))
  // is owned by the parent and already covered by library-preview-flow-wiring.test.ts —
  // this only pins that Preview offers exactly that action, not a fresh one.
});

// ── Motion selector only when real alternatives exist ───────────────────────

test("Motion shows a select-styled, clickable control only when onChangeMotion was handed down; otherwise a static label", () => {
  const src = read(PREVIEW);
  assert.match(
    src,
    /\{onChangeMotion \? \(\s*<button type="button" onClick=\{onChangeMotion\} className="preview-composer__motion-select">/,
    "no conditional select-styled motion control gated on onChangeMotion"
  );
  assert.match(
    src,
    /preview-composer__motion-value preview-composer__motion-value--static/,
    "no static (non-interactive) fallback for the single-motion case"
  );
  // onChangeMotion itself is only ever passed by My Library when there is more
  // than one real published motion — pinned by library-preview-flow-wiring.test.ts.
  assert.match(
    read(MY_LIBRARY),
    /\.motions\.length > 1\s*\n\s*\? \(\) => setStep\("library"\)\s*\n\s*: undefined/
  );
});

// ── Play on Web ──────────────────────────────────────────────────────────────

test("Play on Web sends no device command — it only replays local playback", () => {
  const src = read(PREVIEW);
  const start = src.indexOf("const handlePlayOnWeb = useCallback(");
  assert.ok(start > 0, "handlePlayOnWeb not found");
  const end = src.indexOf("}, []);", start);
  const body = src.slice(start, end);
  assert.ok(!body.includes("sendDeviceCommand"), "Play on Web calls a device command");
  assert.match(body, /setReplayKey/, "Play on Web doesn't drive the replay-remount key");
});

test("PUBLISHED enables playback — Play on Web / Play on Beam are gated on hasIdle", () => {
  const src = read(PREVIEW);
  assert.match(src, /onClick=\{handlePlayOnWeb\}\s*\n\s*disabled=\{!hasIdle\}/);
  assert.match(
    src,
    /onClick=\{handlePlayOnBeam\}\s*\n\s*disabled=\{!onPlayOnBeam \|\| !hasIdle \|\| beamStatus === "sending"\}/
  );
});

// ── Play on Beam: theme_play → pet_asset, failure handling, repeatability ───
// (Full coverage already lives in library-preview-flow-wiring.test.ts; these
// re-assert the same invariants now that the surrounding JSX changed, so a
// future edit to this screen can't silently drop them.)

test("Play on Beam command order is still theme_play → pet_asset in My Library's onPlayOnBeam", () => {
  const src = read(MY_LIBRARY);
  const themeIdx = src.indexOf('event: "theme_play"');
  const petIdx = src.indexOf('event: "pet_asset"');
  assert.ok(themeIdx > 0 && petIdx > 0 && themeIdx < petIdx);
});

test("A failed Play on Beam command does not show success", () => {
  const src = read(PREVIEW);
  const start = src.indexOf("const handlePlayOnBeam = useCallback(");
  const end = src.indexOf("}, [onPlayOnBeam, beamStatus, pollForAck]);", start);
  const body = src.slice(start, end);
  assert.match(body, /if \(result\.ok\) \{\s*\n\s*setBeamStatus\("sent"\);/);
  assert.match(body, /\} else \{\s*\n\s*setBeamStatus\("error"\);/);
});

test("Repeated Play on Beam still works — no post-send guard blocks resending", () => {
  const src = read(PREVIEW);
  const start = src.indexOf("const handlePlayOnBeam = useCallback(");
  const end = src.indexOf("}, [onPlayOnBeam, beamStatus, pollForAck]);", start);
  const body = src.slice(start, end);
  assert.match(body, /beamStatus === "sending"/);
  assert.ok(!/beamStatus === "sent"/.test(body));
});

// ── No fabricated data ───────────────────────────────────────────────────────

test("Pet identity shows the real thumbnail only — no invented name/breed text", () => {
  const src = read(PREVIEW);
  assert.match(src, /petAvatarUrl = cutoutDisplay \|\| null;/);
  assert.match(src, /\{petAvatarUrl \? \(/);
  assert.ok(
    !/Golden Retriever|Labrador|Poodle/.test(src),
    "a hardcoded breed string leaked into the component"
  );
});

test("No fullscreen/expand control was added — no real support exists anywhere in the app", () => {
  const src = read(PREVIEW);
  assert.ok(!/requestFullscreen|Maximize2?/.test(src));
});
