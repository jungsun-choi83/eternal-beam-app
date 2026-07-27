"use client";

import { memorialT } from "@/components/memorial/memorial-i18n";
import { EternalBeamLogoSymbol } from "@/components/memorial/eternal-beam-logo-symbol";

interface EternalBeamLogoIconProps {
  size?: number;
  className?: string;
}

/** 로고 심볼만 (버튼·배지·NFC 태그 등) */
export function EternalBeamLogoIcon({ size = 20, className = "" }: EternalBeamLogoIconProps) {
  return (
    <span
      className={`eb-brand-mark shrink-0 inline-flex ${className}`}
      style={{ width: size, height: size }}
      aria-hidden
    >
      <EternalBeamLogoSymbol size={size} className="eb-logo-symbol" />
    </span>
  );
}

interface EternalBeamLogoHeroProps {
  size?: "hero" | "compact" | "splash";
  className?: string;
  showGlow?: boolean;
  showSubtitle?: boolean;
  language?: string;
}

/** SVG 심볼 + CSS 타이포 */
export function EternalBeamLogoHero({
  size = "hero",
  className = "",
  showGlow = true,
  showSubtitle = true,
  language = "ko",
}: EternalBeamLogoHeroProps) {
  const symbolPx = size === "splash" ? 132 : size === "hero" ? 92 : 56;
  const subtitle = memorialT(language).auth.subtitle;

  return (
    <div className={`eb-logo-hero relative mx-auto text-center ${className}`}>
      {showGlow ? (
        <div
          className="absolute left-1/2 top-[20%] -translate-x-1/2 w-[150%] aspect-square pointer-events-none"
          style={{
            background:
              "radial-gradient(circle, rgba(212, 175, 55, 0.42) 0%, rgba(212, 175, 55, 0.08) 45%, transparent 70%)",
            filter: "blur(20px)",
          }}
          aria-hidden
        />
      ) : null}

      <div className="relative flex flex-col items-center gap-3">
        <EternalBeamLogoSymbol size={symbolPx} className="eb-logo-symbol select-none" />
        <div className="logo-holo-wrap">
          <p className="logo-title logo-title--holo m-0">Eternal Beam</p>
        </div>
        {showSubtitle ? (
          <p className="logo-subtitle m-0 mt-0.5">{subtitle}</p>
        ) : null}
      </div>
    </div>
  );
}

interface EternalBeamBrandMarkProps {
  language?: string;
  className?: string;
  textClassName?: string;
}

export function EternalBeamBrandMark({
  language = "ko",
  className = "",
  textClassName = "text-[11px] text-[#888] tracking-wide",
}: EternalBeamBrandMarkProps) {
  const label = memorialT(language).brand;

  return (
    <div className={`flex items-center justify-center gap-2 ${className}`}>
      <EternalBeamLogoIcon size={18} />
      <span className={textClassName}>{label}</span>
    </div>
  );
}
