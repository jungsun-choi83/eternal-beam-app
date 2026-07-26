"use client";

import { memorialT } from "@/components/memorial/memorial-i18n";

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
        src="/eternal-beam-logo.png"
        alt=""
        draggable={false}
        style={{ width: size }}
      />
    </span>
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
        <img src="/eternal-beam-logo.png" alt="" draggable={false} />
      </span>
      <span className={textClassName}>{label}</span>
    </div>
  );
}
