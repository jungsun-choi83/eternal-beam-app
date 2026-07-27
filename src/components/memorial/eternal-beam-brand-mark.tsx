"use client";

import { memorialT } from "@/components/memorial/memorial-i18n";

const LOGO_SYMBOL_SRC = "/eternal-beam-logo-symbol.png?v=1";
const LOGO_ICON_SRC = "/eternal-beam-logo-symbol.png?v=1";

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
        className="eb-logo-symbol"
        style={{ width: size, height: size }}
      />
    </span>
  );
}

interface EternalBeamLogoHeroProps {
  /** hero: 스플래시·홈·회원가입, compact: 작은 헤더 */
  size?: "hero" | "compact" | "splash";
  className?: string;
  showGlow?: boolean;
  showSubtitle?: boolean;
  language?: string;
}

/**
 * 심볼 PNG(투명) + CSS 타이포 — full PNG 회색 박스 문제 회피
 */
export function EternalBeamLogoHero({
  size = "hero",
  className = "",
  showGlow = true,
  showSubtitle = true,
  language = "ko",
}: EternalBeamLogoHeroProps) {
  const symbolPx = size === "splash" ? 128 : size === "hero" ? 88 : 56;
  const subtitle = memorialT(language).auth.subtitle;

  return (
    <div className={`eb-logo-hero relative mx-auto text-center ${className}`}>
      {showGlow ? (
        <div
          className="absolute left-1/2 top-[18%] -translate-x-1/2 w-[140%] aspect-square pointer-events-none"
          style={{
            background:
              "radial-gradient(circle, rgba(212, 175, 55, 0.38) 0%, rgba(212, 175, 55, 0.08) 42%, transparent 68%)",
            filter: "blur(18px)",
          }}
          aria-hidden
        />
      ) : null}

      <div className="relative flex flex-col items-center gap-3">
        <img
          src={LOGO_SYMBOL_SRC}
          alt=""
          draggable={false}
          aria-hidden
          className="eb-logo-symbol select-none"
          style={{ width: symbolPx, height: symbolPx }}
        />
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

/** 로고 심볼 + 브랜드명 (하단 워터마크 등) */
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
