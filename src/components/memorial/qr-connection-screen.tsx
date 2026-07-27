"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { HolographicBackground } from "@/components/memorial/holographic-background";
import { HologramEffects } from "@/components/memorial/hologram-effects";
import { EternalBeamLogoHero } from "@/components/memorial/eternal-beam-brand-mark";

interface QRConnectionScreenProps {
  language?: string;
  showBack?: boolean;
  onComplete: () => void;
  onBack: () => void;
  onSkip: () => void;
}

/** 기계 QR → 앱 첫 진입: 로고만 표시 후 회원가입으로 이동 */
export function QRConnectionScreen({
  language = "ko",
  onComplete,
}: QRConnectionScreenProps) {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const reveal = window.setTimeout(() => setReady(true), 80);
    const auto = window.setTimeout(() => onComplete(), 2400);
    return () => {
      window.clearTimeout(reveal);
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
        initial={{ opacity: 0, scale: 0.94 }}
        animate={{ opacity: ready ? 1 : 0, scale: ready ? 1 : 0.94 }}
        transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
        className="relative z-10 px-8"
      >
        <EternalBeamLogoHero
          size="splash"
          showGlow
          showSubtitle={false}
          language={language}
        />
      </motion.div>
    </div>
  );
}
