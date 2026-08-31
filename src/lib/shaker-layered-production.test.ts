import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { describe, test } from "node:test";

const SCREEN = readFileSync("src/components/memorial/shaker-screen.tsx", "utf8");
const PLAYER = readFileSync("src/components/memorial/shaker-layered-player.tsx", "utf8");
const BOUNDARY = readFileSync("src/components/memorial/shaker-layered-boundary.tsx", "utf8");
const GENERATE = readFileSync("backend/routers/generate.py", "utf8");
const MATTE = readFileSync("backend/services/layered_v2_pipeline.py", "utf8");
const SCENE = readFileSync("src/lib/build-canonical-scene.ts", "utf8");
const LAYERED = readFileSync("src/lib/shaker-layered.ts", "utf8");

describe("production V1 + V2 routing", () => {
  test("V2 is lazy and visually exclusive while its layered renderer loads", () => {
    assert.match(SCREEN, /lazy\(\(\) =>\s*import\("@\/components\/memorial\/shaker-layered-player"\)/);
    assert.match(SCREEN, /deriveShakerPlaybackRoute\(/);
    assert.match(SCREEN, /playbackRoute\.showV1/);
    assert.match(SCREEN, /failedLayeredAssetId/);
  });

  test("runtime media or WebGL failures report to automatic V1 fallback", () => {
    for (const reason of [
      "background-media-error",
      "pet-media-error",
      "webgl-renderer-error",
      "asset-ready-timeout",
    ]) {
      assert.ok(PLAYER.includes(reason), reason);
    }
    assert.match(SCREEN, /setFailedLayeredAssetId\(layeredManifest\.assetId\)/);
    assert.match(SCREEN, /<ShakerLayeredBoundary/);
    assert.match(BOUNDARY, /getDerivedStateFromError/);
    assert.match(BOUNDARY, /this\.props\.onFailure\(\)/);
  });

  test("motion denial or unavailable sensors keep V2 mounted without pointer motion", () => {
    assert.match(SCREEN, /permission === "granted" && gyroSupport !== "unsupported"/);
    assert.doesNotMatch(SCREEN, /\? "pointer"/);
    assert.match(PLAYER, /if \(motionMode === "off"\)/);
    assert.match(PLAYER, /motion: permission off/);
  });

  test("the iOS motion control stays above inert V2 media and bypasses Shaker gestures", () => {
    assert.match(PLAYER, /className=\{`pointer-events-none absolute inset-0 overflow-hidden/);
    assert.match(SCREEN, /bottom-0 z-\[20\]/);
    assert.match(SCREEN, /onPointerDown=\{\(event\) => event\.stopPropagation\(\)\}/);
    assert.match(SCREEN, /onPointerUp=\{\(event\) => event\.stopPropagation\(\)\}/);
    assert.match(SCREEN, /void askMotion\(\)/);
  });

  test("real-device motion diagnostics are query-gated and bypass React frame state", () => {
    assert.match(SCREEN, /get\("motionDebug"\) === "1"/);
    assert.match(SCREEN, /debugMotion=\{motionDebug\}/);
    assert.match(PLAYER, /motionDebugRef\.current\.textContent = value/);
    assert.doesNotMatch(PLAYER, /setMotionDebug/);
  });

  test("image and video backgrounds share one multi-plane renderer and packed pet material", () => {
    assert.match(PLAYER, /function HiddenSceneMedia/);
    assert.match(PLAYER, /new THREE\.VideoTexture\(element\)/);
    assert.match(PLAYER, /const petMaterial = new THREE\.ShaderMaterial/);
    assert.match(PLAYER, /data-renderer="multi-plane-webgl"/);
    assert.doesNotMatch(PLAYER, /black.?key|chroma.?key/i);
  });

  test("one production background stays one rigid far plane without fabricated segmentation", () => {
    assert.match(
      PLAYER,
      /const farBackground = makeMediaPlane\(\s*background,\s*LAYERED_WEBGL_SCENE\.farBackgroundZ/s,
    );
    assert.match(PLAYER, /const midgroundSlot = new THREE\.Group\(\)/);
    assert.match(PLAYER, /midgroundSlot\.name = "verified-midground-slot"/);
    assert.match(
      PLAYER,
      /foreground\s*\? makeMediaPlane\(foreground, LAYERED_WEBGL_SCENE\.foregroundZ/,
    );
    assert.doesNotMatch(PLAYER, /segmentScene|autoSegment|depthTexture|uDepthMap/);
  });

  test("V2 uses one real perspective camera and depth-separated rigid planes", () => {
    assert.match(PLAYER, /new THREE\.PerspectiveCamera\(/);
    assert.match(PLAYER, /LAYERED_WEBGL_SCENE\.farBackgroundZ/);
    assert.match(PLAYER, /LAYERED_WEBGL_SCENE\.midgroundZ/);
    assert.match(PLAYER, /LAYERED_WEBGL_SCENE\.shadowZ/);
    assert.match(PLAYER, /LAYERED_WEBGL_SCENE\.petZ/);
    assert.match(PLAYER, /LAYERED_WEBGL_SCENE\.foregroundZ/);
    assert.match(PLAYER, /perspectiveCameraOffsetFromPetFrame/);
    assert.match(PLAYER, /sceneMotionRef\.current\?\.setPetFrame/);
    assert.doesNotMatch(PLAYER, /translate3d\(|rotateY\(|rotateX\(/);
    assert.doesNotMatch(PLAYER, /depth.?map|displace/i);
    assert.doesNotMatch(SCREEN, /sceneLayerRef|applyParallaxFrame|SCENE_OVERSCAN/);
  });

  test("production gyro translates a tightly clamped camera without orbiting it", () => {
    assert.match(PLAYER, /createParallaxFrameLoop\(/);
    assert.match(PLAYER, /createOrientationMotionSession\(/);
    assert.match(PLAYER, /alignGyroSampleToScreen\(/);
    assert.match(PLAYER, /requestFrame: \(callback\) => window\.requestAnimationFrame\(callback\)/);
    assert.match(PLAYER, /camera\.position\.set\(offset\.x, offset\.y, LAYERED_WEBGL_SCENE\.cameraZ\)/);
    assert.match(PLAYER, /camera\.rotation\.set\(0, 0, 0\)/);
    assert.match(LAYERED, /cameraMaxX: 0\.18/);
    assert.match(LAYERED, /cameraMaxY: 0\.20/);
    assert.doesNotMatch(PLAYER, /camera\.lookAt|camera\.rotate|camera\.rotation\.[xy]/);
    assert.match(PLAYER, /document\.hidden/);
    assert.match(SCREEN, /const reducedMotion = useMemo\(prefersReducedMotion/);
    assert.match(SCREEN, /requestGyroPermission\(\)/);
  });

  test("packed-alpha pet stays one flat rigid plane with only tiny whole-mesh rotation", () => {
    assert.match(PLAYER, /const petMesh = new THREE\.Mesh\(geometry, petMaterial\)/);
    assert.match(PLAYER, /rigidPetRotationFromOffset\(x, y\)/);
    assert.match(PLAYER, /petMesh\.rotation\.set\(/);
    assert.match(PLAYER, /THREE\.MathUtils\.degToRad/);
    assert.match(LAYERED, /petRotateYMaxDeg: 1\.5/);
    assert.match(LAYERED, /petRotateXMaxDeg: 0\.75/);
    assert.doesNotMatch(PLAYER, /SkinnedMesh|morphTarget|Bone\(|geometry\.attributes/);
    assert.doesNotMatch(LAYERED, /uDepth|depthTexture|vertex\s*displace/i);
  });

  test("READY V2 crops to the sampled alpha silhouette instead of cover-zooming its canvas", () => {
    assert.match(MATTE, /_anchored_placement_metadata/);
    assert.match(MATTE, /"crop_y_min"/);
    assert.match(MATTE, /"crop_y_max"/);
    assert.match(MATTE, /placement=\(\s*dict\(qa\["placement"\]\)/s);
    assert.match(PLAYER, /uCropY/);
    assert.match(PLAYER, /placement\.crop_y_min/);
    assert.match(PLAYER, /placement\.crop_y_max/);
    assert.match(PLAYER, /topOriginCropToUv\(cropYMin, cropYMax\)/);
  });

  test("screen-aligned vertical tilt gets a distinct visible rigid-motion budget", () => {
    assert.match(PLAYER, /aligned\.beta \* LAYERED_PARALLAX\.verticalTiltGain/);
    assert.match(LAYERED, /petMaxPx: 16/);
    assert.match(LAYERED, /backgroundMaxPx: 5/);
    assert.match(LAYERED, /petVerticalMaxPx: 18/);
    assert.match(LAYERED, /backgroundVerticalMaxPx: 5/);
    assert.match(PLAYER, /verticalLayerOffsetsFromPetOffset\(frame\.pet\.y\)/);
  });

  test("contact shadow is a shader plane and adds no decoder or pet filter", () => {
    assert.match(PLAYER, /CONTACT_SHADOW_FRAGMENT_SHADER/);
    assert.match(PLAYER, /shadowMesh\.position\.z = LAYERED_WEBGL_SCENE\.shadowZ/);
    assert.match(PLAYER, /shadowMaterial\.uniforms\.uOpacity/);
    assert.match(PLAYER, /contactShadowCameraCompensation\(currentCameraOffset\)/);
    assert.match(PLAYER, /shadowBaseX \+ compensation\.x/);
    assert.match(LAYERED, /cameraFollowRatio: 0\.45/);
    assert.equal(PLAYER.match(/<video/g)?.length, 2);
    assert.doesNotMatch(PLAYER, /drop-shadow|filter: `blur/);
    assert.match(MATTE, /alpha_bounds/);
    assert.match(MATTE, /contact_shadow/);
  });

  test("mobile rendering is lazy, DPR-capped, frame-bounded, and pauses all V2 decoders", () => {
    assert.match(SCREEN, /const ShakerLayeredPlayer = lazy\(\(\) =>/);
    assert.match(PLAYER, /maxDevicePixelRatio/);
    assert.match(LAYERED, /maxDevicePixelRatio: 1\.5/);
    assert.match(PLAYER, /if \(disposed \|\| document\.hidden \|\| !renderer\) return/);
    assert.match(PLAYER, /window\.requestAnimationFrame\(render\)/);
    assert.match(PLAYER, /if \(animationFrame !== null\) window\.cancelAnimationFrame\(animationFrame\)/);
    assert.match(PLAYER, /for \(const video of videos\) video\.pause\(\)/);
    assert.match(PLAYER, /Promise\.all\(videos\.map\(\(video\) => video\.play\(\)\)\)/);
    assert.doesNotMatch(PLAYER, /setPetFrameState|setCameraOffset|setGyroFrame/);
  });

  test("background media is conditional and shadow/lighting add no decoder", () => {
    assert.match(PLAYER, /if \(type === "video"\)/);
    assert.match(PLAYER, /<HiddenSceneMedia\s+type=\{manifest\.background\.type\}/s);
    assert.match(PLAYER, /const videos = \[petVideo, background, foreground\]\.filter/);
    assert.match(PLAYER, /shadowMesh = new THREE\.Mesh/);
    assert.doesNotMatch(PLAYER, /shadowVideo|lightingVideo|rimVideo/);
  });

  test("foreground is optional, closer than the pet, and renders after it for occlusion", () => {
    assert.match(PLAYER, /useState\(!manifest\.foreground\)/);
    assert.match(PLAYER, /foreground\s*\? makeMediaPlane\(foreground, LAYERED_WEBGL_SCENE\.foregroundZ, 4, true\)/);
    assert.match(PLAYER, /petMesh\.renderOrder = 3/);
    assert.match(LAYERED, /foregroundZ: 3/);
    assert.match(LAYERED, /petZ: 0/);
  });

  test("premium actions stay on the existing V1 player", () => {
    assert.match(SCREEN, /eventSources=\{vm\.eventSources\}/);
    assert.match(SCREEN, /onActionStateChange=\{onActionStateChange\}/);
    assert.match(SCREEN, /setActionOverlay\(true\)/);
  });
});

describe("non-blocking generation extension", () => {
  test("the HTTP route uses BackgroundTasks and V2 cannot replace idle_url", () => {
    assert.match(GENERATE, /background_tasks\.add_task\(/);
    assert.match(GENERATE, /v1_video_url=idle_url/);
    assert.match(GENERATE, /"idle_video_url": idle_url/);
    assert.ok(
      GENERATE.indexOf("mark_completed(") < GENERATE.lastIndexOf("_schedule_layered_v2("),
      "V1 must be completed before optional V2 is queued",
    );
  });

  test("a completed historical V1 reuse is not automatically backfilled", () => {
    const reusedStart = GENERATE.indexOf("if done and done.completed:");
    const reusedEnd = GENERATE.indexOf("if done and done.is_stale_reservation", reusedStart);
    const reusedBranch = GENERATE.slice(reusedStart, reusedEnd);
    assert.doesNotMatch(reusedBranch, /_schedule_layered_v2\(/);
  });

  test("original-photo scenes never advertise an independent V2 background", () => {
    assert.match(SCENE, /if \(!isOriginal && backgroundUrl/);
  });

  test("V2 requires real alpha matting and temporal stabilization", () => {
    assert.match(MATTE, /temporal_alpha_stabilization=True/);
    assert.match(MATTE, /require_alpha_matting=True/);
    assert.doesNotMatch(MATTE, /black.?key|chroma.?key/i);
  });
});
