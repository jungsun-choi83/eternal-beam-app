import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

import {
  DEPTH_DISPLACEMENT,
  DEPTH_FRAGMENT_SHADER,
  SHAKER_DEPTH_PROTOTYPE_ASSETS,
  displacementForDepth,
  isShakerDepthPrototypePath,
} from "./shaker-depth-prototype.ts";

const APP = readFileSync("src/app/App.tsx", "utf8");
const SCREEN = readFileSync(
  "src/components/memorial/shaker-depth-prototype-screen.tsx",
  "utf8"
);
const PRODUCTION_SHAKER = readFileSync(
  "src/components/memorial/shaker-screen.tsx",
  "utf8"
);

describe("isolated prototype entry", () => {
  it("recognizes only the dedicated prototype path", () => {
    assert.equal(isShakerDepthPrototypePath("/prototype/shaker-depth"), true);
    assert.equal(isShakerDepthPrototypePath("/prototype/shaker-depth/"), true);
    assert.equal(isShakerDepthPrototypePath("/shaker"), false);
    assert.equal(isShakerDepthPrototypePath("/ops/shaker"), false);
  });

  it("lazy-loads Three.js prototype before the production Shaker branch", () => {
    assert.match(APP, /lazy\(\(\) =>/);
    const prototypeAt = APP.indexOf("isShakerDepthPrototypePath(window.location.pathname)");
    const shakerAt = APP.indexOf("isShakerEntry()");
    assert.ok(prototypeAt > -1 && shakerAt > prototypeAt);
  });

  it("does not import prototype code into the production Shaker screen", () => {
    assert.ok(!PRODUCTION_SHAKER.includes("shaker-depth-prototype"));
    assert.ok(!PRODUCTION_SHAKER.includes("THREE"));
  });
});

describe("one-scene asset contract", () => {
  it("ships a baked MP4, matching canonical still, and grayscale depth map", () => {
    const publicPath = (url: string) => `public${url}`;
    const mp4 = readFileSync(publicPath(SHAKER_DEPTH_PROTOTYPE_ASSETS.video));
    const canonical = readFileSync(publicPath(SHAKER_DEPTH_PROTOTYPE_ASSETS.canonical));
    const depth = readFileSync(publicPath(SHAKER_DEPTH_PROTOTYPE_ASSETS.depth));

    assert.equal(mp4.subarray(4, 8).toString("ascii"), "ftyp");
    assert.equal(canonical[0], 0xff);
    assert.equal(canonical[1], 0xd8);
    assert.equal(depth.subarray(1, 4).toString("ascii"), "PNG");
    assert.equal(depth.readUInt32BE(16), 480);
    assert.equal(depth.readUInt32BE(20), 832);
  });
});

describe("depth displacement", () => {
  it("maps far, middle, near, and closest depths into the subtle target range", () => {
    assert.equal(displacementForDepth(0), 2);
    assert.ok(displacementForDepth(0.4) >= 4 && displacementForDepth(0.4) <= 5);
    assert.ok(displacementForDepth(0.7) >= 7 && displacementForDepth(0.7) <= 9);
    assert.equal(displacementForDepth(1), 12);
  });

  it("clamps malformed and out-of-range depth values", () => {
    assert.equal(displacementForDepth(-10), DEPTH_DISPLACEMENT.farPx);
    assert.equal(displacementForDepth(10), DEPTH_DISPLACEMENT.maxPx);
    assert.equal(displacementForDepth(Number.NaN), DEPTH_DISPLACEMENT.farPx);
  });

  it("samples depth separately and applies overscanned UV displacement", () => {
    assert.match(DEPTH_FRAGMENT_SHADER, /texture2D\(uDepth, baseUv\)\.r/);
    assert.match(DEPTH_FRAGMENT_SHADER, /2\.0 \+ 8\.0 \* shapedDepth/);
    assert.match(DEPTH_FRAGMENT_SHADER, /2\.0 \+ 10\.0 \* shapedDepth/);
    assert.match(DEPTH_FRAGMENT_SHADER, /baseUv - displacementUv/);
    assert.equal(DEPTH_DISPLACEMENT.horizontalMaxPx, 10);
    assert.ok(DEPTH_DISPLACEMENT.overscan >= 1.04);
    assert.ok(DEPTH_DISPLACEMENT.overscan <= 1.07);
  });

  it("protects horizontal silhouette edges with a motion-directional depth probe", () => {
    assert.match(DEPTH_FRAGMENT_SHADER, /horizontalDirection = sign\(uTilt\.x\)/);
    assert.match(DEPTH_FRAGMENT_SHADER, /uTilt\.x \* 10\.0/);
    assert.match(DEPTH_FRAGMENT_SHADER, /horizontalDirection \* 2\.5/);
    assert.match(
      DEPTH_FRAGMENT_SHADER,
      /depth = max\(depth, texture2D\(uDepth, probeUv\)\.r\)/
    );
    assert.equal(DEPTH_DISPLACEMENT.horizontalEdgeGuardPx, 2.5);
  });
});

describe("fallback and gyro reuse", () => {
  it("keeps the normal baked video mounted underneath WebGL", () => {
    assert.match(SCREEN, /Normal baked idle video fallback/);
    assert.match(SCREEN, /<video/);
    assert.match(SCREEN, /<DepthVideoRenderer/);
  });

  it("shares one video decoder between fallback playback and WebGL", () => {
    assert.equal((SCREEN.match(/<video/g) || []).length, 1);
    assert.ok(!SCREEN.includes('document.createElement("video")'));
    assert.match(SCREEN, /new THREE\.VideoTexture\(video\)/);
    assert.match(SCREEN, /videoRef=\{videoRef\}/);
  });

  it("uses the existing permission, tracker, and lifecycle implementation", () => {
    assert.match(SCREEN, /requestGyroPermission\(\)/);
    assert.match(SCREEN, /createParallaxFrameLoop\(/);
    assert.match(SCREEN, /createOrientationMotionSession\(/);
    assert.match(SCREEN, /alignGyroSampleToScreen\(/);
    assert.match(SCREEN, /horizontalInputGain/);
    assert.ok(!SCREEN.includes("normalizeTilt("));
  });

  it("requires permission, renderer readiness, and a valid sensor sample before showing depth", () => {
    assert.match(SCREEN, /permission === "granted"/);
    assert.match(SCREEN, /rendererReady && sensorActive/);
    assert.match(SCREEN, /permission === "denied"/);
    assert.match(SCREEN, /gyroSupport === "unsupported"/);
  });
});
