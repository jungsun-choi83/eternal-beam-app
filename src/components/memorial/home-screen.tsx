"use client";

import { memorialLang, memorialT } from "@/components/memorial/memorial-i18n";
import { motion } from "framer-motion";
import { Plus, Settings, Grid3X3 } from "lucide-react";
import { HolographicBackground } from "./holographic-background";
import { HologramEffects } from "./hologram-effects";
import { MediaFileTrigger } from "./media-file-trigger";
import { LanguageToggle } from "./language-toggle";

interface HomeScreenProps {
  cutoutImage: string | null;
  userName?: string;
  language?: string;
  onLanguageChange?: (lang: string) => void;
  onMediaFile: (file: File) => void;
  onGallery?: () => void;
  onSettings?: () => void;
  onSaveToNFC: () => void;
}

export function HomeScreen({
  cutoutImage,
  userName,
  language = "ko",
  onLanguageChange,
  onMediaFile,
  onGallery,
  onSettings,
  onSaveToNFC,
}: HomeScreenProps) {
  const lang = memorialLang(language);
  const texts = memorialT(language).home;

  const handleLanguageSelect = (code: "ko" | "en") => {
    onLanguageChange?.(code);
  };

  return (
    <div className="hologram-bg-active h-full flex flex-col bg-[#000000] relative overflow-hidden min-h-0">
      {/* Holographic Background */}
      <HolographicBackground />
      <HologramEffects />

      {/* Header with Brand */}
      <header className="px-6 pt-8 pb-4 relative z-10 shrink-0">
        {/* Top Row - Gallery, Brand, Settings */}
        <div className="flex items-center justify-between gap-2 mb-4">
          {/* Gallery Button */}
          <motion.button
            onClick={onGallery}
            className="p-2.5 rounded-xl relative shrink-0"
            style={{
              background: "rgba(255, 255, 255, 0.06)",
              backdropFilter: "blur(20px)",
            }}
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
          >
            <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-white/20 via-white/10 to-transparent" />
            <div className="absolute top-0 left-0 bottom-0 w-px bg-gradient-to-b from-white/20 via-white/10 to-transparent" />
            <Grid3X3 className="w-5 h-5" style={{ color: "#E2E2E2" }} />
          </motion.button>

          <LanguageToggle
            language={language}
            onChange={handleLanguageSelect}
            className="relative z-20"
          />

          {/* Settings Button */}
          <motion.button
            onClick={onSettings}
            className="p-2.5 rounded-xl relative shrink-0"
            style={{
              background: "rgba(255, 255, 255, 0.06)",
              backdropFilter: "blur(20px)",
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

          <h1 className="logo-title relative">
            Eternal Beam
          </h1>
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
                  className="px-5 py-2.5 rounded-full text-sm font-light text-center"
                  style={{
                    background: "rgba(255, 255, 255, 0.08)",
                    border: "1px solid rgba(201, 162, 39, 0.25)",
                    color: "#d4af37",
                  }}
                >
                  {texts.addMedia}
                </div>
              </MediaFileTrigger>
            </div>
          ) : (
            <div className="relative">
              {/* Double Glass Layer Container */}
              {/* Back Glass Layer - slightly offset */}
              <div
                className="absolute -inset-2 rounded-[36px]"
                style={{
                  background: "linear-gradient(145deg, rgba(255, 255, 255, 0.08) 0%, rgba(255, 255, 255, 0.02) 100%)",
                  backdropFilter: "blur(30px)",
                  WebkitBackdropFilter: "blur(30px)",
                  border: "1px solid rgba(255, 255, 255, 0.08)",
                }}
              />
              
              {/* Front Glass Layer - Main Button */}
              <MediaFileTrigger
                onFile={onMediaFile}
                className="relative w-56 h-56 rounded-[32px] touch-manipulation"
              >
                <motion.div
                  className="relative w-full h-full rounded-[32px] flex flex-col items-center justify-center gap-4"
                  style={{
                    background: "linear-gradient(145deg, rgba(60, 60, 65, 0.6) 0%, rgba(40, 40, 45, 0.7) 50%, rgba(28, 28, 30, 0.8) 100%)",
                    backdropFilter: "blur(60px)",
                    WebkitBackdropFilter: "blur(60px)",
                    boxShadow: `
                    0 8px 32px rgba(0, 0, 0, 0.4),
                    0 0 0 1px rgba(255, 255, 255, 0.06) inset,
                    0 32px 64px -12px rgba(0, 0, 0, 0.5),
                    inset 0 -2px 6px rgba(0, 0, 0, 0.2)
                  `,
                  }}
                >
                {/* Glass Border - Bright top edge */}
                <div 
                  className="absolute top-0 left-6 right-6 h-px"
                  style={{
                    background: "linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.4) 20%, rgba(255,255,255,0.5) 50%, rgba(255,255,255,0.4) 80%, transparent 100%)",
                  }}
                />
                {/* Left edge highlight */}
                <div 
                  className="absolute top-6 bottom-6 left-0 w-px"
                  style={{
                    background: "linear-gradient(180deg, rgba(255,255,255,0.4), rgba(255,255,255,0.2) 50%, transparent)",
                  }}
                />
                {/* Inner glow effect */}
                <div 
                  className="absolute inset-0 rounded-[32px] pointer-events-none"
                  style={{
                    background: "radial-gradient(ellipse at 30% 20%, rgba(255,255,255,0.08) 0%, transparent 50%)",
                  }}
                />

                <Plus className="w-8 h-8" style={{ color: "#d4af37" }} strokeWidth={1.5} />

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
                  <span className="text-xs font-light mt-1 block" style={{ color: "#A1A1A6" }}>
                    {texts.photoOrVideo}
                  </span>
                </div>

                <div className="flex items-center gap-2">
                  {["JPG", "PNG", "MP4"].map((format) => (
                    <div
                      key={format}
                      className="px-2.5 py-1 rounded-full text-[10px] font-medium relative"
                      style={{
                        background: "rgba(201, 162, 39, 0.12)",
                        color: "#d4af37",
                        border: "1px solid rgba(201, 162, 39, 0.2)",
                      }}
                    >
                      {format}
                    </div>
                  ))}
                </div>
                </motion.div>
              </MediaFileTrigger>
            </div>
          )}
        </motion.div>
      </div>

      {/* Bottom Action - Save to Memory 버튼 항상 보임 */}
      <div className="px-8 pb-10 pt-4 relative z-10 shrink-0">
        <motion.button
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.6 }}
          onClick={onSaveToNFC}
          className="w-full py-4 rounded-2xl font-normal text-sm relative overflow-hidden"
          style={{
            background: "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)",
            boxShadow: "0 10px 40px rgba(201, 162, 39, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.2)",
          }}
          whileHover={{ scale: 1.02, boxShadow: "0 15px 50px rgba(201, 162, 39, 0.35)" }}
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
