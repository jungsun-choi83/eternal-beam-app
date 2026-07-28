"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { ArrowLeft, Radio, WifiOff } from "lucide-react";
import { HolographicBackground } from "@/components/memorial/holographic-background";
import { HologramEffects } from "@/components/memorial/hologram-effects";
import {
  ETERNAL_BEAM_PIPELINE_KEY,
  type StoredPipeline,
} from "@/components/memorial/ai-processing-screen";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { PetIdleDisplay } from "@/components/memorial/pet-idle-display";
import { ThemeBackgroundVideo } from "@/components/memorial/theme-background-video";
import { getMemorialTheme } from "@/components/memorial/themes";
import { getEffectiveBgVideo } from "@/lib/custom-background-store";
import {
  broadcastFreeThemeToDevice,
  finalizePreviewContent,
  type PreviewFinalizeSettings,
} from "@/lib/finalize-preview-content";
import { resetThemeBackgroundSyncCache, scheduleThemeBackgroundSync } from "@/lib/device-theme-sync";
import { resolveIdleVideoUrl } from "@/app/services/videoProcessingApi";
import { resolveSelectedThemeId } from "@/lib/theme-selection-store";

interface MemorialDevicePlayScreenProps {
  cutoutImage: string | null;
  selectedTheme: number | null;
  settings: PreviewFinalizeSettings;
  language?: string;
  onBack: () => void;
  onComplete: () => void;
}

export function MemorialDevicePlayScreen({
  cutoutImage,
  selectedTheme,
  settings,
  language = "ko",
  onBack,
  onComplete,
}: MemorialDevicePlayScreenProps) {
  const d = memorialT(language).devicePlay;
  const themeId = resolveSelectedThemeId(selectedTheme);
  const theme = (themeId != null ? getMemorialTheme(themeId) : undefined) ?? getMemorialTheme(1)!;
  const bgVideo = getEffectiveBgVideo(theme);
  const [pipeline, setPipeline] = useState<StoredPipeline | null>(null);
  const [status, setStatus] = useState<"starting" | "live" | "offline">("starting");
  const [statusHint, setStatusHint] = useState<string | null>(null);

  const cutoutDisplay = useMemo(
    () =>
      cutoutImage ||
      pipeline?.cutout_display_url ||
      pipeline?.dog_only_nobg_url ||
      null,
    [cutoutImage, pipeline]
  );

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(ETERNAL_BEAM_PIPELINE_KEY);
      if (raw) setPipeline(JSON.parse(raw) as StoredPipeline);
    } catch {
      setPipeline(null);
    }
  }, [cutoutImage]);

  const rebroadcastToDevice = useCallback(async () => {
    if (themeId == null) return;
    setStatus("starting");
    setStatusHint(d.startingHint);
    resetThemeBackgroundSyncCache();
    scheduleThemeBackgroundSync(themeId);
    try {
      const contentId = await finalizePreviewContent(themeId, settings);
      const ok = await broadcastFreeThemeToDevice(theme, contentId);
      if (ok) {
        setStatus("live");
        setStatusHint(d.liveHint);
      } else {
        setStatus("offline");
        setStatusHint(d.offlineHint);
      }
    } catch {
      setStatus("offline");
      setStatusHint(d.offlineHint);
    }
  }, [themeId, theme, settings, d.startingHint, d.liveHint, d.offlineHint]);

  useEffect(() => {
    void rebroadcastToDevice();
  }, [rebroadcastToDevice]);

  const idleVideoUrl = resolveIdleVideoUrl(pipeline?.idle_video_url, cutoutDisplay);

  return (
    <div className="hologram-bg-active memorial-screen-shell h-full flex flex-col relative overflow-hidden min-h-0">
      <HolographicBackground />
      <HologramEffects />

      <header className="px-6 pt-8 pb-4 relative z-10 shrink-0 flex items-center justify-between">
        <button
          type="button"
          onClick={onBack}
          className="mem-icon-btn relative shrink-0"
          style={{
            background: "rgba(255, 255, 255, 0.08)",
            borderColor: "rgba(255, 255, 255, 0.14)",
          }}
        >
          <ArrowLeft className="w-5 h-5" style={{ color: "#E2E2E2" }} />
        </button>
        <div className="text-center flex-1 px-3">
          <p className="logo-subtitle text-[11px] tracking-[0.28em] opacity-80">ETERNAL BEAM</p>
          <p className="text-sm font-light mt-1" style={{ color: "#F5F5F7" }}>
            {d.title}
          </p>
        </div>
        <div className="w-10" aria-hidden />
      </header>

      <div className="flex-1 px-6 pb-4 relative z-10 flex flex-col items-center min-h-0">
        <motion.div
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          className="theme-preview-frame relative w-full aspect-[3/4] max-h-[min(52vh,360px)]"
        >
          {bgVideo ? (
            <ThemeBackgroundVideo
              key={`live-bg-${theme.id}-${bgVideo}`}
              src={bgVideo}
              poster={theme.thumb}
            />
          ) : (
            <div
              className="absolute inset-0 bg-center bg-cover"
              style={{ backgroundImage: `url(${theme.thumb})` }}
            />
          )}
          <div className={`absolute inset-0 bg-gradient-to-b ${theme.gradient} opacity-25`} />

          {cutoutDisplay ? (
            <div
              className="absolute inset-0 flex items-center justify-center p-4 preview-subject-layer"
              style={{
                transform: `translate3d(${settings.posX}px, ${settings.posY}px, 0) scale(${settings.scale})`,
              }}
            >
              <PetIdleDisplay
                idleVideoUrl={idleVideoUrl ?? pipeline?.idle_video_url}
                cutoutUrl={cutoutDisplay}
                className="theme-preview-frame__pet max-h-[62%] max-w-[92%]"
                style={{
                  filter: `drop-shadow(0 16px 32px ${theme.accent}66)`,
                }}
              />
            </div>
          ) : null}
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-5 flex flex-col items-center gap-2 text-center max-w-[280px]"
        >
          <button
            type="button"
            onClick={() => void rebroadcastToDevice()}
            className="flex flex-col items-center gap-2 text-center bg-transparent border-0 p-2 cursor-pointer"
            aria-label={d.liveTitle(theme.nameKo || theme.name)}
          >
          {status === "live" ? (
            <Radio className="w-7 h-7 text-emerald-300/90" strokeWidth={1.25} />
          ) : status === "offline" ? (
            <WifiOff className="w-7 h-7 text-amber-200/80" strokeWidth={1.25} />
          ) : (
            <motion.div
              className="w-7 h-7 rounded-full border-2 border-[#c9a227]/40 border-t-[#c9a227]"
              animate={{ rotate: 360 }}
              transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
            />
          )}
          <p className="text-base font-medium" style={{ color: "#F1E5D1" }}>
            {status === "starting"
              ? d.starting
              : status === "live"
                ? d.liveTitle(theme.nameKo || theme.name)
                : d.offlineTitle}
          </p>
          <p className="text-sm memorial-body">{statusHint ?? d.startingHint}</p>
          </button>
        </motion.div>
      </div>

      <div className="px-8 pb-10 shrink-0 relative z-10">
        <motion.button
          type="button"
          onClick={onComplete}
          className="w-full py-4 rounded-2xl font-normal text-[15px] tracking-wider"
          style={{
            background: "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)",
            boxShadow: "0 10px 40px rgba(201, 162, 39, 0.25)",
            color: "#0a0a0a",
          }}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          {d.done}
        </motion.button>
      </div>
    </div>
  );
}
