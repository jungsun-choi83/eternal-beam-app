"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { HolographicBackground } from "@/components/memorial/holographic-background";
import { HologramEffects } from "@/components/memorial/hologram-effects";
import { EternalBeamLogoHero } from "@/components/memorial/eternal-beam-brand-mark";
import { memorialT } from "@/components/memorial/memorial-i18n";

interface QRConnectionScreenProps {
  language?: string;
  showBack?: boolean;
  onComplete: () => void;
  onBack: () => void;
  onSkip: () => void;
}

/** 기계 QR → 앱 첫 진입: 스캔 카메라 대신 브랜드 로고 스플래시 → 회원가입 */
export function QRConnectionScreen({
  language = "ko",
  showBack = false,
  onComplete,
  onSkip,
}: QRConnectionScreenProps) {
  const q = memorialT(language).qr;
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const reveal = window.setTimeout(() => setReady(true), 120);
    const auto = window.setTimeout(() => onComplete(), 2800);
    return () => {
      window.clearTimeout(reveal);
      window.clearTimeout(auto);
    };
  }, [onComplete]);

  return (
    <div
      data-screen="qrConnection"
      className="hologram-bg-active memorial-screen-shell h-full flex flex-col relative overflow-hidden min-h-0 bg-[#050505]"
    >
      <HolographicBackground />
      <HologramEffects />

      <div className="flex-1 flex flex-col items-center justify-center px-8 min-h-0 relative z-10">
        <motion.div
          initial={{ opacity: 0, scale: 0.92, y: 12 }}
          animate={{ opacity: ready ? 1 : 0, scale: ready ? 1 : 0.92, y: ready ? 0 : 12 }}
          transition={{ duration: 0.85, ease: [0.22, 1, 0.36, 1] }}
          className="relative w-[min(17rem,78vw)] aspect-square flex items-center justify-center"
        >
          {/* 골드 프레임 — 예전 QR 스캔 박스 자리 */}
          <div
            className="absolute inset-0 rounded-[2rem]"
            style={{
              background:
                "radial-gradient(ellipse at 50% 42%, rgba(212, 175, 55, 0.12) 0%, rgba(0,0,0,0) 62%)",
              border: "1px solid rgba(255, 255, 255, 0.07)",
              boxShadow:
                "0 0 0 1px rgba(212, 175, 55, 0.08) inset, 0 24px 64px rgba(0,0,0,0.55)",
            }}
          />
          <motion.div
            className="absolute inset-0 rounded-[2rem] pointer-events-none"
            animate={{ opacity: [0.35, 0.7, 0.35] }}
            transition={{ duration: 3.2, repeat: Infinity, ease: "easeInOut" }}
            style={{
              boxShadow: "0 0 48px rgba(212, 175, 55, 0.22)",
            }}
          />

          <div className="absolute top-4 left-4 w-9 h-9 border-t-2 border-l-2 rounded-tl-lg border-[#d4af37]/80" />
          <div className="absolute top-4 right-4 w-9 h-9 border-t-2 border-r-2 rounded-tr-lg border-[#d4af37]/80" />
          <div className="absolute bottom-4 left-4 w-9 h-9 border-b-2 border-l-2 rounded-bl-lg border-[#d4af37]/80" />
          <div className="absolute bottom-4 right-4 w-9 h-9 border-b-2 border-r-2 rounded-br-lg border-[#d4af37]/80" />

          <div className="relative z-10 px-6 py-4 w-full flex items-center justify-center">
            <EternalBeamLogoHero size="hero" showGlow className="splash-logo" />
          </div>
        </motion.div>

        <motion.p
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: ready ? 1 : 0, y: ready ? 0 : 8 }}
          transition={{ delay: 0.35, duration: 0.6 }}
          className="mt-8 text-center memorial-body max-w-[16rem] leading-relaxed"
          style={{ color: "#A1A1A6" }}
        >
          {q.splashHint}
        </motion.p>

        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: ready ? 1 : 0 }}
          transition={{ delay: 0.55, duration: 0.5 }}
          className="mt-6 h-0.5 w-24 rounded-full overflow-hidden bg-white/10"
          aria-hidden
        >
          <motion.div
            className="h-full origin-left bg-gradient-to-r from-[#d4af37] to-[#f5e6b8]"
            initial={{ scaleX: 0 }}
            animate={{ scaleX: 1 }}
            transition={{ duration: 2.6, ease: "easeInOut" }}
          />
        </motion.div>
      </div>

      <div className="shrink-0 px-6 pb-[max(2rem,env(safe-area-inset-bottom,0px))] pt-2 relative z-10">
        <motion.button
          type="button"
          onClick={onComplete}
          className="w-full py-4 rounded-2xl memorial-btn-label"
          style={{
            background: "linear-gradient(135deg, #d4af37 0%, #c9a227 100%)",
            boxShadow: "0 8px 32px rgba(212, 175, 55, 0.28)",
            color: "#0a0a0a",
          }}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: ready ? 1 : 0, y: ready ? 0 : 10 }}
          transition={{ delay: 0.45, duration: 0.5 }}
        >
          {q.splashContinue}
        </motion.button>
        {showBack ? (
          <button
            type="button"
            onClick={onSkip}
            className="w-full mt-3 py-2 text-sm memorial-caption"
            style={{ color: "#666" }}
          >
            {q.skip}
          </button>
        ) : null}
      </div>
    </div>
  );
}
