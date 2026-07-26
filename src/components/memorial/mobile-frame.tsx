"use client";

import { ReactNode } from "react";
import { motion } from "framer-motion";
import { MemorialSparkleField } from "@/components/memorial/memorial-sparkle-field";

interface MobileFrameProps {
  children: ReactNode;
}

export function MobileFrame({ children }: MobileFrameProps) {
  return (
    <motion.div 
      className="relative"
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.8, ease: [0.25, 0.46, 0.45, 0.94] }}
    >
      {/* Phone Frame - Apple-style minimal bezel */}
      <div
        className="relative w-full max-w-[430px] h-[100dvh] max-h-[100dvh] overflow-hidden md:w-[375px] md:h-[812px] md:max-h-[812px] md:rounded-[55px]"
        style={{
          background: "linear-gradient(180deg, #0a0a0b 0%, #050506 100%)",
          border: "1px solid rgba(255,255,255,0.06)",
          boxShadow: `
            0 48px 96px -24px rgba(0,0,0,0.85),
            inset 0 1px 0 rgba(255,255,255,0.04),
            0 0 120px rgba(201, 162, 39, 0.06)
          `,
        }}
      >
        {/* Dynamic Island — 데스크톱 미리보기용 */}
        <div
          className="absolute top-3 left-1/2 -translate-x-1/2 w-[126px] h-[37px] rounded-full z-50 hidden md:block"
          style={{
            background: "#000",
            boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.05)",
          }}
        >
          <div className="absolute right-4 top-1/2 -translate-y-1/2 flex items-center gap-2">
            <div className="w-3 h-3 rounded-full bg-[#1a1a1a]" />
            <div className="w-[6px] h-[6px] rounded-full bg-[#0a84ff]" />
          </div>
        </div>

        <div className="memorial-ui relative w-full h-full overflow-hidden safe-area-top safe-area-bottom bg-[#0a0a0a]">
          <MemorialSparkleField />
          <div className="relative z-[2] h-full w-full overflow-hidden">{children}</div>
        </div>

        <div
          className="absolute bottom-2 left-1/2 -translate-x-1/2 w-[134px] h-[5px] rounded-full hidden md:block"
          style={{
            background:
              "linear-gradient(90deg, rgba(201,162,39,0.2) 0%, rgba(201,162,39,0.4) 50%, rgba(201,162,39,0.2) 100%)",
          }}
        />
      </div>

      {/* Ambient Glow Effect */}
      <div className="absolute inset-0 -z-10 blur-[80px] opacity-40">
        <div 
          className="w-full h-full rounded-[55px]"
          style={{
            background: "radial-gradient(ellipse at center, rgba(201,162,39,0.15) 0%, transparent 60%)",
          }}
        />
      </div>
    </motion.div>
  );
}
