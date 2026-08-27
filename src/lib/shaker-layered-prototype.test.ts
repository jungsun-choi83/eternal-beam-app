import { test } from "node:test";
import assert from "node:assert/strict";

import {
  LAYERED_PARALLAX,
  PACKED_ALPHA_FRAGMENT_SHADER,
  PACKED_PET_CROP,
  SHAKER_LAYERED_PROTOTYPE_ASSETS,
  backgroundAssetFor,
  foregroundOffsetFromPet,
  isShakerLayeredPrototypePath,
} from "./shaker-layered-prototype.ts";

test("layered prototype route is isolated from production Shaker", () => {
  assert.equal(isShakerLayeredPrototypePath("/prototype/shaker-layered"), true);
  assert.equal(isShakerLayeredPrototypePath("/prototype/shaker-layered/"), true);
  assert.equal(isShakerLayeredPrototypePath("/shaker"), false);
  assert.equal(isShakerLayeredPrototypePath("/ops/shaker"), false);
});

test("image and video tests use the same packed pet asset", () => {
  assert.equal(backgroundAssetFor("image"), SHAKER_LAYERED_PROTOTYPE_ASSETS.imageBackground);
  assert.equal(backgroundAssetFor("video"), SHAKER_LAYERED_PROTOTYPE_ASSETS.videoBackground);
  assert.match(SHAKER_LAYERED_PROTOTYPE_ASSETS.petPackedAlpha, /_packed\.mp4$/);
});

test("layer offsets stay inside the prototype motion budget", () => {
  assert.equal(LAYERED_PARALLAX.backgroundMaxPx, 3);
  assert.equal(LAYERED_PARALLAX.petMaxPx, 9);
  assert.equal(foregroundOffsetFromPet(9), 12);
  assert.equal(foregroundOffsetFromPet(-9), -12);
  assert.equal(foregroundOffsetFromPet(Number.NaN), 0);
});

test("packed alpha shader samples RGB and synchronized matte separately", () => {
  assert.match(PACKED_ALPHA_FRAGMENT_SHADER, /premultipliedRgb/);
  assert.match(PACKED_ALPHA_FRAGMENT_SHADER, /straightRgb/);
  assert.match(PACKED_ALPHA_FRAGMENT_SHADER, /alphaUv/);
  assert.doesNotMatch(PACKED_ALPHA_FRAGMENT_SHADER, /depth|displace|chroma/i);
  assert.ok(PACKED_PET_CROP.xMin >= 0 && PACKED_PET_CROP.xMax <= 1);
  assert.ok(PACKED_PET_CROP.xMin < PACKED_PET_CROP.xMax);
});
