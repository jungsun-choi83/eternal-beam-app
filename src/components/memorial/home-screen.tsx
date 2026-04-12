"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Plus, Globe, ChevronDown, Check, Settings, Grid3X3 } from "lucide-react";
import { HolographicBackground } from "./holographic-background";
import { HologramEffects } from "./hologram-effects";

interface HomeScreenProps {
  cutoutImage: string | null;
  userName?: string;
  language?: string;
  onLanguageChange?: (lang: string) => void;
  onUploadPhoto: () => void;
  onGallery?: () => void;
  onSettings?: () => void;
  onSaveToNFC: () => void;
}

const languages = [
  { code: "en", name: "English", native: "English" },
  { code: "ko", name: "Korean", native: "한국어" },
  { code: "zh", name: "Chinese", native: "中文" },
  { code: "ja", name: "Japanese", native: "日本語" },
];

const translations = {
  en: {
    subtitle: "Holographic Memorial",
    addMedia: "Add Media",
    photoOrVideo: "Photo or Video",
    saveToMemory: "Save to Memory",
    welcome: "Welcome",
  },
  ko: {
    subtitle: "홀로그램 메모리얼",
    addMedia: "미디어 추가",
    photoOrVideo: "사진 또는 동영상",
    saveToMemory: "메모리에 저장",
    welcome: "환영합니다",
  },
  zh: {
    subtitle: "全息纪念",
    addMedia: "添加媒体",
    photoOrVideo: "照片或视频",
    saveToMemory: "保存到记忆",
    welcome: "欢迎",
  },
  ja: {
    subtitle: "ホログラムメモリアル",
    addMedia: "メディア追加",
    photoOrVideo: "写真または動画",
    saveToMemory: "メモリーに保存",
    welcome: "ようこそ",
  },
};

export function HomeScreen({
  cutoutImage,
  userName,
  language = "en",
  onLanguageChange,
  onUploadPhoto,
  onGallery,
  onSettings,
  onSaveToNFC,
}: HomeScreenProps) {
  const [selectedLanguage, setSelectedLanguage] = useState(language);
  const [showLanguageMenu, setShowLanguageMenu] = useState(false);

  const currentLang = languages.find(l => l.code === selectedLanguage) || languages[0];
  const texts = translations[selectedLanguage as keyof typeof translations] || translations.en;

  const handleLanguageSelect = (code: string) => {
    setSelectedLanguage(code);
    onLanguageChange?.(code);
    setShowLanguageMenu(false);
  };

  return (
    <div className="hologram-bg-active h-full flex flex-col bg-[#000000] relative overflow-hidden min-h-0">
      {/* Holographic Background */}
      <HolographicBackground />
      <HologramEffects />

      {/* Header with Brand */}
      <header className="px-6 pt-8 pb-4 relative z-10 shrink-0">
        {/* Top Row - Gallery, Brand, Settings */}
        <div className="flex items-center justify-between mb-4">
          {/* Gallery Button */}
          <motion.button
            onClick={onGallery}
            className="p-2.5 rounded-xl relative"
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

          {/* Settings Button */}
          <motion.button
            onClick={onSettings}
            className="p-2.5 rounded-xl relative"
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
            className="absolute inset-0 blur-[40px] opacity-50"
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
              <span className="absolute inset-0 blur-[6px] opacity-30">
                {userName}{selectedLanguage === "ko" ? "님, " : ", "}{texts.welcome}
              </span>
              <span className="relative">
                {selectedLanguage === "ko" ? `${userName}님, ${texts.welcome}` : `${texts.welcome}, ${userName}`}
              </span>
            </motion.p>
          )}

          {/* Language Selector - Positioned below brand */}
          <div className="relative inline-block mt-4">
            <motion.button
              onClick={() => setShowLanguageMenu(!showLanguageMenu)}
              className="flex items-center gap-2 px-4 py-2 rounded-xl relative mx-auto"
              style={{
                background: "rgba(255, 255, 255, 0.06)",
                backdropFilter: "blur(20px)",
              }}
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.98 }}
            >
              <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-white/20 via-white/10 to-transparent" />
              <div className="absolute top-0 left-0 bottom-0 w-px bg-gradient-to-b from-white/20 via-white/10 to-transparent" />
              <Globe className="w-4 h-4" style={{ color: "#E2E2E2" }} />
              <span className="text-xs font-light" style={{ color: "#F1E5D1" }}>
                {currentLang.native}
              </span>
              <ChevronDown
                className={`w-3 h-3 transition-transform duration-200 ${showLanguageMenu ? "rotate-180" : ""}`}
                style={{ color: "#E2E2E2" }}
              />
            </motion.button>

            <AnimatePresence>
              {showLanguageMenu && (
                <motion.div
                  initial={{ opacity: 0, y: -10, scale: 0.95 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: -10, scale: 0.95 }}
                  transition={{ duration: 0.2 }}
                  className="absolute left-1/2 -translate-x-1/2 mt-2 w-40 rounded-xl overflow-hidden z-50"
                  style={{
                    background: "rgba(20, 20, 22, 0.95)",
                    backdropFilter: "blur(40px)",
                    boxShadow: "0 20px 50px rgba(0, 0, 0, 0.5)",
                  }}
                >
                  <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-white/15 via-white/10 to-transparent" />
                  <div className="absolute top-0 left-0 bottom-0 w-px bg-gradient-to-b from-white/15 via-white/10 to-transparent" />
                  {languages.map((lang) => (
                    <button
                      key={lang.code}
                      onClick={() => handleLanguageSelect(lang.code)}
                      className="w-full px-4 py-3 flex items-center justify-between hover:bg-white/5 transition-colors"
                    >
                      <span className="text-sm font-light" style={{ color: "#F1E5D1" }}>
                        {lang.native}
                      </span>
                      {selectedLanguage === lang.code && (
                        <Check className="w-4 h-4" style={{ color: "#d4af37" }} />
                      )}
                    </button>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
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
            <div className="relative">
              <motion.div
                className="absolute -inset-4 rounded-full"
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
                <img src={cutoutImage} alt="Your companion" className="w-full h-full object-cover" />
              </div>
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
              <motion.button
                onClick={onUploadPhoto}
                className="relative w-56 h-56 rounded-[32px] flex flex-col items-center justify-center gap-4"
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
                whileHover={{
                  scale: 1.03,
                  boxShadow: `
                    0 12px 48px rgba(201, 162, 39, 0.2),
                    0 0 0 1px rgba(201, 162, 39, 0.15) inset,
                    0 40px 80px -16px rgba(0, 0, 0, 0.6),
                    inset 0 -2px 6px rgba(0, 0, 0, 0.2)
                  `,
                }}
                whileTap={{ scale: 0.97 }}
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
              </motion.button>
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
