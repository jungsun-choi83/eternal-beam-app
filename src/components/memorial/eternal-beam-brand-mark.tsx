"use client";

import { memorialT } from "@/components/memorial/memorial-i18n";

const LOGO_FULL_SRC = "/eternal-beam-logo-full.png?v=1";
const LOGO_ICON_SRC = "/eternal-beam-logo.png?v=1";

interface EternalBeamLogoIconProps {
  size?: number;
  className?: string;
}

/** 로고 심볼만 (버튼·배지·NFC 태그 등) */
export function EternalBeamLogoIcon({ size = 20, className = "" }: EternalBeamLogoIconProps) {
  return (
    <span
      className={`eb-brand-mark shrink-0 ${className}`}
      style={{ width: size, height: size }}
      aria-hidden
    >
      <img
        src={LOGO_ICON_SRC}
        alt=""
        draggable={false}
        style={{ width: size, height: size, objectFit: "contain" }}
      />
    </span>
  );
}

interface EternalBeamLogoHeroProps {
  /** hero: 회원가입·홈 상단, compact: 작은 헤더 */
  size?: "hero" | "compact";
  className?: string;
  showGlow?: boolean;
}

/** 누끼 PNG — 심볼 + ETERNAL BEAM (검정 배경 위) */
export function EternalBeamLogoHero({
  size = "hero",
  className = "",
  showGlow = true,
}: EternalBeamLogoHeroProps) {
  const width = size === "hero" ? 240 : 168;

  return (
    <div className={`relative mx-auto ${className}`} style={{ width, maxWidth: "88vw" }}>
      {showGlow ? (
        <div
          className="absolute inset-0 blur-[48px] opacity-40 pointer-events-none"
          style={{
            background:
              "radial-gradient(ellipse at center, rgba(212, 175, 55, 0.55) 0%, transparent 70%)",
          }}
          aria-hidden
        />
      ) : null}
      <img
        src={LOGO_FULL_SRC}
        alt="Eternal Beam"
        draggable={false}
        className="relative w-full h-auto select-none"
        style={{ objectFit: "contain" }}
      />
    </div>
  );
}

interface EternalBeamBrandMarkProps {
  language?: string;
  className?: string;
  textClassName?: string;
}

/** 로고 심볼 + 브랜드명 (하단 워터마크 등) */
export function EternalBeamBrandMark({
  language = "ko",
  className = "",
  textClassName = "text-[11px] text-[#888] tracking-wide",
}: EternalBeamBrandMarkProps) {
  const label = memorialT(language).brand;

  return (
    <div className={`flex items-center justify-center gap-2 ${className}`}>
      <span className="eb-brand-mark shrink-0" aria-hidden>
        <img src={LOGO_ICON_SRC} alt="" draggable={false} />
      </span>
      <span className={textClassName}>{label}</span>
    </div>
  );
}
