import { strict as assert } from "node:assert";
import { existsSync, readFileSync } from "node:fs";
import { describe, it } from "node:test";

const splash = readFileSync("src/components/memorial/splash-screen.tsx", "utf8");
const start = readFileSync("src/components/memorial/get-started-screen.tsx", "utf8");
const app = readFileSync("src/app/EternalBeamApp.tsx", "utf8");
const css = readFileSync("src/styles/splash-screen.css", "utf8");

describe("splash screen", () => {
  it("uses the official SoulTrace metal mark, not a redrawn SVG", () => {
    assert.equal(existsSync("public/brand/eternal-beam-mark.png"), true);
    assert.match(splash, /\/brand\/eternal-beam-mark\.png/);
    assert.doesNotMatch(splash, /EternalBeamLogoSymbol/);
    assert.match(splash, />Eternal Beam</);
  });

  it("is a logo-only field — no painting, flowers, cards, or entry CTAs", () => {
    assert.doesNotMatch(splash, /entry-meadow|entry-scene|flora|시작하기|get-started|sign-in/);
    assert.match(css, /#fffaf0/);
    assert.match(css, /eb-splash-fade 600ms/);
    assert.match(css, /prefers-reduced-motion/);
    assert.doesNotMatch(css, /scale|rotate|blur\(/);
  });

  it("checks the session in parallel with the fade and then opens Home first", () => {
    assert.match(splash, /hasSession/);
    assert.match(splash, /onContinueRef\.current\("home"\)/);
    assert.match(app, /screen === 'splash' && \(/);
    assert.match(app, /onContinue=\{\(next\) => replaceScreen\(next\)\}/);
    assert.doesNotMatch(app, /SPLASH_FADE_MS|SPLASH_HOLD_MS/);
  });

  it("Get Started after splash reuses the same uncropped mark plus the meadow scene", () => {
    assert.match(start, /\/brand\/eternal-beam-mark\.png/);
    assert.match(start, /\/auth\/entry-meadow\.jpg/);
    assert.match(start, /<LanguageToggle language=\{language\} onChange=\{onLanguageChange\} className="eb-entry__lang" \/>/);
    assert.doesNotMatch(start, /setTimeout|setInterval|SPLASH_/);
  });
});
