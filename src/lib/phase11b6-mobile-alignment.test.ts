/**
 * Phase 11B-6 — Mobile structural alignment.
 *
 * Same testing convention as preview-composer-structural.test.ts: this repo
 * has no DOM-rendering harness (no jsdom/@testing-library dependency), so
 * these are source-pattern assertions against the real files, not rendered
 * output. They pin the specific gaps 11B-6 closed between the mobile target
 * flow and the 11B-1..5 output: the Upload dropzone shape/guide placement
 * and the Generation preview's "tiny boxed thumbnail".
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (p: string) => readFileSync(p, "utf8");

const UPLOAD = "src/components/memorial/photo-upload-screen.tsx";
const UPLOAD_CSS = "src/styles/pet-intake.css";
const GEN_CSS = "src/styles/generation-progress.css";

// ── Upload: short/wide dropzone, guide never pushes it below the fold ──────

test("Upload: the mobile dropzone is short/wide, not the old portrait 4:5 card", () => {
  const src = read(UPLOAD);
  assert.ok(
    !src.includes("aspect-[4/5]"),
    "the JSX still hardcodes the old portrait 4:5 aspect ratio inline"
  );

  const css = read(UPLOAD_CSS);
  assert.match(
    css,
    /\.pet-intake__dropzone\s*\{[^}]*aspect-ratio:\s*16 \/ 10;/,
    "the mobile dropzone no longer declares a short/wide aspect-ratio"
  );
});

test("Upload: the photo guide sits below the dropzone/thumbnails as a collapsed disclosure, not above them", () => {
  const src = read(UPLOAD);

  const dropzoneIndex = src.indexOf("pet-intake__dropzone");
  const gridIndex = src.indexOf("pet-intake__grid");
  const tipsIndex = src.indexOf("pet-intake__tips");

  assert.ok(dropzoneIndex > -1 && gridIndex > -1 && tipsIndex > -1);
  assert.ok(
    tipsIndex > dropzoneIndex && tipsIndex > gridIndex,
    "the mobile guide/tips block renders before the dropzone or thumbnail grid " +
      "and would push the upload area below the first viewport"
  );

  // Collapsed by default (a native <details>, not an always-open guide card).
  assert.match(src, /<details className="pet-intake__mobile-only pet-intake__tips">/);
});

test("Upload: the mobile tips disclosure still renders the real PhotoUploadGuide content, not fabricated copy", () => {
  const src = read(UPLOAD);
  assert.match(
    src,
    /<details className="pet-intake__mobile-only pet-intake__tips">[\s\S]*?<PhotoUploadGuide language={language} \/>[\s\S]*?<\/details>/,
  );
});

// ── Generation preview: the pet visual anchors the screen ──────────────────

test("Generation preview: the pet visual scales with the viewport instead of a fixed tiny thumbnail box", () => {
  const css = read(GEN_CSS);
  assert.ok(
    !/\.gen-progress__preview\s*\{[^}]*width:\s*9\.5rem;/.test(css),
    "the generation preview is still pinned to a fixed 9.5rem thumbnail box"
  );
  assert.match(
    css,
    /\.gen-progress__preview\s*\{[^}]*width:\s*min\(72vw,\s*20rem\);/,
    "the generation preview no longer scales with the viewport"
  );
});

test("Generation preview: desktop still gets its own wide/flex treatment (unchanged by the mobile fix)", () => {
  const css = read(GEN_CSS);
  assert.match(
    css,
    /@media \(min-width: 1024px\) \{[\s\S]*\.gen-progress__preview \{\s*width: auto;/,
    "the desktop (1024px+) override for the preview panel is missing or changed"
  );
});
