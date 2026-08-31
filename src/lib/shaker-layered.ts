/** Shared production/prototype constants for rigid layered Shaker playback. */

export const LAYERED_PARALLAX = {
  backgroundMaxPx: 5,
  petMaxPx: 16,
  backgroundVerticalMaxPx: 5,
  petVerticalMaxPx: 18,
  foregroundMaxPx: 12,
  backgroundOverscan: 1.04,
  // Reach the same safe displacement with an ordinary hand tilt. The former
  // 26-degree range produced only 2–3px during real-device testing.
  tiltRangeDeg: 18,
  // People naturally produce less screen-space pitch than wrist roll. Boost
  // the aligned vertical sensor delta before normalization, while the tracker
  // still clamps final pet/background displacement to the same safe maxima.
  verticalTiltGain: 1.6,
  deadZoneDeg: 1,
  smoothing: 0.18,
  perspectivePx: 1000,
  petRotateYMaxDeg: 1.5,
  petRotateXMaxDeg: 0.75,
  maxDevicePixelRatio: 1.5,
  assetReadyTimeoutMs: 15_000,
} as const;

/**
 * Real perspective scene used by V2 BREATHING.
 *
 * Z positions are deliberately centralized so the renderer cannot slowly
 * drift back into unrelated per-layer pixel transforms.  An empty midground
 * node is still part of the scene graph until the manifest grows a verified
 * independent midground asset; the background is never duplicated to fake it.
 */
export const LAYERED_WEBGL_SCENE = {
  cameraZ: 10,
  cameraFovDeg: 45,
  cameraNear: 0.1,
  cameraFar: 40,
  cameraMaxX: 0.18,
  cameraMaxY: 0.20,
  foregroundZ: 3,
  petZ: 0,
  shadowZ: -0.1,
  midgroundZ: -3,
  farBackgroundZ: -7,
  farOverscan: 1.08,
  foregroundOverscan: 1.16,
  petOverscan: 1.03,
} as const;

export interface PerspectiveCameraOffset {
  x: number;
  y: number;
}

/** Convert the smoothed tracker output into a bounded world-space camera move. */
export function perspectiveCameraOffsetFromPetFrame(
  petX: number,
  petY: number,
): PerspectiveCameraOffset {
  const safeX = Number.isFinite(petX) ? petX : 0;
  const safeY = Number.isFinite(petY) ? petY : 0;
  const normalizedX = clamp(safeX / LAYERED_PARALLAX.petMaxPx, -1, 1);
  const normalizedY = clamp(safeY / LAYERED_PARALLAX.petVerticalMaxPx, -1, 1);
  return {
    // A positive screen-space request needs the camera to move the other way.
    x: normalizedX === 0 ? 0 : -normalizedX * LAYERED_WEBGL_SCENE.cameraMaxX,
    // Three.js world Y points up; moving the camera up moves the scene down.
    y: normalizedY * LAYERED_WEBGL_SCENE.cameraMaxY,
  };
}

/** Visible world-space rectangle at one Z plane for the configured camera. */
export function perspectivePlaneSizeAtZ(
  aspect: number,
  planeZ: number,
  overscan = 1,
): readonly [number, number] {
  const safeAspect = Number.isFinite(aspect) && aspect > 0 ? aspect : 1;
  const safeOverscan = Number.isFinite(overscan) && overscan > 0 ? overscan : 1;
  const distance = Math.max(
    LAYERED_WEBGL_SCENE.cameraNear,
    LAYERED_WEBGL_SCENE.cameraZ - planeZ,
  );
  const height = 2 * Math.tan((LAYERED_WEBGL_SCENE.cameraFovDeg * Math.PI) / 360) * distance;
  return [height * safeAspect * safeOverscan, height * safeOverscan];
}

export const LAYERED_CONTACT_SHADOW = {
  defaultOpacity: 0.24,
  maxOpacity: 0.30,
  defaultBlurPx: 11,
  minBlurPx: 6,
  maxBlurPx: 18,
  tiltBlurAddPx: 3,
  tiltOpacityReduction: 0.12,
  // Move the shadow in world space with part of the camera translation. This
  // cancels some screen-space parallax so it reacts, but less than the pet.
  cameraFollowRatio: 0.45,
  followXRatio: 0.5,
  followYRatio: 0.3,
} as const;

export function contactShadowCameraCompensation(
  cameraOffset: PerspectiveCameraOffset,
): PerspectiveCameraOffset {
  const x = Number.isFinite(cameraOffset.x) ? cameraOffset.x : 0;
  const y = Number.isFinite(cameraOffset.y) ? cameraOffset.y : 0;
  return {
    x: x * LAYERED_CONTACT_SHADOW.cameraFollowRatio,
    y: y * LAYERED_CONTACT_SHADOW.cameraFollowRatio,
  };
}

export interface RigidPetRotation {
  rotateXDeg: number;
  rotateYDeg: number;
}

export interface ContactShadowFrame {
  x: number;
  y: number;
  opacity: number;
  blurPx: number;
}

export interface VerticalLayerOffsets {
  petY: number;
  backgroundY: number;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/**
 * Convert the tracker's safe normalized vertical output into a more visible
 * screen-space separation. Pitch keeps its own slightly larger rigid
 * translation budget, independent of the horizontal layer travel.
 */
export function verticalLayerOffsetsFromPetOffset(petOffset: number): VerticalLayerOffsets {
  const safe = Number.isFinite(petOffset) ? petOffset : 0;
  const normalized = clamp(safe / LAYERED_PARALLAX.petMaxPx, -1, 1);
  return {
    petY: normalized * LAYERED_PARALLAX.petVerticalMaxPx,
    backgroundY: normalized * LAYERED_PARALLAX.backgroundVerticalMaxPx,
  };
}

/**
 * Convert the already-smoothed pet translation into a very small rigid-plane
 * rotation. This never reaches the shader and therefore cannot bend the pet,
 * split silhouettes, or move fur/ears independently.
 */
export function rigidPetRotationFromOffset(x: number, y: number): RigidPetRotation {
  const safeX = Number.isFinite(x) ? x : 0;
  const safeY = Number.isFinite(y) ? y : 0;
  const normalizedX = clamp(safeX / LAYERED_PARALLAX.petMaxPx, -1, 1);
  const normalizedY = clamp(safeY / LAYERED_PARALLAX.petVerticalMaxPx, -1, 1);
  return {
    // Vertical screen movement feels natural when the plane pitches the other way.
    rotateXDeg: normalizedY === 0
      ? 0
      : -normalizedY * LAYERED_PARALLAX.petRotateXMaxDeg,
    rotateYDeg: normalizedX * LAYERED_PARALLAX.petRotateYMaxDeg,
  };
}

/**
 * A grounded shadow follows the rigid pet, but stays closer to the floor.
 * Opacity falls and softness rises only slightly at full tilt; it should read
 * as contact with the scene, never as a glow around the silhouette.
 */
export function contactShadowFrameFromPetOffset(
  x: number,
  y: number,
  baseOpacity = LAYERED_CONTACT_SHADOW.defaultOpacity,
  baseBlurPx = LAYERED_CONTACT_SHADOW.defaultBlurPx,
): ContactShadowFrame {
  const safeX = Number.isFinite(x) ? x : 0;
  const safeY = Number.isFinite(y) ? y : 0;
  const tilt = clamp(
    Math.max(Math.abs(safeX), Math.abs(safeY)) / LAYERED_PARALLAX.petMaxPx,
    0,
    1,
  );
  const opacity = clamp(baseOpacity, 0, LAYERED_CONTACT_SHADOW.maxOpacity);
  const blurPx = clamp(
    baseBlurPx,
    LAYERED_CONTACT_SHADOW.minBlurPx,
    LAYERED_CONTACT_SHADOW.maxBlurPx,
  );
  return {
    x: safeX * LAYERED_CONTACT_SHADOW.followXRatio,
    y: safeY * LAYERED_CONTACT_SHADOW.followYRatio,
    opacity: opacity * (1 - tilt * LAYERED_CONTACT_SHADOW.tiltOpacityReduction),
    blurPx: Math.min(
      LAYERED_CONTACT_SHADOW.maxBlurPx,
      blurPx + tilt * LAYERED_CONTACT_SHADOW.tiltBlurAddPx,
    ),
  };
}

export function foregroundOffsetFromPet(petOffset: number): number {
  if (!Number.isFinite(petOffset)) return 0;
  return (petOffset / LAYERED_PARALLAX.petMaxPx) * LAYERED_PARALLAX.foregroundMaxPx;
}

/** UV scale that exactly mirrors CSS `object-fit: cover` center-cropping. */
export function coverUvScale(
  sourceWidth: number,
  sourceHeight: number,
  targetWidth: number,
  targetHeight: number,
): readonly [number, number] {
  const sourceAspect = Math.max(1, sourceWidth) / Math.max(1, sourceHeight);
  const targetAspect = Math.max(1, targetWidth) / Math.max(1, targetHeight);
  if (sourceAspect > targetAspect) return [targetAspect / sourceAspect, 1];
  if (sourceAspect < targetAspect) return [1, sourceAspect / targetAspect];
  return [1, 1];
}

/** Convert top-origin OpenCV/manifest crop bounds into bottom-origin WebGL UVs. */
export function topOriginCropToUv(
  topOriginMin: number,
  topOriginMax: number,
): readonly [number, number] {
  const safeMin = clamp(Number.isFinite(topOriginMin) ? topOriginMin : 0, 0, 0.99);
  const safeMax = clamp(
    Number.isFinite(topOriginMax) ? topOriginMax : 1,
    safeMin + 0.01,
    1,
  );
  return [1 - safeMax, 1 - safeMin];
}

export const PACKED_ALPHA_VERTEX_SHADER = /* glsl */ `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

export const LAYERED_MEDIA_FRAGMENT_SHADER = /* glsl */ `
  precision highp float;

  uniform sampler2D uMedia;
  uniform vec2 uCoverScale;
  varying vec2 vUv;

  void main() {
    vec2 mediaUv = (vUv - vec2(0.5)) * uCoverScale + vec2(0.5);
    gl_FragColor = texture2D(uMedia, mediaUv);
  }
`;

export const CONTACT_SHADOW_FRAGMENT_SHADER = /* glsl */ `
  precision highp float;

  uniform float uOpacity;
  uniform float uSoftness;
  varying vec2 vUv;

  void main() {
    vec2 centered = (vUv - vec2(0.5)) * 2.0;
    float radius = length(centered);
    float alpha = 1.0 - smoothstep(max(0.05, 1.0 - uSoftness), 1.0, radius);
    gl_FragColor = vec4(0.0, 0.0, 0.0, alpha * uOpacity);
  }
`;

export const PACKED_ALPHA_FRAGMENT_SHADER = /* glsl */ `
  precision highp float;

  uniform sampler2D uPackedVideo;
  uniform vec2 uCropX;
  uniform vec2 uCropY;
  uniform vec2 uCoverScale;
  varying vec2 vUv;

  void main() {
    // Match CSS object-cover on the independent background. Without this,
    // the background crops on tall/wide phones while the pet stretches to the
    // viewport, so two assets derived from the same scene no longer align.
    vec2 coverUv = (vUv - vec2(0.5)) * uCoverScale + vec2(0.5);
    vec2 frameUv = vec2(
      mix(uCropX.x, uCropX.y, coverUv.x),
      mix(uCropY.x, uCropY.y, coverUv.y)
    );

    // Vertical packed-alpha contract:
    //   top half    = premultiplied RGB
    //   bottom half = synchronized grayscale alpha matte
    vec2 rgbUv = vec2(frameUv.x, 0.5 + frameUv.y * 0.5);
    vec2 alphaUv = vec2(frameUv.x, frameUv.y * 0.5);
    vec3 premultipliedRgb = texture2D(uPackedVideo, rgbUv).rgb;
    float alpha = texture2D(uPackedVideo, alphaUv).r;

    if (alpha < 0.01) discard;
    vec3 straightRgb = clamp(premultipliedRgb / max(alpha, 1.0 / 255.0), 0.0, 1.0);
    gl_FragColor = vec4(straightRgb, alpha);
  }
`;
