"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import * as THREE from "three";

import type { ShakerLayeredManifest, ShakerLayeredMediaType } from "@/lib/shaker-api";
import {
  CONTACT_SHADOW_FRAGMENT_SHADER,
  LAYERED_CONTACT_SHADOW,
  LAYERED_MEDIA_FRAGMENT_SHADER,
  LAYERED_PARALLAX,
  LAYERED_WEBGL_SCENE,
  PACKED_ALPHA_FRAGMENT_SHADER,
  PACKED_ALPHA_VERTEX_SHADER,
  contactShadowCameraCompensation,
  coverUvScale,
  perspectiveCameraOffsetFromPetFrame,
  perspectivePlaneSizeAtZ,
  rigidPetRotationFromOffset,
  topOriginCropToUv,
  verticalLayerOffsetsFromPetOffset,
} from "@/lib/shaker-layered";
import {
  alignGyroSampleToScreen,
  createOrientationMotionSession,
  createParallaxFrameLoop,
  pointerToGyroSample,
} from "@/lib/shaker-gyro";

export type LayeredMotionMode = "sensor" | "pointer" | "off";

interface ShakerLayeredPlayerProps {
  manifest: ShakerLayeredManifest;
  motionMode: LayeredMotionMode;
  /** Query-gated real-device diagnostic; never enabled by the manifest/API. */
  debugMotion?: boolean;
  onActiveChange?: (active: boolean) => void;
  onFailure?: (reason: string) => void;
  className?: string;
}

interface SceneMotionController {
  setPetFrame(x: number, y: number): void;
  reset(): void;
}

type SceneMediaElement = HTMLImageElement | HTMLVideoElement;

function finite(value: unknown, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function isVideo(element: SceneMediaElement): element is HTMLVideoElement {
  return element.tagName === "VIDEO";
}

function sourceSize(element: SceneMediaElement): readonly [number, number] {
  if (isVideo(element)) {
    return [Math.max(1, element.videoWidth), Math.max(1, element.videoHeight)];
  }
  return [Math.max(1, element.naturalWidth), Math.max(1, element.naturalHeight)];
}

function HiddenSceneMedia({
  type,
  url,
  imageRef,
  videoRef,
  onReady,
  onError,
}: {
  type: ShakerLayeredMediaType;
  url: string;
  imageRef: RefObject<HTMLImageElement | null>;
  videoRef: RefObject<HTMLVideoElement | null>;
  onReady: () => void;
  onError: () => void;
}) {
  if (type === "video") {
    return (
      <video
        ref={videoRef}
        src={url}
        crossOrigin="anonymous"
        autoPlay
        muted
        loop
        playsInline
        preload="auto"
        className="pointer-events-none absolute h-px w-px opacity-0"
        onCanPlay={onReady}
        onError={onError}
        aria-hidden
      />
    );
  }
  return (
    <img
      ref={imageRef}
      src={url}
      crossOrigin="anonymous"
      alt=""
      className="pointer-events-none absolute h-px w-px opacity-0"
      onLoad={onReady}
      onError={onError}
      aria-hidden
    />
  );
}

/**
 * Production V2 BREATHING renderer.
 *
 * All available visual layers live in one real Three.js perspective scene.
 * Gyro input moves only the camera. The packed-alpha pet remains one rigid
 * plane with no per-pixel deformation or color-based transparency removal.
 */
export function ShakerLayeredPlayer({
  manifest,
  motionMode,
  debugMotion = false,
  onActiveChange,
  onFailure,
  className = "",
}: ShakerLayeredPlayerProps) {
  const [backgroundReady, setBackgroundReady] = useState(false);
  const [petVideoReady, setPetVideoReady] = useState(false);
  const [rendererReady, setRendererReady] = useState(false);
  const [foregroundReady, setForegroundReady] = useState(!manifest.foreground);
  const [failed, setFailed] = useState(false);
  const failureSent = useRef(false);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const petVideoRef = useRef<HTMLVideoElement>(null);
  const backgroundImageRef = useRef<HTMLImageElement>(null);
  const backgroundVideoRef = useRef<HTMLVideoElement>(null);
  const foregroundImageRef = useRef<HTMLImageElement>(null);
  const foregroundVideoRef = useRef<HTMLVideoElement>(null);
  const sceneMotionRef = useRef<SceneMotionController | null>(null);
  const motionDebugRef = useRef<HTMLDivElement>(null);

  const active =
    backgroundReady && petVideoReady && rendererReady && foregroundReady && !failed;

  const fail = useCallback((reason: string) => {
    setFailed(true);
    if (!failureSent.current) {
      failureSent.current = true;
      onFailure?.(reason);
    }
  }, [onFailure]);

  const writeMotionDebug = useCallback((value: string) => {
    if (debugMotion && motionDebugRef.current) motionDebugRef.current.textContent = value;
  }, [debugMotion]);

  useEffect(() => {
    onActiveChange?.(active);
    return () => onActiveChange?.(false);
  }, [active, onActiveChange]);

  useEffect(() => {
    const id = window.setTimeout(() => {
      if (!active) fail("asset-ready-timeout");
    }, LAYERED_PARALLAX.assetReadyTimeoutMs);
    return () => window.clearTimeout(id);
  }, [active, fail]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const petVideo = petVideoRef.current;
    const background = manifest.background.type === "video"
      ? backgroundVideoRef.current
      : backgroundImageRef.current;
    const foreground = manifest.foreground
      ? manifest.foreground.type === "video"
        ? foregroundVideoRef.current
        : foregroundImageRef.current
      : null;
    if (!canvas || !petVideo || !background || (manifest.foreground && !foreground)) return;

    let disposed = false;
    let animationFrame: number | null = null;
    let resizeObserver: ResizeObserver | null = null;
    let readySent = false;
    let renderer: THREE.WebGLRenderer | null = null;
    const textures: THREE.Texture[] = [];
    const materials: THREE.Material[] = [];
    const geometry = new THREE.PlaneGeometry(1, 1);

    const failRenderer = () => {
      if (!disposed) fail("webgl-renderer-error");
    };

    try {
      renderer = new THREE.WebGLRenderer({
        canvas,
        alpha: false,
        antialias: true,
        premultipliedAlpha: true,
        powerPreference: "high-performance",
      });
    } catch {
      failRenderer();
      return;
    }

    renderer.setClearColor(0x050609, 1);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.setPixelRatio(Math.min(
      window.devicePixelRatio || 1,
      LAYERED_PARALLAX.maxDevicePixelRatio,
    ));

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(
      LAYERED_WEBGL_SCENE.cameraFovDeg,
      1,
      LAYERED_WEBGL_SCENE.cameraNear,
      LAYERED_WEBGL_SCENE.cameraFar,
    );
    camera.position.set(0, 0, LAYERED_WEBGL_SCENE.cameraZ);
    camera.rotation.set(0, 0, 0);

    const makeTexture = (element: SceneMediaElement, colorSpace: THREE.ColorSpace) => {
      const texture = isVideo(element)
        ? new THREE.VideoTexture(element)
        : new THREE.Texture(element);
      texture.colorSpace = colorSpace;
      texture.minFilter = THREE.LinearFilter;
      texture.magFilter = THREE.LinearFilter;
      texture.generateMipmaps = false;
      texture.wrapS = THREE.ClampToEdgeWrapping;
      texture.wrapT = THREE.ClampToEdgeWrapping;
      if (!isVideo(element) && element.complete) texture.needsUpdate = true;
      textures.push(texture);
      return texture;
    };

    const makeMediaPlane = (
      element: SceneMediaElement,
      z: number,
      renderOrder: number,
      transparent: boolean,
    ) => {
      const material = new THREE.ShaderMaterial({
        uniforms: {
          uMedia: { value: makeTexture(element, THREE.SRGBColorSpace) },
          uCoverScale: { value: new THREE.Vector2(1, 1) },
        },
        vertexShader: PACKED_ALPHA_VERTEX_SHADER,
        fragmentShader: LAYERED_MEDIA_FRAGMENT_SHADER,
        transparent,
        depthTest: true,
        depthWrite: !transparent,
      });
      materials.push(material);
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.z = z;
      mesh.renderOrder = renderOrder;
      scene.add(mesh);
      return { element, material, mesh, z };
    };

    const farBackground = makeMediaPlane(
      background,
      LAYERED_WEBGL_SCENE.farBackgroundZ,
      0,
      false,
    );

    // Deliberately empty until a verified pet-free midground asset exists.
    // Duplicating the background here would recreate the doubled-edge defect.
    const midgroundSlot = new THREE.Group();
    midgroundSlot.name = "verified-midground-slot";
    midgroundSlot.position.z = LAYERED_WEBGL_SCENE.midgroundZ;
    scene.add(midgroundSlot);

    const shadow = manifest.shadow?.kind === "css-contact" ? manifest.shadow : null;
    const shadowOpacity = clamp(
      finite(shadow?.opacity, LAYERED_CONTACT_SHADOW.defaultOpacity),
      0,
      LAYERED_CONTACT_SHADOW.maxOpacity,
    );
    const shadowBlurPx = clamp(
      finite(shadow?.blur_px, LAYERED_CONTACT_SHADOW.defaultBlurPx),
      LAYERED_CONTACT_SHADOW.minBlurPx,
      LAYERED_CONTACT_SHADOW.maxBlurPx,
    );
    let shadowMesh: THREE.Mesh<THREE.PlaneGeometry, THREE.ShaderMaterial> | null = null;
    let shadowMaterial: THREE.ShaderMaterial | null = null;
    let shadowBaseX = 0;
    let shadowBaseY = 0;
    let currentCameraOffset = { x: 0, y: 0 };
    const placeShadowForCamera = () => {
      if (!shadowMesh) return;
      const compensation = contactShadowCameraCompensation(currentCameraOffset);
      shadowMesh.position.x = shadowBaseX + compensation.x;
      shadowMesh.position.y = shadowBaseY + compensation.y;
    };
    if (shadow) {
      shadowMaterial = new THREE.ShaderMaterial({
        uniforms: {
          uOpacity: { value: shadowOpacity },
          uSoftness: { value: clamp(shadowBlurPx / 18, 0.35, 0.9) },
        },
        vertexShader: PACKED_ALPHA_VERTEX_SHADER,
        fragmentShader: CONTACT_SHADOW_FRAGMENT_SHADER,
        transparent: true,
        depthTest: true,
        depthWrite: false,
      });
      materials.push(shadowMaterial);
      shadowMesh = new THREE.Mesh(geometry, shadowMaterial);
      shadowMesh.position.z = LAYERED_WEBGL_SCENE.shadowZ;
      shadowMesh.renderOrder = 2;
      scene.add(shadowMesh);
    }

    const petTexture = makeTexture(petVideo, THREE.NoColorSpace);
    const petMaterial = new THREE.ShaderMaterial({
      uniforms: {
        uPackedVideo: { value: petTexture },
        uCropX: { value: new THREE.Vector2(0, 1) },
        uCropY: { value: new THREE.Vector2(0, 1) },
        uCoverScale: { value: new THREE.Vector2(1, 1) },
      },
      vertexShader: PACKED_ALPHA_VERTEX_SHADER,
      fragmentShader: PACKED_ALPHA_FRAGMENT_SHADER,
      transparent: true,
      premultipliedAlpha: false,
      depthTest: true,
      depthWrite: false,
    });
    materials.push(petMaterial);
    const petMesh = new THREE.Mesh(geometry, petMaterial);
    petMesh.position.z = LAYERED_WEBGL_SCENE.petZ;
    petMesh.renderOrder = 3;
    scene.add(petMesh);

    const foregroundPlane = foreground
      ? makeMediaPlane(foreground, LAYERED_WEBGL_SCENE.foregroundZ, 4, true)
      : null;

    const placement = manifest.placement;
    const anchored = placement.mode === "anchored";
    const cropXMin = clamp(finite(placement.crop_x_min, 0), 0, 0.95);
    const cropXMax = clamp(finite(placement.crop_x_max, 1), cropXMin + 0.01, 1);
    const cropYMin = clamp(finite(placement.crop_y_min, 0), 0, 0.95);
    const cropYMax = clamp(finite(placement.crop_y_max, 1), cropYMin + 0.01, 1);
    // QA bounds come from OpenCV (Y=0 at the top). WebGL video UVs use Y=0
    // at the bottom, so using manifest values directly samples transparent
    // space above the pet and makes an otherwise READY pet disappear.
    const [cropUvYMin, cropUvYMax] = topOriginCropToUv(cropYMin, cropYMax);
    (petMaterial.uniforms.uCropX.value as THREE.Vector2).set(cropXMin, cropXMax);
    (petMaterial.uniforms.uCropY.value as THREE.Vector2).set(cropUvYMin, cropUvYMax);

    const resize = () => {
      if (!renderer || disposed) return;
      const parent = canvas.parentElement;
      const width = Math.max(1, parent?.clientWidth || canvas.clientWidth || 1);
      const height = Math.max(1, parent?.clientHeight || canvas.clientHeight || 1);
      const aspect = width / height;
      renderer.setSize(width, height, false);
      camera.aspect = aspect;
      camera.updateProjectionMatrix();

      const sizePlane = (plane: typeof farBackground, overscan: number) => {
        const [worldWidth, worldHeight] = perspectivePlaneSizeAtZ(aspect, plane.z, overscan);
        plane.mesh.scale.set(worldWidth, worldHeight, 1);
        const [sourceWidth, sourceHeight] = sourceSize(plane.element);
        const [coverX, coverY] = coverUvScale(sourceWidth, sourceHeight, width, height);
        (plane.material.uniforms.uCoverScale.value as THREE.Vector2).set(
          coverX * overscan,
          coverY * overscan,
        );
        if (!isVideo(plane.element) && plane.element.complete) {
          (plane.material.uniforms.uMedia.value as THREE.Texture).needsUpdate = true;
        }
      };

      sizePlane(farBackground, LAYERED_WEBGL_SCENE.farOverscan);
      if (foregroundPlane) {
        sizePlane(foregroundPlane, LAYERED_WEBGL_SCENE.foregroundOverscan);
      }

      const [petViewWidth, petViewHeight] = perspectivePlaneSizeAtZ(
        aspect,
        LAYERED_WEBGL_SCENE.petZ,
      );
      const packedWidth = Math.max(1, petVideo.videoWidth || 1);
      const packedFrameHeight = Math.max(1, (petVideo.videoHeight || 2) / 2);
      if (anchored) {
        const heightRatio = clamp(finite(placement.height_pct, 50), 5, 100) / 100;
        const centerXRatio = clamp(finite(placement.center_x_pct, 50), 0, 100) / 100;
        const bottomRatio = clamp(finite(placement.bottom_pct, 10), 0, 80) / 100;
        const croppedAspect =
          (packedWidth * (cropXMax - cropXMin)) /
          (packedFrameHeight * (cropYMax - cropYMin));
        const petHeight = petViewHeight * heightRatio;
        const petWidth = petHeight * Math.max(0.05, croppedAspect);
        petMesh.scale.set(petWidth, petHeight, 1);
        petMesh.position.x = (centerXRatio - 0.5) * petViewWidth;
        petMesh.position.y = -petViewHeight / 2 + bottomRatio * petViewHeight + petHeight / 2;
        (petMaterial.uniforms.uCoverScale.value as THREE.Vector2).set(1, 1);
      } else {
        const overscan = LAYERED_WEBGL_SCENE.petOverscan;
        petMesh.scale.set(petViewWidth * overscan, petViewHeight * overscan, 1);
        petMesh.position.x = 0;
        petMesh.position.y = 0;
        const [coverX, coverY] = coverUvScale(
          packedWidth,
          packedFrameHeight,
          width,
          height,
        );
        (petMaterial.uniforms.uCoverScale.value as THREE.Vector2).set(
          coverX * overscan,
          coverY * overscan,
        );
      }

      if (shadowMesh) {
        const [shadowViewWidth, shadowViewHeight] = perspectivePlaneSizeAtZ(
          aspect,
          LAYERED_WEBGL_SCENE.shadowZ,
        );
        const containerWidth = anchored ? petMesh.scale.x : shadowViewWidth;
        const containerHeight = anchored ? petMesh.scale.y : shadowViewHeight;
        const containerX = anchored ? petMesh.position.x : 0;
        const containerBottom = anchored
          ? petMesh.position.y - petMesh.scale.y / 2
          : -shadowViewHeight / 2;
        const centerX = clamp(finite(shadow?.center_x_pct, 50), 0, 100) / 100;
        const bottom = clamp(finite(shadow?.bottom_pct, 7), 0, 80) / 100;
        const shadowWidth = containerWidth * clamp(finite(shadow?.width_pct, 38), 8, 70) / 100;
        const shadowHeight = containerHeight * clamp(finite(shadow?.height_pct, 4), 1, 12) / 100;
        shadowMesh.scale.set(shadowWidth, shadowHeight, 1);
        shadowBaseX = containerX + (centerX - 0.5) * containerWidth;
        shadowBaseY = containerBottom + bottom * containerHeight + shadowHeight / 2;
        placeShadowForCamera();
      }
    };

    sceneMotionRef.current = {
      setPetFrame(x, y) {
        const offset = perspectiveCameraOffsetFromPetFrame(x, y);
        const petRotation = rigidPetRotationFromOffset(x, y);
        currentCameraOffset = offset;
        camera.position.set(offset.x, offset.y, LAYERED_WEBGL_SCENE.cameraZ);
        camera.updateMatrixWorld();
        placeShadowForCamera();
        petMesh.rotation.set(
          THREE.MathUtils.degToRad(petRotation.rotateXDeg),
          THREE.MathUtils.degToRad(petRotation.rotateYDeg),
          0,
        );
        if (shadowMaterial) {
          const tilt = clamp(Math.max(
            Math.abs(x) / LAYERED_PARALLAX.petMaxPx,
            Math.abs(y) / LAYERED_PARALLAX.petVerticalMaxPx,
          ), 0, 1);
          shadowMaterial.uniforms.uOpacity.value =
            shadowOpacity * (1 - tilt * LAYERED_CONTACT_SHADOW.tiltOpacityReduction);
          shadowMaterial.uniforms.uSoftness.value = clamp(
            shadowBlurPx / 18 + tilt * 0.08,
            0.35,
            0.95,
          );
        }
      },
      reset() {
        currentCameraOffset = { x: 0, y: 0 };
        camera.position.set(0, 0, LAYERED_WEBGL_SCENE.cameraZ);
        camera.rotation.set(0, 0, 0);
        camera.updateMatrixWorld();
        petMesh.rotation.set(0, 0, 0);
        placeShadowForCamera();
        if (shadowMaterial) {
          shadowMaterial.uniforms.uOpacity.value = shadowOpacity;
          shadowMaterial.uniforms.uSoftness.value = clamp(shadowBlurPx / 18, 0.35, 0.9);
        }
      },
    };

    const videos = [petVideo, background, foreground].filter(
      (element): element is HTMLVideoElement => Boolean(element && isVideo(element)),
    );

    const render = () => {
      animationFrame = null;
      if (disposed || document.hidden || !renderer) return;
      renderer.render(scene, camera);
      if (!readySent) {
        readySent = true;
        setRendererReady(true);
      }
      animationFrame = window.requestAnimationFrame(render);
    };

    const start = () => {
      if (disposed || document.hidden || animationFrame !== null) return;
      resize();
      renderer?.render(scene, camera);
      animationFrame = window.requestAnimationFrame(render);
    };

    const visibility = () => {
      if (document.hidden) {
        if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
        animationFrame = null;
        for (const video of videos) video.pause();
        return;
      }
      void Promise.all(videos.map((video) => video.play())).then(start).catch(failRenderer);
    };
    const contextLost = (event: Event) => {
      event.preventDefault();
      failRenderer();
    };

    const resizeSources = [background, foreground, petVideo].filter(Boolean) as SceneMediaElement[];
    for (const element of resizeSources) {
      element.addEventListener(isVideo(element) ? "loadedmetadata" : "load", resize);
    }
    document.addEventListener("visibilitychange", visibility);
    canvas.addEventListener("webglcontextlost", contextLost);
    window.addEventListener("resize", resize);
    if (typeof ResizeObserver !== "undefined") {
      resizeObserver = new ResizeObserver(resize);
      if (canvas.parentElement) resizeObserver.observe(canvas.parentElement);
    }
    resize();
    start();

    return () => {
      disposed = true;
      sceneMotionRef.current = null;
      if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
      // A runtime failure unmounts V2 immediately and reveals V1. Stop every
      // V2 decoder explicitly instead of relying on DOM removal/GC timing,
      // which is especially important for two-video themes on mobile Safari.
      for (const video of videos) video.pause();
      for (const element of resizeSources) {
        element.removeEventListener(isVideo(element) ? "loadedmetadata" : "load", resize);
      }
      document.removeEventListener("visibilitychange", visibility);
      canvas.removeEventListener("webglcontextlost", contextLost);
      window.removeEventListener("resize", resize);
      resizeObserver?.disconnect();
      for (const texture of textures) texture.dispose();
      for (const material of materials) material.dispose();
      geometry.dispose();
      renderer?.dispose();
    };
  }, [fail, manifest]);

  const resetCamera = useCallback(() => {
    sceneMotionRef.current?.reset();
  }, []);

  useEffect(() => {
    resetCamera();
    if (!active) {
      writeMotionDebug("motion: V2 media loading");
      return;
    }
    if (motionMode === "off") {
      writeMotionDebug("motion: permission off · WebGL scene active");
      return;
    }
    writeMotionDebug("motion: waiting for sensor sample");

    const frameLoop = createParallaxFrameLoop({
      config: {
        backgroundMaxPx: LAYERED_PARALLAX.backgroundMaxPx,
        petMaxPx: LAYERED_PARALLAX.petMaxPx,
        rangeDeg: LAYERED_PARALLAX.tiltRangeDeg,
        deadZoneDeg: LAYERED_PARALLAX.deadZoneDeg,
        smoothing: LAYERED_PARALLAX.smoothing,
      },
      onFrame(frame) {
        const vertical = verticalLayerOffsetsFromPetOffset(frame.pet.y);
        sceneMotionRef.current?.setPetFrame(frame.pet.x, vertical.petY);
        writeMotionDebug(
          `motion: WebGL camera · x ${frame.pet.x.toFixed(1)} · y ${vertical.petY.toFixed(1)}`,
        );
      },
      requestFrame: (callback) => window.requestAnimationFrame(callback),
      cancelFrame: (id) => window.cancelAnimationFrame(id),
    });

    if (motionMode === "pointer") {
      const onPointer = (event: PointerEvent) => {
        frameLoop.push(pointerToGyroSample(
          { x: event.clientX, y: event.clientY },
          { width: window.innerWidth, height: window.innerHeight },
        ));
      };
      window.addEventListener("pointermove", onPointer, { passive: true });
      return () => {
        window.removeEventListener("pointermove", onPointer);
        frameLoop.destroy();
        resetCamera();
      };
    }

    const session = createOrientationMotionSession({
      frameLoop,
      subscribeOrientation(listener) {
        const handler = (event: DeviceOrientationEvent) => {
          const modern = window.screen.orientation?.angle;
          const legacy = (window as unknown as { orientation?: number }).orientation;
          const aligned = alignGyroSampleToScreen(
            { beta: event.beta, gamma: event.gamma },
            typeof modern === "number" ? modern : typeof legacy === "number" ? legacy : 0,
          );
          listener({
            beta: typeof aligned.beta === "number"
              ? aligned.beta * LAYERED_PARALLAX.verticalTiltGain
              : aligned.beta,
            gamma: aligned.gamma,
          });
        };
        window.addEventListener("deviceorientation", handler, { passive: true });
        return () => window.removeEventListener("deviceorientation", handler);
      },
      subscribeOrientationChange(listener) {
        window.addEventListener("orientationchange", listener);
        return () => window.removeEventListener("orientationchange", listener);
      },
      subscribeVisibilityChange(listener) {
        document.addEventListener("visibilitychange", listener);
        return () => document.removeEventListener("visibilitychange", listener);
      },
      isHidden: () => document.hidden,
      scheduleTimeout: (callback, delayMs) => window.setTimeout(callback, delayMs),
      cancelTimeout: (handle) => window.clearTimeout(handle as number),
      onActiveChange(sensorActive) {
        if (!sensorActive) {
          resetCamera();
          writeMotionDebug("motion: no valid sensor samples · WebGL scene static");
        }
      },
    });
    session.start();
    return () => {
      session.destroy();
      resetCamera();
    };
  }, [active, motionMode, resetCamera, writeMotionDebug]);

  return (
    <section
      className={`pointer-events-none absolute inset-0 overflow-hidden ${className}`}
      data-layered-active={active}
      data-renderer="multi-plane-webgl"
    >
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />

      <HiddenSceneMedia
        type={manifest.background.type}
        url={manifest.background.url}
        imageRef={backgroundImageRef}
        videoRef={backgroundVideoRef}
        onReady={() => setBackgroundReady(true)}
        onError={() => fail("background-media-error")}
      />

      {manifest.foreground ? (
        <HiddenSceneMedia
          type={manifest.foreground.type}
          url={manifest.foreground.url}
          imageRef={foregroundImageRef}
          videoRef={foregroundVideoRef}
          onReady={() => setForegroundReady(true)}
          onError={() => fail("foreground-media-error")}
        />
      ) : null}

      <video
        ref={petVideoRef}
        src={manifest.pet.url}
        crossOrigin="anonymous"
        autoPlay
        muted
        loop
        playsInline
        preload="auto"
        className="pointer-events-none absolute h-px w-px opacity-0"
        onCanPlay={() => setPetVideoReady(true)}
        onError={() => fail("pet-media-error")}
        aria-hidden
      />

      {debugMotion ? (
        <div
          ref={motionDebugRef}
          className="absolute left-3 top-3 z-[10] rounded bg-black/75 px-2 py-1 font-mono text-[11px] text-emerald-300"
          aria-live="polite"
        >
          motion: initializing WebGL scene
        </div>
      ) : null}
    </section>
  );
}
