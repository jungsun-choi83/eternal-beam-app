"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";

import {
  NO_PARALLAX,
  PARALLAX_DEFAULT,
  createOrientationMotionSession,
  createParallaxFrameLoop,
  detectGyroSupport,
  prefersReducedMotion,
  requestGyroPermission,
  type GyroPermission,
} from "@/lib/shaker-gyro";
import {
  DEPTH_DISPLACEMENT,
  DEPTH_FRAGMENT_SHADER,
  DEPTH_VERTEX_SHADER,
  SHAKER_DEPTH_PROTOTYPE_ASSETS,
} from "@/lib/shaker-depth-prototype";

type TiltRef = React.MutableRefObject<{ x: number; y: number }>;

export function ShakerDepthPrototypeScreen() {
  const reducedMotion = useMemo(prefersReducedMotion, []);
  const gyroSupport = useMemo(detectGyroSupport, []);
  const [permission, setPermission] = useState<GyroPermission | null>(() =>
    detectGyroSupport() === "auto" ? "granted" : null
  );
  const [rendererReady, setRendererReady] = useState(false);
  const [rendererFailed, setRendererFailed] = useState(false);
  const [sensorActive, setSensorActive] = useState(false);
  const tiltRef = useRef({ x: 0, y: 0 });
  const videoRef = useRef<HTMLVideoElement>(null);

  const canTryDepth =
    permission === "granted" && !reducedMotion && !rendererFailed;
  const showDepth = canTryDepth && rendererReady && sensorActive;

  useEffect(() => {
    if (!canTryDepth || !rendererReady) return;

    const frameLoop = createParallaxFrameLoop({
      onFrame(frame) {
        tiltRef.current.x = frame.pet.x / PARALLAX_DEFAULT.petMaxPx;
        tiltRef.current.y = frame.pet.y / PARALLAX_DEFAULT.petMaxPx;
      },
      requestFrame: (callback) => window.requestAnimationFrame(callback),
      cancelFrame: (id) => window.cancelAnimationFrame(id),
    });
    const session = createOrientationMotionSession({
      frameLoop,
      subscribeOrientation(listener) {
        const handler = (event: DeviceOrientationEvent) =>
          listener({ beta: event.beta, gamma: event.gamma });
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
        if (!active) tiltRef.current = { ...NO_PARALLAX.pet };
        setSensorActive(active);
      },
    });
    session.start();

    return () => {
      session.destroy();
      tiltRef.current = { ...NO_PARALLAX.pet };
      setSensorActive(false);
    };
  }, [canTryDepth, rendererReady]);

  const askMotion = useCallback(async () => {
    const result = await requestGyroPermission();
    setPermission(result);
  }, []);

  const onRendererFailure = useCallback(() => {
    setRendererFailed(true);
    setRendererReady(false);
    setSensorActive(false);
  }, []);
  const onRendererReady = useCallback(() => setRendererReady(true), []);

  const fallbackReason = reducedMotion
    ? "Reduced motion — normal video"
    : rendererFailed
      ? "WebGL/depth unavailable — normal video"
      : gyroSupport === "unsupported"
        ? "No phone sensor — normal video"
        : permission === "denied"
          ? "Motion denied — normal video"
          : showDepth
            ? "Depth displacement active"
            : "Normal video while motion initializes";

  return (
    <main className="fixed inset-0 overflow-hidden bg-[#050609] text-white">
      <video
        ref={videoRef}
        src={SHAKER_DEPTH_PROTOTYPE_ASSETS.video}
        poster={SHAKER_DEPTH_PROTOTYPE_ASSETS.canonical}
        autoPlay
        muted
        loop
        playsInline
        preload="auto"
        className="absolute inset-0 h-full w-full object-cover"
        aria-label="Normal baked idle video fallback"
      />

      {canTryDepth && (
        <div
          className={`absolute inset-0 transition-opacity duration-200 ${
            showDepth ? "opacity-100" : "pointer-events-none opacity-0"
          }`}
          aria-hidden={!showDepth}
        >
          <DepthVideoRenderer
            videoRef={videoRef}
            tiltRef={tiltRef}
            onReady={onRendererReady}
            onFailure={onRendererFailure}
          />
        </div>
      )}

      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-4 p-[max(1rem,env(safe-area-inset-top))]">
        <div className="rounded-2xl border border-white/15 bg-black/45 px-3 py-2 backdrop-blur-md">
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-emerald-300">
            V2 depth prototype
          </p>
          <p className="mt-1 text-xs text-white/70">{fallbackReason}</p>
        </div>
        <div className="rounded-2xl border border-white/10 bg-black/35 px-3 py-2 text-right text-[10px] leading-relaxed text-white/55 backdrop-blur-md">
          <p>far {DEPTH_DISPLACEMENT.farPx}px</p>
          <p>x near {DEPTH_DISPLACEMENT.horizontalMaxPx}px</p>
          <p>y near {DEPTH_DISPLACEMENT.maxPx}px</p>
          <p>overscan {DEPTH_DISPLACEMENT.overscan.toFixed(3)}×</p>
        </div>
      </div>

      {gyroSupport === "ios-permission" && permission !== "granted" && !reducedMotion && (
        <div className="absolute inset-x-0 bottom-0 flex justify-center pb-[max(2rem,env(safe-area-inset-bottom))]">
          <button
            type="button"
            onClick={askMotion}
            className="rounded-full border border-white/20 bg-black/55 px-5 py-3 text-sm text-white/85 backdrop-blur-md active:bg-white/10"
          >
            Enable depth motion
          </button>
        </div>
      )}
    </main>
  );
}

function DepthVideoRenderer({
  videoRef,
  tiltRef,
  onReady,
  onFailure,
}: {
  videoRef: { readonly current: HTMLVideoElement | null };
  tiltRef: TiltRef;
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
    let depthReady = false;
    let videoReady = false;
    let resizeObserver: ResizeObserver | null = null;
    let renderer: THREE.WebGLRenderer | null = null;
    let depthTexture: THREE.Texture | null = null;

    const fail = () => {
      if (!disposed) onFailure();
    };

    try {
      renderer = new THREE.WebGLRenderer({
        canvas,
        antialias: false,
        alpha: false,
        powerPreference: "high-performance",
      });
    } catch {
      fail();
      return;
    }

    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.setPixelRatio(
      Math.min(window.devicePixelRatio || 1, DEPTH_DISPLACEMENT.maxDevicePixelRatio)
    );

    const scene = new THREE.Scene();
    const camera = new THREE.Camera();
    const geometry = new THREE.PlaneGeometry(2, 2);
    const videoTexture = new THREE.VideoTexture(video);
    videoTexture.colorSpace = THREE.SRGBColorSpace;
    videoTexture.minFilter = THREE.LinearFilter;
    videoTexture.magFilter = THREE.LinearFilter;

    const uniforms = {
      uVideo: { value: videoTexture },
      uDepth: { value: new THREE.Texture() },
      uViewport: { value: new THREE.Vector2(1, 1) },
      uTextureSize: { value: new THREE.Vector2(480, 720) },
      uTilt: { value: new THREE.Vector2(0, 0) },
      uOverscan: { value: DEPTH_DISPLACEMENT.overscan },
    };
    const material = new THREE.ShaderMaterial({
      uniforms,
      vertexShader: DEPTH_VERTEX_SHADER,
      fragmentShader: DEPTH_FRAGMENT_SHADER,
      depthTest: false,
      depthWrite: false,
    });
    scene.add(new THREE.Mesh(geometry, material));

    const resize = () => {
      const parent = canvas.parentElement;
      const width = Math.max(1, parent?.clientWidth || window.innerWidth);
      const height = Math.max(1, parent?.clientHeight || window.innerHeight);
      renderer?.setSize(width, height, false);
      uniforms.uViewport.value.set(width, height);
    };

    const render = () => {
      animationFrame = null;
      if (disposed || document.hidden) return;
      uniforms.uTilt.value.set(tiltRef.current.x, tiltRef.current.y);
      renderer?.render(scene, camera);
      animationFrame = window.requestAnimationFrame(render);
    };

    const startRendering = () => {
      if (!depthReady || !videoReady || disposed) return;
      resize();
      if (animationFrame === null && !document.hidden) {
        animationFrame = window.requestAnimationFrame(render);
      }
      onReady();
    };

    const onCanPlay = () => {
      videoReady = true;
      uniforms.uTextureSize.value.set(video.videoWidth || 480, video.videoHeight || 720);
      video.play().then(startRendering).catch(fail);
    };
    video.addEventListener("canplay", onCanPlay);
    video.addEventListener("error", fail);
    if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) onCanPlay();

    new THREE.TextureLoader().load(
      SHAKER_DEPTH_PROTOTYPE_ASSETS.depth,
      (texture) => {
        if (disposed) {
          texture.dispose();
          return;
        }
        depthTexture = texture;
        depthTexture.colorSpace = THREE.NoColorSpace;
        depthTexture.minFilter = THREE.LinearFilter;
        depthTexture.magFilter = THREE.LinearFilter;
        depthTexture.generateMipmaps = false;
        uniforms.uDepth.value = depthTexture;
        depthReady = true;
        startRendering();
      },
      undefined,
      fail
    );

    const onVisibilityChange = () => {
      if (document.hidden) {
        if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
        animationFrame = null;
        video.pause();
        return;
      }
      video.play().then(startRendering).catch(fail);
    };
    const onContextLost = (event: Event) => {
      event.preventDefault();
      fail();
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    canvas.addEventListener("webglcontextlost", onContextLost);
    window.addEventListener("resize", resize);
    if (typeof ResizeObserver !== "undefined") {
      resizeObserver = new ResizeObserver(resize);
      if (canvas.parentElement) resizeObserver.observe(canvas.parentElement);
    }
    resize();

    return () => {
      disposed = true;
      if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      canvas.removeEventListener("webglcontextlost", onContextLost);
      window.removeEventListener("resize", resize);
      resizeObserver?.disconnect();
      video.removeEventListener("canplay", onCanPlay);
      video.removeEventListener("error", fail);
      depthTexture?.dispose();
      videoTexture.dispose();
      material.dispose();
      geometry.dispose();
      renderer?.dispose();
    };
  }, [onFailure, onReady, tiltRef, videoRef]);

  return <canvas ref={canvasRef} className="h-full w-full" />;
}
