import { test } from "node:test";
import assert from "node:assert/strict";
import { coverUvScale, topOriginCropToUv } from "./shaker-layered.ts";

import {
  LAYERED_CONTACT_SHADOW,
  LAYERED_PARALLAX,
  LAYERED_WEBGL_SCENE,
  PACKED_ALPHA_FRAGMENT_SHADER,
  PACKED_ALPHA_VERTEX_SHADER,
  PACKED_PET_CROP,
  SHAKER_LAYERED_PROTOTYPE_ASSETS,
  backgroundAssetFor,
  contactShadowCameraCompensation,
  contactShadowFrameFromPetOffset,
  foregroundOffsetFromPet,
  isShakerLayeredPrototypePath,
  perspectiveCameraOffsetFromPetFrame,
  perspectivePlaneSizeAtZ,
  rigidPetRotationFromOffset,
  verticalLayerOffsetsFromPetOffset,
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
  assert.equal(LAYERED_PARALLAX.backgroundMaxPx, 5);
  assert.equal(LAYERED_PARALLAX.petMaxPx, 16);
  assert.equal(foregroundOffsetFromPet(16), 12);
  assert.equal(foregroundOffsetFromPet(-16), -12);
  assert.equal(foregroundOffsetFromPet(Number.NaN), 0);
  assert.deepEqual(verticalLayerOffsetsFromPetOffset(16), {
    petY: 18,
    backgroundY: 5,
  });
  assert.deepEqual(verticalLayerOffsetsFromPetOffset(-160), {
    petY: -18,
    backgroundY: -5,
  });
  assert.deepEqual(verticalLayerOffsetsFromPetOffset(Number.NaN), {
    petY: 0,
    backgroundY: 0,
  });
});

test("pet perspective stays subtle and rotates the complete pet as one rigid layer", () => {
  assert.equal(LAYERED_PARALLAX.perspectivePx, 1000);
  assert.equal(LAYERED_PARALLAX.tiltRangeDeg, 18);
  assert.equal(LAYERED_PARALLAX.verticalTiltGain, 1.6);
  assert.equal(LAYERED_PARALLAX.deadZoneDeg, 1);
  assert.equal(LAYERED_PARALLAX.smoothing, 0.18);
  assert.equal(LAYERED_PARALLAX.petRotateYMaxDeg, 1.5);
  assert.equal(LAYERED_PARALLAX.petRotateXMaxDeg, 0.75);
  assert.deepEqual(rigidPetRotationFromOffset(16, 18), {
    rotateXDeg: -0.75,
    rotateYDeg: 1.5,
  });
  assert.deepEqual(rigidPetRotationFromOffset(-900, -900), {
    rotateXDeg: 0.75,
    rotateYDeg: -1.5,
  });
  assert.deepEqual(rigidPetRotationFromOffset(Number.NaN, Number.NaN), {
    rotateXDeg: 0,
    rotateYDeg: 0,
  });
});

test("multi-plane scene uses bounded camera motion and ordered perspective depths", () => {
  assert.deepEqual(perspectiveCameraOffsetFromPetFrame(16, 18), {
    x: -0.18,
    y: 0.2,
  });
  assert.deepEqual(perspectiveCameraOffsetFromPetFrame(-160, -180), {
    x: 0.18,
    y: -0.2,
  });
  assert.deepEqual(perspectiveCameraOffsetFromPetFrame(Number.NaN, Number.NaN), {
    x: 0,
    y: 0,
  });
  assert.ok(LAYERED_WEBGL_SCENE.foregroundZ > LAYERED_WEBGL_SCENE.petZ);
  assert.ok(LAYERED_WEBGL_SCENE.petZ > LAYERED_WEBGL_SCENE.shadowZ);
  assert.ok(LAYERED_WEBGL_SCENE.shadowZ > LAYERED_WEBGL_SCENE.midgroundZ);
  assert.ok(LAYERED_WEBGL_SCENE.midgroundZ > LAYERED_WEBGL_SCENE.farBackgroundZ);

  const foregroundSize = perspectivePlaneSizeAtZ(0.5, LAYERED_WEBGL_SCENE.foregroundZ);
  const petSize = perspectivePlaneSizeAtZ(0.5, LAYERED_WEBGL_SCENE.petZ);
  const farSize = perspectivePlaneSizeAtZ(0.5, LAYERED_WEBGL_SCENE.farBackgroundZ);
  assert.ok(foregroundSize[1] < petSize[1]);
  assert.ok(petSize[1] < farSize[1]);
  assert.match(PACKED_ALPHA_VERTEX_SHADER, /projectionMatrix \* modelViewMatrix/);

  const phoneAspect = 390 / 844;
  for (const [z, overscan] of [
    [LAYERED_WEBGL_SCENE.farBackgroundZ, LAYERED_WEBGL_SCENE.farOverscan],
    [LAYERED_WEBGL_SCENE.foregroundZ, LAYERED_WEBGL_SCENE.foregroundOverscan],
  ] as const) {
    const base = perspectivePlaneSizeAtZ(phoneAspect, z);
    const covered = perspectivePlaneSizeAtZ(phoneAspect, z, overscan);
    assert.ok((covered[0] - base[0]) / 2 >= LAYERED_WEBGL_SCENE.cameraMaxX);
    assert.ok((covered[1] - base[1]) / 2 >= LAYERED_WEBGL_SCENE.cameraMaxY);
  }
});

test("contact shadow follows less strongly and becomes only slightly softer on tilt", () => {
  const neutral = contactShadowFrameFromPetOffset(0, 0);
  const tilted = contactShadowFrameFromPetOffset(16, -16);
  assert.deepEqual(neutral, { x: 0, y: 0, opacity: 0.24, blurPx: 11 });
  assert.equal(tilted.x, 8);
  assert.ok(Math.abs(tilted.y + 4.8) < 1e-9);
  assert.ok(Math.abs(tilted.opacity - 0.2112) < 1e-9);
  assert.equal(tilted.blurPx, 14);
  assert.ok(Math.abs(tilted.x) < LAYERED_PARALLAX.petMaxPx);
  assert.ok(Math.abs(tilted.y) < LAYERED_PARALLAX.petMaxPx);
  assert.equal(LAYERED_CONTACT_SHADOW.maxOpacity, 0.3);
  assert.equal(LAYERED_CONTACT_SHADOW.cameraFollowRatio, 0.45);
  assert.deepEqual(contactShadowCameraCompensation({ x: 0.18, y: -0.2 }), {
    x: 0.081,
    y: -0.09000000000000001,
  });
  assert.deepEqual(contactShadowCameraCompensation({ x: Number.NaN, y: Number.NaN }), {
    x: 0,
    y: 0,
  });
});

test("packed alpha shader samples RGB and synchronized matte separately", () => {
  assert.match(PACKED_ALPHA_FRAGMENT_SHADER, /premultipliedRgb/);
  assert.match(PACKED_ALPHA_FRAGMENT_SHADER, /straightRgb/);
  assert.match(PACKED_ALPHA_FRAGMENT_SHADER, /alphaUv/);
  assert.match(PACKED_ALPHA_FRAGMENT_SHADER, /uCropY/);
  assert.doesNotMatch(PACKED_ALPHA_FRAGMENT_SHADER, /depth|displace|chroma/i);
  assert.ok(PACKED_PET_CROP.xMin >= 0 && PACKED_PET_CROP.xMax <= 1);
  assert.ok(PACKED_PET_CROP.xMin < PACKED_PET_CROP.xMax);
});

test("packed pet cover crop matches portrait and landscape scene cropping", () => {
  assert.deepEqual(
    coverUvScale(1080, 1920, 390, 844),
    [(390 / 844) / (1080 / 1920), 1],
  );
  assert.deepEqual(
    coverUvScale(1920, 1080, 390, 844),
    [(390 / 844) / (1920 / 1080), 1],
  );
  assert.deepEqual(coverUvScale(1080, 1920, 360, 640), [1, 1]);
});

test("top-origin alpha QA bounds are inverted for bottom-origin WebGL video UVs", () => {
  assert.deepEqual(topOriginCropToUv(0.125, 0.75), [0.25, 0.875]);
  assert.deepEqual(topOriginCropToUv(Number.NaN, Number.NaN), [0, 1]);
});
