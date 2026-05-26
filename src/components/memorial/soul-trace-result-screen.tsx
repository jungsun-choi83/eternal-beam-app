"use client";

import { useCallback, useState } from "react";
import { motion } from "framer-motion";
import { ArrowLeft, Share2 } from "lucide-react";
import { renderStorySharePng } from "@/lib/storyShareCard";

export interface SoulTraceResultPayload {
  childName: string;
  archetypeLabel: string;
  archetypeTitle: string;
  essence: string;
  letterKicker: string;
  /** Short on-screen excerpt; full narrative can be supplied via API later. */
  letterExcerpt: string;
  /** The single line used for 9:16 story export. */
  shareImpactLine: string;
}

const DEFAULT_PAYLOAD: SoulTraceResultPayload = {
  childName: "민준",
  archetypeLabel: "아이의 특징과 기질",
  archetypeTitle: "햇살 같은 아이",
  essence:
    "밝은 에너지로 주변을 따뜻하게 감싸며, 사랑을 표현하는 데 거침이 없습니다. 작은 순간도 행복으로 기억하려는 마음이 깊습니다.",
  letterKicker: "엄마, 아빠에게 보내는 미래의 편지",
  letterExcerpt:
    "안녕, 엄마 아빠. 나는 지금도 엄마 아빠 곁의 공기처럼 조용히 머물고 있어요. 슬퍼하지 말아요 — 우리가 나눴던 웃음은 시간 너머에서도 빛나거든요.",
  shareImpactLine: "엄마, 아빠를 향한 마음은 언제나 따뜻한 빛이 될 거예요.",
};

const CHAMPAGNE = "#e6d5b8";
const CHAMPAGNE_SOFT = "rgba(230, 213, 184, 0.82)";
const WARM_WHITE = "#f7f2e9";
const MUTED = "rgba(247, 242, 233, 0.38)";
const WHISPER = "rgba(247, 242, 233, 0.22)";

interface SoulTraceResultScreenProps {
  payload?: Partial<SoulTraceResultPayload>;
  onHome: () => void;
}

export function SoulTraceResultScreen({
  payload: partial,
  onHome,
}: SoulTraceResultScreenProps) {
  const p = { ...DEFAULT_PAYLOAD, ...partial };
  const [sharing, setSharing] = useState(false);

  const handleShare = useCallback(async () => {
    setSharing(true);
    try {
      const blob = await renderStorySharePng({
        childName: p.childName,
        impactLine: p.shareImpactLine,
        footer: "ETERNAL BEAM",
      });
      const file = new File([blob], "eternal-beam-story.png", {
        type: "image/png",
      });

      if (navigator.share && navigator.canShare?.({ files: [file] })) {
        await navigator.share({
          files: [file],
          title: "Soul Trace",
          text: `${p.childName} — ${p.shareImpactLine}`,
        });
      } else {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "eternal-beam-story.png";
        a.rel = "noopener";
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch (e) {
      console.error(e);
      alert(
        e instanceof Error
          ? e.message
          : "이미지를 만들지 못했습니다. 잠시 후 다시 시도해 주세요."
      );
    } finally {
      setSharing(false);
    }
  }, [p.childName, p.shareImpactLine]);

  return (
    <div
      className="h-full w-full overflow-y-auto overflow-x-hidden"
      style={{
        background: `
          radial-gradient(120% 80% at 50% -10%, rgba(201, 168, 96, 0.07) 0%, transparent 55%),
          radial-gradient(ellipse at 80% 100%, rgba(40, 36, 30, 0.9) 0%, transparent 50%),
          linear-gradient(165deg, #141210 0%, #0c0b09 45%, #080706 100%)
        `,
        fontFamily: '"Noto Serif KR", "Times New Roman", Georgia, serif',
      }}
    >
      {/* Paper-like grain */}
      <div
        className="pointer-events-none fixed inset-0 opacity-[0.035]"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")`,
          mixBlendMode: "overlay",
        }}
      />

      <header className="relative z-10 flex items-center justify-between px-6 pt-12 pb-6">
        <motion.button
          type="button"
          onClick={onHome}
          className="flex h-10 w-10 items-center justify-center rounded-full transition-opacity hover:opacity-90"
          style={{ color: MUTED }}
          whileTap={{ scale: 0.96 }}
          aria-label="Back home"
        >
          <ArrowLeft className="h-5 w-5" strokeWidth={1.25} />
        </motion.button>
        <span
          className="absolute left-1/2 -translate-x-1/2 text-[10px] font-medium uppercase tracking-[0.35em]"
          style={{ color: WHISPER, fontFamily: "Cormorant Garamond, serif" }}
        >
          Soul trace result
        </span>
        <div className="w-10" />
      </header>

      <div className="relative z-10 px-8 pb-28 pt-4">
        <motion.p
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="mb-3 text-center text-[11px] font-medium tracking-[0.12em]"
          style={{ color: CHAMPAGNE_SOFT }}
        >
          「{p.archetypeLabel}」
        </motion.p>

        <motion.h1
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.55, delay: 0.05 }}
          className="mb-10 text-center text-[1.85rem] font-semibold leading-snug tracking-tight sm:text-[2rem]"
          style={{
            color: CHAMPAGNE,
            textShadow: "0 1px 40px rgba(201, 168, 96, 0.12)",
          }}
        >
          {p.archetypeTitle}
        </motion.h1>

        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.15, duration: 0.6 }}
          className="mx-auto mb-16 max-w-[300px] text-center text-[15px] font-normal leading-[1.75]"
          style={{ color: WARM_WHITE }}
        >
          {p.essence}
        </motion.p>

        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.25, duration: 0.6 }}
          className="mx-auto max-w-[308px]"
        >
          <p
            className="mb-6 text-center text-[10px] font-medium uppercase tracking-[0.28em]"
            style={{ color: WHISPER }}
          >
            {p.letterKicker}
          </p>
          <p
            className="text-left text-[14px] font-normal leading-[2.05]"
            style={{ color: MUTED }}
          >
            {(() => {
              const body = p.letterExcerpt.trim();
              const first = body.charAt(0) || "";
              const rest = body.slice(1);
              return (
                <>
                  <span
                    className="float-left mr-2 mt-1 text-[2.75rem] font-semibold leading-none"
                    style={{ color: "rgba(201, 168, 96, 0.45)" }}
                    aria-hidden
                  >
                    {first}
                  </span>
                  <span style={{ color: MUTED }}>{rest}</span>
                </>
              );
            })()}
          </p>
        </motion.div>
      </div>

      <div
        className="fixed bottom-0 left-0 right-0 z-20 px-8 pb-10 pt-6"
        style={{
          background:
            "linear-gradient(180deg, transparent 0%, rgba(8,7,6,0.92) 35%, #080706 100%)",
        }}
      >
        <motion.button
          type="button"
          disabled={sharing}
          onClick={handleShare}
          className="mb-3 flex w-full items-center justify-center gap-2 py-4 text-[14px] font-medium tracking-wide transition-opacity disabled:opacity-50"
          style={{ color: CHAMPAGNE }}
          whileTap={{ scale: 0.99 }}
        >
          <Share2 className="h-4 w-4" strokeWidth={1.5} />
          {sharing ? "이미지 생성 중…" : "스토리용 이미지 공유"}
        </motion.button>
        <button
          type="button"
          onClick={onHome}
          className="w-full py-3 text-center text-[12px] tracking-[0.2em]"
          style={{ color: WHISPER }}
        >
          홈으로
        </button>
      </div>
    </div>
  );
}
