/**
 * One-scene WebGL depth prototype.
 *
 * This module deliberately contains no QR parsing, API calls, or production Shaker policy.
 * It only defines the isolated route, fixed demo assets, and shader contract.
 */

export const SHAKER_DEPTH_PROTOTYPE_PATH = "/prototype/shaker-depth";

export const SHAKER_DEPTH_PROTOTYPE_ASSETS = {
  video: "/prototypes/shaker-depth/real-baked-pet-client-v3.mp4",
  canonical: "/prototypes/shaker-depth/real-baked-pet-client-canonical-v3.jpg",
  depth: "/prototypes/shaker-depth/real-baked-pet-client-depth-v3.png",
} as const;

export const DEPTH_DISPLACEMENT = {
  farPx: 2,
  maxPx: 12,
  horizontalMaxPx: 10,
  horizontalEdgeGuardPx: 2.5,
  overscan: 1.055,
  maxDevicePixelRatio: 1.5,
} as const;

export function isShakerDepthPrototypePath(pathname: string): boolean {
  const path = (pathname || "").replace(/\/+$/, "") || "/";
  return path === SHAKER_DEPTH_PROTOTYPE_PATH;
}

/** Mirrors the fragment shader's 2px → 12px depth response. */
export function displacementForDepth(depth: number): number {
  const d = Math.min(1, Math.max(0, Number.isFinite(depth) ? depth : 0));
  return DEPTH_DISPLACEMENT.farPx +
    (DEPTH_DISPLACEMENT.maxPx - DEPTH_DISPLACEMENT.farPx) * Math.pow(d, 1.5);
}

export const DEPTH_VERTEX_SHADER = /* glsl */ `
  varying vec2 vUv;

  void main() {
    vUv = uv;
    gl_Position = vec4(position.xy, 0.0, 1.0);
  }
`;

export const DEPTH_FRAGMENT_SHADER = /* glsl */ `
  precision highp float;

  uniform sampler2D uVideo;
  uniform sampler2D uDepth;
  uniform vec2 uViewport;
  uniform vec2 uTextureSize;
  uniform vec2 uTilt;
  uniform float uOverscan;

  varying vec2 vUv;

  vec2 coverUv(vec2 uv, vec2 viewport, vec2 textureSize) {
    float viewportAspect = viewport.x / max(viewport.y, 1.0);
    float textureAspect = textureSize.x / max(textureSize.y, 1.0);
    vec2 scale = vec2(1.0);

    if (viewportAspect < textureAspect) {
      scale.x = viewportAspect / textureAspect;
    } else {
      scale.y = textureAspect / viewportAspect;
    }

    return (uv - 0.5) * scale + 0.5;
  }

  void main() {
    vec2 baseUv = coverUv(vUv, uViewport, uTextureSize);
    baseUv = (baseUv - 0.5) / uOverscan + 0.5;

    float depth = texture2D(uDepth, baseUv).r;

    // A single depth lookup erodes a near object's leading edge: the output pixel
    // still sees background depth even though the moving pet should occupy it.
    // Probe back toward the possible source pixel, plus a small mask guard, so
    // thin features such as ears remain part of the near layer while moving.
    float horizontalDirection = sign(uTilt.x);
    vec2 horizontalProbeUv = vec2(
      uTilt.x * 10.0 / max(uViewport.x, 1.0),
      0.0
    );
    vec2 horizontalGuardUv = vec2(
      horizontalDirection * 2.5 / max(uTextureSize.x, 1.0),
      0.0
    );
    vec2 probeUv = clamp(
      baseUv - horizontalProbeUv - horizontalGuardUv,
      vec2(0.001),
      vec2(0.999)
    );
    depth = max(depth, texture2D(uDepth, probeUv).r);

    float shapedDepth = pow(clamp(depth, 0.0, 1.0), 1.5);
    vec2 displacementPx = vec2(
      2.0 + 8.0 * shapedDepth,
      2.0 + 10.0 * shapedDepth
    );
    vec2 displacementUv = uTilt * displacementPx / max(uViewport, vec2(1.0));

    // Sampling in the opposite direction makes the visible pixels travel with the tilt.
    vec2 videoUv = clamp(baseUv - displacementUv, vec2(0.001), vec2(0.999));
    gl_FragColor = texture2D(uVideo, videoUv);
  }
`;
