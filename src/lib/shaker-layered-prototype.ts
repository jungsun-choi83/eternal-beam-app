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
export {
  LAYERED_CONTACT_SHADOW,
  LAYERED_PARALLAX,
  LAYERED_WEBGL_SCENE,
  PACKED_ALPHA_FRAGMENT_SHADER,
  PACKED_ALPHA_VERTEX_SHADER,
  contactShadowFrameFromPetOffset,
  contactShadowCameraCompensation,
  foregroundOffsetFromPet,
  perspectiveCameraOffsetFromPetFrame,
  perspectivePlaneSizeAtZ,
  rigidPetRotationFromOffset,
  verticalLayerOffsetsFromPetOffset,
} from "./shaker-layered.ts";

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
