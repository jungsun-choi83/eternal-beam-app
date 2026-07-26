"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { ArrowLeft, Nfc } from "lucide-react";
import { HolographicBackground } from "@/components/memorial/holographic-background";
import { HologramEffects } from "@/components/memorial/hologram-effects";
import { ForestExperienceScreen } from "@/components/memorial/forest-experience-screen";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { subscribeNfcActivation } from "@/lib/nfc-activation";
import { subscribePiNfcEvents } from "@/lib/pi-sensor-bridge";

interface MemorialDevicePlayScreenProps {
  cutoutImage: string | null;
  language?: string;
  onBack: () => void;
}

export function MemorialDevicePlayScreen({
  cutoutImage,
  language = "ko",
  onBack,
}: MemorialDevicePlayScreenProps) {
  const [nfcActive, setNfcActive] = useState(false);
  const [piHint, setPiHint] = useState<string | null>(null);
  const t = memorialT(language).home;
  const waitHint =
    language === "ko"
      ? "NFC 카드를 리더에 대주세요"
      : "Tap your NFC card on the reader";

  useEffect(() => subscribeNfcActivation(() => setNfcActive(true)), []);
  useEffect(
    () =>
      subscribePiNfcEvents(() => setNfcActive(true), (msg) => {
        if (msg.includes('실패')) setPiHint(msg);
        else if (msg.includes('연결됨')) setPiHint(msg);
      }),
    [],
  );

  if (nfcActive) {
    return (
      <div className="relative h-full w-full">
        <ForestExperienceScreen
          language={language}
          publicDemo={false}
          onBack={onBack}
        />
        <div className="pointer-events-none absolute inset-x-0 top-0 z-[20] px-6 pt-8 text-center">
          <p className="logo-subtitle text-[11px] tracking-[0.28em] opacity-80">
            ETERNAL BEAM
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="hologram-bg-active memorial-screen-shell h-full flex flex-col relative overflow-hidden min-h-0">
      <HolographicBackground />
      <HologramEffects />

      <header className="px-6 pt-8 pb-4 relative z-10 shrink-0">
        <button
          type="button"
          onClick={onBack}
          className="mem-icon-btn relative shrink-0 mb-4"
          style={{
            background: "rgba(255, 255, 255, 0.08)",
            borderColor: "rgba(255, 255, 255, 0.14)",
          }}
        >
          <ArrowLeft className="w-5 h-5" style={{ color: "#E2E2E2" }} />
        </button>
        <div className="text-center relative">
          <div className="logo-holo-wrap">
            <h1 className="logo-title logo-title--holo relative">Eternal Beam</h1>
          </div>
          <p className="logo-subtitle">{t.subtitle}</p>
        </div>
      </header>

      <div className="flex-1 flex flex-col items-center justify-center px-8 relative z-10 gap-6">
        {cutoutImage ? (
          <motion.div
            initial={{ opacity: 0, scale: 0.92 }}
            animate={{ opacity: 1, scale: 1 }}
            className="relative w-56 h-56 rounded-full overflow-hidden"
            style={{
              boxShadow:
                "0 0 60px rgba(201, 162, 39, 0.3), inset 0 0 30px rgba(201, 162, 39, 0.1)",
            }}
          >
            <img src={cutoutImage} alt="" className="w-full h-full object-cover" />
          </motion.div>
        ) : null}

        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex flex-col items-center gap-2 text-center"
        >
          <Nfc className="w-8 h-8 text-emerald-300/90" strokeWidth={1.25} />
          <p className="text-base font-medium" style={{ color: "#F1E5D1" }}>
            {language === "ko" ? "숲속 테마 · 대기 중" : "Forest theme · waiting"}
          </p>
          <p className="text-sm memorial-body max-w-[240px]">{waitHint}</p>
          <p className="text-xs text-white/40 mt-2">
            {language === "ko"
              ? "카드 인식 후 숲 배경과 고야가 나타납니다"
              : "Forest and Goya appear after NFC"}
          </p>
          {piHint ? (
            <p className="text-xs text-amber-200/70 mt-3 max-w-[260px]">{piHint}</p>
          ) : null}
        </motion.div>
      </div>
    </div>
  );
}
