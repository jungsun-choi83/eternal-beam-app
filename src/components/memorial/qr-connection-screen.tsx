"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { HolographicBackground } from "@/components/memorial/holographic-background";
import { HologramEffects } from "@/components/memorial/hologram-effects";

/** 1P 로고 표시 시간 */
export const SPLASH_HOLD_MS = 2000;
/** 1P → 2P 페이드·화면 전환 (~2초) */
export const SPLASH_FADE_MS = 2000;

interface QRConnectionScreenProps {
  language?: string;
  showBack?: boolean;
  onComplete: () => void;
  onBack: () => void;
  onSkip: () => void;
}

/** 기계 QR → 앱 첫 진입: 브랜드 PNG 로고 → 회원가입 */
export function QRConnectionScreen({
  onComplete,
}: QRConnectionScreenProps) {
  const [phase, setPhase] = useState<"idle" | "shown" | "exit">("idle");

  useEffect(() => {
    const reveal = window.setTimeout(() => setPhase("shown"), 80);
    const fade = window.setTimeout(() => setPhase("exit"), SPLASH_HOLD_MS);
    const auto = window.setTimeout(() => onComplete(), SPLASH_HOLD_MS + SPLASH_FADE_MS);
    return () => {
      window.clearTimeout(reveal);
      window.clearTimeout(fade);
      window.clearTimeout(auto);
    };
  }, [onComplete]);

  return (
    <div
      data-screen="qrConnection"
      role="button"
      tabIndex={0}
      onClick={onComplete}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onComplete();
      }}
      className="hologram-bg-active memorial-screen-shell h-full flex items-center justify-center relative overflow-hidden min-h-0 bg-[#050505] cursor-pointer"
      aria-label="Eternal Beam"
    >
      <HolographicBackground />
      <HologramEffects />

      <motion.div
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{
          opacity: phase === "exit" ? 0 : phase === "shown" ? 1 : 0,
          scale: phase === "exit" ? 0.98 : phase === "shown" ? 1 : 0.96,
        }}
        transition={{
          duration: phase === "exit" ? SPLASH_FADE_MS / 1000 : 0.85,
          ease: [0.22, 1, 0.36, 1],
        }}
        className="relative z-10 px-8"
      >
        <div className="logo-holo-img-wrap splash-logo-wrap">
          <img
            src="/eternal-beam-logo-full.png?v=2"
            alt="Eternal Beam"
            className="splash-logo-full"
            draggable={false}
          />
        </div>
      </motion.div>
    </div>
  );
}
