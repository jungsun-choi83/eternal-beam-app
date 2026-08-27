/**
 * One-pet layered 2.5D prototype.
 *
 * This contract is intentionally local-only: it has no QR parsing, API lookup,
 * storage policy, or production asset selection. The production Shaker keeps
 * using its existing baked video contract.
 */

export const SHAKER_LAYERED_PROTOTYPE_PATH = "/prototype/shaker-layered";

export type LayeredBackgroundMode = "image" | "video";

export const SHAKER_LAYERED_PROTOTYPE_ASSETS = {
  petPackedAlpha: "/demo/goya_idle_packed.mp4",
  imageBackground: "/theme-thumbs/fresh_forest.jpg",
  videoBackground: "/demo/forest.mp4",
  bakedFallback: "/prototypes/shaker-depth/goya-forest-baked-v2.mp4",
} as const;

export const LAYERED_PARALLAX = {
  backgroundMaxPx: 3,
  petMaxPx: 9,
  foregroundMaxPx: 12,
  backgroundOverscan: 1.04,
  fallbackOverscan: 1.04,
  maxDevicePixelRatio: 1.5,
} as const;

/**
 * The packed source is 1284x1432: top 716px RGB, bottom 716px alpha.
 * Empty horizontal space is cropped in the shader so the dog is large enough
 * to judge on a phone. The crop still leaves margin for the idle motion.
 */
export const PACKED_PET_CROP = {
  xMin: 0.27,
  xMax: 0.73,
} as const;

export function isShakerLayeredPrototypePath(pathname: string): boolean {
  const path = (pathname || "").replace(/\/+$/, "") || "/";
  return path === SHAKER_LAYERED_PROTOTYPE_PATH;
}

export function backgroundAssetFor(mode: LayeredBackgroundMode): string {
  return mode === "video"
    ? SHAKER_LAYERED_PROTOTYPE_ASSETS.videoBackground
    : SHAKER_LAYERED_PROTOTYPE_ASSETS.imageBackground;
}

export function foregroundOffsetFromPet(petOffset: number): number {
  if (!Number.isFinite(petOffset)) return 0;
  return (petOffset / LAYERED_PARALLAX.petMaxPx) * LAYERED_PARALLAX.foregroundMaxPx;
}

export const PACKED_ALPHA_VERTEX_SHADER = /* glsl */ `
  varying vec2 vUv;

  void main() {
    vUv = uv;
    gl_Position = vec4(position.xy, 0.0, 1.0);
  }
`;

export const PACKED_ALPHA_FRAGMENT_SHADER = /* glsl */ `
  precision highp float;

  uniform sampler2D uPackedVideo;
  uniform vec2 uCropX;
  varying vec2 vUv;

  void main() {
    vec2 frameUv = vec2(mix(uCropX.x, uCropX.y, vUv.x), vUv.y);

    // Vertical packed-alpha contract:
    //   top half    = premultiplied RGB
    //   bottom half = synchronized grayscale alpha matte
    vec2 rgbUv = vec2(frameUv.x, 0.5 + frameUv.y * 0.5);
    vec2 alphaUv = vec2(frameUv.x, frameUv.y * 0.5);
    vec3 premultipliedRgb = texture2D(uPackedVideo, rgbUv).rgb;
    float alpha = texture2D(uPackedVideo, alphaUv).r;

    // Match drawPackedAlphaVideo(): the encoder stores premultiplied RGB, then
    // H.264 compresses it. Restore straight RGB before normal alpha blending so
    // semitransparent fur does not inherit a black fringe. Very small matte
    // values are discarded rather than amplifying compression noise.
    if (alpha < 0.01) discard;
    vec3 straightRgb = clamp(premultipliedRgb / max(alpha, 1.0 / 255.0), 0.0, 1.0);
    gl_FragColor = vec4(straightRgb, alpha);
  }
`;
