"use client";

import { memorialLang, memorialT } from "@/components/memorial/memorial-i18n";
import { motion } from "framer-motion";
import { Plus, Settings, Grid3X3 } from "lucide-react";
import { HolographicBackground } from "./holographic-background";
import { HologramEffects } from "./hologram-effects";
import { MediaFileTrigger } from "./media-file-trigger";

interface HomeScreenProps {
  cutoutImage: string | null;
  userName?: string;
  language?: string;
  onMediaFile: (file: File) => void;
  onGallery?: () => void;
  onSettings?: () => void;
  onTryForest?: () => void;
  onSaveToNFC: () => void;
}

export function HomeScreen({
  cutoutImage,
  userName,
  language = "ko",
  onMediaFile,
  onGallery,
  onSettings,
  onTryForest,
  onSaveToNFC,
}: HomeScreenProps) {
  const lang = memorialLang(language);
  const texts = memorialT(language).home;

  return (
    <div className="hologram-bg-active memorial-screen-shell h-full flex flex-col relative overflow-hidden min-h-0">
      <HolographicBackground />
      <HologramEffects />

      {/* Header with Brand */}
      <header className="px-6 pt-[max(2.75rem,env(safe-area-inset-top,0px))] pb-4 relative z-10 shrink-0">
        <div className="flex items-center justify-between gap-2 mb-4">
          <motion.button
            onClick={onGallery}
            className="mem-icon-btn relative shrink-0"
            style={{
              background: "rgba(255, 255, 255, 0.08)",
              borderColor: "rgba(255, 255, 255, 0.14)",
            }}
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
          >
            <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-white/20 via-white/10 to-transparent" />
            <div className="absolute top-0 left-0 bottom-0 w-px bg-gradient-to-b from-white/20 via-white/10 to-transparent" />
            <Grid3X3 className="w-5 h-5" style={{ color: "#E2E2E2" }} />
          </motion.button>

          <div className="flex-1" aria-hidden />

          <motion.button
            onClick={onSettings}
            className="mem-icon-btn relative shrink-0"
            style={{
              background: "rgba(255, 255, 255, 0.08)",
              borderColor: "rgba(255, 255, 255, 0.14)",
            }}
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
          >
            <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-white/20 via-white/10 to-transparent" />
            <div className="absolute top-0 left-0 bottom-0 w-px bg-gradient-to-b from-white/20 via-white/10 to-transparent" />
            <Settings className="w-5 h-5" style={{ color: "#E2E2E2" }} />
          </motion.button>
        </div>

        {/* Brand Name - Centered */}
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="text-center relative"
        >
          {/* Bloom Effect behind title */}
          <div
            className="absolute inset-0 blur-[40px] opacity-50 pointer-events-none"
            style={{
              background: "radial-gradient(ellipse at center, rgba(212, 175, 55, 0.4) 0%, rgba(241, 229, 209, 0.1) 40%, transparent 70%)",
            }}
          />

          <div className="logo-holo-wrap">
            <h1 className="logo-title logo-title--holo relative">Eternal Beam</h1>
          </div>
          <p className="logo-subtitle">
            {texts.subtitle}
          </p>

          {/* Welcome Message */}
          {userName && (
            <motion.p
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.5 }}
              className="text-sm mt-4 font-light relative"
              style={{ color: "#F1E5D1" }}
            >
              <span className="absolute inset-0 blur-[6px] opacity-30 pointer-events-none">
                {userName}{lang === "ko" ? "님, " : ", "}{texts.welcome}
              </span>
              <span className="relative">
                {lang === "ko" ? `${userName}님, ${texts.welcome}` : `${texts.welcome}, ${userName}`}
              </span>
            </motion.p>
          )}
        </motion.div>
      </header>

      {/* Main Content - Add Media Button with Glassmorphism */}
      <div className="flex-1 flex items-center justify-center px-8 relative z-10 min-h-0 overflow-y-auto">
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.3, duration: 0.6 }}
          className="relative"
        >
          {cutoutImage ? (
            <div className="relative flex flex-col items-center gap-4">
              <MediaFileTrigger
                onFile={onMediaFile}
                className="relative touch-manipulation"
              >
                <motion.div
                  className="absolute -inset-4 rounded-full pointer-events-none"
                  style={{
                    background: "conic-gradient(from 0deg, transparent, rgba(201, 162, 39, 0.4), transparent)",
                  }}
                  animate={{ rotate: 360 }}
                  transition={{ duration: 8, repeat: Infinity, ease: "linear" }}
                />
                <div
                  className="relative w-56 h-56 rounded-full overflow-hidden"
                  style={{
                    boxShadow: "0 0 60px rgba(201, 162, 39, 0.3), inset 0 0 30px rgba(201, 162, 39, 0.1)",
                  }}
                >
                  <img src={cutoutImage} alt="" className="w-full h-full object-cover" />
                </div>
              </MediaFileTrigger>
              <MediaFileTrigger onFile={onMediaFile} className="touch-manipulation">
                <div
                  className="px-5 py-2.5 rounded-full text-[13px] font-normal text-center"
                  style={{
                    background: "rgba(255, 255, 255, 0.10)",
                    border: "1px solid rgba(201, 162, 39, 0.32)",
                    color: "#e8d5a3",
                  }}
                >
                  {texts.addMedia}
                </div>
              </MediaFileTrigger>
            </div>
          ) : (
            <div className="relative">
              <MediaFileTrigger
                onFile={onMediaFile}
                className="relative w-56 h-[15.5rem] touch-manipulation block"
              >
                <motion.div
                  className="memory-cta-card relative w-full h-full flex flex-col items-center justify-center gap-4 px-6"
                >
                <div className="memory-cta-card__shine" />

                <div className="upload-card__empty-icon">
                  <Plus className="w-6 h-6 text-[#e8d5a3]" strokeWidth={1.25} />
                </div>

                <div className="text-center relative">
                  <span 
                    className="absolute inset-0 blur-[6px] opacity-40 text-base font-medium block"
                    style={{ color: "#F1E5D1" }}
                  >
                    {texts.addMedia}
                  </span>
                  <span className="relative text-base font-medium block" style={{ color: "#F1E5D1" }}>
                    {texts.addMedia}
                  </span>
                  {texts.photoOrVideo ? (
                    <span className="text-sm font-normal mt-1 block memorial-body">
                      {texts.photoOrVideo}
                    </span>
                  ) : null}
                </div>

                <div className="upload-formats">
                  {["HEIC", "JPG", "PNG"].map((format) => (
                    <span key={format} className="upload-format-pill">
                      {format}
                    </span>
                  ))}
                </div>
                </motion.div>
              </MediaFileTrigger>
            </div>
          )}
        </motion.div>
      </div>

      {/* Bottom Action */}
      <div className="px-8 pb-10 pt-4 relative z-10 shrink-0 space-y-3">
        {onTryForest ? (
          <motion.button
            type="button"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.5 }}
            onClick={onTryForest}
            className="w-full py-3.5 rounded-full text-sm font-medium"
            style={{
              background: "rgba(255,255,255,0.08)",
              border: "1px solid rgba(110, 231, 183, 0.35)",
              color: "#a7f3d0",
            }}
            whileHover={{ scale: 1.01 }}
            whileTap={{ scale: 0.98 }}
          >
            <span className="block">{texts.tryForest}</span>
            <span className="block text-xs mt-0.5 opacity-70 font-normal">{texts.tryForestHint}</span>
          </motion.button>
        ) : null}
        <motion.button
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.6 }}
          onClick={onSaveToNFC}
          className="cta-gold w-full py-4 font-semibold text-base relative overflow-hidden"
          whileHover={{ scale: 1.015, boxShadow: "0 14px 34px rgba(201, 162, 39, 0.34)" }}
          whileTap={{ scale: 0.98 }}
        >
          {/* Top shine */}
          <div className="absolute top-0 left-8 right-8 h-px bg-gradient-to-r from-transparent via-white/40 to-transparent" />
          <span className="text-[#0a0a0a] font-medium tracking-wide">{texts.saveToMemory}</span>
        </motion.button>
      </div>
    </div>
  );
}
