"use client";

import { useCallback, useMemo, useState } from "react";

import {
  ShakerLayeredPlayer,
  type LayeredMotionMode,
} from "@/components/memorial/shaker-layered-player";
import type { ShakerLayeredManifest } from "@/lib/shaker-api";
import {
  detectGyroSupport,
  prefersReducedMotion,
  requestGyroPermission,
  type GyroPermission,
} from "@/lib/shaker-gyro";
import {
  LAYERED_PARALLAX,
  PACKED_PET_CROP,
  SHAKER_LAYERED_PROTOTYPE_ASSETS,
  type LayeredBackgroundMode,
} from "@/lib/shaker-layered-prototype";

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
  const [active, setActive] = useState(false);
  const [failed, setFailed] = useState(false);

  const manifest = useMemo<ShakerLayeredManifest>(() => ({
    version: 2,
    assetId: "prototype-goya",
    assetVersion: "vprototype",
    sceneId: `prototype-${backgroundMode}`,
    pet: {
      url: SHAKER_LAYERED_PROTOTYPE_ASSETS.petPackedAlpha,
      encoding: "packed-vstack-h264",
      alphaLayout: "rgb-top-alpha-bottom",
    },
    background: {
      type: backgroundMode,
      url: backgroundMode === "video"
        ? SHAKER_LAYERED_PROTOTYPE_ASSETS.videoBackground
        : SHAKER_LAYERED_PROTOTYPE_ASSETS.imageBackground,
    },
    placement: {
      mode: "anchored",
      center_x_pct: 50,
      bottom_pct: 10,
      height_pct: 50,
      crop_x_min: PACKED_PET_CROP.xMin,
      crop_x_max: PACKED_PET_CROP.xMax,
    },
    shadow: {
      kind: "css-contact",
      opacity: 0.24,
      blur_px: 11,
      center_x_pct: 50,
      bottom_pct: 7,
      width_pct: 38,
      height_pct: 4,
    },
    foreground: null,
  }), [backgroundMode]);

  const motionMode: LayeredMotionMode = reducedMotion
    ? "off"
    : permission === "granted"
      ? "sensor"
      : gyroSupport === "unsupported"
        ? "pointer"
        : "off";

  const askMotion = useCallback(async () => {
    setPermission(await requestGyroPermission());
  }, []);
  const markFailure = useCallback(() => setFailed(true), []);

  return (
    <main className="fixed inset-0 overflow-hidden bg-[#050609] text-white">
      <video
        src={SHAKER_LAYERED_PROTOTYPE_ASSETS.bakedFallback}
        poster={SHAKER_LAYERED_PROTOTYPE_ASSETS.imageBackground}
        autoPlay
        muted
        loop
        playsInline
        preload="auto"
        className={`absolute inset-0 h-full w-full object-cover transition-opacity duration-200 ${
          active && !failed ? "opacity-0" : "opacity-100"
        }`}
        aria-label="Existing V1 baked video fallback"
      />

      {!failed ? (
        <ShakerLayeredPlayer
          key={manifest.sceneId}
          manifest={manifest}
          motionMode={motionMode}
          onActiveChange={setActive}
          onFailure={markFailure}
          className={active ? "opacity-100" : "opacity-0"}
        />
      ) : null}

      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex justify-between gap-3 p-[max(1rem,env(safe-area-inset-top))]">
        <div className="rounded-2xl border border-white/15 bg-black/50 px-3 py-2 backdrop-blur-md">
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-emerald-300">
            V2 shared renderer prototype
          </p>
          <p className="mt-1 text-xs text-white/70">
            {failed ? "V1 fallback" : active ? "Layered playback active" : "Loading V2 over V1"}
          </p>
        </div>
        <div className="rounded-2xl border border-white/10 bg-black/40 px-3 py-2 text-right text-[10px] text-white/65 backdrop-blur-md">
          <p>background {LAYERED_PARALLAX.backgroundMaxPx}px</p>
          <p>pet {LAYERED_PARALLAX.petMaxPx}px</p>
          <p>no pixel warp</p>
        </div>
      </div>

      <div className="absolute inset-x-0 bottom-0 z-10 flex flex-col items-center gap-3 pb-[max(1.5rem,env(safe-area-inset-bottom))]">
        <div className="flex rounded-full border border-white/15 bg-black/55 p-1 backdrop-blur-md">
          {(["image", "video"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => {
                setFailed(false);
                setActive(false);
                setBackgroundMode(mode);
              }}
              className={`rounded-full px-4 py-2 text-xs capitalize ${
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
            className="rounded-full border border-white/20 bg-black/60 px-5 py-3 text-sm text-white/90 backdrop-blur-md"
          >
            Enable layered motion
          </button>
        ) : null}
      </div>
    </main>
  );
}
