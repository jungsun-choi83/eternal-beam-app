"use client";

import { memorialT } from "@/components/memorial/memorial-i18n";
import { EternalBeamLogoSymbol } from "@/components/memorial/eternal-beam-logo-symbol";

interface EternalBeamLogoIconProps {
  size?: number;
  className?: string;
}

/** 로고 심볼만 (버튼·배지·NFC 태그 등) */
export function EternalBeamLogoIcon({ size = 20, className = "" }: EternalBeamLogoIconProps) {
  const height = Math.round(size * (72 / 80));

  return (
    <span
      className={`eb-brand-mark shrink-0 inline-flex items-center justify-center ${className}`}
      style={{ width: size, height }}
      aria-hidden
    >
      <EternalBeamLogoSymbol size={size} variant="icon" className="eb-logo-symbol eb-logo-symbol--icon" />
    </span>
  );
}

interface EternalBeamLogoHeroProps {
  size?: "hero" | "compact" | "splash";
  className?: string;
  showGlow?: boolean;
  showSubtitle?: boolean;
  /** splash: 홀로 shimmer 없이 정적 골드 타이포 */
  titleVariant?: "holo" | "brand";
  language?: string;
}

/** SVG 심볼 + CSS 타이포 */
export function EternalBeamLogoHero({
  size = "hero",
  className = "",
  showGlow = true,
  showSubtitle = true,
  titleVariant = "holo",
  language = "ko",
}: EternalBeamLogoHeroProps) {
  const symbolPx = size === "splash" ? 132 : size === "hero" ? 92 : 56;
  const subtitle = memorialT(language).auth.subtitle;
  const titleClass =
    titleVariant === "brand"
      ? "logo-title logo-title--splash m-0"
      : "logo-title logo-title--holo m-0";
  const useBackdropGlow = showGlow && size !== "splash";

  return (
    <div className={`eb-logo-hero relative mx-auto text-center ${className}`}>
      {useBackdropGlow ? (
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
        <EternalBeamLogoSymbol
          size={symbolPx}
          variant={size === "splash" ? "splash" : "default"}
          className="eb-logo-symbol select-none"
        />
        {titleVariant === "brand" ? (
          <p className={titleClass}>Eternal Beam</p>
        ) : (
          <div className="logo-holo-wrap">
            <p className={titleClass}>Eternal Beam</p>
          </div>
        )}
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
      <EternalBeamLogoIcon size={20} />
      <span className={textClassName}>{label}</span>
    </div>
  );
}
