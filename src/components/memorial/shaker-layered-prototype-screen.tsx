"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";

import {
  alignGyroSampleToScreen,
  createOrientationMotionSession,
  createParallaxFrameLoop,
  detectGyroSupport,
  prefersReducedMotion,
  requestGyroPermission,
  type GyroPermission,
} from "@/lib/shaker-gyro";
import {
  LAYERED_PARALLAX,
  PACKED_ALPHA_FRAGMENT_SHADER,
  PACKED_ALPHA_VERTEX_SHADER,
  PACKED_PET_CROP,
  SHAKER_LAYERED_PROTOTYPE_ASSETS,
  type LayeredBackgroundMode,
  foregroundOffsetFromPet,
} from "@/lib/shaker-layered-prototype";

function translateScale(x: number, y: number, scale: number): string {
  return `translate3d(${x.toFixed(2)}px, ${y.toFixed(2)}px, 0) scale(${scale})`;
}

function resetTransform(
  background: HTMLElement | null,
  pet: HTMLElement | null,
  foreground: HTMLElement | null,
  fallback: HTMLElement | null,
) {
  if (background) background.style.transform = translateScale(0, 0, LAYERED_PARALLAX.backgroundOverscan);
  if (pet) pet.style.transform = translateScale(0, 0, 1);
  if (foreground) foreground.style.transform = translateScale(0, 0, 1.02);
  if (fallback) fallback.style.transform = translateScale(0, 0, LAYERED_PARALLAX.fallbackOverscan);
}

export function ShakerLayeredPrototypeScreen() {
  const reducedMotion = useMemo(prefersReducedMotion, []);
  const gyroSupport = useMemo(detectGyroSupport, []);
  const [permission, setPermission] = useState<GyroPermission | null>(() =>
    gyroSupport === "auto" ? "granted" : null,
  );
  const [backgroundMode, setBackgroundMode] = useState<LayeredBackgroundMode>(() =>
    new URLSearchParams(window.location.search).get("background") === "video"
      ? "video"
      : "image",
  );
  const [backgroundReady, setBackgroundReady] = useState(false);
  const [backgroundFailed, setBackgroundFailed] = useState(false);
  const [petVideoReady, setPetVideoReady] = useState(false);
  const [petRendererReady, setPetRendererReady] = useState(false);
  const [petFailed, setPetFailed] = useState(false);
  const [sensorActive, setSensorActive] = useState(false);

  const backgroundLayerRef = useRef<HTMLDivElement>(null);
  const petLayerRef = useRef<HTMLDivElement>(null);
  const foregroundLayerRef = useRef<HTMLDivElement>(null);
  const fallbackLayerRef = useRef<HTMLVideoElement>(null);
  const packedVideoRef = useRef<HTMLVideoElement>(null);

  const layeredReady =
    backgroundReady && petVideoReady && petRendererReady && !backgroundFailed && !petFailed;

  useEffect(() => {
    setBackgroundReady(false);
    setBackgroundFailed(false);
  }, [backgroundMode]);

  useEffect(() => {
    resetTransform(
      backgroundLayerRef.current,
      petLayerRef.current,
      foregroundLayerRef.current,
      fallbackLayerRef.current,
    );
    if (permission !== "granted" || reducedMotion) return;

    const frameLoop = createParallaxFrameLoop({
      config: {
        backgroundMaxPx: LAYERED_PARALLAX.backgroundMaxPx,
        petMaxPx: LAYERED_PARALLAX.petMaxPx,
      },
      onFrame(frame) {
        const foregroundX = foregroundOffsetFromPet(frame.pet.x);
        const foregroundY = foregroundOffsetFromPet(frame.pet.y);
        if (backgroundLayerRef.current) {
          backgroundLayerRef.current.style.transform = translateScale(
            frame.background.x,
            frame.background.y,
            LAYERED_PARALLAX.backgroundOverscan,
          );
        }
        if (petLayerRef.current) {
          petLayerRef.current.style.transform = translateScale(frame.pet.x, frame.pet.y, 1);
        }
        if (foregroundLayerRef.current) {
          foregroundLayerRef.current.style.transform = translateScale(
            foregroundX,
            foregroundY,
            1.02,
          );
        }
        if (fallbackLayerRef.current) {
          fallbackLayerRef.current.style.transform = translateScale(
            frame.pet.x,
            frame.pet.y,
            LAYERED_PARALLAX.fallbackOverscan,
          );
        }
      },
      requestFrame: (callback) => window.requestAnimationFrame(callback),
      cancelFrame: (id) => window.cancelAnimationFrame(id),
    });
    const session = createOrientationMotionSession({
      frameLoop,
      subscribeOrientation(listener) {
        const handler = (event: DeviceOrientationEvent) => {
          const modernAngle = window.screen.orientation?.angle;
          const legacyAngle = (window as unknown as { orientation?: number }).orientation;
          const screenAngle =
            typeof modernAngle === "number"
              ? modernAngle
              : typeof legacyAngle === "number"
                ? legacyAngle
                : 0;
          listener(
            alignGyroSampleToScreen(
              { beta: event.beta, gamma: event.gamma },
              screenAngle,
            ),
          );
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
      onActiveChange(active) {
        setSensorActive(active);
        if (!active) {
          resetTransform(
            backgroundLayerRef.current,
            petLayerRef.current,
            foregroundLayerRef.current,
            fallbackLayerRef.current,
          );
        }
      },
    });
    session.start();

    return () => {
      session.destroy();
      setSensorActive(false);
      resetTransform(
        backgroundLayerRef.current,
        petLayerRef.current,
        foregroundLayerRef.current,
        fallbackLayerRef.current,
      );
    };
  }, [permission, reducedMotion]);

  const askMotion = useCallback(async () => {
    setPermission(await requestGyroPermission());
  }, []);

  const markPetFailure = useCallback(() => {
    setPetFailed(true);
    setPetRendererReady(false);
  }, []);
  const markPetRendererReady = useCallback(() => setPetRendererReady(true), []);

  const status = petFailed || backgroundFailed
    ? "Layered asset unavailable — V1 baked fallback"
    : !layeredReady
      ? "Loading layered assets — V1 baked fallback"
      : reducedMotion
        ? "Layered playback — reduced motion"
        : gyroSupport === "unsupported" || permission === "denied" || permission === "unavailable"
          ? "Layered playback — motion unavailable"
          : sensorActive
            ? "Layered 2.5D motion active"
            : "Layered playback — waiting for sensor";

  return (
    <main
      className="fixed inset-0 overflow-hidden bg-[#050609] text-white"
      data-background-ready={backgroundReady}
      data-pet-video-ready={petVideoReady}
      data-pet-renderer-ready={petRendererReady}
      data-background-failed={backgroundFailed}
      data-pet-failed={petFailed}
    >
      <video
        ref={fallbackLayerRef}
        src={SHAKER_LAYERED_PROTOTYPE_ASSETS.bakedFallback}
        poster={SHAKER_LAYERED_PROTOTYPE_ASSETS.imageBackground}
        autoPlay
        muted
        loop
        playsInline
        preload="auto"
        className={`absolute inset-0 h-full w-full object-cover will-change-transform transition-opacity duration-200 ${
          layeredReady ? "opacity-0" : "opacity-100"
        }`}
        aria-label="Existing V1 baked video fallback"
      />

      <section
        className={`absolute inset-0 transition-opacity duration-200 ${
          layeredReady ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        aria-hidden={!layeredReady}
      >
        <div
          ref={backgroundLayerRef}
          className="absolute inset-0 will-change-transform"
          style={{ transform: translateScale(0, 0, LAYERED_PARALLAX.backgroundOverscan) }}
        >
          {backgroundMode === "image" ? (
            <img
              src={SHAKER_LAYERED_PROTOTYPE_ASSETS.imageBackground}
              alt="Static forest prototype background"
              className="h-full w-full object-cover"
              onLoad={() => setBackgroundReady(true)}
              onError={() => setBackgroundFailed(true)}
            />
          ) : (
            <video
              src={SHAKER_LAYERED_PROTOTYPE_ASSETS.videoBackground}
              autoPlay
              muted
              loop
              playsInline
              preload="auto"
              className="h-full w-full object-cover"
              onCanPlay={() => setBackgroundReady(true)}
              onError={() => setBackgroundFailed(true)}
            />
          )}
        </div>

        <div
          className="absolute bottom-[10%] left-1/2 z-[2] h-[min(50vh,520px)] -translate-x-1/2"
          style={{
            aspectRatio: `${1284 * (PACKED_PET_CROP.xMax - PACKED_PET_CROP.xMin)} / 716`,
          }}
        >
          <div ref={petLayerRef} className="absolute inset-0 will-change-transform">
            <div
              className="absolute bottom-[3%] left-1/2 h-[6%] w-[52%] -translate-x-1/2 rounded-[50%] bg-black/45 blur-[10px]"
              aria-hidden="true"
            />
            <PackedAlphaPetRenderer
              videoRef={packedVideoRef}
              onReady={markPetRendererReady}
              onFailure={markPetFailure}
            />
          </div>
        </div>

        <div
          ref={foregroundLayerRef}
          className="pointer-events-none absolute inset-[-2%] z-[3] will-change-transform"
          style={{
            background:
              "radial-gradient(circle at 50% 42%, transparent 56%, rgba(4, 9, 8, 0.14) 100%)",
          }}
          aria-hidden="true"
        />
      </section>

      <video
        ref={packedVideoRef}
        src={SHAKER_LAYERED_PROTOTYPE_ASSETS.petPackedAlpha}
        autoPlay
        muted
        loop
        playsInline
        preload="auto"
        className="pointer-events-none absolute h-px w-px opacity-0"
        onCanPlay={() => setPetVideoReady(true)}
        onError={markPetFailure}
        aria-hidden="true"
      />

      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex items-start justify-between gap-3 p-[max(1rem,env(safe-area-inset-top))]">
        <div className="rounded-2xl border border-white/15 bg-black/50 px-3 py-2 backdrop-blur-md">
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-emerald-300">
            V2 layered prototype
          </p>
          <p className="mt-1 text-xs text-white/70">{status}</p>
        </div>
        <div className="rounded-2xl border border-white/10 bg-black/40 px-3 py-2 text-right text-[10px] leading-relaxed text-white/65 backdrop-blur-md">
          <p>background {LAYERED_PARALLAX.backgroundMaxPx}px</p>
          <p>pet {LAYERED_PARALLAX.petMaxPx}px</p>
          <p>foreground {LAYERED_PARALLAX.foregroundMaxPx}px</p>
          <p>no pixel warp</p>
        </div>
      </div>

      <div className="absolute inset-x-0 bottom-0 z-10 flex flex-col items-center gap-3 pb-[max(1.5rem,env(safe-area-inset-bottom))]">
        <div className="flex rounded-full border border-white/15 bg-black/55 p-1 backdrop-blur-md">
          {(["image", "video"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => setBackgroundMode(mode)}
              className={`rounded-full px-4 py-2 text-xs capitalize transition-colors ${
                backgroundMode === mode ? "bg-white text-black" : "text-white/75"
              }`}
            >
              {mode} background
            </button>
          ))}
        </div>

        {gyroSupport === "ios-permission" && permission !== "granted" && !reducedMotion ? (
          <button
            type="button"
            onClick={askMotion}
            className="rounded-full border border-white/20 bg-black/60 px-5 py-3 text-sm text-white/90 backdrop-blur-md active:bg-white/10"
          >
            Enable layered motion
          </button>
        ) : null}
      </div>
    </main>
  );
}

function PackedAlphaPetRenderer({
  videoRef,
  onReady,
  onFailure,
}: {
  videoRef: { readonly current: HTMLVideoElement | null };
  onReady: () => void;
  onFailure: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const video = videoRef.current;
    if (!canvas || !video) return;

    let disposed = false;
    let animationFrame: number | null = null;
    let resizeObserver: ResizeObserver | null = null;
    let renderer: THREE.WebGLRenderer | null = null;

    const fail = () => {
      if (!disposed) onFailure();
    };

    try {
      renderer = new THREE.WebGLRenderer({
        canvas,
        alpha: true,
        antialias: true,
        premultipliedAlpha: true,
        powerPreference: "high-performance",
      });
    } catch {
      fail();
      return;
    }

    renderer.setClearColor(0x000000, 0);
    renderer.setPixelRatio(
      Math.min(window.devicePixelRatio || 1, LAYERED_PARALLAX.maxDevicePixelRatio),
    );

    const scene = new THREE.Scene();
    const camera = new THREE.Camera();
    const geometry = new THREE.PlaneGeometry(2, 2);
    const packedTexture = new THREE.VideoTexture(video);
    packedTexture.colorSpace = THREE.NoColorSpace;
    packedTexture.minFilter = THREE.LinearFilter;
    packedTexture.magFilter = THREE.LinearFilter;
    packedTexture.generateMipmaps = false;

    const material = new THREE.ShaderMaterial({
      uniforms: {
        uPackedVideo: { value: packedTexture },
        uCropX: { value: new THREE.Vector2(PACKED_PET_CROP.xMin, PACKED_PET_CROP.xMax) },
      },
      vertexShader: PACKED_ALPHA_VERTEX_SHADER,
      fragmentShader: PACKED_ALPHA_FRAGMENT_SHADER,
      transparent: true,
      premultipliedAlpha: false,
      depthTest: false,
      depthWrite: false,
    });
    scene.add(new THREE.Mesh(geometry, material));

    const resize = () => {
      const parent = canvas.parentElement;
      const width = Math.max(1, parent?.clientWidth || 1);
      const height = Math.max(1, parent?.clientHeight || 1);
      renderer?.setSize(width, height, false);
    };

    const render = () => {
      animationFrame = null;
      if (disposed || document.hidden) return;
      renderer?.render(scene, camera);
      animationFrame = window.requestAnimationFrame(render);
    };

    const start = () => {
      if (disposed || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;
      resize();
      void video.play().catch(fail);
      if (animationFrame === null && !document.hidden) {
        renderer?.render(scene, camera);
        onReady();
        animationFrame = window.requestAnimationFrame(render);
      }
    };

    const onVisibilityChange = () => {
      if (document.hidden) {
        if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
        animationFrame = null;
        video.pause();
      } else {
        void video.play().then(start).catch(fail);
      }
    };
    const onContextLost = (event: Event) => {
      event.preventDefault();
      fail();
    };

    video.addEventListener("canplay", start);
    video.addEventListener("error", fail);
    document.addEventListener("visibilitychange", onVisibilityChange);
    canvas.addEventListener("webglcontextlost", onContextLost);
    window.addEventListener("resize", resize);
    if (typeof ResizeObserver !== "undefined") {
      resizeObserver = new ResizeObserver(resize);
      if (canvas.parentElement) resizeObserver.observe(canvas.parentElement);
    }
    if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) start();
    resize();

    return () => {
      disposed = true;
      if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
      video.removeEventListener("canplay", start);
      video.removeEventListener("error", fail);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      canvas.removeEventListener("webglcontextlost", onContextLost);
      window.removeEventListener("resize", resize);
      resizeObserver?.disconnect();
      packedTexture.dispose();
      material.dispose();
      geometry.dispose();
      renderer?.dispose();
    };
  }, [onFailure, onReady, videoRef]);

  return <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />;
}
